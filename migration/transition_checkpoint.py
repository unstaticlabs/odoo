"""Exact private checkpoints for an evolving local transition runtime."""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import tarfile
import time
from pathlib import Path
from typing import Any, Iterable

from migration.runtime import (
    CommandRunner,
    RuntimeError,
    inspect_project,
    private_file,
    verify_recorded_resources,
    write_json,
)


ROOT = Path(__file__).resolve().parents[1]
SAFE_CHECKPOINT = re.compile(r"[0-9]{8}T[0-9]{6}Z(?:-[a-z0-9][a-z0-9-]{0,31})?\Z")
DATABASE_SERVICES = {"db", "paperless-db"}
LOCAL_STATE_DIRECTORIES = (
    ".secrets/sign/step-ca",
    ".secrets/sign/dss",
    ".secrets/sign/odoo",
    "private/document-renderer-certs",
)


def run(
    arguments: Iterable[str],
    *,
    input_path: Path | None = None,
    output_path: Path | None = None,
) -> subprocess.CompletedProcess[bytes]:
    stdin = input_path.open("rb") if input_path else None
    stdout = output_path.open("wb") if output_path else subprocess.PIPE
    try:
        result = subprocess.run(
            tuple(arguments),
            cwd=ROOT,
            stdin=stdin,
            stdout=stdout,
            stderr=subprocess.PIPE,
            check=False,
        )
    finally:
        if stdin:
            stdin.close()
        if output_path:
            stdout.close()
    if result.returncode:
        error = result.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(error or f"command failed: {arguments!r}")
    return result


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def database_fingerprint(container: str, user: str, database: str) -> dict[str, int]:
    sql = r"""
CREATE TEMP TABLE usl_checkpoint_counts(name text PRIMARY KEY, row_count bigint);
DO $checkpoint$
DECLARE item record;
BEGIN
  FOR item IN
    SELECT tablename FROM pg_tables WHERE schemaname = 'public' ORDER BY tablename
  LOOP
    EXECUTE format(
      'INSERT INTO usl_checkpoint_counts SELECT %L, count(*) FROM %I',
      item.tablename, item.tablename
    );
  END LOOP;
END
$checkpoint$;
SELECT COALESCE(json_object_agg(name, row_count ORDER BY name), '{}'::json)
FROM usl_checkpoint_counts;
"""
    result = run(
        (
            "docker",
            "exec",
            container,
            "psql",
            "-X",
            "-q",
            "-A",
            "-t",
            "-v",
            "ON_ERROR_STOP=1",
            "-U",
            user,
            "-d",
            database,
            "-c",
            sql,
        )
    )
    lines = result.stdout.decode("utf-8").strip().splitlines()
    try:
        value = json.loads(lines[-1])
    except (IndexError, json.JSONDecodeError) as error:
        raise RuntimeError(f"could not fingerprint database {database}") from error
    return {str(name): int(count) for name, count in value.items()}


def dump_database(container: str, user: str, database: str, output: Path) -> None:
    run(
        ("docker", "exec", container, "pg_dump", "-U", user, "-Fc", database),
        output_path=output,
    )
    output.chmod(0o600)


def archive_volume(volume: str, output: Path) -> None:
    run(
        (
            "docker",
            "run",
            "--rm",
            "-v",
            f"{volume}:/source:ro",
            "-v",
            f"{output.parent}:/checkpoint",
            "alpine:3.24.1",
            "tar",
            "-C",
            "/source",
            "-czf",
            f"/checkpoint/{output.name}",
            ".",
        )
    )
    output.chmod(0o600)


def archive_local_state(output: Path) -> list[str]:
    included: list[str] = []
    with tarfile.open(output, "w:gz") as archive:
        for relative in LOCAL_STATE_DIRECTORIES:
            path = (ROOT / relative).resolve()
            if path.exists():
                if ROOT not in path.parents:
                    raise RuntimeError(f"local checkpoint path escaped the checkout: {path}")
                archive.add(path, arcname=relative, recursive=True)
                included.append(relative)
    output.chmod(0o600)
    return included


def verify_archive(path: Path) -> int:
    members = 0
    with tarfile.open(path, "r:gz") as archive:
        for member in archive:
            members += 1
            stream = archive.extractfile(member) if member.isfile() else None
            if stream:
                for _ in iter(lambda: stream.read(1024 * 1024), b""):
                    pass
    return members


def wait_for_original_containers(containers: list[dict[str, Any]]) -> None:
    expected = {item["id"]: item for item in containers}
    deadline = time.monotonic() + 180
    while time.monotonic() < deadline:
        result = run(("docker", "inspect", *expected))
        current = {item["Id"]: item for item in json.loads(result.stdout)}
        ready = True
        for identifier, recorded in expected.items():
            state = current[identifier]["State"]
            if state.get("Status") != "running":
                ready = False
                break
            health = (state.get("Health") or {}).get("Status")
            if health not in {None, "healthy"}:
                if health == "unhealthy":
                    raise RuntimeError(f"restarted container is unhealthy: {recorded['name']}")
                ready = False
                break
        if ready:
            return
        time.sleep(2)
    raise RuntimeError("transition containers did not recover after checkpoint capture")


def verify_database_restore(
    container: str,
    user: str,
    source_database: str,
    dump: Path,
    checkpoint_id: str,
    expected: dict[str, int],
) -> None:
    suffix = re.sub(r"[^a-z0-9]", "_", checkpoint_id.lower())
    clone = f"usl_checkpoint_{suffix}"[:63]
    exists = run(
        (
            "docker",
            "exec",
            container,
            "psql",
            "-X",
            "-A",
            "-t",
            "-U",
            user,
            "-d",
            "postgres",
            "-c",
            f"SELECT 1 FROM pg_database WHERE datname = '{clone}'",
        )
    ).stdout.decode("utf-8").strip()
    if exists:
        raise RuntimeError(f"checkpoint verification database already exists: {clone}")
    run(("docker", "exec", container, "createdb", "-U", user, clone))
    try:
        run(
            (
                "docker",
                "exec",
                "-i",
                container,
                "pg_restore",
                "-U",
                user,
                "-d",
                clone,
                "--no-owner",
                "--no-privileges",
            ),
            input_path=dump,
        )
        actual = database_fingerprint(container, user, clone)
        if actual != expected:
            raise RuntimeError(f"restored database fingerprint differs: {source_database}")
    finally:
        run(("docker", "exec", container, "dropdb", "-U", user, "--force", clone))


def create_checkpoint(runtime_path: Path, checkpoint_id: str) -> Path:
    if not SAFE_CHECKPOINT.fullmatch(checkpoint_id):
        raise RuntimeError("checkpoint ID is invalid")
    private_file(runtime_path)
    runtime = json.loads(runtime_path.read_text(encoding="utf-8"))
    if runtime.get("kind") != "transition" or runtime.get("status") not in {
        "reconstructed",
        "transition-live",
        "frozen-read-only",
    }:
        raise RuntimeError("checkpoint requires a reconstructed transition runtime")
    if Path(runtime["compose"]["working_directory"]).resolve() != ROOT:
        raise RuntimeError("runtime belongs to another checkout")

    runner = CommandRunner()
    current = inspect_project(runner, runtime["compose"]["project"], ROOT)
    verify_recorded_resources(runtime["resources"], current)
    running = [item for item in current["containers"] if item["state"] == "running"]
    by_service = {item.get("service"): item for item in running}
    if not DATABASE_SERVICES.issubset(by_service):
        raise RuntimeError("both Odoo and Paperless databases must be running")

    checkpoint_root = runtime_path.parent / "checkpoints"
    checkpoint_root.mkdir(mode=0o700, parents=True, exist_ok=True)
    checkpoint_root.chmod(0o700)
    destination = checkpoint_root / checkpoint_id
    pending = checkpoint_root / f".pending-{checkpoint_id}"
    if destination.exists() or pending.exists():
        raise RuntimeError(f"checkpoint already exists: {checkpoint_id}")
    pending.mkdir(mode=0o700)
    data = pending / "data"
    data.mkdir(mode=0o700)

    stopped: list[dict[str, Any]] = []
    manifest: dict[str, Any] = {
        "schema": "usl-transition-checkpoint/v1",
        "id": checkpoint_id,
        "runtime_id": runtime["id"],
        "runtime_status": runtime["status"],
        "project": runtime["compose"]["project"],
        "database": runtime["database"],
        "release_commit": runtime["release_commit"],
        "reconstruction_commit": runtime.get("reconstruction_commit"),
        "source": runtime["source"],
        "running_services": sorted(item.get("service") or item["name"] for item in running),
        "databases": {},
        "volumes": {},
        "local_state": {},
        "ollama": {
            "mode": runtime["ollama"]["mode"],
            "model": runtime["ollama"]["model"],
            "manifest_sha256": runtime["ollama"]["manifest_sha256"],
        },
    }
    try:
        database_ids = {by_service[name]["id"] for name in DATABASE_SERVICES}
        writers = [item for item in running if item["id"] not in database_ids]
        if writers:
            run(("docker", "stop", *(item["id"] for item in writers)))
            stopped.extend(writers)

        odoo_dump = data / "odoo.dump"
        paperless_dump = data / "paperless.dump"
        odoo_counts = database_fingerprint(
            by_service["db"]["id"], "odoo", runtime["database"]
        )
        paperless_counts = database_fingerprint(
            by_service["paperless-db"]["id"], "paperless", "paperless"
        )
        dump_database(by_service["db"]["id"], "odoo", runtime["database"], odoo_dump)
        dump_database(by_service["paperless-db"]["id"], "paperless", "paperless", paperless_dump)

        remaining = [item for item in running if item["id"] in database_ids]
        run(("docker", "stop", *(item["id"] for item in remaining)))
        stopped.extend(remaining)

        for volume in sorted(current["volumes"], key=lambda item: item["name"]):
            output = data / f"volume-{volume['name']}.tgz"
            archive_volume(volume["name"], output)
            manifest["volumes"][volume["name"]] = {
                "archive": output.name,
                "members": verify_archive(output),
            }

        local_archive = data / "local-runtime-state.tgz"
        included = archive_local_state(local_archive)
        manifest["local_state"] = {
            "archive": local_archive.name,
            "included": included,
            "members": verify_archive(local_archive),
        }

        if runtime["ollama"]["mode"] != "native":
            raise RuntimeError("local transition checkpoint requires native Ollama")
        model_archive = data / "native-ollama-model.tgz"
        run(
            (
                sys.executable,
                str(ROOT / "migration/documents_archive/ollama_model_archive.py"),
                "create",
                "--models-root",
                runtime["ollama"]["models_path"],
                "--expected-manifest-sha256",
                runtime["ollama"]["manifest_sha256"],
                "--output",
                str(model_archive),
            )
        )
        model_archive.chmod(0o600)
        manifest["ollama"]["archive"] = model_archive.name
        manifest["ollama"]["members"] = verify_archive(model_archive)

        run(("docker", "start", *(item["id"] for item in running)))
        stopped.clear()
        wait_for_original_containers(running)

        verify_database_restore(
            by_service["db"]["id"],
            "odoo",
            runtime["database"],
            odoo_dump,
            checkpoint_id,
            odoo_counts,
        )
        verify_database_restore(
            by_service["paperless-db"]["id"],
            "paperless",
            "paperless",
            paperless_dump,
            checkpoint_id,
            paperless_counts,
        )
        manifest["databases"] = {
            "odoo": {
                "archive": odoo_dump.name,
                "tables": len(odoo_counts),
                "rows": sum(odoo_counts.values()),
                "restored_fingerprint": "passed",
            },
            "paperless": {
                "archive": paperless_dump.name,
                "tables": len(paperless_counts),
                "rows": sum(paperless_counts.values()),
                "restored_fingerprint": "passed",
            },
        }
        files = {}
        for path in sorted(data.iterdir()):
            files[path.name] = {"sha256": sha256(path), "size": path.stat().st_size}
        manifest["files"] = files
        manifest["status"] = "verified"
        write_json(pending / "manifest.json", manifest)
        os.replace(pending, destination)
        destination.chmod(0o700)
        return destination
    finally:
        if stopped:
            run(("docker", "start", *(item["id"] for item in running)))
            wait_for_original_containers(running)


def main() -> int:
    if len(sys.argv) != 3 or sys.argv[1] != "create":
        print("Usage: python3 migration/transition_checkpoint.py create CHECKPOINT_ID", file=sys.stderr)
        return 2
    state = os.environ.get("USL_MIGRATION_RUNTIME_STATE")
    if not state:
        print("transition checkpoint must be invoked through migration/manage", file=sys.stderr)
        return 2
    try:
        destination = create_checkpoint(Path(state).resolve(), sys.argv[2])
    except (RuntimeError, OSError, json.JSONDecodeError) as error:
        print(f"transition checkpoint: {error}", file=sys.stderr)
        return 1
    print(destination)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
