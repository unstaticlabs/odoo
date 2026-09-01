"""Single operator interface for USL runtime lifecycle and recovery."""

from __future__ import annotations

import argparse
import base64
import hashlib
import io
import json
import sys
import time
from contextlib import contextmanager, redirect_stdout
from datetime import UTC, datetime
from pathlib import Path

from operations.release_manifest import validate as validate_release
from operations.runtime import (
    RuntimeError,
    compose_command,
    compose_identity,
    inspect_runtime,
    load_target,
    read_active_state,
    validate_secret_text,
)


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_TARGETS = ROOT / "operations/targets"
VOLUME_LOGICAL_NAMES = {
    "odoo_postgres": "postgres-data",
    "odoo_filestore": "odoo-data",
    "paperless_postgres": "paperless-postgres-data",
    "paperless_broker": "paperless-broker-data",
    "paperless_media": "paperless-media",
    "paperless_data": "paperless-data",
    "paperless_trash": "paperless-trash",
    "paperless_consume": "paperless-consume",
    "paperless_export": "paperless-export",
    "mcp_oauth": "odoo-mcp-oauth-data",
}
RELEASE_IMAGE_SERVICES = {
    "distribution": ("odoo", "init-db"),
    "paperless": (
        "paperless-model-preflight",
        "paperless-webserver",
        "paperless-access-init",
        "paperless-identity-init",
    ),
    "sign-dss": ("usl-sign-dss",),
    "mcp": ("odoo-mcp-oauth-init", "odoo-mcp"),
    "renderer": ("usl-document-renderer",),
}


def runtime_command(arguments: argparse.Namespace) -> int:
    target = load_target(arguments.target, arguments.targets)
    runner = target.runner()
    if arguments.action == "status":
        result = inspect_runtime(target, runner)
    else:
        identity = compose_identity(target, runner)
        if arguments.action == "start":
            runner.run(compose_command(identity, ["up", "--detach", "--wait"]))
        else:
            runner.run(compose_command(identity, ["stop"]))
        result = inspect_runtime(target, runner)
    print(json.dumps(result, indent=None if arguments.json else 2, sort_keys=True))
    return 0


def _read_path(target, runner, path: Path) -> str:
    if path.is_file():
        return path.read_text(encoding="utf-8")
    return runner.run(["cat", str(path)]).stdout


def _release(target, runner, override: Path | None) -> tuple[dict, str, str]:
    active = read_active_state(target, runner) if override is None else None
    path = override or Path(
        active["release_manifest"] if active is not None else target.value["release_manifest"],
    )
    raw = _read_path(target, runner, path)
    try:
        value = validate_release(json.loads(raw))
    except (ValueError, json.JSONDecodeError) as error:
        raise RuntimeError(f"release manifest is invalid: {path}") from error
    canonical = json.dumps(value, separators=(",", ":"), sort_keys=True)
    return value, hashlib.sha256(canonical.encode()).hexdigest(), canonical


def _secret_file(target, runner) -> str:
    path = target.value["secrets"]["env_file"]
    text = runner.run(["cat", path]).stdout
    validate_secret_text(text, target.value["secrets"]["allowed_keys"])
    return path


def _cohort_command(
    target,
    image: str,
    action: str,
    arguments: list[str],
    *,
    volumes=None,
) -> list[str]:
    volumes = volumes or target.value["volumes"]
    paths = target.value["paths"]
    command = [
        "docker",
        "run",
        "--rm",
        "--network",
        target.value["compose"]["default_network"],
        "--env-file",
        target.value["secrets"]["env_file"],
        "--env",
        f"RESTIC_REPOSITORY={target.value['backup']['durable_repository']}",
        "--env",
        f"USL_BACKUP_CACHE_REPOSITORY={target.value['backup']['cache_repository']}",
        "--volume",
        f"{target.value['state_directory']}:/cohort",
        "--volume",
        f"{volumes['odoo_filestore']['name']}:/source/odoo-data:ro",
        "--volume",
        f"{volumes['paperless_media']['name']}:/source/paperless-media:ro",
        "--volume",
        f"{volumes['paperless_data']['name']}:/source/paperless-data:ro",
        "--volume",
        f"{volumes['paperless_trash']['name']}:/source/paperless-trash:ro",
        "--volume",
        f"{volumes['paperless_consume']['name']}:/source/paperless-consume:ro",
        "--volume",
        f"{volumes['mcp_oauth']['name']}:/source/mcp-oauth:ro",
        "--volume",
        f"{paths['sign_ca']['path']}:/source/sign-ca:ro",
        image,
        action,
        *arguments,
    ]
    if paths["sign_evidence"]["required"]:
        insertion = command.index(image)
        command[insertion:insertion] = [
            "--volume",
            f"{paths['sign_evidence']['path']}:/source/sign-evidence:ro",
        ]
    return command


def _database_environment(target) -> list[str]:
    values = []
    for prefix, database in (("ODOO", target.value["databases"]["odoo"]), ("PAPERLESS", target.value["databases"]["paperless"])):
        values.extend(
            (
                "--env",
                f"{prefix}_DB_HOST={database['service']}",
                "--env",
                f"{prefix}_DB_PORT=5432",
                "--env",
                f"{prefix}_DB_NAME={database['name']}",
                "--env",
                f"{prefix}_DB_USER={database['user']}",
            ),
        )
    return values


def _run_cohort(
    target,
    runner,
    image: str,
    action: str,
    arguments: list[str],
    environment: list[str] | None = None,
    *,
    volumes=None,
):
    command = _cohort_command(target, image, action, arguments, volumes=volumes)
    if environment:
        insertion = command.index(image)
        command[insertion:insertion] = environment
    result = runner.run(command)
    lines = [line for line in result.stdout.splitlines() if line.strip()]
    if not lines:
        raise RuntimeError(f"cohort {action} returned no result")
    try:
        return json.loads(lines[-1])
    except json.JSONDecodeError as error:
        raise RuntimeError(f"cohort {action} returned invalid JSON") from error


def with_writers_paused(runner, identity: dict, services: list[str], callback):
    runner.run(compose_command(identity, ["stop", "--timeout", "30", *services]))
    try:
        return callback()
    finally:
        runner.run(
            compose_command(
                identity,
                ["up", "--detach", "--wait", "--no-recreate", *services],
            ),
        )


def _ensure_image(runner, image: str) -> None:
    present = runner.run(["docker", "image", "inspect", image], check=False)
    if present.returncode:
        runner.run(["docker", "pull", image])


@contextmanager
def runtime_lock(target, runner, operation: str, run_id: str):
    root = target.value["state_directory"]
    lock = f"{root}/operation.lock"
    runner.run(["install", "-d", "-m", "0700", root])
    acquired = runner.run(["mkdir", lock], check=False)
    if acquired.returncode:
        raise RuntimeError(f"another operation already owns {target.name}: {lock}")
    owner = json.dumps(
        {
            "schema": "usl-operation-lock/v1",
            "target": target.name,
            "operation": operation,
            "run_id": run_id,
            "started_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        },
        sort_keys=True,
    )
    _write_remote(target, runner, f"{lock}/owner.json", owner + "\n")
    try:
        yield
    finally:
        runner.run(["rm", "-f", f"{lock}/owner.json"], check=False)
        runner.run(["rmdir", lock], check=False)


def _record_event(target, runner, run_id: str, operation: str, phase: str, status: str, **details) -> None:
    event = {
        "schema": "usl-operation-event/v1",
        "at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "target": target.name,
        "run_id": run_id,
        "operation": operation,
        "phase": phase,
        "status": status,
        **details,
    }
    path = f"{target.value['state_directory']}/runs/{run_id}.jsonl"
    encoded = base64.b64encode((json.dumps(event, sort_keys=True) + "\n").encode()).decode()
    program = (
        "import base64,os,pathlib,sys;"
        "p=pathlib.Path(sys.argv[1]);p.parent.mkdir(parents=True,exist_ok=True);"
        "f=p.open('ab');f.write(base64.b64decode(sys.argv[2]));f.close();os.chmod(p,0o600)"
    )
    runner.run(["python3", "-c", program, path, encoded])


def backup_command(arguments: argparse.Namespace) -> int:
    target = load_target(arguments.target, arguments.targets)
    runner = target.runner()
    runtime = inspect_runtime(target, runner)
    release, release_sha, release_raw = _release(target, runner, arguments.release)
    _secret_file(target, runner)
    image = release["components"]["backup-tool"]["digest_reference"]
    if arguments.action == "list":
        result = _run_cohort(target, runner, image, "list", [], volumes=runtime["volumes"])
    elif arguments.action == "verify":
        if not arguments.snapshot:
            raise RuntimeError("backup verify requires --snapshot")
        result = _run_cohort(
            target,
            runner,
            image,
            "verify",
            ["--durable-snapshot", arguments.snapshot],
            volumes=runtime["volumes"],
        )
    else:
        if arguments.resume and arguments.run_id:
            raise RuntimeError("use either --run-id or --resume")
        run_id = arguments.resume or arguments.run_id or (
            f"{datetime.now(UTC):%Y%m%dt%H%M%Sz}-{release['source']['commit'][:8]}"
        )
        _ensure_image(runner, image)
        with runtime_lock(target, runner, "backup", run_id):
            started = time.monotonic()
            _record_event(target, runner, run_id, "backup", "operation", "started")
            captured = None
            freeze_seconds = 0.0
            if not arguments.resume:
                identity = compose_identity(target, runner)
                writer_services = [
                    target.value["services"][name]
                    for name in ("odoo", "paperless", "mcp", "sign")
                ]

                def capture_phase():
                    environment = [
                        *_database_environment(target),
                        "--env",
                        f"USL_TARGET={target.name}",
                        "--env",
                        f"USL_RELEASE_COMMIT={release['source']['commit']}",
                        "--env",
                        f"USL_RELEASE_MANIFEST_SHA256={release_sha}",
                        "--env",
                        f"USL_RELEASE_MANIFEST_JSON={release_raw}",
                        "--env",
                        f"USL_OLLAMA_MODEL={target.value['ollama']['model']}",
                        "--env",
                        f"USL_OLLAMA_MANIFEST_SHA256={target.value['ollama']['manifest_sha256']}",
                        "--env",
                        f"USL_OLLAMA_DIMENSION={target.value['ollama']['dimension']}",
                    ]
                    return _run_cohort(
                        target,
                        runner,
                        image,
                        "capture",
                        ["--run-id", run_id],
                        environment,
                        volumes=runtime["volumes"],
                    )

                freeze_started = time.monotonic()
                _record_event(target, runner, run_id, "backup", "capture", "started")
                captured = with_writers_paused(
                    runner,
                    identity,
                    writer_services,
                    capture_phase,
                )
                freeze_seconds = round(time.monotonic() - freeze_started, 3)
                _record_event(
                    target,
                    runner,
                    run_id,
                    "backup",
                    "capture",
                    "completed",
                    duration_seconds=freeze_seconds,
                )
            upload_started = time.monotonic()
            _record_event(target, runner, run_id, "backup", "upload", "started")
            uploaded = _run_cohort(
                target,
                runner,
                image,
                "push",
                ["--run-id", run_id],
                volumes=runtime["volumes"],
            )
            upload_seconds = round(time.monotonic() - upload_started, 3)
            _record_event(
                target,
                runner,
                run_id,
                "backup",
                "upload",
                "completed",
                duration_seconds=upload_seconds,
            )
            verify_started = time.monotonic()
            _record_event(target, runner, run_id, "backup", "verification", "started")
            qualified = _run_cohort(
                target,
                runner,
                image,
                "qualify",
                ["--durable-snapshot", uploaded["durable_snapshot_id"]],
                volumes=runtime["volumes"],
            )
            verify_seconds = round(time.monotonic() - verify_started, 3)
            total_seconds = round(time.monotonic() - started, 3)
            _record_event(
                target,
                runner,
                run_id,
                "backup",
                "operation",
                "completed",
                duration_seconds=total_seconds,
            )
            result = {
                "schema": "usl-backup-run/v1",
                "run_id": run_id,
                "capture": captured,
                "upload": uploaded,
                "qualification": qualified,
                "performance": {
                    "writer_freeze_seconds": freeze_seconds,
                    "writer_freeze_sla_seconds": 120,
                    "writer_freeze_sla_passed": arguments.resume or freeze_seconds <= 120,
                    "upload_seconds": upload_seconds,
                    "verification_seconds": verify_seconds,
                    "total_seconds": total_seconds,
                },
                "status": "qualified",
            }
    print(json.dumps(result, indent=None if arguments.json else 2, sort_keys=True))
    return 0


def _psql(target, runner, identity, database_key: str, query: str) -> str:
    database = target.value["databases"][database_key]
    command = compose_command(
        identity,
        [
            "exec",
            "--no-TTY",
            database["service"],
            "psql",
            "--username",
            database["user"],
            "--dbname",
            database["name"],
            "--tuples-only",
            "--no-align",
            "--command",
            query,
        ],
    )
    return runner.run(command).stdout.strip()


def _python_probe(target, runner, identity, service_key: str, program: str) -> dict:
    service = target.value["services"][service_key]
    result = runner.run(
        compose_command(identity, ["exec", "--no-TTY", service, "python", "-c", program]),
    )
    try:
        value = json.loads(result.stdout.strip())
    except json.JSONDecodeError as error:
        raise RuntimeError(f"{service} storage probe did not return JSON") from error
    if not isinstance(value, dict):
        raise RuntimeError(f"{service} storage probe did not return an object")
    return value


def health_command(arguments: argparse.Namespace) -> int:
    target = load_target(arguments.target, arguments.targets)
    runner = target.runner()
    status = inspect_runtime(target, runner)
    expected = set(target.value["services"].values())
    containers = {item.get("Service"): item for item in status["containers"]}
    failures = []
    for service in sorted(expected):
        item = containers.get(service)
        if not item or item.get("State") != "running":
            failures.append(f"{service}:not-running")
        elif item.get("Health") not in {None, "", "healthy"}:
            failures.append(f"{service}:{item.get('Health')}")
    endpoints = {}
    paths = {"odoo": "/web/health?db_server_status=1", "paperless": "/api/", "mcp": "/readyz"}
    for name, origin in target.value["endpoints"].items():
        url = origin.rstrip("/") + paths[name]
        process = runner.run(
            [
                "curl",
                "--silent",
                "--show-error",
                "--output",
                "/dev/null",
                "--write-out",
                "%{http_code}",
                "--max-time",
                "10",
                url,
            ],
            check=False,
        )
        code = int(process.stdout) if process.returncode == 0 and process.stdout.isdigit() else 0
        ok = 200 <= code < 500
        endpoints[name] = {"url": url, "status_code": code, "ok": ok}
        if not ok:
            failures.append(f"{name}:http")
    paperless = target.value["services"]["paperless"]
    ollama = target.value["ollama"]
    probe = (
        "import json,urllib.request;"
        f"u={ollama['url']!r};m={ollama['model']!r};"
        "t=json.load(urllib.request.urlopen(u+'/api/tags',timeout=10));"
        "x=[i for i in t.get('models',[]) if i.get('name')==m];"
        f"assert len(x)==1 and x[0].get('digest')=={ollama['manifest_sha256']!r};"
        "r=urllib.request.Request(u+'/api/embed',data=json.dumps({'model':m,'input':['USL health']}).encode(),headers={'Content-Type':'application/json'});"
        "e=json.load(urllib.request.urlopen(r,timeout=60)).get('embeddings',[]);"
        f"assert len(e)==1 and len(e[0])=={ollama['dimension']};"
        "print(json.dumps({'digest':x[0]['digest'],'dimension':len(e[0])}))"
    )
    identity = status["compose"]
    ollama_result = runner.run(
        compose_command(identity, ["exec", "--no-TTY", paperless, "python", "-c", probe]),
        check=False,
    )
    if ollama_result.returncode:
        failures.append("ollama:identity")
        ollama_status = None
    else:
        ollama_status = json.loads(ollama_result.stdout)
    result = {
        "schema": "usl-runtime-health/v1",
        "target": target.name,
        "status": "passed" if not failures else "failed",
        "failures": failures,
        "endpoints": endpoints,
        "ollama": ollama_status,
    }
    print(json.dumps(result, indent=None if arguments.json else 2, sort_keys=True))
    return 0 if not failures else 2


def smoke_command(arguments: argparse.Namespace) -> int:
    target = load_target(arguments.target, arguments.targets)
    runner = target.runner()
    status = inspect_runtime(target, runner)
    identity = status["compose"]
    odoo_query = """
SELECT json_build_object(
  'companies', (SELECT count(*) FROM res_company),
  'users', (SELECT count(*) FROM res_users),
  'moves', (SELECT count(*) FROM account_move),
  'move_lines', (SELECT count(*) FROM account_move_line),
  'attachments', (SELECT count(*) FROM ir_attachment),
  'stored_attachments', (SELECT count(DISTINCT store_fname) FROM ir_attachment WHERE store_fname IS NOT NULL),
  'projects', (SELECT count(*) FROM project_project),
  'tasks', (SELECT count(*) FROM project_task),
  'expenses', (SELECT count(*) FROM hr_expense),
  'ledger_delta', (SELECT coalesce(sum(debit-credit), 0) FROM account_move_line),
  'queued_mail', (SELECT count(*) FROM mail_mail WHERE state IN ('outgoing','exception')),
  'pending_documents', (SELECT count(*) FROM usl_document_operation WHERE state IN ('pending','uploading','processing','failed','duplicate')),
  'bank_unsettled', (SELECT count(*) FROM account_bank_ingestion WHERE state IN ('received','processing','failed')),
  'payment_unsettled', (SELECT count(*) FROM payment_transaction WHERE state IN ('draft','pending','authorized','error')),
  'sign_archive_unsettled', (SELECT count(*) FROM sign_oca_request WHERE archive_status IN ('pending','processing','failed')),
  'cron_failures', (SELECT coalesce(sum(failure_count), 0) FROM ir_cron WHERE active)
);""".strip()
    paperless_query = """
SELECT json_build_object(
  'documents', count(*),
  'with_ocr', count(*) FILTER (WHERE coalesce(content, '') <> ''),
  'missing_original_name', count(*) FILTER (WHERE coalesce(filename, '') = '')
) FROM documents_document;""".strip()
    try:
        odoo = json.loads(_psql(target, runner, identity, "odoo", odoo_query))
        paperless = json.loads(_psql(target, runner, identity, "paperless", paperless_query))
        odoo_storage = _python_probe(
            target,
            runner,
            identity,
            "odoo",
            "import json,os,pathlib;"
            "p=pathlib.Path('/var/lib/odoo/filestore')/os.environ['ODOO_DB_NAME'];"
            "print(json.dumps({'files':sum(1 for x in p.rglob('*') if x.is_file())}))",
        )
        paperless_storage = _python_probe(
            target,
            runner,
            identity,
            "paperless",
            "import json,pathlib;"
            "roots={'originals':'/usr/src/paperless/media/documents/originals',"
            "'archive':'/usr/src/paperless/media/documents/archive',"
            "'thumbnails':'/usr/src/paperless/media/documents/thumbnails',"
            "'tantivy':'/usr/src/paperless/data/index',"
            "'vectors':'/usr/src/paperless/data/llm_index'};"
            "print(json.dumps({k:sum(1 for x in pathlib.Path(v).rglob('*') if x.is_file()) for k,v in roots.items()}))",
        )
    except (RuntimeError, json.JSONDecodeError) as error:
        raise RuntimeError(f"read-only database smoke failed: {error}") from error
    failures = []
    if float(odoo["ledger_delta"]) != 0:
        failures.append("accounting:unbalanced")
    if min(odoo["companies"], odoo["users"], odoo["moves"], odoo["attachments"]) < 1:
        failures.append("odoo:empty-control")
    queue_keys = (
        "queued_mail",
        "pending_documents",
        "bank_unsettled",
        "payment_unsettled",
        "sign_archive_unsettled",
    )
    if any(odoo[key] for key in queue_keys):
        failures.append("odoo:pending-queue")
    if odoo["cron_failures"]:
        failures.append("odoo:cron-failures")
    if odoo_storage["files"] < odoo["stored_attachments"]:
        failures.append("odoo:filestore-coverage")
    if paperless["documents"] < 1 or paperless["with_ocr"] < 1:
        failures.append("paperless:empty-control")
    if paperless["missing_original_name"]:
        failures.append("paperless:missing-filename")
    if paperless_storage["originals"] < paperless["documents"]:
        failures.append("paperless:original-coverage")
    for role in ("archive", "thumbnails", "tantivy", "vectors"):
        if paperless_storage[role] < 1:
            failures.append(f"paperless:{role}-missing")
    result = {
        "schema": "usl-runtime-smoke/v1",
        "target": target.name,
        "status": "passed" if not failures else "failed",
        "failures": failures,
        "controls": {"odoo": odoo, "paperless": paperless},
        "storage": {"odoo": odoo_storage, "paperless": paperless_storage},
    }
    print(json.dumps(result, indent=None if arguments.json else 2, sort_keys=True))
    return 0 if not failures else 2


def generation_volume_names(target, generation: str) -> dict[str, str]:
    return {
        role: f"{target.project}-{generation}-{role.replace('_', '-')}"
        for role in target.value["volumes"]
    }


def _create_generation_resources(target, runner, generation: str) -> tuple[dict[str, str], str]:
    volumes = generation_volume_names(target, generation)
    network = f"{target.project}-{generation}-recovery"
    for role, name in volumes.items():
        probe = runner.run(["docker", "volume", "inspect", name], check=False)
        if probe.returncode == 0:
            raise RuntimeError(f"generation volume already exists: {name}")
        runner.run(
            [
                "docker",
                "volume",
                "create",
                "--label",
                f"com.unstaticlabs.runtime.project={target.project}",
                "--label",
                f"com.unstaticlabs.runtime.target={target.name}",
                "--label",
                f"com.unstaticlabs.runtime.generation={generation}",
                "--label",
                f"com.unstaticlabs.runtime.role={role}",
                name,
            ],
        )
    probe = runner.run(["docker", "network", "inspect", network], check=False)
    if probe.returncode == 0:
        raise RuntimeError(f"generation network already exists: {network}")
    runner.run(
        [
            "docker",
            "network",
            "create",
            "--label",
            f"com.unstaticlabs.runtime.project={target.project}",
            "--label",
            f"com.unstaticlabs.runtime.target={target.name}",
            "--label",
            f"com.unstaticlabs.runtime.generation={generation}",
            network,
        ],
    )
    return volumes, network


def _runtime_images(runner, identity: dict) -> dict[str, str]:
    result = runner.run(compose_command(identity, ["config", "--format", "json"]))
    try:
        config = json.loads(result.stdout)
        services = config["services"]
        return {name: value["image"] for name, value in services.items()}
    except (KeyError, TypeError, json.JSONDecodeError) as error:
        raise RuntimeError("Compose image inventory is invalid") from error


def _start_generation_database(
    target,
    runner,
    generation: str,
    network: str,
    volume: str,
    image: str,
    database_key: str,
    alias: str,
) -> str:
    database = target.value["databases"][database_key]
    name = f"{target.project}-{generation}-{database_key}-db"
    password_key = database["password_key"]
    runner.run(
        [
            "docker",
            "run",
            "--detach",
            "--name",
            name,
            "--label",
            f"com.unstaticlabs.runtime.project={target.project}",
            "--label",
            f"com.unstaticlabs.runtime.generation={generation}",
            "--network",
            network,
            "--network-alias",
            alias,
            "--env-file",
            target.value["secrets"]["env_file"],
            "--env",
            f"POSTGRES_USER={database['user']}",
            "--env",
            "POSTGRES_DB=postgres",
            "--volume",
            f"{volume}:/var/lib/postgresql/data",
            "--entrypoint",
            "/bin/bash",
            image,
            "-ceu",
            f'export POSTGRES_PASSWORD="${password_key}"; exec /usr/local/bin/docker-entrypoint.sh postgres',
        ],
    )
    for _attempt in range(60):
        ready = runner.run(
            ["docker", "exec", name, "pg_isready", "--username", database["user"], "--dbname", "postgres"],
            check=False,
        )
        if ready.returncode == 0:
            return name
        time.sleep(1)
    raise RuntimeError(f"generation database did not become ready: {name}")


def _materialize_command(
    source,
    target,
    image: str,
    snapshot: str,
    generation: str,
    network: str,
    volumes: dict[str, str],
) -> list[str]:
    generation_root = f"{target.value['state_directory']}/generations/{generation}"
    command = [
        "docker",
        "run",
        "--rm",
        "--network",
        network,
        "--env-file",
        target.value["secrets"]["env_file"],
        "--env",
        f"RESTIC_REPOSITORY={source.value['backup']['durable_repository']}",
        "--env",
        f"USL_BACKUP_CACHE_REPOSITORY={source.value['backup']['cache_repository']}",
        "--env",
        f"USL_TARGET={target.name}",
        "--env",
        f"USL_TARGET_ENVIRONMENT={target.value['environment']}",
        "--env",
        f"USL_RESTORE_GENERATION_CONFIRMED={generation}",
        "--env",
        f"USL_EXPECTED_RESTORE_CONFIRMATION={generation}",
        *_database_environment(target),
        "--volume",
        f"{generation_root}/work:/cohort",
        "--volume",
        f"{volumes['odoo_filestore']}:/target/odoo-data",
        "--volume",
        f"{volumes['paperless_media']}:/target/paperless-media",
        "--volume",
        f"{volumes['paperless_data']}:/target/paperless-data",
        "--volume",
        f"{volumes['paperless_trash']}:/target/paperless-trash",
        "--volume",
        f"{volumes['paperless_consume']}:/target/paperless-consume",
        "--volume",
        f"{volumes['mcp_oauth']}:/target/mcp-oauth",
        "--volume",
        f"{generation_root}/sign-ca:/target/sign-ca",
        "--volume",
        f"{generation_root}/sign-evidence:/target/sign-evidence",
        image,
        "materialize",
        "--durable-snapshot",
        snapshot,
    ]
    return command


def _write_remote(target, runner, path: str, content: str, mode: str = "0600") -> None:
    encoded = base64.b64encode(content.encode()).decode()
    program = (
        "import base64,os,pathlib,sys;"
        "p=pathlib.Path(sys.argv[1]);p.parent.mkdir(parents=True,exist_ok=True);"
        "p.write_bytes(base64.b64decode(sys.argv[2]));os.chmod(p,int(sys.argv[3],8))"
    )
    runner.run(["python3", "-c", program, path, encoded, mode])


def _generation_overlay(
    volumes: dict[str, str],
    release: dict | None = None,
    available_services: set[str] | None = None,
) -> str:
    value = {
        "volumes": {
            VOLUME_LOGICAL_NAMES[role]: {"external": True, "name": name}
            for role, name in volumes.items()
        },
    }
    if release is not None:
        images = {
            "distribution": release["components"]["distribution"]["digest_reference"],
            "paperless": release["components"]["paperless"]["digest_reference"],
            "sign-dss": release["components"]["sign-dss"]["digest_reference"],
            "mcp": release["mcp"]["image"],
            "renderer": release["renderer"]["image"],
        }
        value["services"] = {
            service: {"image": images[component], "build": None}
            for component, services in RELEASE_IMAGE_SERVICES.items()
            for service in services
            if available_services is None or service in available_services
        }
    return json.dumps(value, indent=2, sort_keys=True) + "\n"


def _neutralize_generation(target, runner, release: dict, generation: str, network: str, volumes: dict[str, str]) -> None:
    if target.value["environment"] == "production":
        return
    database = target.value["databases"]["odoo"]
    image = release["components"]["distribution"]["digest_reference"]
    runner.run(
        [
            "docker",
            "run",
            "--rm",
            "--network",
            network,
            "--env-file",
            target.value["secrets"]["env_file"],
            "--env",
            f"ODOO_DB_HOST={database['service']}",
            "--env",
            "ODOO_DB_PORT=5432",
            "--env",
            f"ODOO_DB_USER={database['user']}",
            "--env",
            f"ODOO_DB_NAME={database['name']}",
            "--env",
            f"ODOO_DB_FILTER=^{database['name']}$",
            "--env",
            "ODOO_MAX_CRON_THREADS=0",
            "--env",
            "USL_DEPLOYMENT_ENV=recovery",
            "--env",
            "USL_EINVOICE_LIVE_ENABLED=0",
            "--env",
            "USL_EREPORTING_LIVE_ENABLED=0",
            "--env",
            "USL_POCKET_ID_ENABLED=0",
            "--volume",
            f"{volumes['odoo_filestore']}:/var/lib/odoo",
            "--label",
            f"com.unstaticlabs.runtime.generation={generation}",
            image,
            "odoo",
            "neutralize",
            "--config=/etc/odoo/odoo.conf",
            f"--database={database['name']}",
        ],
    )


def _gate(handler, target, targets: Path) -> dict:
    output = io.StringIO()
    arguments = argparse.Namespace(target=target.name, targets=targets, json=True)
    with redirect_stdout(output):
        status = handler(arguments)
    lines = [line for line in output.getvalue().splitlines() if line.strip()]
    value = json.loads(lines[-1]) if lines else {"status": "failed"}
    if status:
        raise RuntimeError(f"{value.get('schema', 'runtime gate')} failed: {value.get('failures', [])}")
    return value


def _active_generation_state(
    target,
    generation: str,
    volumes: dict[str, str],
    network: str,
    snapshot: str,
    release_manifest: str,
    previous: dict,
) -> str:
    value = {
        "schema": "usl-active-generation/v1",
        "target": target.name,
        "generation": generation,
        "volumes": volumes,
        "network": network,
        "snapshot": snapshot,
        "release_manifest": release_manifest,
        "previous": previous,
    }
    return json.dumps(value, indent=2, sort_keys=True) + "\n"


def _validate_materialized_release(
    materialized: dict,
    release: dict,
    release_sha: str,
) -> None:
    embedded = materialized.get("release", {})
    if embedded.get("manifest_sha256") != release_sha:
        raise RuntimeError("selected release differs from the cohort release")
    if embedded.get("commit") != release["source"]["commit"]:
        raise RuntimeError("selected release commit differs from the cohort release")


def _restore_unlocked(arguments: argparse.Namespace) -> int:
    source = load_target(arguments.source, arguments.targets)
    target = load_target(arguments.target, arguments.targets)
    if target.protected and (not arguments.replace or arguments.confirm != target.name):
        raise RuntimeError("protected restore requires --replace and exact --confirm")
    if arguments.replace and arguments.confirm != target.name:
        raise RuntimeError("replacement confirmation must equal the target name")
    target_runner = target.runner()
    if source.value["transport"] != target.value["transport"]:
        raise RuntimeError("source and target must be reachable through the same runtime host")
    current = inspect_runtime(target, target_runner)
    _secret_file(target, target_runner)
    release, release_sha, release_raw = _release(source, target_runner, arguments.release)
    tool_image = release["components"]["backup-tool"]["digest_reference"]
    generation = arguments.generation or f"g{datetime.now(UTC):%Y%m%dt%H%M}-{arguments.snapshot[:8]}"
    if len(generation) > 32 or not generation.startswith("g"):
        raise RuntimeError("generation name is invalid")
    generation_root = f"{target.value['state_directory']}/generations/{generation}"
    target_runner.run(["install", "-d", "-m", "0700", generation_root])
    volumes, network = _create_generation_resources(target, target_runner, generation)
    identity = current["compose"]
    images = _runtime_images(target_runner, identity)
    database_containers = []
    try:
        database_containers.append(
            _start_generation_database(
                target,
                target_runner,
                generation,
                network,
                volumes["odoo_postgres"],
                images[target.value["services"]["odoo_db"]],
                "odoo",
                target.value["databases"]["odoo"]["service"],
            ),
        )
        database_containers.append(
            _start_generation_database(
                target,
                target_runner,
                generation,
                network,
                volumes["paperless_postgres"],
                images[target.value["services"]["paperless_db"]],
                "paperless",
                target.value["databases"]["paperless"]["service"],
            ),
        )
        materialized = target_runner.run(
            _materialize_command(
                source,
                target,
                tool_image,
                arguments.snapshot,
                generation,
                network,
                volumes,
            ),
        )
        materialize_state = json.loads(materialized.stdout.splitlines()[-1])
        _validate_materialized_release(materialize_state, release, release_sha)
        _neutralize_generation(target, target_runner, release, generation, network, volumes)
    finally:
        for container in database_containers:
            target_runner.run(["docker", "rm", "--force", container], check=False)
    release_path = f"{generation_root}/usl-release.json"
    _write_remote(target, target_runner, release_path, release_raw + "\n")
    overlay = f"{generation_root}/compose.generation.json"
    _write_remote(
        target,
        target_runner,
        overlay,
        _generation_overlay(volumes, release, set(images)),
    )
    generation_identity = {**identity, "compose_files": [*identity["compose_files"], overlay]}
    previous = {
        "generation": current["generation"],
        "volumes": {role: item["name"] for role, item in current["volumes"].items()},
        "network": (current["active_state"] or {}).get("network"),
        "release_manifest": (current["active_state"] or {}).get("release_manifest"),
        "snapshot": (current["active_state"] or {}).get("snapshot"),
    }
    try:
        target_runner.run(compose_command(identity, ["stop", "--timeout", "60"]))
        target_runner.run(
            compose_command(generation_identity, ["up", "--detach", "--wait", "--force-recreate"]),
        )
    except Exception:
        target_runner.run(compose_command(identity, ["up", "--detach", "--wait", "--force-recreate"]))
        raise
    active_path = f"{target.value['state_directory']}/active.json"
    _write_remote(
        target,
        target_runner,
        active_path,
        _active_generation_state(
            target,
            generation,
            volumes,
            network,
            arguments.snapshot,
            release_path,
            previous,
        ),
    )
    try:
        health = _gate(health_command, target, arguments.targets)
        smoke = _gate(smoke_command, target, arguments.targets)
        if smoke["controls"] != materialize_state["controls"]:
            raise RuntimeError("restored business controls differ from the source cohort")
    except Exception:
        target_runner.run(compose_command(generation_identity, ["stop", "--timeout", "60"]), check=False)
        if current["active_state"] is None:
            target_runner.run(["rm", "-f", active_path], check=False)
        else:
            _write_remote(
                target,
                target_runner,
                active_path,
                json.dumps(current["active_state"], indent=2, sort_keys=True) + "\n",
            )
        target_runner.run(compose_command(identity, ["up", "--detach", "--wait", "--force-recreate"]))
        raise
    result = {
        "schema": "usl-restore-run/v1",
        "source": source.name,
        "target": target.name,
        "snapshot": arguments.snapshot,
        "generation": generation,
        "materialize": materialize_state,
        "health": health,
        "smoke": smoke,
        "status": "activated",
    }
    print(json.dumps(result, indent=None if arguments.json else 2, sort_keys=True))
    return 0


def restore_command(arguments: argparse.Namespace) -> int:
    target = load_target(arguments.target, arguments.targets)
    generation = arguments.generation or (
        f"g{datetime.now(UTC):%Y%m%dt%H%M}-{arguments.snapshot[:8]}"
    )
    arguments.generation = generation
    runner = target.runner()
    started = time.monotonic()
    with runtime_lock(target, runner, "restore", generation):
        _record_event(target, runner, generation, "restore", "operation", "started")
        try:
            status = _restore_unlocked(arguments)
        except Exception as error:
            _record_event(
                target,
                runner,
                generation,
                "restore",
                "operation",
                "failed",
                duration_seconds=round(time.monotonic() - started, 3),
                error_type=type(error).__name__,
            )
            raise
        _record_event(
            target,
            runner,
            generation,
            "restore",
            "operation",
            "completed",
            duration_seconds=round(time.monotonic() - started, 3),
        )
        return status


def cleanup_command(arguments: argparse.Namespace) -> int:
    target = load_target(arguments.target, arguments.targets)
    runner = target.runner()
    current = inspect_runtime(target, runner)
    active = {item["name"] for item in current["volumes"].values()}
    state_path = f"{target.value['state_directory']}/active.json"
    state_result = runner.run(["cat", state_path], check=False)
    previous: set[str] = set()
    protected_networks: set[str] = set()
    if state_result.returncode == 0:
        state = json.loads(state_result.stdout)
        previous = set((state.get("previous") or {}).get("volumes", {}).values())
        protected_networks.add(state["network"])
        previous_network = (state.get("previous") or {}).get("network")
        if previous_network:
            protected_networks.add(previous_network)
    inventory = runner.run(
        [
            "docker",
            "volume",
            "ls",
            "--filter",
            f"label=com.unstaticlabs.runtime.target={target.name}",
            "--format",
            "{{.Name}}",
        ],
    )
    candidates = sorted(
        name for name in inventory.stdout.splitlines() if name and name not in active | previous
    )
    network_inventory = runner.run(
        [
            "docker",
            "network",
            "ls",
            "--filter",
            f"label=com.unstaticlabs.runtime.target={target.name}",
            "--format",
            "{{.Name}}",
        ],
    )
    network_candidates = sorted(
        name
        for name in network_inventory.stdout.splitlines()
        if name and name not in protected_networks
    )
    plan = {
        "schema": "usl-cleanup-plan/v1",
        "target": target.name,
        "protected_volumes": sorted(active | previous),
        "protected_networks": sorted(protected_networks),
        "delete_volumes": candidates,
        "delete_networks": network_candidates,
    }
    if arguments.action == "apply":
        if arguments.confirm != target.name:
            raise RuntimeError("cleanup apply requires exact --confirm")
        for name in candidates:
            labels = json.loads(
                runner.run(
                    ["docker", "volume", "inspect", name, "--format", "{{json .Labels}}"],
                ).stdout,
            )
            if labels.get("com.unstaticlabs.runtime.target") != target.name:
                raise RuntimeError(f"cleanup candidate became foreign: {name}")
            runner.run(["docker", "volume", "rm", name])
        for name in network_candidates:
            labels = json.loads(
                runner.run(
                    ["docker", "network", "inspect", name, "--format", "{{json .Labels}}"],
                ).stdout,
            )
            if labels.get("com.unstaticlabs.runtime.target") != target.name:
                raise RuntimeError(f"cleanup candidate became foreign: {name}")
            runner.run(["docker", "network", "rm", name])
        plan["status"] = "applied"
    else:
        plan["status"] = "planned"
    print(json.dumps(plan, indent=None if arguments.json else 2, sort_keys=True))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--targets", type=Path, default=DEFAULT_TARGETS)
    commands = parser.add_subparsers(dest="command", required=True)
    runtime = commands.add_parser("runtime")
    runtime.add_argument("action", choices=("status", "start", "stop"))
    runtime.add_argument("--target", required=True)
    runtime.add_argument("--json", action="store_true")
    runtime.set_defaults(handler=runtime_command)
    backup = commands.add_parser("backup")
    backup.add_argument("action", choices=("create", "list", "verify"))
    backup.add_argument("--target", required=True)
    backup.add_argument("--release", type=Path)
    backup.add_argument("--run-id")
    backup.add_argument("--resume")
    backup.add_argument("--snapshot")
    backup.add_argument("--json", action="store_true")
    backup.set_defaults(handler=backup_command)
    health = commands.add_parser("health")
    health.add_argument("--target", required=True)
    health.add_argument("--json", action="store_true")
    health.set_defaults(handler=health_command)
    smoke = commands.add_parser("smoke")
    smoke.add_argument("--target", required=True)
    smoke.add_argument("--json", action="store_true")
    smoke.set_defaults(handler=smoke_command)
    restore = commands.add_parser("restore")
    restore.add_argument("action", choices=("run",))
    restore.add_argument("--source", required=True)
    restore.add_argument("--target", required=True)
    restore.add_argument("--snapshot", required=True)
    restore.add_argument("--release", type=Path)
    restore.add_argument("--generation")
    restore.add_argument("--replace", action="store_true")
    restore.add_argument("--confirm")
    restore.add_argument("--json", action="store_true")
    restore.set_defaults(handler=restore_command)
    cleanup = commands.add_parser("cleanup")
    cleanup.add_argument("action", choices=("plan", "apply"))
    cleanup.add_argument("--target", required=True)
    cleanup.add_argument("--confirm")
    cleanup.add_argument("--json", action="store_true")
    cleanup.set_defaults(handler=cleanup_command)
    return parser


def main() -> int:
    arguments = build_parser().parse_args()
    try:
        return arguments.handler(arguments)
    except RuntimeError as error:
        print(f"usl-stack: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
