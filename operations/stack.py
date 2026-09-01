"""Single operator interface for USL runtime lifecycle and recovery."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

from operations.release_manifest import validate as validate_release
from operations.runtime import (
    RuntimeError,
    compose_command,
    compose_identity,
    inspect_runtime,
    load_target,
    validate_secret_text,
)


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_TARGETS = ROOT / "operations/targets"


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


def _release(target, runner, override: Path | None) -> tuple[dict, str]:
    path = override or Path(target.value["release_manifest"])
    raw = _read_path(target, runner, path)
    try:
        value = validate_release(json.loads(raw))
    except (ValueError, json.JSONDecodeError) as error:
        raise RuntimeError(f"release manifest is invalid: {path}") from error
    return value, hashlib.sha256(raw.encode()).hexdigest()


def _secret_file(target, runner) -> str:
    path = target.value["secrets"]["env_file"]
    text = runner.run(["cat", path]).stdout
    validate_secret_text(text, target.value["secrets"]["allowed_keys"])
    return path


def _cohort_command(target, image: str, action: str, arguments: list[str]) -> list[str]:
    volumes = target.value["volumes"]
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


def _run_cohort(target, runner, image: str, action: str, arguments: list[str], environment: list[str] | None = None):
    command = _cohort_command(target, image, action, arguments)
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
    runner.run(compose_command(identity, ["stop", "--timeout", "60", *services]))
    try:
        return callback()
    finally:
        runner.run(
            compose_command(
                identity,
                ["up", "--detach", "--wait", "--no-recreate", *services],
            ),
        )


def backup_command(arguments: argparse.Namespace) -> int:
    target = load_target(arguments.target, arguments.targets)
    runner = target.runner()
    inspect_runtime(target, runner)
    release, release_sha = _release(target, runner, arguments.release)
    _secret_file(target, runner)
    image = release["components"]["backup-tool"]["digest_reference"]
    if arguments.action == "list":
        result = _run_cohort(target, runner, image, "list", [])
    elif arguments.action == "verify":
        result = _run_cohort(
            target,
            runner,
            image,
            "verify",
            ["--durable-snapshot", arguments.snapshot],
        )
    else:
        run_id = arguments.run_id or (
            f"{datetime.now(UTC):%Y%m%dt%H%M%Sz}-{release['source']['commit'][:8]}"
        )
        runner.run(["install", "-d", "-m", "0700", target.value["state_directory"]])
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
            )
        captured = with_writers_paused(runner, identity, writer_services, capture_phase)
        uploaded = _run_cohort(target, runner, image, "push", ["--run-id", run_id])
        result = {"capture": captured, "upload": uploaded}
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
  'attachments', (SELECT count(*) FROM ir_attachment),
  'ledger_delta', (SELECT coalesce(sum(debit-credit), 0) FROM account_move_line),
  'queued_mail', (SELECT count(*) FROM mail_mail WHERE state IN ('outgoing','exception')),
  'pending_documents', (SELECT count(*) FROM usl_document_operation WHERE state IN ('pending','processing','failed'))
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
    except (RuntimeError, json.JSONDecodeError) as error:
        raise RuntimeError(f"read-only database smoke failed: {error}") from error
    failures = []
    if float(odoo["ledger_delta"]) != 0:
        failures.append("accounting:unbalanced")
    if min(odoo["companies"], odoo["users"], odoo["moves"], odoo["attachments"]) < 1:
        failures.append("odoo:empty-control")
    if odoo["queued_mail"] or odoo["pending_documents"]:
        failures.append("odoo:pending-queue")
    if paperless["documents"] < 1 or paperless["with_ocr"] < 1:
        failures.append("paperless:empty-control")
    if paperless["missing_original_name"]:
        failures.append("paperless:missing-filename")
    result = {
        "schema": "usl-runtime-smoke/v1",
        "target": target.name,
        "status": "passed" if not failures else "failed",
        "failures": failures,
        "controls": {"odoo": odoo, "paperless": paperless},
    }
    print(json.dumps(result, indent=None if arguments.json else 2, sort_keys=True))
    return 0 if not failures else 2


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
