"""Coordinated durable and reusable-cache backup runtime."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from operations.control_manifest import ODOO_CONTROL_SQL, PAPERLESS_CONTROL_SQL

from operations.release_manifest import ReleaseManifestError, validate as validate_release


SCHEMA = "usl-recovery-cohort/v2"
LEGACY_SCHEMA = "usl-recovery-cohort/v1"
STATE_SCHEMA = "usl-recovery-cohort-state/v1"
RUN_ID = re.compile(r"[a-z0-9][a-z0-9._-]{7,95}\Z")
SNAPSHOT = re.compile(r"[0-9a-f]{64}\Z")
SHA256 = re.compile(r"[0-9a-f]{64}\Z")
COMMIT = re.compile(r"[0-9a-f]{40}\Z")
DATABASES = ("odoo", "paperless")
SIGN_SECRET_FILES = (
    "dss.env",
    "dss/client-trust.p12",
    "dss/local-trust.p12",
    "dss/manifest.crt",
    "dss/manifest.key",
    "dss/manifest.p12",
    "dss/platform.crt",
    "dss/platform.key",
    "dss/platform.p12",
    "dss/server.crt",
    "dss/server.key",
    "dss/server.p12",
    "odoo.env",
    "odoo/client-chain.crt",
    "odoo/client.crt",
    "odoo/client.key",
    "odoo/provisioner.jwk",
    "odoo/root_ca.crt",
    "offline-root/root_ca.crt",
    "offline-root/root_ca_key",
    "step-ca.env",
    "step-ca/certs/intermediate_ca.crt",
    "step-ca/certs/root_ca.crt",
    "step-ca/certs/usl-sign-ca.srl",
    "step-ca/config/ca.json",
    "step-ca/password",
    "step-ca/secrets/intermediate_ca_key",
    "step-ca/templates/personal-certificate.tpl",
)
PRIVATE_SIGN_SECRET_FILES = (
    "dss.env",
    "dss/client-trust.p12",
    "dss/local-trust.p12",
    "dss/manifest.key",
    "dss/manifest.p12",
    "dss/platform.key",
    "dss/platform.p12",
    "dss/server.key",
    "dss/server.p12",
    "odoo.env",
    "odoo/client.key",
    "odoo/provisioner.jwk",
    "offline-root/root_ca_key",
    "step-ca.env",
    "step-ca/password",
    "step-ca/secrets/intermediate_ca_key",
)


class CohortError(RuntimeError):
    """The coordinated backup or restore is incomplete or inconsistent."""


def run(
    command: list[str],
    *,
    environment: dict[str, str] | None = None,
    capture: bool = False,
) -> subprocess.CompletedProcess[str]:
    process = subprocess.run(
        command,
        check=False,
        env=environment,
        text=True,
        capture_output=capture,
    )
    if process.returncode:
        detail = (process.stderr or process.stdout or "").strip()
        raise CohortError(f"command failed ({' '.join(command)}): {detail}")
    return process


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def tree_identity(root: Path) -> dict[str, Any]:
    digest = hashlib.sha256()
    count = 0
    size = 0
    if not root.exists():
        return {"files": 0, "bytes": 0, "sha256": digest.hexdigest()}
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise CohortError(f"backup tree contains a symlink: {path}")
        if not path.is_file():
            continue
        relative = path.relative_to(root).as_posix()
        file_size = path.stat().st_size
        count += 1
        size += file_size
        digest.update(relative.encode())
        digest.update(b"\0")
        digest.update(str(file_size).encode())
        digest.update(b"\0")
        with path.open("rb") as stream:
            while chunk := stream.read(1024 * 1024):
                digest.update(chunk)
        digest.update(b"\0")
    return {"files": count, "bytes": size, "sha256": digest.hexdigest()}


def copy_tree(source: Path, destination: Path, *, required: bool = True) -> None:
    if not source.is_dir() or source.is_symlink():
        if required:
            raise CohortError(f"required source directory is missing: {source}")
        return
    destination.parent.mkdir(parents=True, exist_ok=True)
    run(["cp", "--archive", "--reflink=auto", str(source), str(destination)])


def validate_sign_secrets(root: Path) -> dict[str, Any]:
    """Validate complete Sign recovery material without exposing its contents."""
    if not root.is_dir() or root.is_symlink():
        raise CohortError("complete Sign secret root is missing or unsafe")
    if root.stat().st_mode & 0o077:
        raise CohortError("complete Sign secret root has unsafe permissions")
    for path in root.rglob("*"):
        if path.is_symlink():
            raise CohortError("Sign secret root contains a symlink")
        if path.stat().st_mode & 0o022:
            kind = "directory" if path.is_dir() else "file"
            raise CohortError(f"Sign secret {kind} is group- or world-writable")
    for relative in SIGN_SECRET_FILES:
        path = root / relative
        if not path.is_file() or path.stat().st_size < 1:
            raise CohortError(f"required Sign recovery material is missing: {relative}")
    for relative in PRIVATE_SIGN_SECRET_FILES:
        if (root / relative).stat().st_mode & 0o077:
            raise CohortError(f"private Sign recovery material has unsafe permissions: {relative}")
    database = root / "step-ca/db"
    if not database.is_dir() or not any(path.is_file() for path in database.iterdir()):
        raise CohortError("Step CA database is missing")
    for relative in ("step-ca/config/ca.json", "odoo/provisioner.jwk"):
        try:
            value = json.loads((root / relative).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise CohortError(f"Sign recovery JSON is invalid: {relative}") from error
        if not isinstance(value, dict) or not value:
            raise CohortError(f"Sign recovery JSON is empty: {relative}")
    return tree_identity(root)


def database_environment(prefix: str) -> tuple[list[str], dict[str, str]]:
    keys = {
        "host": f"{prefix}_DB_HOST",
        "port": f"{prefix}_DB_PORT",
        "name": f"{prefix}_DB_NAME",
        "user": f"{prefix}_DB_USER",
        "password": f"{prefix}_DB_PASSWORD",
    }
    values = {name: os.environ.get(key, "").strip() for name, key in keys.items()}
    if not all(values.values()):
        missing = [keys[name] for name, value in values.items() if not value]
        raise CohortError(f"database settings are missing: {', '.join(missing)}")
    command = [
        "--host",
        values["host"],
        "--port",
        values["port"],
        "--username",
        values["user"],
    ]
    environment = os.environ.copy()
    environment["PGPASSWORD"] = values["password"]
    return command, environment


def dump_database(prefix: str, destination: Path) -> dict[str, Any]:
    connection, environment = database_environment(prefix)
    name = os.environ[f"{prefix}_DB_NAME"]
    destination.parent.mkdir(parents=True, exist_ok=True)
    run(
        [
            "pg_dump",
            *connection,
            "--format=custom",
            "--no-owner",
            "--no-acl",
            "--file",
            str(destination),
            name,
        ],
        environment=environment,
    )
    if not destination.is_file() or destination.stat().st_size < 1:
        raise CohortError(f"{prefix} database dump is empty")
    run(["pg_restore", "--list", str(destination)], capture=True)
    return {
        "name": name,
        "bytes": destination.stat().st_size,
        "sha256": sha256_file(destination),
    }


def _required_environment(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise CohortError(f"{name} is required")
    return value


def validate_manifest(value: object) -> dict[str, Any]:
    expected = {
        "schema",
        "run_id",
        "created_at",
        "target",
        "release",
        "ollama",
        "databases",
        "controls",
        "durable",
        "cache",
        "resources",
        "cache_snapshot_id",
    }
    if not isinstance(value, dict) or set(value) != expected:
        raise CohortError("cohort manifest fields differ")
    if value["schema"] not in {SCHEMA, LEGACY_SCHEMA} or not RUN_ID.fullmatch(str(value["run_id"])):
        raise CohortError("cohort manifest identity is invalid")
    if not SHA256.fullmatch(str(value["release"].get("manifest_sha256", ""))):
        raise CohortError("release manifest digest is invalid")
    if not COMMIT.fullmatch(str(value["release"].get("commit", ""))):
        raise CohortError("release commit is invalid")
    if value["release"].get("path") != "durable/release.json":
        raise CohortError("release manifest path is invalid")
    if not SHA256.fullmatch(str(value["ollama"].get("manifest_sha256", ""))):
        raise CohortError("Ollama manifest digest is invalid")
    if value["ollama"].get("dimension") != 1024:
        raise CohortError("Ollama dimension must be 1024")
    if set(value["databases"]) != set(DATABASES):
        raise CohortError("database manifest is incomplete")
    for database in value["databases"].values():
        if not SHA256.fullmatch(str(database.get("sha256", ""))) or database.get("bytes", 0) < 1:
            raise CohortError("database dump identity is invalid")
    controls = value["controls"]
    if not isinstance(controls, dict) or set(controls) != {"odoo", "paperless"}:
        raise CohortError("business controls are incomplete")
    if controls["odoo"].get("ledger_delta") != 0:
        raise CohortError("captured Accounting ledger is unbalanced")
    for section in ("durable", "cache"):
        identity = value[section]
        if set(identity) != {"files", "bytes", "sha256"}:
            raise CohortError(f"{section} identity fields differ")
        if not SHA256.fullmatch(str(identity["sha256"])):
            raise CohortError(f"{section} digest is invalid")
    resources = value["resources"]
    if not isinstance(resources, dict) or not resources:
        raise CohortError("resource inventory is missing")
    for role, resource in resources.items():
        if set(resource) != {"class", "path", "identity"}:
            raise CohortError(f"resource {role} fields differ")
        if resource["class"] not in {"durable", "cache"}:
            raise CohortError(f"resource {role} class is invalid")
        if not isinstance(resource["path"], str) or resource["path"].startswith("/"):
            raise CohortError(f"resource {role} path is invalid")
        identity = resource["identity"]
        if set(identity) != {"files", "bytes", "sha256"} or not SHA256.fullmatch(
            str(identity["sha256"]),
        ):
            raise CohortError(f"resource {role} identity is invalid")
    if value["schema"] == SCHEMA and "sign_secrets" not in resources:
        raise CohortError("complete Sign recovery material is missing")
    cache_snapshot = value["cache_snapshot_id"]
    if cache_snapshot is not None and not SNAPSHOT.fullmatch(str(cache_snapshot)):
        raise CohortError("cache snapshot ID is invalid")
    return value


def capture(arguments: argparse.Namespace) -> dict[str, Any]:
    if not RUN_ID.fullmatch(arguments.run_id):
        raise CohortError("run ID is invalid")
    root = Path(arguments.root) / arguments.run_id
    if root.exists():
        raise CohortError(f"run already exists: {arguments.run_id}")
    partial = root.with_name(f".{arguments.run_id}.partial")
    partial.mkdir(parents=True, mode=0o700)
    durable = partial / "durable"
    cache = partial / "cache"
    try:
        databases = {
            "odoo": dump_database("ODOO", durable / "databases/odoo.dump"),
            "paperless": dump_database("PAPERLESS", durable / "databases/paperless.dump"),
        }
        controls = capture_controls()
        release_raw = _required_environment("USL_RELEASE_MANIFEST_JSON")
        if hashlib.sha256(release_raw.encode()).hexdigest() != _required_environment(
            "USL_RELEASE_MANIFEST_SHA256",
        ):
            raise CohortError("supplied release manifest differs from its digest")
        try:
            release_value = validate_release(json.loads(release_raw))
        except (json.JSONDecodeError, ReleaseManifestError) as error:
            raise CohortError("supplied release manifest is invalid JSON") from error
        if release_value.get("source", {}).get("commit") != _required_environment(
            "USL_RELEASE_COMMIT",
        ):
            raise CohortError("supplied release manifest commit differs")
        (durable / "release.json").write_text(release_raw, encoding="utf-8")
        odoo_database = os.environ["ODOO_DB_NAME"]
        copy_tree(
            Path("/source/odoo-data/filestore") / odoo_database,
            durable / "odoo-filestore" / odoo_database,
        )
        copy_tree(
            Path("/source/paperless-media/documents/originals"),
            durable / "paperless-media/documents/originals",
        )
        copy_tree(Path("/source/paperless-trash"), durable / "paperless-trash", required=False)
        copy_tree(Path("/source/paperless-consume"), durable / "paperless-consume", required=False)
        copy_tree(Path("/source/mcp-oauth"), durable / "mcp-oauth")
        sign_secrets_identity = validate_sign_secrets(Path("/source/sign-secrets"))
        copy_tree(Path("/source/sign-secrets"), durable / "sign-secrets")
        copy_tree(Path("/source/sign-evidence"), durable / "sign-evidence", required=False)
        copy_tree(
            Path("/source/paperless-media/documents/archive"),
            cache / "paperless-media/documents/archive",
        )
        copy_tree(
            Path("/source/paperless-media/documents/thumbnails"),
            cache / "paperless-media/documents/thumbnails",
        )
        copy_tree(Path("/source/paperless-data/index"), cache / "paperless-data/index")
        copy_tree(Path("/source/paperless-data/llm_index"), cache / "paperless-data/llm_index")
        resource_paths = {
            "odoo_filestore": ("durable", durable / "odoo-filestore" / odoo_database),
            "paperless_originals": ("durable", durable / "paperless-media/documents/originals"),
            "paperless_trash": ("durable", durable / "paperless-trash"),
            "paperless_consume": ("durable", durable / "paperless-consume"),
            "mcp_oauth": ("durable", durable / "mcp-oauth"),
            "sign_secrets": ("durable", durable / "sign-secrets"),
            "sign_evidence": ("durable", durable / "sign-evidence"),
            "paperless_archive": ("cache", cache / "paperless-media/documents/archive"),
            "paperless_thumbnails": ("cache", cache / "paperless-media/documents/thumbnails"),
            "paperless_tantivy": ("cache", cache / "paperless-data/index"),
            "paperless_vectors": ("cache", cache / "paperless-data/llm_index"),
        }
        resources = {
            role: {
                "class": classification,
                "path": path.relative_to(partial).as_posix(),
                "identity": tree_identity(path),
            }
            for role, (classification, path) in resource_paths.items()
        }
        if resources["sign_secrets"]["identity"] != sign_secrets_identity:
            raise CohortError("Sign recovery material changed during capture")
        manifest = {
            "schema": SCHEMA,
            "run_id": arguments.run_id,
            "created_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            "target": _required_environment("USL_TARGET"),
            "release": {
                "commit": _required_environment("USL_RELEASE_COMMIT"),
                "manifest_sha256": _required_environment("USL_RELEASE_MANIFEST_SHA256"),
                "path": "durable/release.json",
            },
            "ollama": {
                "model": _required_environment("USL_OLLAMA_MODEL"),
                "manifest_sha256": _required_environment("USL_OLLAMA_MANIFEST_SHA256"),
                "dimension": int(_required_environment("USL_OLLAMA_DIMENSION")),
            },
            "databases": databases,
            "controls": controls,
            "durable": tree_identity(durable),
            "cache": tree_identity(cache),
            "resources": resources,
            "cache_snapshot_id": None,
        }
        validate_manifest(manifest)
        (partial / "manifest.json").write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        partial.rename(root)
        return manifest
    except Exception:
        shutil.rmtree(partial, ignore_errors=True)
        raise


def query_json(prefix: str, query: str) -> dict[str, Any]:
    connection, environment = database_environment(prefix)
    name = os.environ[f"{prefix}_DB_NAME"]
    result = run(
        [
            "psql",
            *connection,
            "--dbname",
            name,
            "--tuples-only",
            "--no-align",
            "--command",
            query,
        ],
        environment=environment,
        capture=True,
    )
    try:
        value = json.loads(result.stdout.strip())
    except json.JSONDecodeError as error:
        raise CohortError(f"{prefix} control query did not return JSON") from error
    if not isinstance(value, dict):
        raise CohortError(f"{prefix} control query did not return an object")
    return value


def capture_controls() -> dict[str, dict[str, Any]]:
    odoo = query_json("ODOO", ODOO_CONTROL_SQL)
    paperless = query_json("PAPERLESS", PAPERLESS_CONTROL_SQL)
    return {"odoo": odoo, "paperless": paperless}


def restic_environment(repository_key: str, password_key: str) -> dict[str, str]:
    repository = _required_environment(repository_key)
    password = _required_environment(password_key)
    if len(password) < 20:
        raise CohortError(f"{password_key} must contain at least 20 characters")
    environment = os.environ.copy()
    environment["RESTIC_REPOSITORY"] = repository
    environment["RESTIC_PASSWORD"] = password
    return environment


def ensure_repository(environment: dict[str, str]) -> None:
    probe = subprocess.run(
        ["restic", "cat", "config"],
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )
    if probe.returncode == 0:
        return
    if probe.returncode != 10:
        detail = (probe.stderr or probe.stdout).strip()
        raise CohortError(
            "cannot inspect the Restic repository; refusing to initialize it"
            + (f": {detail}" if detail else ""),
        )
    run(["restic", "init"], environment=environment, capture=True)


def restic_backup(paths: list[Path], environment: dict[str, str], tags: list[str]) -> str:
    ensure_repository(environment)
    command = ["restic", "backup", *(str(path) for path in paths), "--json", "--host", "usl-odoo"]
    for tag in tags:
        command.extend(("--tag", tag))
    result = retry_restic(command, environment)
    snapshot = ""
    for line in result.stdout.splitlines():
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if item.get("message_type") == "summary":
            snapshot = item.get("snapshot_id", "")
    if not SNAPSHOT.fullmatch(snapshot):
        raise CohortError("Restic did not return a full snapshot ID")
    return snapshot


def retry_restic(
    command: list[str],
    environment: dict[str, str],
    *,
    attempts: int = 3,
) -> subprocess.CompletedProcess[str]:
    failure: CohortError | None = None
    for attempt in range(1, attempts + 1):
        try:
            return run(command, environment=environment, capture=True)
        except CohortError as error:
            failure = error
            if attempt < attempts:
                time.sleep(2 ** (attempt - 1))
    assert failure is not None
    raise failure


def resolve_tagged_snapshot(environment: dict[str, str], required_tags: set[str]) -> str:
    command = ["restic", "snapshots", "--json"]
    for tag in sorted(required_tags):
        command.extend(("--tag", tag))
    result = retry_restic(command, environment)
    try:
        snapshots = json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise CohortError("Restic snapshot inventory is invalid") from error
    if not isinstance(snapshots, list):
        raise CohortError("Restic snapshot inventory is invalid")
    matches = [
        item["id"]
        for item in snapshots
        if isinstance(item, dict)
        and SNAPSHOT.fullmatch(str(item.get("id", "")))
        and required_tags <= set(item.get("tags", []))
    ]
    if len(matches) != 1:
        raise CohortError("qualified Restic snapshot identity is not unique")
    return matches[0]


def push(arguments: argparse.Namespace) -> dict[str, Any]:
    root = Path(arguments.root) / arguments.run_id
    manifest_path = root / "manifest.json"
    state_path = root / "state.json"
    if state_path.is_file():
        state = json.loads(state_path.read_text(encoding="utf-8"))
        if (
            state.get("schema") == STATE_SCHEMA
            and state.get("run_id") == arguments.run_id
            and state.get("status") in {"uploaded", "qualified"}
            and SNAPSHOT.fullmatch(str(state.get("durable_snapshot_id", "")))
            and SNAPSHOT.fullmatch(str(state.get("cache_snapshot_id", "")))
        ):
            return state
    try:
        manifest = validate_manifest(json.loads(manifest_path.read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError) as error:
        raise CohortError("captured cohort manifest is missing or invalid") from error
    if tree_identity(root / "cache") != manifest["cache"]:
        raise CohortError("captured reusable cache changed before upload")
    cache_environment = restic_environment(
        "USL_BACKUP_CACHE_REPOSITORY",
        "USL_BACKUP_CACHE_PASSWORD",
    )
    cache_snapshot = manifest["cache_snapshot_id"]
    if cache_snapshot is None:
        cache_snapshot = restic_backup(
            [root / "cache"],
            cache_environment,
            ["usl-cohort", "cache", f"target-{manifest['target']}", f"run-{arguments.run_id}"],
        )
        manifest["cache_snapshot_id"] = cache_snapshot
        manifest_path.write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    manifest["durable"] = tree_identity(root / "durable")
    validate_manifest(manifest)
    durable_environment = restic_environment("RESTIC_REPOSITORY", "RESTIC_PASSWORD")
    durable_snapshot = restic_backup(
        [root / "durable", root / "manifest.json"],
        durable_environment,
        ["usl-cohort", "durable", "pending-verification", f"target-{manifest['target']}", f"run-{arguments.run_id}"],
    )
    state = {
        "schema": STATE_SCHEMA,
        "cohort_schema": manifest["schema"],
        "run_id": arguments.run_id,
        "target": manifest["target"],
        "durable_snapshot_id": durable_snapshot,
        "cache_snapshot_id": cache_snapshot,
        "status": "uploaded",
    }
    state_path.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return state


def resolve_snapshot_reference(environment: dict[str, str], snapshot: str) -> str:
    if not SNAPSHOT.fullmatch(snapshot):
        raise CohortError("snapshot must be a full 64-character ID")
    result = retry_restic(["restic", "snapshots", "--json"], environment)
    try:
        snapshots = json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise CohortError("Restic snapshot inventory is invalid") from error
    if not isinstance(snapshots, list):
        raise CohortError("Restic snapshot inventory is invalid")
    exact = [
        item["id"]
        for item in snapshots
        if isinstance(item, dict) and item.get("id") == snapshot
    ]
    if len(exact) == 1:
        return exact[0]
    rewritten = [
        item["id"]
        for item in snapshots
        if isinstance(item, dict)
        and item.get("original") == snapshot
        and SNAPSHOT.fullmatch(str(item.get("id", "")))
    ]
    if len(rewritten) != 1:
        raise CohortError("Restic snapshot reference is missing or ambiguous")
    return rewritten[0]


def restore_snapshot(snapshot: str, destination: Path, environment: dict[str, str]) -> str:
    snapshot = resolve_snapshot_reference(environment, snapshot)
    if destination.exists():
        raise CohortError(f"restore destination already exists: {destination}")
    destination.mkdir(parents=True, mode=0o700)
    retry_restic(
        ["restic", "restore", snapshot, "--target", str(destination)],
        environment,
    )
    return snapshot


def verify_embedded_release(cohort_root: Path, manifest: dict[str, Any]) -> dict[str, Any]:
    path = cohort_root / manifest["release"]["path"]
    try:
        value = validate_release(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError, ReleaseManifestError) as error:
        raise CohortError("embedded release manifest is missing or invalid") from error
    canonical = json.dumps(value, separators=(",", ":"), sort_keys=True)
    if hashlib.sha256(canonical.encode()).hexdigest() != manifest["release"]["manifest_sha256"]:
        raise CohortError("embedded release manifest digest differs")
    if value["source"]["commit"] != manifest["release"]["commit"]:
        raise CohortError("embedded release commit differs")
    return value


def verify(arguments: argparse.Namespace) -> dict[str, Any]:
    durable_environment = restic_environment("RESTIC_REPOSITORY", "RESTIC_PASSWORD")
    cache_environment = restic_environment(
        "USL_BACKUP_CACHE_REPOSITORY",
        "USL_BACKUP_CACHE_PASSWORD",
    )
    verify_root = Path(arguments.root) / f"verify-{arguments.durable_snapshot[:12]}"
    if verify_root.exists():
        shutil.rmtree(verify_root)
    try:
        durable_snapshot = restore_snapshot(
            arguments.durable_snapshot,
            verify_root / "durable-snapshot",
            durable_environment,
        )
        candidates = list((verify_root / "durable-snapshot").rglob("manifest.json"))
        if len(candidates) != 1:
            raise CohortError("durable snapshot has no unique cohort manifest")
        manifest = validate_manifest(json.loads(candidates[0].read_text(encoding="utf-8")))
        cohort_root = candidates[0].parent
        verify_embedded_release(cohort_root, manifest)
        if tree_identity(cohort_root / "durable") != manifest["durable"]:
            raise CohortError("restored durable tree differs from its manifest")
        if manifest["schema"] == SCHEMA:
            sign_identity = validate_sign_secrets(cohort_root / "durable/sign-secrets")
            if sign_identity != manifest["resources"]["sign_secrets"]["identity"]:
                raise CohortError("restored Sign recovery material differs from its manifest")
        cache_snapshot = manifest["cache_snapshot_id"]
        if not cache_snapshot:
            raise CohortError("durable snapshot does not bind a cache snapshot")
        cache_snapshot = restore_snapshot(
            cache_snapshot,
            verify_root / "cache-snapshot",
            cache_environment,
        )
        cache_roots = [path for path in (verify_root / "cache-snapshot").rglob("cache") if path.is_dir()]
        if len(cache_roots) != 1 or tree_identity(cache_roots[0]) != manifest["cache"]:
            raise CohortError("restored cache differs from the durable manifest")
        for name, metadata in manifest["databases"].items():
            path = cohort_root / "durable/databases" / f"{name}.dump"
            if path.stat().st_size != metadata["bytes"] or sha256_file(path) != metadata["sha256"]:
                raise CohortError(f"restored {name} dump differs from its manifest")
            run(["pg_restore", "--list", str(path)], capture=True)
        return {
            "schema": STATE_SCHEMA,
            "cohort_schema": manifest["schema"],
            "run_id": manifest["run_id"],
            "target": manifest["target"],
            "durable_snapshot_id": durable_snapshot,
            "cache_snapshot_id": cache_snapshot,
            "status": "verified",
        }
    finally:
        shutil.rmtree(verify_root, ignore_errors=True)


def qualify(arguments: argparse.Namespace) -> dict[str, Any]:
    state = verify(arguments)
    if state["target"] == "production" and state["cohort_schema"] != SCHEMA:
        raise CohortError("legacy production snapshot lacks complete Sign recovery material")
    durable_environment = restic_environment("RESTIC_REPOSITORY", "RESTIC_PASSWORD")
    retry_restic(
        [
            "restic",
            "tag",
            arguments.durable_snapshot,
            "--remove",
            "pending-verification",
            "--add",
            "recovery-eligible",
        ],
        durable_environment,
    )
    run_tag = f"run-{state['run_id']}"
    state["durable_snapshot_id"] = resolve_tagged_snapshot(
        durable_environment,
        {"usl-cohort", "durable", "recovery-eligible", run_tag},
    )
    state["status"] = "qualified"
    state_path = Path(arguments.root) / state["run_id"] / "state.json"
    if state_path.parent.is_dir():
        state_path.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return state


def list_snapshots(_arguments: argparse.Namespace) -> dict[str, Any]:
    environment = restic_environment("RESTIC_REPOSITORY", "RESTIC_PASSWORD")
    ensure_repository(environment)
    result = run(
        ["restic", "snapshots", "--json", "--tag", "usl-cohort", "--tag", "durable"],
        environment=environment,
        capture=True,
    )
    snapshots = json.loads(result.stdout)
    if not isinstance(snapshots, list):
        raise CohortError("Restic snapshot inventory is invalid")
    return {"schema": STATE_SCHEMA, "snapshots": snapshots}


def _empty_directory(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    entries = [entry for entry in path.iterdir() if entry.name != "lost+found"]
    if entries:
        raise CohortError(f"restore target is not empty: {path}")


def _copy_contents(source: Path, destination: Path) -> None:
    if not source.is_dir():
        raise CohortError(f"restored resource is missing: {source}")
    _empty_directory(destination)
    run(["cp", "--archive", "--reflink=auto", f"{source}/.", str(destination)])


def should_restore_resource(role: str, target_environment: str) -> bool:
    production_only = {"mcp_oauth", "sign_ca", "sign_secrets"}
    return role not in production_only or target_environment == "production"


def _reset_database(prefix: str, dump: Path) -> None:
    if _required_environment("USL_RESTORE_GENERATION_CONFIRMED") != _required_environment(
        "USL_EXPECTED_RESTORE_CONFIRMATION",
    ):
        raise CohortError("restore generation confirmation differs")
    connection, environment = database_environment(prefix)
    name = os.environ[f"{prefix}_DB_NAME"]
    run(["dropdb", *connection, "--if-exists", name], environment=environment)
    run(["createdb", *connection, name], environment=environment)
    run(
        ["pg_restore", *connection, "--dbname", name, "--no-owner", "--no-acl", str(dump)],
        environment=environment,
    )


def materialize(arguments: argparse.Namespace) -> dict[str, Any]:
    durable_environment = restic_environment("RESTIC_REPOSITORY", "RESTIC_PASSWORD")
    cache_environment = restic_environment(
        "USL_BACKUP_CACHE_REPOSITORY",
        "USL_BACKUP_CACHE_PASSWORD",
    )
    work = Path(arguments.root) / f"materialize-{arguments.durable_snapshot[:12]}"
    if work.exists():
        raise CohortError(f"materialization workspace already exists: {work}")
    durable_snapshot = restore_snapshot(
        arguments.durable_snapshot,
        work / "durable-snapshot",
        durable_environment,
    )
    candidates = list((work / "durable-snapshot").rglob("manifest.json"))
    if len(candidates) != 1:
        raise CohortError("durable snapshot has no unique cohort manifest")
    manifest = validate_manifest(json.loads(candidates[0].read_text(encoding="utf-8")))
    cohort_root = candidates[0].parent
    embedded_release = verify_embedded_release(cohort_root, manifest)
    embedded_canonical = json.dumps(embedded_release, separators=(",", ":"), sort_keys=True)
    cache_snapshot = manifest["cache_snapshot_id"]
    if not cache_snapshot:
        raise CohortError("durable snapshot does not bind a cache snapshot")
    cache_snapshot = restore_snapshot(
        cache_snapshot,
        work / "cache-snapshot",
        cache_environment,
    )
    cache_roots = [path for path in (work / "cache-snapshot").rglob("cache") if path.is_dir()]
    if len(cache_roots) != 1:
        raise CohortError("cache snapshot has no unique cache root")
    cache_root = cache_roots[0]
    if tree_identity(cohort_root / "durable") != manifest["durable"]:
        raise CohortError("restored durable tree differs before materialization")
    if tree_identity(cache_root) != manifest["cache"]:
        raise CohortError("restored cache tree differs before materialization")

    target_database = os.environ["ODOO_DB_NAME"]
    source_database = manifest["databases"]["odoo"]["name"]
    target_environment = _required_environment("USL_TARGET_ENVIRONMENT")
    if target_environment == "production" and manifest["schema"] != SCHEMA:
        raise CohortError("legacy production snapshot lacks complete Sign recovery material")
    transformations: list[str] = []
    destinations = {
        "odoo_filestore": Path("/target/odoo-data/filestore") / target_database,
        "paperless_originals": Path("/target/paperless-media/documents/originals"),
        "paperless_trash": Path("/target/paperless-trash"),
        "paperless_consume": Path("/target/paperless-consume"),
        "mcp_oauth": Path("/target/mcp-oauth"),
        "sign_ca": Path("/target/sign-secrets/step-ca"),
        "sign_secrets": Path("/target/sign-secrets"),
        "sign_evidence": Path("/target/sign-evidence"),
        "paperless_archive": Path("/target/paperless-media/documents/archive"),
        "paperless_thumbnails": Path("/target/paperless-media/documents/thumbnails"),
        "paperless_tantivy": Path("/target/paperless-data/index"),
        "paperless_vectors": Path("/target/paperless-data/llm_index"),
    }
    for role, metadata in manifest["resources"].items():
        if not should_restore_resource(role, target_environment):
            _empty_directory(destinations[role])
            transformations.append(f"{role}:target-isolated")
            continue
        source = cohort_root / metadata["path"] if metadata["class"] == "durable" else cache_root.parent / metadata["path"]
        if role == "odoo_filestore":
            source = cohort_root / "durable/odoo-filestore" / source_database
        if metadata["identity"]["files"] == 0 and not source.exists():
            destinations[role].mkdir(parents=True, exist_ok=True)
        else:
            _copy_contents(source, destinations[role])
        if tree_identity(destinations[role]) != metadata["identity"]:
            raise CohortError(f"materialized resource differs: {role}")
    sign_secrets_restored = target_environment == "production" and manifest["schema"] == SCHEMA
    if sign_secrets_restored:
        identity = validate_sign_secrets(destinations["sign_secrets"])
        if identity != manifest["resources"]["sign_secrets"]["identity"]:
            raise CohortError("materialized Sign recovery material differs")

    for name in DATABASES:
        dump = cohort_root / "durable/databases" / f"{name}.dump"
        metadata = manifest["databases"][name]
        if dump.stat().st_size != metadata["bytes"] or sha256_file(dump) != metadata["sha256"]:
            raise CohortError(f"restored {name} dump differs before database restore")
        _reset_database(name.upper(), dump)
    return {
        "schema": STATE_SCHEMA,
        "cohort_schema": manifest["schema"],
        "run_id": manifest["run_id"],
        "target": _required_environment("USL_TARGET"),
        "durable_snapshot_id": durable_snapshot,
        "cache_snapshot_id": cache_snapshot,
        "release": {
            "commit": embedded_release["source"]["commit"],
            "manifest_sha256": hashlib.sha256(embedded_canonical.encode()).hexdigest(),
        },
        "controls": manifest["controls"],
        "transformations": transformations,
        "sign_secrets_restored": sign_secrets_restored,
        "status": "materialized",
    }


def parser() -> argparse.ArgumentParser:
    command = argparse.ArgumentParser(description=__doc__)
    command.add_argument(
        "action",
        choices=("capture", "push", "verify", "qualify", "list", "materialize"),
    )
    command.add_argument("--root", default="/cohort")
    command.add_argument("--run-id")
    command.add_argument("--durable-snapshot")
    return command


def main() -> int:
    arguments = parser().parse_args()
    try:
        if arguments.action in {"capture", "push"} and not arguments.run_id:
            raise CohortError(f"{arguments.action} requires --run-id")
        if arguments.action in {"verify", "qualify", "materialize"} and not arguments.durable_snapshot:
            raise CohortError(f"{arguments.action} requires --durable-snapshot")
        handler = list_snapshots if arguments.action == "list" else globals()[arguments.action]
        result = handler(arguments)
        print(json.dumps(result, sort_keys=True))
        return 0
    except (CohortError, OSError, ValueError, json.JSONDecodeError) as error:
        print(f"cohort: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
