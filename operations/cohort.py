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
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


SCHEMA = "usl-recovery-cohort/v1"
STATE_SCHEMA = "usl-recovery-cohort-state/v1"
RUN_ID = re.compile(r"[a-z0-9][a-z0-9._-]{7,95}\Z")
SNAPSHOT = re.compile(r"[0-9a-f]{64}\Z")
SHA256 = re.compile(r"[0-9a-f]{64}\Z")
DATABASES = ("odoo", "paperless")


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
        "durable",
        "cache",
        "cache_snapshot_id",
    }
    if not isinstance(value, dict) or set(value) != expected:
        raise CohortError("cohort manifest fields differ")
    if value["schema"] != SCHEMA or not RUN_ID.fullmatch(str(value["run_id"])):
        raise CohortError("cohort manifest identity is invalid")
    if not SHA256.fullmatch(str(value["release"].get("manifest_sha256", ""))):
        raise CohortError("release manifest digest is invalid")
    if not SHA256.fullmatch(str(value["ollama"].get("manifest_sha256", ""))):
        raise CohortError("Ollama manifest digest is invalid")
    if value["ollama"].get("dimension") != 1024:
        raise CohortError("Ollama dimension must be 1024")
    if set(value["databases"]) != set(DATABASES):
        raise CohortError("database manifest is incomplete")
    for database in value["databases"].values():
        if not SHA256.fullmatch(str(database.get("sha256", ""))) or database.get("bytes", 0) < 1:
            raise CohortError("database dump identity is invalid")
    for section in ("durable", "cache"):
        identity = value[section]
        if set(identity) != {"files", "bytes", "sha256"}:
            raise CohortError(f"{section} identity fields differ")
        if not SHA256.fullmatch(str(identity["sha256"])):
            raise CohortError(f"{section} digest is invalid")
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
        copy_tree(Path("/source/sign-ca"), durable / "sign-ca")
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
        manifest = {
            "schema": SCHEMA,
            "run_id": arguments.run_id,
            "created_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            "target": _required_environment("USL_TARGET"),
            "release": {
                "commit": _required_environment("USL_RELEASE_COMMIT"),
                "manifest_sha256": _required_environment("USL_RELEASE_MANIFEST_SHA256"),
            },
            "ollama": {
                "model": _required_environment("USL_OLLAMA_MODEL"),
                "manifest_sha256": _required_environment("USL_OLLAMA_MANIFEST_SHA256"),
                "dimension": int(_required_environment("USL_OLLAMA_DIMENSION")),
            },
            "databases": databases,
            "durable": tree_identity(durable),
            "cache": tree_identity(cache),
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
    if probe.returncode:
        run(["restic", "init"], environment=environment, capture=True)


def restic_backup(paths: list[Path], environment: dict[str, str], tags: list[str]) -> str:
    ensure_repository(environment)
    command = ["restic", "backup", *(str(path) for path in paths), "--json", "--host", "usl-odoo"]
    for tag in tags:
        command.extend(("--tag", tag))
    result = run(command, environment=environment, capture=True)
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


def push(arguments: argparse.Namespace) -> dict[str, Any]:
    root = Path(arguments.root) / arguments.run_id
    manifest_path = root / "manifest.json"
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
    cache_snapshot = restic_backup(
        [root / "cache"],
        cache_environment,
        ["usl-cohort", "cache", f"target-{manifest['target']}", f"run-{arguments.run_id}"],
    )
    manifest["cache_snapshot_id"] = cache_snapshot
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    manifest["durable"] = tree_identity(root / "durable")
    validate_manifest(manifest)
    durable_environment = restic_environment("RESTIC_REPOSITORY", "RESTIC_PASSWORD")
    durable_snapshot = restic_backup(
        [root / "durable", root / "manifest.json"],
        durable_environment,
        ["usl-cohort", "durable", "recovery-eligible", f"target-{manifest['target']}", f"run-{arguments.run_id}"],
    )
    state = {
        "schema": STATE_SCHEMA,
        "run_id": arguments.run_id,
        "target": manifest["target"],
        "durable_snapshot_id": durable_snapshot,
        "cache_snapshot_id": cache_snapshot,
        "status": "uploaded",
    }
    (root / "state.json").write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return state


def restore_snapshot(snapshot: str, destination: Path, environment: dict[str, str]) -> None:
    if not SNAPSHOT.fullmatch(snapshot):
        raise CohortError("snapshot must be a full 64-character ID")
    if destination.exists():
        raise CohortError(f"restore destination already exists: {destination}")
    destination.mkdir(parents=True, mode=0o700)
    run(
        ["restic", "restore", snapshot, "--target", str(destination)],
        environment=environment,
        capture=True,
    )


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
        restore_snapshot(arguments.durable_snapshot, verify_root / "durable-snapshot", durable_environment)
        candidates = list((verify_root / "durable-snapshot").rglob("manifest.json"))
        if len(candidates) != 1:
            raise CohortError("durable snapshot has no unique cohort manifest")
        manifest = validate_manifest(json.loads(candidates[0].read_text(encoding="utf-8")))
        cohort_root = candidates[0].parent
        if tree_identity(cohort_root / "durable") != manifest["durable"]:
            raise CohortError("restored durable tree differs from its manifest")
        cache_snapshot = manifest["cache_snapshot_id"]
        if not cache_snapshot:
            raise CohortError("durable snapshot does not bind a cache snapshot")
        restore_snapshot(cache_snapshot, verify_root / "cache-snapshot", cache_environment)
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
            "run_id": manifest["run_id"],
            "target": manifest["target"],
            "durable_snapshot_id": arguments.durable_snapshot,
            "cache_snapshot_id": cache_snapshot,
            "status": "verified",
        }
    finally:
        shutil.rmtree(verify_root, ignore_errors=True)


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


def parser() -> argparse.ArgumentParser:
    command = argparse.ArgumentParser(description=__doc__)
    command.add_argument("action", choices=("capture", "push", "verify", "list"))
    command.add_argument("--root", default="/cohort")
    command.add_argument("--run-id")
    command.add_argument("--durable-snapshot")
    return command


def main() -> int:
    arguments = parser().parse_args()
    try:
        if arguments.action in {"capture", "push"} and not arguments.run_id:
            raise CohortError(f"{arguments.action} requires --run-id")
        if arguments.action == "verify" and not arguments.durable_snapshot:
            raise CohortError("verify requires --durable-snapshot")
        handler = list_snapshots if arguments.action == "list" else globals()[arguments.action]
        result = handler(arguments)
        print(json.dumps(result, sort_keys=True))
        return 0
    except (CohortError, OSError, ValueError, json.JSONDecodeError) as error:
        print(f"cohort: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
