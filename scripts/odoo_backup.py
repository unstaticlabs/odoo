#!/usr/bin/env python3
"""Fail-closed runtime for production Odoo backup and restore stages."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any, Iterable


SCHEMA = "usl-odoo-backup/v1"
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
SNAPSHOT_RE = re.compile(r"^[0-9a-f]{64}$")
BACKUP_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{7,95}$")
SOURCE_IMAGE_RE = re.compile(r"^ghcr\.io/unstaticlabs/usl-odoo@sha256:[0-9a-f]{64}$")
TOOL_IMAGE_RE = re.compile(r"^ghcr\.io/unstaticlabs/usl-odoo-operations@sha256:[0-9a-f]{64}$")
PRODUCTION_REPOSITORY_RE = re.compile(r"/usl-backups/odoo-production/(?:prod|qualification)(?:/|$)")
COUNT_TABLES = (
    "res_users",
    "res_company",
    "ir_module_module",
    "res_partner",
    "account_move",
    "ir_attachment",
)
NONEMPTY_TABLES = ("res_users", "res_company", "ir_module_module", "res_partner")


class BackupError(RuntimeError):
    pass


def fail(message: str) -> None:
    raise BackupError(message)


def require_safe_identifier(value: str, label: str) -> str:
    if not isinstance(value, str) or not BACKUP_ID_RE.fullmatch(value):
        fail(f"{label} must match {BACKUP_ID_RE.pattern}")
    return value


def require_snapshot(value: str) -> str:
    if not isinstance(value, str) or not SNAPSHOT_RE.fullmatch(value):
        fail("snapshot must be the full 64-character Restic snapshot ID")
    return value


def secret(name: str) -> str:
    direct = os.environ.get(name, "")
    file_name = os.environ.get(f"{name}_FILE", "")
    if direct and file_name:
        fail(f"set only one of {name} or {name}_FILE")
    if file_name:
        path = Path(file_name)
        if not path.is_file():
            fail(f"{name}_FILE does not name a readable file")
        direct = path.read_text(encoding="utf-8").strip()
    if not direct:
        fail(f"required secret binding {name} or {name}_FILE is missing")
    return direct


def restic_environment() -> dict[str, str]:
    repository = os.environ.get("RESTIC_REPOSITORY", "").strip()
    if not repository:
        fail("RESTIC_REPOSITORY is required")
    if "odoo-qa" in repository or not PRODUCTION_REPOSITORY_RE.search(repository):
        fail("RESTIC_REPOSITORY must use the dedicated odoo-production prod or qualification path")
    password = secret("RESTIC_PASSWORD")
    if len(password) < 20:
        fail("RESTIC_PASSWORD must contain at least 20 characters")
    result = os.environ.copy()
    result.update(
        {
            "RESTIC_REPOSITORY": repository,
            "RESTIC_PASSWORD": password,
            "AWS_ACCESS_KEY_ID": secret("AWS_ACCESS_KEY_ID"),
            "AWS_SECRET_ACCESS_KEY": secret("AWS_SECRET_ACCESS_KEY"),
        }
    )
    result.pop("RESTIC_PASSWORD_FILE", None)
    result.pop("AWS_ACCESS_KEY_ID_FILE", None)
    result.pop("AWS_SECRET_ACCESS_KEY_FILE", None)
    return result


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def safe_store_name(value: str) -> PurePosixPath:
    candidate = PurePosixPath(value)
    if not value or candidate.is_absolute() or ".." in candidate.parts or "." in candidate.parts:
        fail(f"unsafe ir_attachment.store_fname: {value!r}")
    if any(not part or part.startswith(".") for part in candidate.parts):
        fail(f"unsafe ir_attachment.store_fname: {value!r}")
    return candidate


def filestore_metadata(root: Path, store_names: Iterable[str]) -> dict[str, int]:
    count = 0
    total = 0
    for path in root.rglob("*") if root.is_dir() else ():
        if path.is_symlink():
            fail(f"filestore contains a symlink: {path}")
        if path.is_file():
            count += 1
            total += path.stat().st_size
    missing = 0
    stored = 0
    for raw in store_names:
        relative = safe_store_name(raw)
        stored += 1
        candidate = root.joinpath(*relative.parts)
        if not candidate.is_file() or candidate.is_symlink():
            missing += 1
    return {
        "file_count": count,
        "total_bytes": total,
        "stored_attachment_count": stored,
        "missing_attachment_count": missing,
    }


def _require_exact_keys(value: Any, keys: Iterable[str], label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        fail(f"{label} must be an object")
    expected = set(keys)
    actual = set(value)
    if actual != expected:
        missing = sorted(expected - actual)
        extra = sorted(actual - expected)
        details = []
        if missing:
            details.append(f"missing {', '.join(missing)}")
        if extra:
            details.append(f"unexpected {', '.join(extra)}")
        fail(f"{label} has invalid fields: {'; '.join(details)}")
    return value


def _non_negative_integer(value: Any, label: str, *, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        fail(f"{label} must be an integer greater than or equal to {minimum}")
    return value


def validate_manifest(value: dict[str, Any]) -> dict[str, Any]:
    value = _require_exact_keys(
        value,
        ("schema", "backup_id", "created_at", "consistency", "source", "tool", "database", "filestore"),
        "manifest",
    )
    if value["schema"] != SCHEMA:
        fail(f"unsupported manifest schema: {value['schema']!r}")
    require_safe_identifier(value["backup_id"], "backup_id")
    try:
        timestamp = datetime.fromisoformat(value["created_at"].replace("Z", "+00:00"))
    except (AttributeError, ValueError) as exc:
        raise BackupError("created_at must be an ISO-8601 timestamp") from exc
    if timestamp.tzinfo is None:
        fail("created_at must include a timezone")
    if not isinstance(value["consistency"], str) or value["consistency"] not in {"live", "quiesced"}:
        fail("consistency must be live or quiesced")
    source = _require_exact_keys(
        value["source"],
        ("database", "postgres_version", "postgres_version_num", "pg_dump_version", "git_sha", "image_digest_reference"),
        "source",
    )
    tool = _require_exact_keys(value["tool"], ("image_digest_reference",), "tool")
    database = _require_exact_keys(
        value["database"], ("dump_file", "dump_bytes", "dump_sha256", "row_counts"), "database"
    )
    filestore = _require_exact_keys(
        value["filestore"],
        ("directory", "file_count", "total_bytes", "stored_attachment_count", "missing_attachment_count"),
        "filestore",
    )
    if not isinstance(source["database"], str) or not source["database"]:
        fail("source.database must be a non-empty string")
    if not isinstance(source["postgres_version"], str) or not source["postgres_version"]:
        fail("source.postgres_version must be a non-empty string")
    if not SHA_RE.fullmatch(str(source["git_sha"])):
        fail("source.git_sha must be a full Git SHA")
    if not SOURCE_IMAGE_RE.fullmatch(str(source["image_digest_reference"])):
        fail("source.image_digest_reference must be the immutable USL Odoo image")
    if not TOOL_IMAGE_RE.fullmatch(str(tool["image_digest_reference"])):
        fail("tool.image_digest_reference must be the immutable USL backup image")
    version_num = _non_negative_integer(source["postgres_version_num"], "source.postgres_version_num")
    if version_num // 10000 != 16:
        fail("source PostgreSQL major version must be 16")
    if not re.match(r"^16(?:\.|$)", str(source["pg_dump_version"])):
        fail("pg_dump major version must be 16")
    if database["dump_file"] != "database.dump":
        fail("database dump metadata is invalid")
    _non_negative_integer(database["dump_bytes"], "database.dump_bytes", minimum=1)
    if not re.fullmatch(r"[0-9a-f]{64}", str(database["dump_sha256"])):
        fail("database.dump_sha256 is invalid")
    counts = _require_exact_keys(database["row_counts"], COUNT_TABLES, "database.row_counts")
    for table in COUNT_TABLES:
        _non_negative_integer(counts[table], f"database.row_counts.{table}")
    for table in NONEMPTY_TABLES:
        if counts[table] < 1:
            fail(f"database.row_counts.{table} must be non-zero")
    if filestore["directory"] != "filestore":
        fail("filestore.directory must be filestore")
    for key in ("file_count", "total_bytes", "stored_attachment_count", "missing_attachment_count"):
        _non_negative_integer(filestore[key], f"filestore.{key}")
    if filestore["missing_attachment_count"] != 0:
        fail("backup contains attachment references missing from the filestore")
    return value


def read_manifest(root: Path) -> dict[str, Any]:
    path = root / "manifest.json"
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise BackupError(f"cannot read valid manifest at {path}") from exc
    if not isinstance(value, dict):
        fail("manifest root must be an object")
    return validate_manifest(value)


def write_json(path: Path, value: dict[str, Any]) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def pipeline_state_path() -> Path:
    return Path(os.environ.get("ODOO_BACKUP_STATE", "/state")) / "current.json"


def read_pipeline_state(*, phases: set[str] | None = None) -> dict[str, Any]:
    path = pipeline_state_path()
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise BackupError("scheduled pipeline state is missing or invalid") from exc
    if not isinstance(value, dict) or value.get("schema") != "usl-odoo-backup-pipeline/v1":
        fail("scheduled pipeline state has the wrong schema")
    if phases is not None and value.get("phase") not in phases:
        fail(f"scheduled pipeline phase is {value.get('phase')!r}; expected one of {sorted(phases)}")
    return value


def update_pipeline_state(**updates: Any) -> dict[str, Any]:
    path = pipeline_state_path()
    if path.is_file():
        value = read_pipeline_state()
    else:
        value = {"schema": "usl-odoo-backup-pipeline/v1"}
    value.update(updates)
    path.parent.mkdir(parents=True, exist_ok=True)
    write_json(path, value)
    return value


def run(command: list[str], *, env: dict[str, str] | None = None, capture: bool = False) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, check=True, env=env, text=True, capture_output=capture)


def db_connection(database: str):
    try:
        import psycopg2
    except ImportError as exc:
        raise BackupError("psycopg2 is required inside the backup runtime") from exc
    password = secret("PGPASSWORD")
    return psycopg2.connect(
        host=os.environ.get("PGHOST", "db"),
        port=int(os.environ.get("PGPORT", "5432")),
        user=os.environ.get("PGUSER", "odoo"),
        password=password,
        dbname=database,
        connect_timeout=15,
        application_name="usl-odoo-backup",
    )


def table_counts(cursor: Any) -> dict[str, int]:
    missing = [table for table in COUNT_TABLES if not table_exists(cursor, table)]
    if missing:
        fail(f"database is missing required table(s): {', '.join(missing)}")
    counts: dict[str, int] = {}
    for table in COUNT_TABLES:
        cursor.execute(f'SELECT count(*) FROM "{table}"')  # fixed allow-list
        counts[table] = int(cursor.fetchone()[0])
    return counts


def stored_attachments(cursor: Any) -> list[str]:
    cursor.execute("SELECT store_fname FROM ir_attachment WHERE store_fname IS NOT NULL")
    return [row[0] for row in cursor.fetchall()]


def table_exists(cursor: Any, table: str) -> bool:
    cursor.execute("SELECT to_regclass(%s) IS NOT NULL", (f"public.{table}",))
    return bool(cursor.fetchone()[0])


def pg_dump_version() -> str:
    output = run(["pg_dump", "--version"], capture=True).stdout.strip()
    match = re.search(r"(\d+(?:\.\d+)*)", output)
    if not match:
        fail("unable to determine pg_dump version")
    return match.group(1)


def prepare(args: argparse.Namespace) -> None:
    scheduled = args.backup_id is None
    if scheduled and pipeline_state_path().is_file():
        read_pipeline_state(phases={"verified", "abandoned"})
    git_sha = os.environ.get("USL_SOURCE_GIT_SHA", "")
    generated_id = f"{datetime.now(UTC):%Y%m%dt%H%M%Sz}-{git_sha[:8]}"
    backup_id = require_safe_identifier(args.backup_id or generated_id, "backup_id")
    database_name = os.environ.get("ODOO_DB_NAME", "").strip()
    if (
        not database_name
        or database_name in {".", ".."}
        or not re.fullmatch(r"[A-Za-z0-9_.-]+", database_name)
    ):
        fail("ODOO_DB_NAME is required and contains an unsafe character")
    source_image = os.environ.get("USL_SOURCE_IMAGE_DIGEST", "")
    tool_image = os.environ.get("USL_OPERATIONS_IMAGE_DIGEST", "")
    if not SHA_RE.fullmatch(git_sha):
        fail("USL_SOURCE_GIT_SHA must be a full Git SHA")
    if not SOURCE_IMAGE_RE.fullmatch(source_image):
        fail("USL_SOURCE_IMAGE_DIGEST must be an immutable USL Odoo digest reference")
    if not TOOL_IMAGE_RE.fullmatch(tool_image):
        fail("USL_OPERATIONS_IMAGE_DIGEST must be an immutable USL operations-tool digest reference")
    if args.mode == "quiesced" and os.environ.get("USL_BACKUP_QUIESCED_CONFIRMED") != "odoo-writers-stopped":
        fail("quiesced mode requires USL_BACKUP_QUIESCED_CONFIRMED=odoo-writers-stopped")

    staging = Path(os.environ.get("ODOO_BACKUP_STAGING", "/staging"))
    final = staging / "backups" / backup_id
    partial = staging / "backups" / f".{backup_id}.partial"
    if final.exists() or partial.exists():
        fail(f"backup staging already exists for {backup_id}")
    partial.mkdir(parents=True, mode=0o700)
    dump_path = partial / "database.dump"

    connection = db_connection(database_name)
    try:
        connection.set_session(isolation_level="REPEATABLE READ", readonly=True, autocommit=False)
        with connection.cursor() as cursor:
            cursor.execute("SELECT current_database(), version(), current_setting('server_version_num')::int")
            current_database, server_version, server_version_num = cursor.fetchone()
            if current_database != database_name:
                fail("database connection resolved to the wrong database")
            if server_version_num // 10000 != 16:
                fail(f"PostgreSQL server major must be 16, found {server_version_num}")
            dump_version = pg_dump_version()
            if int(dump_version.split(".", 1)[0]) != server_version_num // 10000:
                fail(f"pg_dump {dump_version} does not match PostgreSQL server major {server_version_num // 10000}")
            if args.mode == "quiesced":
                cursor.execute(
                    "SELECT count(*) FROM pg_stat_activity "
                    "WHERE datname=current_database() AND pid<>pg_backend_pid() AND backend_type='client backend'"
                )
                other_sessions = int(cursor.fetchone()[0])
                if other_sessions:
                    fail(f"quiesced backup found {other_sessions} other database session(s)")
            cursor.execute("SELECT pg_export_snapshot()")
            snapshot = cursor.fetchone()[0]
            counts = table_counts(cursor)
            for table in NONEMPTY_TABLES:
                if counts[table] < 1:
                    fail(f"source database is not meaningful: {table} has zero rows")
            attachments = stored_attachments(cursor)
            dump_env = os.environ.copy()
            dump_env["PGPASSWORD"] = secret("PGPASSWORD")
            run(
                [
                    "pg_dump",
                    "--format=custom",
                    "--no-owner",
                    "--no-acl",
                    f"--snapshot={snapshot}",
                    "--file",
                    str(dump_path),
                    database_name,
                ],
                env=dump_env,
            )
        connection.commit()
    finally:
        connection.close()

    if not dump_path.is_file() or dump_path.stat().st_size < 1:
        fail("pg_dump did not produce a non-empty custom-format dump")
    source_root = Path(os.environ.get("ODOO_DATA_ROOT", "/source-data")) / "filestore" / database_name
    if not source_root.is_dir() or source_root.is_symlink():
        fail(f"source filestore does not exist: {source_root}")
    source_metadata = filestore_metadata(source_root, attachments)
    if source_metadata["missing_attachment_count"]:
        fail(f"{source_metadata['missing_attachment_count']} stored attachment(s) are absent from the source filestore")
    destination = partial / "filestore"
    shutil.copytree(source_root, destination, symlinks=True)
    metadata = filestore_metadata(destination, attachments)
    if metadata["missing_attachment_count"]:
        fail(f"{metadata['missing_attachment_count']} stored attachment(s) are absent from the copied filestore")
    manifest = {
        "schema": SCHEMA,
        "backup_id": backup_id,
        "created_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "consistency": args.mode,
        "source": {
            "database": database_name,
            "postgres_version": server_version,
            "postgres_version_num": server_version_num,
            "pg_dump_version": dump_version,
            "git_sha": git_sha,
            "image_digest_reference": source_image,
        },
        "tool": {"image_digest_reference": tool_image},
        "database": {
            "dump_file": "database.dump",
            "dump_bytes": dump_path.stat().st_size,
            "dump_sha256": sha256(dump_path),
            "row_counts": counts,
        },
        "filestore": {"directory": "filestore", **metadata},
    }
    validate_manifest(manifest)
    write_json(partial / "manifest.json", manifest)
    partial.rename(final)
    if scheduled:
        update_pipeline_state(
            phase="prepared",
            backup_id=backup_id,
            consistency=args.mode,
            snapshot_id=None,
            clone_id=None,
        )
    print(json.dumps({"backup_id": backup_id, "stage": "prepare", "status": "passed"}, sort_keys=True))


def _restic_json(command: list[str], env: dict[str, str]) -> Any:
    result = run(["restic", *command], env=env, capture=True)
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise BackupError(f"Restic returned invalid JSON for {' '.join(command)}") from exc


def preflight(_args: argparse.Namespace) -> None:
    environment = restic_environment()
    print(
        json.dumps(
            {
                "repository": environment["RESTIC_REPOSITORY"],
                "stage": "preflight",
                "status": "passed",
            },
            sort_keys=True,
        )
    )


def push(args: argparse.Namespace) -> None:
    scheduled = args.backup_id is None
    state = read_pipeline_state(phases={"prepared"}) if scheduled else None
    backup_id = require_safe_identifier(args.backup_id or state["backup_id"], "backup_id")
    root = Path(os.environ.get("ODOO_BACKUP_STAGING", "/staging")) / "backups" / backup_id
    manifest = read_manifest(root)
    dump = root / manifest["database"]["dump_file"]
    if not dump.is_file():
        fail("staged database dump is missing")
    if dump.stat().st_size != manifest["database"]["dump_bytes"] or sha256(dump) != manifest["database"]["dump_sha256"]:
        fail("staged database dump no longer matches its manifest")
    restored_filestore = filestore_metadata(root / "filestore", [])
    if restored_filestore["file_count"] != manifest["filestore"]["file_count"] or restored_filestore["total_bytes"] != manifest["filestore"]["total_bytes"]:
        fail("staged filestore no longer matches its manifest")
    env = restic_environment()
    probe = subprocess.run(["restic", "cat", "config"], env=env, text=True, capture_output=True)
    if probe.returncode:
        run(["restic", "init"], env=env)
    result = run(
        [
            "restic",
            "backup",
            str(root),
            "--json",
            "--host",
            "odoo-production",
            "--tag",
            "usl-odoo",
            "--tag",
            "production",
            "--tag",
            "pending",
            "--tag",
            f"backup-id-{backup_id}",
            "--tag",
            f"mode-{manifest['consistency']}",
        ],
        env=env,
        capture=True,
    )
    snapshot = ""
    for line in result.stdout.splitlines():
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            continue
        if message.get("message_type") == "summary":
            snapshot = message.get("snapshot_id", "")
    require_snapshot(snapshot)
    state = Path(os.environ.get("ODOO_BACKUP_STATE", "/state")) / "receipts"
    state.mkdir(parents=True, exist_ok=True)
    write_json(state / f"{backup_id}.json", {"schema": "usl-odoo-backup-receipt/v1", "backup_id": backup_id, "pending_snapshot_id": snapshot})
    if scheduled:
        update_pipeline_state(phase="pushed", backup_id=backup_id, snapshot_id=snapshot)
    print(snapshot)


def snapshots(args: argparse.Namespace) -> None:
    env = restic_environment()
    values = _restic_json(["snapshots", "--json", "--host", "odoo-production"], env)
    if args.json:
        print(json.dumps(values, indent=2, sort_keys=True))
        return
    print("SNAPSHOT      STATE       TIME                       BACKUP")
    for value in values:
        tags = value.get("tags", [])
        backup = next((tag.removeprefix("backup-id-") for tag in tags if tag.startswith("backup-id-")), "unknown")
        state = "verified" if "verified" in tags else "pending"
        print(f"{value['short_id']:<13} {state:<11} {value['time']:<26} {backup}")


def locate_restored_manifest(root: Path) -> Path:
    matches = list(root.rglob("manifest.json"))
    if len(matches) != 1:
        fail(f"restored snapshot must contain exactly one manifest, found {len(matches)}")
    return matches[0].parent


def restore_fetch(args: argparse.Namespace) -> None:
    scheduled = args.snapshot is None
    pipeline = read_pipeline_state(phases={"pushed"}) if scheduled else None
    snapshot = require_snapshot(args.snapshot or pipeline["snapshot_id"])
    clone_id = require_safe_identifier(
        args.clone_id or f"scheduled-{pipeline['backup_id']}", "clone_id"
    )
    restore_root = Path(os.environ.get("ODOO_BACKUP_RESTORE", "/restore")) / clone_id
    if restore_root.exists():
        fail(f"restore scratch already exists for {clone_id}")
    restore_root.mkdir(parents=True, mode=0o700)
    env = restic_environment()
    run(["restic", "restore", snapshot, "--target", str(restore_root)], env=env)
    artifact = locate_restored_manifest(restore_root)
    manifest = read_manifest(artifact)
    dump = artifact / manifest["database"]["dump_file"]
    if dump.stat().st_size != manifest["database"]["dump_bytes"] or sha256(dump) != manifest["database"]["dump_sha256"]:
        fail("restored database dump checksum or size does not match the manifest")
    metadata = filestore_metadata(artifact / "filestore", [])
    if metadata["file_count"] != manifest["filestore"]["file_count"] or metadata["total_bytes"] != manifest["filestore"]["total_bytes"]:
        fail("restored filestore metadata does not match the manifest")
    write_json(restore_root / "restore-state.json", {"schema": "usl-odoo-restore/v1", "clone_id": clone_id, "snapshot_id": snapshot, "artifact": str(artifact.relative_to(restore_root))})
    if scheduled:
        update_pipeline_state(phase="fetched", snapshot_id=snapshot, clone_id=clone_id)
    print(manifest["source"]["image_digest_reference"])


def restore_state(clone_id: str) -> tuple[Path, dict[str, Any], dict[str, Any]]:
    clone_id = require_safe_identifier(clone_id, "clone_id")
    root = Path(os.environ.get("ODOO_BACKUP_RESTORE", "/restore")) / clone_id
    try:
        state = json.loads((root / "restore-state.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise BackupError(f"restore state is missing for {clone_id}") from exc
    state = _require_exact_keys(
        state, ("schema", "clone_id", "snapshot_id", "artifact"), "restore state"
    )
    if state["schema"] != "usl-odoo-restore/v1" or state["clone_id"] != clone_id:
        fail("restore state identity does not match the requested clone")
    require_snapshot(state["snapshot_id"])
    if not isinstance(state["artifact"], str):
        fail("restore state artifact must be a relative path")
    relative = PurePosixPath(state["artifact"])
    if relative.is_absolute() or not relative.parts or any(part in {"", ".", ".."} for part in relative.parts):
        fail("restore state artifact must be a safe relative path")
    artifact = root.joinpath(*relative.parts)
    return artifact, read_manifest(artifact), state


def restore_apply(args: argparse.Namespace) -> None:
    scheduled = args.clone_id is None
    pipeline = read_pipeline_state(phases={"fetched"}) if scheduled else None
    clone_id = args.clone_id or pipeline["clone_id"]
    artifact, manifest, _state = restore_state(clone_id)
    target_database = os.environ.get("ODOO_RESTORE_DB_NAME", "odoo_restore")
    connection = db_connection(target_database)
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT count(*) FROM information_schema.tables WHERE table_schema='public'")
            if int(cursor.fetchone()[0]):
                fail("restore target database is not empty")
    finally:
        connection.close()
    env = os.environ.copy()
    env["PGPASSWORD"] = secret("PGPASSWORD")
    run(["pg_restore", "--no-owner", "--no-acl", "--exit-on-error", "--dbname", target_database, str(artifact / manifest["database"]["dump_file"])], env=env)
    target = Path(os.environ.get("ODOO_RESTORE_FILESTORE", "/clone-filestore"))
    if any(target.iterdir()):
        fail("restore filestore target is not empty")
    shutil.copytree(artifact / "filestore", target / target_database, symlinks=False)
    if scheduled:
        update_pipeline_state(phase="restored", clone_id=clone_id)
    print(json.dumps({"clone_id": clone_id, "stage": "restore", "status": "passed"}, sort_keys=True))


def restore_reset_apply(args: argparse.Namespace) -> None:
    if os.environ.get("PGHOST") != "clone-db" or os.environ.get("ODOO_RESTORE_DB_NAME", "odoo_restore") != "odoo_restore":
        fail("scheduled restore reset may target only clone-db/odoo_restore")
    if os.environ.get("USL_RESTORE_RESET_CONFIRMED") != "isolated-odoo-restore":
        fail("scheduled restore reset requires USL_RESTORE_RESET_CONFIRMED=isolated-odoo-restore")
    maintenance = db_connection("postgres")
    maintenance.autocommit = True
    try:
        with maintenance.cursor() as cursor:
            cursor.execute("SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname='odoo_restore' AND pid<>pg_backend_pid()")
            cursor.execute("DROP DATABASE IF EXISTS odoo_restore")
            cursor.execute("CREATE DATABASE odoo_restore")
    finally:
        maintenance.close()
    target = Path(os.environ.get("ODOO_RESTORE_FILESTORE", "/clone-filestore")) / "odoo_restore"
    if target.exists():
        shutil.rmtree(target)
    restore_apply(args)


def verify(args: argparse.Namespace) -> None:
    scheduled = args.clone_id is None
    pipeline = read_pipeline_state(phases={"restored"}) if scheduled else None
    clone_id = args.clone_id or pipeline["clone_id"]
    artifact, manifest, state = restore_state(clone_id)
    neutralizer_image = os.environ.get("USL_NEUTRALIZER_IMAGE_DIGEST", "")
    if neutralizer_image != manifest["source"]["image_digest_reference"]:
        fail("neutralizer image does not exactly match the backed-up Odoo image digest")
    target_database = os.environ.get("ODOO_RESTORE_DB_NAME", "odoo_restore")
    connection = db_connection(target_database)
    try:
        with connection.cursor() as cursor:
            actual = table_counts(cursor)
            if actual != manifest["database"]["row_counts"]:
                fail(f"restored row counts differ from manifest: expected {manifest['database']['row_counts']}, got {actual}")
            attachments = stored_attachments(cursor)
            cursor.execute("SELECT value FROM ir_config_parameter WHERE key='database.is_neutralized'")
            marker = cursor.fetchone()
            if not marker or marker[0].lower() not in {"1", "true"}:
                fail("restored database does not carry Odoo's neutralization marker")
            cursor.execute("SELECT count(*) FROM ir_cron WHERE active AND id NOT IN (SELECT res_id FROM ir_model_data WHERE module='base' AND name='autovacuum_job' AND model='ir.cron')")
            if int(cursor.fetchone()[0]):
                fail("restored database still has active non-maintenance crons")
            cursor.execute("SELECT count(*) FROM ir_mail_server WHERE active AND smtp_host <> 'invalid'")
            if int(cursor.fetchone()[0]):
                fail("restored database still has an unsafe outgoing mail server")
            cursor.execute("SELECT count(*) FROM ir_act_server WHERE state='webhook' AND webhook_url <> 'neutralization - disable webhook'")
            if int(cursor.fetchone()[0]):
                fail("restored database still has an active automation webhook")
            if table_exists(cursor, "auth_oauth_provider"):
                cursor.execute("SELECT count(*) FROM auth_oauth_provider WHERE enabled")
                if int(cursor.fetchone()[0]):
                    fail("restored database still has an enabled OAuth provider")
            if table_exists(cursor, "payment_provider"):
                cursor.execute("SELECT count(*) FROM payment_provider WHERE state NOT IN ('test', 'disabled')")
                if int(cursor.fetchone()[0]):
                    fail("restored database still has a live payment provider")
            cursor.execute("SELECT count(*) FROM ir_config_parameter WHERE key='account_peppol.edi.mode' AND value <> 'demo'")
            if int(cursor.fetchone()[0]):
                fail("restored database still has non-demo Peppol connectivity")
            cursor.execute("SELECT count(*) FROM ir_config_parameter WHERE (key LIKE 'usl_documents.paperless\\_%' ESCAPE '\\' OR key LIKE 'usl_documents.sync\\_%' ESCAPE '\\') AND COALESCE(value, '') <> ''")
            if int(cursor.fetchone()[0]):
                fail("restored database still has Paperless connectivity")
    finally:
        connection.close()
    filestore_root = Path(os.environ.get("ODOO_RESTORE_FILESTORE", "/clone-filestore")) / target_database
    metadata = filestore_metadata(filestore_root, attachments)
    expected = manifest["filestore"]
    for key in ("file_count", "total_bytes", "stored_attachment_count", "missing_attachment_count"):
        if metadata[key] != expected[key]:
            fail(f"restored filestore {key} differs: expected {expected[key]}, got {metadata[key]}")
    result = {"schema": "usl-odoo-restore-verification/v1", "clone_id": clone_id, "snapshot_id": state["snapshot_id"], "verified_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"), "row_counts": actual, "filestore": metadata, "neutralized": True, "status": "passed"}
    restore_root = Path(os.environ.get("ODOO_BACKUP_RESTORE", "/restore")) / clone_id
    write_json(restore_root / "verification.json", result)
    verification_state = Path(os.environ.get("ODOO_BACKUP_STATE", "/state")) / "verifications"
    verification_state.mkdir(parents=True, exist_ok=True)
    write_json(verification_state / f"{state['snapshot_id']}.json", result)
    if scheduled:
        update_pipeline_state(phase="restore-verified", clone_id=clone_id, snapshot_id=state["snapshot_id"])
    print(json.dumps(result, sort_keys=True))


def finalize(args: argparse.Namespace) -> None:
    scheduled = args.snapshot is None
    pipeline = read_pipeline_state(phases={"restore-verified"}) if scheduled else None
    snapshot = require_snapshot(args.snapshot or pipeline["snapshot_id"])
    receipt = Path(os.environ.get("ODOO_BACKUP_STATE", "/state")) / "verifications" / f"{snapshot}.json"
    try:
        verification = json.loads(receipt.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise BackupError("snapshot cannot be finalized without its successful restore verification receipt") from exc
    if verification.get("snapshot_id") != snapshot or verification.get("status") != "passed" or verification.get("neutralized") is not True:
        fail("snapshot verification receipt is invalid")
    env = restic_environment()
    current = _restic_json(["snapshots", "--json", snapshot], env)
    if len(current) != 1:
        fail("snapshot does not resolve uniquely")
    tags = current[0].get("tags", [])
    if "verified" in tags:
        if scheduled:
            update_pipeline_state(phase="verified", snapshot_id=current[0]["id"])
        print(current[0]["id"])
        return
    if "pending" not in tags:
        fail("snapshot is neither pending nor verified")
    backup_tag = next((tag for tag in tags if tag.startswith("backup-id-")), "")
    if not backup_tag:
        fail("snapshot has no backup-id tag")
    run(["restic", "tag", "--remove", "pending", "--add", "verified", snapshot], env=env)
    values = _restic_json(["snapshots", "--json", "--tag", backup_tag, "--tag", "verified"], env)
    if len(values) != 1:
        fail("verified snapshot identity does not resolve uniquely")
    final_snapshot = require_snapshot(values[0]["id"])
    if scheduled:
        update_pipeline_state(phase="verified", snapshot_id=final_snapshot)
    print(final_snapshot)


def abandon(args: argparse.Namespace) -> None:
    state = read_pipeline_state()
    if state.get("phase") in {"verified", "abandoned"}:
        fail("pipeline is already closed")
    if args.confirm != state.get("backup_id"):
        fail("abandon confirmation must exactly match the current backup_id")
    update_pipeline_state(phase="abandoned", abandoned_at=datetime.now(UTC).isoformat().replace("+00:00", "Z"))
    print(f"Abandoned incomplete pipeline {state['backup_id']}; any pending Restic snapshot was preserved")


def manifest_field(args: argparse.Namespace) -> None:
    _artifact, manifest, _state = restore_state(args.clone_id)
    if args.field == "source-image":
        print(manifest["source"]["image_digest_reference"])
    elif args.field == "backup-id":
        print(manifest["backup_id"])


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(description=__doc__)
    commands = value.add_subparsers(dest="command", required=True)
    preflight_cmd = commands.add_parser("preflight")
    preflight_cmd.set_defaults(handler=preflight)
    prepare_cmd = commands.add_parser("prepare")
    prepare_cmd.add_argument("--backup-id")
    prepare_cmd.add_argument("--mode", choices=("live", "quiesced"), default="live")
    prepare_cmd.set_defaults(handler=prepare)
    push_cmd = commands.add_parser("push")
    push_cmd.add_argument("--backup-id")
    push_cmd.set_defaults(handler=push)
    list_cmd = commands.add_parser("list")
    list_cmd.add_argument("--json", action="store_true")
    list_cmd.set_defaults(handler=snapshots)
    fetch_cmd = commands.add_parser("restore-fetch")
    fetch_cmd.add_argument("--snapshot")
    fetch_cmd.add_argument("--clone-id")
    fetch_cmd.set_defaults(handler=restore_fetch)
    apply_cmd = commands.add_parser("restore-apply")
    apply_cmd.add_argument("--clone-id")
    apply_cmd.set_defaults(handler=restore_apply)
    reset_apply_cmd = commands.add_parser("restore-reset-apply")
    reset_apply_cmd.add_argument("--clone-id")
    reset_apply_cmd.set_defaults(handler=restore_reset_apply)
    verify_cmd = commands.add_parser("verify")
    verify_cmd.add_argument("--clone-id")
    verify_cmd.set_defaults(handler=verify)
    finalize_cmd = commands.add_parser("finalize")
    finalize_cmd.add_argument("--snapshot")
    finalize_cmd.set_defaults(handler=finalize)
    abandon_cmd = commands.add_parser("abandon")
    abandon_cmd.add_argument("--confirm", required=True)
    abandon_cmd.set_defaults(handler=abandon)
    field_cmd = commands.add_parser("manifest-field")
    field_cmd.add_argument("--clone-id", required=True)
    field_cmd.add_argument("--field", choices=("source-image", "backup-id"), required=True)
    field_cmd.set_defaults(handler=manifest_field)
    return value


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        args.handler(args)
    except (BackupError, OSError, subprocess.CalledProcessError) as exc:
        print(f"odoo backup refused: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
