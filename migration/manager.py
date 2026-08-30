"""Single public migration and cutover command."""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

from migration.runtime import (
    CommandRunner,
    RuntimeError,
    RuntimeStore,
    SECRET_KEYS,
    compose_files,
    identity_values,
    inspect_project,
    now,
    private_file,
    resolve_ollama,
    runtime_environment,
    read_secrets,
    sanitize_adopted_identity,
    source_identity,
    verify_recorded_resources,
    write_private,
)


ROOT = Path(__file__).resolve().parents[1]
INTERNAL = ROOT / "migration/internal"
TTL = re.compile(r"([1-9][0-9]*)([mh])\Z")
CHECKPOINT_LABEL = re.compile(r"[a-z0-9][a-z0-9-]{0,31}\Z")
DOCUMENT_CRON_XMLIDS = (
    "ir_cron_usl_documents_attachment_queue",
    "ir_cron_usl_documents_classification",
    "ir_cron_usl_documents_poll",
    "ir_cron_usl_documents_sync",
)


def git(*arguments: str) -> str:
    process = subprocess.run(
        ("git", *arguments), cwd=ROOT, text=True, capture_output=True, check=False
    )
    if process.returncode:
        raise RuntimeError(process.stderr.strip() or "Git identity inspection failed")
    return process.stdout.strip()


def ensure_clean_identity(runtime: dict[str, Any]) -> None:
    if git("status", "--porcelain=v1", "--untracked-files=no"):
        raise RuntimeError("release operation requires a clean tracked working tree")
    if git("rev-parse", "HEAD") != runtime["release_commit"]:
        raise RuntimeError("checked-out release identity differs from the runtime")


def ensure_clean_checkout() -> str:
    if git("status", "--porcelain=v1", "--untracked-files=no"):
        raise RuntimeError("reconstruction requires a clean tracked working tree")
    return git("rev-parse", "HEAD")


def load_python(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if not spec or not spec.loader:
        raise RuntimeError(f"cannot load internal module: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def create_runtime(
    args: argparse.Namespace,
    runner: CommandRunner,
    *,
    kind: str,
    adopt: bool = True,
) -> dict[str, Any]:
    if not 1 <= args.paperless_task_workers <= 4:
        raise RuntimeError("Paperless task workers must be between 1 and 4")
    if not 1 <= args.embedding_batch_size <= 128:
        raise RuntimeError("embedding batch size must be between 1 and 128")
    if args.database == "odoo_online_source_saas_19_3":
        raise RuntimeError("target runtime may not use the frozen source database")
    ollama = resolve_ollama(
        args.ollama,
        system=getattr(args, "system", None),
        executable=getattr(args, "ollama_executable", None),
        models=getattr(args, "ollama_models", None),
        reachable=getattr(args, "ollama_reachable", None),
    )
    source = source_identity(args.source, args.source_sha256)
    private_file(args.personal_ai_key_file.expanduser().resolve())
    resources = inspect_project(runner, args.project, ROOT)
    if adopt and not resources["containers"]:
        raise RuntimeError(f"no existing Compose containers found for {args.project}")
    if not adopt and any(resources.values()):
        raise RuntimeError("new transition runtime project already owns Docker resources")
    ports = {
        "odoo": args.odoo_port,
        "gevent": args.gevent_port,
        "pocket_id": args.pocket_id_port,
        "paperless": args.paperless_port,
    }
    urls = {
        "odoo": args.odoo_url or f"http://odoo.localhost:{ports['odoo']}",
        "pocket_id": args.pocket_id_url or f"http://id.localhost:{ports['pocket_id']}",
        "paperless": args.paperless_url or f"http://paperless.localhost:{ports['paperless']}",
    }
    if adopt:
        expected = {
            "COMPOSE_PROJECT_NAME": args.project,
            "ODOO_INIT_DB": args.database,
            "ODOO_PUBLIC_BASE_URL": urls["odoo"],
            "POCKET_ID_APP_URL": urls["pocket_id"],
            "PAPERLESS_PUBLIC_URL": urls["paperless"],
        }
        secrets = sanitize_adopted_identity(args.identity_env, expected)
    else:
        secrets = read_secrets(args.secrets_file)
        missing = sorted(
            {
                "POCKET_ID_CLIENT_SECRET",
                "POCKET_ID_ENCRYPTION_KEY",
                "POCKET_ID_STATIC_API_KEY",
                "POCKET_ID_VALENTIN_ID",
            }
            - set(secrets)
        )
        if missing:
            raise RuntimeError("new runtime secret file is missing: " + ", ".join(missing))
    directory = RuntimeStore(ROOT).directory(args.id)
    images = {
        item["service"]: item["image"]
        for item in resources["containers"]
        if item.get("service") and item.get("image")
    }
    for assignment in args.image:
        name, separator, reference = assignment.partition("=")
        if not separator or not name or not reference:
            raise RuntimeError("--image must use SERVICE=IMMUTABLE_REFERENCE")
        images[name] = reference
    release_commits = {
        item["release_commit"]
        for item in resources["containers"]
        if item.get("service") == "odoo" and re.fullmatch(r"[0-9a-f]{40}", item.get("release_commit") or "")
    }
    if len(release_commits) > 1:
        raise RuntimeError("adopted Odoo containers report conflicting release identities")
    release_commit = args.release_commit or (next(iter(release_commits)) if release_commits else None)
    if not adopt and not release_commit:
        release_commit = ensure_clean_checkout()
    if not release_commit or not re.fullmatch(r"[0-9a-f]{40}", release_commit):
        raise RuntimeError(
            "adoption requires an Odoo image release label or explicit --release-commit"
        )
    value = {
        "schema": "usl-migration-runtime-v1",
        "id": args.id,
        "kind": kind,
        "status": "running" if adopt else "defined",
        "created_at": now(),
        "updated_at": now(),
        "adopted": adopt,
        "database": args.database,
        "profile": args.profile,
        "release_commit": release_commit,
        "private_directory": str(directory),
        "source": source,
        "personal_ai_key_file": str(args.personal_ai_key_file.expanduser().resolve()),
        "ports": ports,
        "urls": urls,
        "ollama": ollama,
        "documents": {
            "paperless_task_workers": args.paperless_task_workers,
            "embedding_batch_size": args.embedding_batch_size,
            "embedding_chunk_size": 512,
        },
        "compose": {
            "project": args.project,
            "working_directory": str(ROOT),
            "files": compose_files(ROOT, kind, ollama["mode"]),
            "profiles": ["paperless", "sign", "document-renderer"],
        },
        "images": images,
        "resources": resources,
        "history": [
            {
                "at": now(),
                "action": "adopt" if adopt else "define",
                "result": "passed",
            }
        ],
    }
    return RuntimeStore(ROOT).create(value, secrets)


def documents_runtime_status(
    runtime: dict[str, Any],
    resources: dict[str, Any],
    runner: CommandRunner,
) -> dict[str, Any]:
    database_container = next(
        (
            item
            for item in resources["containers"]
            if item.get("service") == "db" and item.get("state") == "running"
        ),
        None,
    )
    if not database_container:
        return {
            "ready": False,
            "error": "runtime has no running recorded Odoo database container",
        }
    cron_names = ", ".join(f"'{name}'" for name in DOCUMENT_CRON_XMLIDS)
    query = f"""
        SELECT json_build_object(
            'active_operations', (
                SELECT count(*) FROM usl_document_operation
                WHERE state IN ('pending', 'uploading', 'processing')
            ),
            'unresolved_operations', (
                SELECT count(*) FROM usl_document_operation
                WHERE state IN ('failed', 'duplicate')
                  AND NOT COALESCE(acknowledged, FALSE)
            ),
            'approved_jobs', (
                SELECT count(*) FROM ir_model_data data
                JOIN ir_cron cron ON cron.id = data.res_id
                WHERE data.module = 'usl_documents'
                  AND data.model = 'ir.cron'
                  AND data.name IN ({cron_names})
                  AND cron.active
            ),
            'configured_jobs', (
                SELECT count(*) FROM ir_model_data data
                WHERE data.module = 'usl_documents'
                  AND data.model = 'ir.cron'
                  AND data.name IN ({cron_names})
            ),
            'backfill_complete', COALESCE((
                SELECT value = 'complete' FROM ir_config_parameter
                WHERE key = 'usl_documents.attachment_backfill_state'
            ), FALSE)
        )
    """
    process = runner.run(
        [
            "docker",
            "exec",
            database_container["id"],
            "psql",
            "-U",
            "odoo",
            "-d",
            runtime["database"],
            "-A",
            "-t",
            "-c",
            query,
        ],
    )
    try:
        value = json.loads(process.stdout.strip())
    except (json.JSONDecodeError, TypeError) as error:
        raise RuntimeError("Documents runtime status did not return valid JSON") from error
    value["ready"] = (
        value.get("active_operations") == 0
        and value.get("unresolved_operations") == 0
        and value.get("approved_jobs") == len(DOCUMENT_CRON_XMLIDS)
        and value.get("configured_jobs") == len(DOCUMENT_CRON_XMLIDS)
        and value.get("backfill_complete") is True
    )
    return value


def check_runtime(runtime: dict[str, Any], runner: CommandRunner) -> dict[str, Any]:
    source = source_identity(
        Path(runtime["source"]["path"]), runtime["source"]["dump_sha256"]
    )
    for name in ("dump_size", "filestore_file_count", "filestore_size", "filestore_sha256"):
        if source[name] != runtime["source"].get(name):
            raise RuntimeError(f"frozen source {name} differs from the recorded runtime")
    current = inspect_project(runner, runtime["compose"]["project"], ROOT)
    verify_recorded_resources(runtime["resources"], current)
    if runtime["ollama"]["mode"] == "native":
        resolve_ollama(
            "native",
            models=Path(runtime["ollama"]["models_path"]),
            host_url=runtime["ollama"]["host_url"],
        )
    healthy = bool(current["containers"]) and all(
        (
            item.get("state") == "running"
            and item.get("health") in {None, "healthy"}
        )
        or (item.get("state") == "exited" and item.get("exit_code") == 0)
        for item in current["containers"]
    )
    documents = documents_runtime_status(runtime, current, runner)
    healthy = healthy and documents["ready"]
    result = {
        "id": runtime["id"],
        "kind": runtime["kind"],
        "status": runtime["status"],
        "project": runtime["compose"]["project"],
        "database": runtime["database"],
        "release_commit": runtime["release_commit"],
        "checkout_commit": git("rev-parse", "HEAD"),
        "checkout_matches": git("rev-parse", "HEAD") == runtime["release_commit"],
        "source_dump_sha256": source["dump_sha256"],
        "ollama": runtime["ollama"],
        "urls": runtime["urls"],
        "resources": {
            "containers": len(current["containers"]),
            "volumes": len(current["volumes"]),
            "networks": len(current["networks"]),
        },
        "healthy": healthy,
        "documents": documents,
    }
    return result


def confirm(args: argparse.Namespace, operation: str) -> None:
    expected = f"{operation}:{args.runtime}"
    if getattr(args, "confirm", None) != expected:
        raise RuntimeError(f"operation requires exact confirmation: --confirm {expected}")


def stop_runtime(runtime: dict[str, Any], runner: CommandRunner) -> None:
    current = inspect_project(runner, runtime["compose"]["project"], ROOT)
    verify_recorded_resources(runtime["resources"], current)
    running = [item["id"] for item in current["containers"] if item["state"] == "running"]
    if running:
        runner.run(["docker", "stop", *running])


def operational_containers(runtime: dict[str, Any], runner: CommandRunner) -> list[dict[str, Any]]:
    current = inspect_project(runner, runtime["compose"]["project"], ROOT)
    verify_recorded_resources(runtime["resources"], current)
    services = {
        "db",
        "odoo",
        "paperless-db",
        "paperless-broker",
        "paperless-gotenberg",
        "paperless-tika",
        "paperless-webserver",
        "pocket-id",
        "usl-document-renderer",
        "usl-sign-dss",
        "usl-sign-step-ca",
    }
    return [item for item in current["containers"] if item.get("service") in services]


def start_transition_runtime(runtime: dict[str, Any], runner: CommandRunner) -> None:
    containers = operational_containers(runtime, runner)
    stopped = [item["id"] for item in containers if item.get("state") != "running"]
    if stopped:
        runner.run(["docker", "start", *stopped])


def stop_transition_runtime(runtime: dict[str, Any], runner: CommandRunner) -> None:
    containers = operational_containers(runtime, runner)
    running = [item["id"] for item in containers if item.get("state") == "running"]
    if running:
        runner.run(["docker", "stop", *running])


def checkpoint_id(label: str | None) -> str:
    if label and not CHECKPOINT_LABEL.fullmatch(label):
        raise RuntimeError("checkpoint label must use lowercase letters, digits, or hyphens")
    digits = re.sub(r"\D", "", now())[:14]
    timestamp = f"{digits[:8]}T{digits[8:]}Z"
    return f"{timestamp}-{label}" if label else timestamp


def destroy_runtime(runtime: dict[str, Any], runner: CommandRunner) -> None:
    current = inspect_project(runner, runtime["compose"]["project"], ROOT)
    verify_recorded_resources(runtime["resources"], current)
    container_ids = [item["id"] for item in current["containers"]]
    if container_ids:
        runner.run(["docker", "rm", "--force", *container_ids])
    volume_names = [item["name"] for item in current["volumes"]]
    if volume_names:
        runner.run(["docker", "volume", "rm", *volume_names])
    network_ids = [item["id"] for item in current["networks"]]
    if network_ids:
        runner.run(["docker", "network", "rm", *network_ids])


def recover_failed_runtime_resources(
    runtime: dict[str, Any],
    store: RuntimeStore,
    runner: CommandRunner,
) -> None:
    """Record resources left by an interrupted reconstruction before retrying."""
    if runtime["status"] != "failed" or any(runtime["resources"].values()):
        return
    current = inspect_project(runner, runtime["compose"]["project"], ROOT)
    if not any(current.values()):
        return
    runtime["resources"] = current
    record(store, runtime, "qa.refresh.recover-resources")


def ttl_minutes(value: str) -> int:
    match = TTL.fullmatch(value)
    if not match:
        raise RuntimeError("login-link TTL must use minutes or hours, for example 8h")
    amount = int(match.group(1)) * (60 if match.group(2) == "h" else 1)
    if amount > 8 * 60:
        raise RuntimeError("QA login links may not exceed eight hours")
    return amount


def login_link(runtime: dict[str, Any], secrets: dict[str, str], user: str, ttl: str) -> None:
    ttl_minutes(ttl)
    module = load_python(ROOT / "scripts/pocket_id_dev.py", "pocket_id_dev")
    module.one_time_link(identity_values(runtime, secrets), user, ttl)


def combined_env_file(runtime: dict[str, Any], secrets: dict[str, str]):
    values = runtime_environment(runtime, secrets)
    path = Path(runtime["private_directory"]) / "resolved-compose.env"
    excluded = {
        "PATH",
        "HOME",
        "USER",
        "TMPDIR",
        "LANG",
        "SHELL",
        "TERM",
        "DOCKER_HOST",
        "DOCKER_CONTEXT",
    }
    content = "".join(
        f"{key}={values[key]}\n"
        for key in sorted(values)
        if key not in excluded and not key.startswith("LC_")
    )
    write_private(path, content)
    return path, values


def run_internal(
    runtime: dict[str, Any],
    secrets: dict[str, str],
    runner: CommandRunner,
    command: list[str],
    *,
    extra_environment: dict[str, str] | None = None,
) -> None:
    if runtime["ollama"]["mode"] == "native":
        resolve_ollama(
            "native",
            models=Path(runtime["ollama"]["models_path"]),
            host_url=runtime["ollama"]["host_url"],
        )
    env_file, environment = combined_env_file(runtime, secrets)
    if extra_environment:
        environment.update(extra_environment)
        env_file, environment = combined_env_file(runtime, {**secrets, **extra_environment})
    environment["POCKET_ID_ENV_FILE"] = str(env_file)
    resolved_command = [
        str(env_file) if value == "{RUNTIME_ENV_FILE}" else value
        for value in command
    ]
    runner.run(resolved_command, cwd=ROOT, env=environment)


def record(store: RuntimeStore, runtime: dict[str, Any], action: str, result: str = "passed") -> None:
    runtime.setdefault("history", []).append({"at": now(), "action": action, "result": result})
    store.save(runtime)


def command_qa(args: argparse.Namespace, runner: CommandRunner) -> dict[str, Any] | None:
    store = RuntimeStore(ROOT)
    if args.action == "adopt":
        return create_runtime(args, runner, kind="qa")
    runtime = store.load(args.runtime)
    if runtime["kind"] != "qa":
        raise RuntimeError("qa command requires a QA runtime")
    if args.action == "status":
        return check_runtime(runtime, runner)
    if args.action == "login-link":
        login_link(runtime, store.secrets(args.runtime), args.user, args.ttl)
        return None
    if args.action == "stop":
        stop_runtime(runtime, runner)
        runtime["status"] = "stopped"
        record(store, runtime, "qa.stop")
        return {"id": runtime["id"], "status": "stopped", "data_preserved": True}
    if args.action == "destroy":
        confirm(args, "DESTROY")
        if runtime["status"] == "transition-live":
            raise RuntimeError("transition-live runtime cannot be destroyed")
        destroy_runtime(runtime, runner)
        runtime["status"] = "destroyed"
        record(store, runtime, "qa.destroy")
        return {"id": runtime["id"], "status": "destroyed"}
    if args.action == "refresh":
        if not args.fresh:
            raise RuntimeError("QA reconstruction is fresh-source only; pass --fresh")
        confirm(args, "REFRESH")
        if runtime["status"] in {"transition-live", "frozen-read-only"}:
            raise RuntimeError("protected runtime cannot be refreshed")
        release_commit = ensure_clean_checkout()
        recover_failed_runtime_resources(runtime, store, runner)
        destroy_runtime(runtime, runner)
        runtime["status"] = "reconstructing"
        runtime["release_commit"] = release_commit
        runtime["resources"] = {"containers": [], "volumes": [], "networks": []}
        record(store, runtime, "qa.refresh.start")
        try:
            run_internal(
                runtime,
                store.secrets(args.runtime),
                runner,
                [str(INTERNAL / "reconstruct"), "qa"],
            )
        except RuntimeError:
            runtime["resources"] = inspect_project(
                runner, runtime["compose"]["project"], ROOT
            )
            runtime["status"] = "failed"
            record(store, runtime, "qa.refresh", "failed")
            raise
        runtime["resources"] = inspect_project(runner, runtime["compose"]["project"], ROOT)
        runtime["images"] = {
            item["service"]: item["image"]
            for item in runtime["resources"]["containers"]
            if item.get("service") and item.get("image")
        }
        runtime["status"] = "running"
        record(store, runtime, "qa.refresh")
        return check_runtime(runtime, runner)
    raise RuntimeError(f"unsupported qa action: {args.action}")


def command_transition(args: argparse.Namespace, runner: CommandRunner) -> dict[str, Any]:
    store = RuntimeStore(ROOT)
    try:
        runtime = store.load(args.runtime)
    except RuntimeError as error:
        if args.action != "reconstruct" or "runtime identity is invalid" not in str(error) and "private input" not in str(error):
            raise
        required = (
            "project",
            "source",
            "secrets_file",
            "personal_ai_key_file",
            "odoo_port",
            "gevent_port",
            "pocket_id_port",
            "paperless_port",
        )
        missing = [name.replace("_", "-") for name in required if getattr(args, name, None) is None]
        if missing:
            raise RuntimeError(
                "new transition runtime is missing: " + ", ".join(f"--{name}" for name in missing)
            ) from error
        args.id = args.runtime
        args.database = args.database or "odoo_dev"
        args.profile = "full"
        args.identity_env = None
        args.image = args.image or []
        runtime = create_runtime(args, runner, kind="transition", adopt=False)
    if runtime["kind"] != "transition":
        raise RuntimeError("transition command requires a transition runtime")
    if args.action == "status":
        return check_runtime(runtime, runner)
    if args.action == "login-link":
        login_link(runtime, store.secrets(args.runtime), args.user, args.ttl)
        return {"id": runtime["id"], "status": runtime["status"]}
    if args.action == "start":
        if runtime["status"] not in {"transition-live", "frozen-read-only"}:
            raise RuntimeError("only a protected transition runtime can be started")
        start_transition_runtime(runtime, runner)
        record(store, runtime, "transition.start")
        return {"id": runtime["id"], "status": runtime["status"]}
    if args.action == "stop":
        if runtime["status"] not in {"transition-live", "frozen-read-only"}:
            raise RuntimeError("only a protected transition runtime can be stopped")
        stop_transition_runtime(runtime, runner)
        record(store, runtime, "transition.stop")
        return {"id": runtime["id"], "status": runtime["status"], "data_preserved": True}
    if args.action == "checkpoint":
        if runtime["status"] not in {"reconstructed", "transition-live", "frozen-read-only"}:
            raise RuntimeError("checkpoint requires a reconstructed transition runtime")
        identifier = checkpoint_id(args.label)
        run_internal(
            runtime,
            store.secrets(args.runtime),
            runner,
            [sys.executable, "-m", "migration.transition_checkpoint", "create", identifier],
        )
        runtime["last_checkpoint"] = {
            "id": identifier,
            "at": now(),
            "release_commit": runtime["release_commit"],
            "status": "verified",
        }
        record(store, runtime, "transition.checkpoint")
        return {"id": runtime["id"], "status": runtime["status"], "checkpoint": identifier}
    if args.action == "reconstruct":
        confirm(args, "RECONSTRUCT")
        if runtime["status"] in {"transition-live", "frozen-read-only"}:
            raise RuntimeError("protected transition runtime cannot be reconstructed")
        release_commit = ensure_clean_checkout()
        destroy_runtime(runtime, runner)
        runtime["status"] = "reconstructing"
        runtime["release_commit"] = release_commit
        runtime["resources"] = {"containers": [], "volumes": [], "networks": []}
        record(store, runtime, "transition.reconstruct.start")
        try:
            run_internal(
                runtime,
                store.secrets(args.runtime),
                runner,
                [str(INTERNAL / "reconstruct"), "transition"],
            )
        except RuntimeError:
            runtime["status"] = "failed"
            record(store, runtime, "transition.reconstruct", "failed")
            raise
        runtime["resources"] = inspect_project(runner, runtime["compose"]["project"], ROOT)
        runtime["status"] = "reconstructed"
    elif args.action == "mark-live":
        confirm(args, "MARK-LIVE")
        if runtime["status"] != "reconstructed":
            raise RuntimeError("transition must be reconstructed before it is marked live")
        run_internal(
            runtime,
            store.secrets(args.runtime),
            runner,
            [str(INTERNAL / "transition-activate")],
            extra_environment={"USL_MIGRATION_PURPOSE": "transition"},
        )
        runtime["status"] = "transition-live"
    elif args.action == "freeze":
        confirm(args, "FREEZE")
        if runtime["status"] != "transition-live":
            raise RuntimeError("only a transition-live runtime can be frozen")
        runtime["status"] = "frozen-read-only"
    record(store, runtime, f"transition.{args.action}")
    return {"id": runtime["id"], "status": runtime["status"]}


def command_release_domain(args: argparse.Namespace, runner: CommandRunner) -> dict[str, Any]:
    store = RuntimeStore(ROOT)
    runtime = store.load(args.runtime)
    ensure_clean_identity(runtime)
    secrets = store.secrets(args.runtime)
    domain = args.domain
    action = args.action
    if domain == "candidate":
        source = args.source_dir or Path(runtime["source"]["path"])
        if action == "build":
            command = [str(INTERNAL / "candidate"), "build", str(source)]
            if args.candidate_dir:
                command.append(str(args.candidate_dir))
        elif action == "verify":
            if not args.candidate_dir or not args.fingerprint:
                raise RuntimeError("candidate verify requires --candidate-dir and --fingerprint")
            command = [
                str(INTERNAL / "candidate"),
                "verify",
                str(args.candidate_dir),
                args.fingerprint,
                str(source),
            ]
        else:
            command = [str(INTERNAL / "candidate"), "status"]
            if args.candidate_dir:
                command.append(str(args.candidate_dir))
            command.append(str(source))
    elif domain == "cohort":
        state = Path(runtime["private_directory"]) / "cohort-state.json"
        if action == "capture":
            required = (args.step_ca, args.dss, args.evidence, args.release_identity)
            if not all(required):
                raise RuntimeError(
                    "cohort capture requires --step-ca, --dss, --evidence, and --release-identity"
                )
            run_internal(
                runtime,
                secrets,
                runner,
                [
                    str(INTERNAL / "cohort"),
                    "capture-sign",
                    str(args.bundle),
                    str(args.step_ca),
                    str(args.dss),
                    str(args.evidence),
                    str(args.release_identity),
                ],
            )
            command = [str(INTERNAL / "cohort"), "seal", str(args.bundle)]
        elif action == "verify":
            command = [str(INTERNAL / "cohort"), "verify", str(args.bundle)]
        elif action == "restore":
            confirm(args, "RESTORE")
            if not args.destination or not args.fingerprint:
                raise RuntimeError("cohort restore requires --destination and --fingerprint")
            destination = args.destination.expanduser().resolve()
            private_directory = Path(runtime["private_directory"]).resolve()
            if private_directory not in destination.parents:
                raise RuntimeError("cohort restore destination must remain inside the runtime")
            current = inspect_project(runner, runtime["compose"]["project"], ROOT)
            verify_recorded_resources(runtime["resources"], current)
            if any(current.values()):
                raise RuntimeError("cohort restore requires a fresh runtime with no Docker resources")
            run_internal(
                runtime,
                secrets,
                runner,
                [
                    str(INTERNAL / "cohort"),
                    "preflight",
                    str(args.bundle),
                    str(state),
                    args.fingerprint,
                ],
            )
            command = [
                str(INTERNAL / "cohort"),
                "restore",
                str(args.bundle),
                str(state),
                runtime["compose"]["project"],
                str(destination),
                args.fingerprint,
            ]
        else:
            if not args.destination or not args.recipient:
                raise RuntimeError("cohort encrypt requires --destination and --recipient")
            command = [
                str(INTERNAL / "cohort"),
                "encrypt",
                str(args.bundle),
                str(args.destination),
                args.recipient,
            ]
    else:
        cutover_module = load_python(ROOT / "migration/cutover.py", "migration_cutover")
        if args.configuration or args.secrets_file:
            if not args.configuration or not args.secrets_file:
                raise RuntimeError("cutover configuration and secrets must be supplied together")
            private_file(args.configuration)
            try:
                configuration = json.loads(args.configuration.read_text(encoding="utf-8"))
            except json.JSONDecodeError as error:
                raise RuntimeError("cutover configuration is invalid JSON") from error
            if not isinstance(configuration, dict) or not all(
                isinstance(key, str) and isinstance(value, str)
                for key, value in configuration.items()
            ):
                raise RuntimeError("cutover configuration must be a JSON object of strings")
            cutover_secrets = read_secrets(args.secrets_file)
            required = set(cutover_module.REQUIRED)
            nonsecret_required = required - SECRET_KEYS
            missing_config = sorted(nonsecret_required - set(configuration))
            unsupported_config = sorted(set(configuration) - nonsecret_required - {"USL_PRODUCTION_PG_RESTORE_JOBS"})
            missing_secrets = sorted((required & SECRET_KEYS) - set(cutover_secrets))
            if missing_config:
                raise RuntimeError("cutover configuration is missing: " + ", ".join(missing_config))
            if unsupported_config:
                raise RuntimeError("cutover configuration contains unsupported fields: " + ", ".join(unsupported_config))
            if missing_secrets:
                raise RuntimeError("cutover secret file is missing: " + ", ".join(missing_secrets))
            if runtime.get("cutover") and runtime["cutover"].get("configuration") != configuration:
                raise RuntimeError("recorded cutover configuration is immutable")
            runtime["cutover"] = {"configuration": configuration, "resolved_at": now()}
            store.write_secrets(runtime["id"], "cutover-secrets", cutover_secrets)
            store.save(runtime)
        configuration = (runtime.get("cutover") or {}).get("configuration")
        if not configuration:
            raise RuntimeError("cutover preflight requires a resolved --configuration and --secrets-file")
        cutover_secrets = store.named_secrets(runtime["id"], "cutover-secrets")
        extra_environment = {**configuration, **cutover_secrets}
        extra_environment["USL_CUTOVER_STATE_DIR"] = str(
            Path(runtime["private_directory"]) / "cutover"
        )
        command = [
            str(INTERNAL / "cutover"),
            action,
            "{RUNTIME_ENV_FILE}",
            str(args.candidate),
        ]
        if action in {"configure", "gate"}:
            if not args.evidence:
                raise RuntimeError(f"cutover {action} requires --evidence")
            command.extend([str(args.evidence), args.fingerprint])
        elif action in {"admit", "reset"}:
            confirm(args, action.upper())
            command.extend(["--confirm", args.fingerprint])
        else:
            command.append(args.fingerprint)
        run_internal(
            runtime,
            secrets,
            runner,
            command,
            extra_environment=extra_environment,
        )
        record(store, runtime, f"{domain}.{action}")
        return {"id": runtime["id"], "action": f"{domain}.{action}", "status": "passed"}
    run_internal(runtime, secrets, runner, command)
    if domain == "cohort" and action == "restore":
        runtime["resources"] = inspect_project(runner, runtime["compose"]["project"], ROOT)
        runtime["status"] = "cohort-restored"
    record(store, runtime, f"{domain}.{action}")
    return {"id": runtime["id"], "action": f"{domain}.{action}", "status": "passed"}


def common_runtime_arguments(
    parser: argparse.ArgumentParser, *, required: bool = True, adoption: bool = True
) -> None:
    if adoption:
        parser.add_argument("--id", required=required)
    parser.add_argument("--project", required=required)
    parser.add_argument("--database", default="odoo_dev")
    parser.add_argument("--source", required=required, type=Path)
    parser.add_argument("--source-sha256")
    if adoption:
        parser.add_argument("--identity-env", required=required, type=Path)
    parser.add_argument("--personal-ai-key-file", required=required, type=Path)
    parser.add_argument("--profile", choices=("full",), default="full")
    parser.add_argument("--odoo-port", required=required, type=int)
    parser.add_argument("--gevent-port", required=required, type=int)
    parser.add_argument("--pocket-id-port", required=required, type=int)
    parser.add_argument("--paperless-port", required=required, type=int)
    parser.add_argument("--odoo-url")
    parser.add_argument("--pocket-id-url")
    parser.add_argument("--paperless-url")
    parser.add_argument("--ollama", choices=("auto", "native", "container"), default="auto")
    parser.add_argument("--ollama-models", type=Path)
    parser.add_argument("--image", action="append", default=[])
    parser.add_argument("--release-commit")
    parser.add_argument("--paperless-task-workers", type=int, metavar="1..4", default=3)
    parser.add_argument("--embedding-batch-size", type=int, metavar="1..128", default=32)


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(prog="migration/manage")
    domains = result.add_subparsers(dest="domain", required=True)
    qa = domains.add_parser("qa")
    qa_actions = qa.add_subparsers(dest="action", required=True)
    common_runtime_arguments(qa_actions.add_parser("adopt"))
    for action in ("status", "stop"):
        item = qa_actions.add_parser(action)
        item.add_argument("--runtime", required=True)
    destroy = qa_actions.add_parser("destroy")
    destroy.add_argument("--runtime", required=True)
    destroy.add_argument("--confirm", required=True)
    refresh = qa_actions.add_parser("refresh")
    refresh.add_argument("--runtime", required=True)
    refresh.add_argument("--fresh", action="store_true")
    refresh.add_argument("--confirm", required=True)
    link = qa_actions.add_parser("login-link")
    link.add_argument("--runtime", required=True)
    link.add_argument("--user", default="valentin")
    link.add_argument("--ttl", default="8h")

    transition = domains.add_parser("transition")
    transition_actions = transition.add_subparsers(dest="action", required=True)
    for action in ("reconstruct", "mark-live", "freeze"):
        item = transition_actions.add_parser(action)
        item.add_argument("--runtime", required=True)
        item.add_argument("--confirm", required=True)
        if action == "reconstruct":
            common_runtime_arguments(item, required=False, adoption=False)
            item.add_argument("--secrets-file", type=Path)
    for action in ("status", "start", "stop"):
        item = transition_actions.add_parser(action)
        item.add_argument("--runtime", required=True)
    link = transition_actions.add_parser("login-link")
    link.add_argument("--runtime", required=True)
    link.add_argument("--user", default="valentin")
    link.add_argument("--ttl", default="8h")
    checkpoint = transition_actions.add_parser("checkpoint")
    checkpoint.add_argument("--runtime", required=True)
    checkpoint.add_argument("--label")

    candidate = domains.add_parser("candidate")
    candidate_actions = candidate.add_subparsers(dest="action", required=True)
    for action in ("build", "verify", "status"):
        item = candidate_actions.add_parser(action)
        item.add_argument("--runtime", required=True)
        item.add_argument("--source-dir", type=Path)
        item.add_argument("--candidate-dir", type=Path)
        item.add_argument("--fingerprint")

    cohort = domains.add_parser("cohort")
    cohort_actions = cohort.add_subparsers(dest="action", required=True)
    for action in ("capture", "restore", "verify", "encrypt"):
        item = cohort_actions.add_parser(action)
        item.add_argument("--runtime", required=True)
        item.add_argument("--bundle", type=Path, required=True)
        item.add_argument("--destination", type=Path)
        item.add_argument("--fingerprint")
        item.add_argument("--evidence", type=Path)
        item.add_argument("--step-ca", type=Path)
        item.add_argument("--dss", type=Path)
        item.add_argument("--release-identity", type=Path)
        item.add_argument("--recipient")
        if action == "restore":
            item.add_argument("--confirm", required=True)

    cutover = domains.add_parser("cutover")
    cutover_actions = cutover.add_subparsers(dest="action", required=True)
    for action in ("preflight", "stage", "configure", "gate", "admit", "reset"):
        item = cutover_actions.add_parser(action)
        item.add_argument("--runtime", required=True)
        item.add_argument("--candidate", type=Path, required=True)
        item.add_argument("--fingerprint", required=True)
        item.add_argument("--evidence", type=Path)
        item.add_argument("--configuration", type=Path)
        item.add_argument("--secrets-file", type=Path)
        if action in {"admit", "reset"}:
            item.add_argument("--confirm", required=True)
    return result


def main(arguments: list[str] | None = None, *, runner: CommandRunner | None = None) -> int:
    args = parser().parse_args(arguments)
    runner = runner or CommandRunner()
    try:
        if args.domain == "qa":
            value = command_qa(args, runner)
        elif args.domain == "transition":
            value = command_transition(args, runner)
        else:
            value = command_release_domain(args, runner)
        if value is not None:
            print(json.dumps(value, indent=2, sort_keys=True))
        return 0
    except RuntimeError as error:
        print(f"Migration operation refused: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
