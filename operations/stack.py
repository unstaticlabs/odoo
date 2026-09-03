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

from operations.control_manifest import (
    ODOO_CONTROL_SQL,
    PAPERLESS_CONTROL_SQL,
    ControlManifestError,
    validate_restore,
)
from operations.release_controller import (
    ReleaseControllerError,
    abort as abort_release_state,
    parse as parse_release_state,
)
from operations.release_manifest import validate as validate_release
from operations.module_release import (
    ModuleReleaseError,
    derive_legacy_upgrade_plan,
    derive_upgrade_plan,
    validate_upgrade_plan,
)
from operations.plan_evidence import PlanEvidenceError, sign as sign_upgrade_plan, verify as verify_upgrade_plan
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
RELEASE_RUNTIME_SERVICES = {
    "distribution": "odoo",
    "paperless": "paperless",
    "sign-dss": "sign",
    "mcp": "mcp",
    "renderer": "renderer",
}
MINIMUM_FREE_BYTES = 2 * 1024**3
CAPACITY_WARNING_BYTES = 8 * 1024**3
RESTORE_SAFETY_RESERVE_BYTES = 15 * 1024**3
RESOURCE_FIELDS = {
    "cpus",
    "cpu_shares",
    "mem_limit",
    "mem_reservation",
    "memswap_limit",
    "mem_swappiness",
    "oom_score_adj",
    "pids_limit",
}
BACKUP_WRITER_SERVICE_ROLES = ("odoo", "paperless", "mcp", "sign", "sign_ca")


def _report(operation: str, phase: str, status: str, detail: str = "") -> None:
    """Write concise operator progress without contaminating JSON stdout."""
    suffix = f": {detail}" if detail else ""
    print(f"usl-stack [{operation}] {phase}: {status}{suffix}", file=sys.stderr, flush=True)


def _capacity_detail(available: int) -> str:
    gib = available / 1024**3
    if available < MINIMUM_FREE_BYTES:
        return f"{gib:.1f} GiB free; below the 2 GiB safety floor"
    if available < CAPACITY_WARNING_BYTES:
        return f"CRITICAL CAPACITY WARNING: {gib:.1f} GiB free; cleanup is required after this operation"
    return f"{gib:.1f} GiB free"


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
        f"{paths['sign_secrets']['path']}:/source/sign-secrets:ro",
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


def with_writers_paused(
    runner,
    identity: dict,
    services: list[str],
    callback,
    *,
    resume_after_success: bool = True,
):
    runner.run(compose_command(identity, ["stop", "--timeout", "30", *services]))
    succeeded = False
    try:
        result = callback()
        succeeded = True
        return result
    finally:
        if resume_after_success or not succeeded:
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


def _release_images(release: dict) -> list[str]:
    """Return every immutable image needed before a restore can start."""
    return sorted(
        {
            release["components"]["backup-tool"]["digest_reference"],
            release["components"]["distribution"]["digest_reference"],
            release["components"]["paperless"]["digest_reference"],
            release["components"]["sign-dss"]["digest_reference"],
            release["mcp"]["image"],
            release["renderer"]["image"],
        },
    )


def _release_image(release: dict, component: str) -> str:
    if component == "mcp":
        return release["mcp"]["image"]
    if component == "renderer":
        return release["renderer"]["image"]
    return release["components"][component]["digest_reference"]


def _validate_runtime_release_images(target, runner, runtime: dict, release: dict) -> dict[str, str]:
    containers = {
        item.get("Service"): item.get("ID")
        for item in runtime["containers"]
        if item.get("State") == "running"
    }
    verified = {}
    for component, service_key in RELEASE_RUNTIME_SERVICES.items():
        service = target.value["services"][service_key]
        container = containers.get(service)
        if not container:
            raise RuntimeError(f"release service is not running: {service}")
        expected = _release_image(release, component)
        actual_id = runner.run(
            ["docker", "inspect", container, "--format", "{{.Image}}"],
        ).stdout.strip()
        expected_probe = runner.run(
            ["docker", "image", "inspect", expected, "--format", "{{.Id}}"],
            check=False,
        )
        if expected_probe.returncode:
            runner.run(["docker", "pull", expected])
            expected_probe = runner.run(
                ["docker", "image", "inspect", expected, "--format", "{{.Id}}"],
            )
        if actual_id != expected_probe.stdout.strip():
            raise RuntimeError(
                f"running {component} image differs from the selected release",
            )
        verified[component] = expected
    return verified


def _available_bytes(runner, path: str) -> int:
    result = runner.run(["df", "--output=avail", "--block-size=1", path])
    try:
        return int(result.stdout.splitlines()[-1].strip())
    except (IndexError, ValueError) as error:
        raise RuntimeError(f"disk capacity probe returned invalid output for {path}") from error


def _require_restore_capacity(
    target,
    runner,
    phase: str,
    *,
    candidate_bytes: int | None = None,
) -> dict:
    available = _available_bytes(runner, target.value["state_directory"])
    if available < MINIMUM_FREE_BYTES:
        raise RuntimeError(
            f"restore {phase} refused: {_capacity_detail(available)}",
        )
    result = {
        "available_bytes": available,
        "warning": available < CAPACITY_WARNING_BYTES,
    }
    if candidate_bytes is not None:
        required = candidate_bytes + RESTORE_SAFETY_RESERVE_BYTES
        if available < required:
            deficit = required - available
            raise RuntimeError(
                "restore capacity refused: "
                f"{available / 1024**3:.1f} GiB free, "
                f"{candidate_bytes / 1024**3:.1f} GiB measured candidate, "
                f"15.0 GiB reserve, {deficit / 1024**3:.1f} GiB deficit",
            )
        result.update(
            candidate_bytes=candidate_bytes,
            safety_reserve_bytes=RESTORE_SAFETY_RESERVE_BYTES,
            required_bytes=required,
        )
    _report("restore", phase, "capacity checked", _capacity_detail(available))
    return result


def _measure_candidate_bytes(target, runner, tool_image: str, runtime: dict) -> int:
    """Measure the additional persistent state a fresh generation must hold.

    Existing active and rollback generations are already reflected in free
    space, so they must not be added again. The estimate intentionally sums
    allocated file bytes rather than Docker volume metadata.
    """
    total = 0
    seen: set[str] = set()
    for item in runtime["volumes"].values():
        name = item["name"]
        if name in seen:
            continue
        seen.add(name)
        measured = runner.run([
            "docker", "run", "--rm", "--volume", f"{name}:/source:ro",
            "--entrypoint", "du", tool_image, "-sb", "/source",
        ]).stdout.split()
        try:
            total += int(measured[0])
        except (IndexError, ValueError) as error:
            raise RuntimeError(f"cannot measure persistent volume: {name}") from error
    for item in target.value["paths"].values():
        if not item.get("required") and item.get("class") != "durable":
            continue
        result = runner.run(["du", "-sb", item["path"]], check=False)
        if result.returncode:
            if item.get("required"):
                raise RuntimeError(f"cannot measure required persistent path: {item['path']}")
            continue
        try:
            total += int(result.stdout.split()[0])
        except (IndexError, ValueError) as error:
            raise RuntimeError(f"cannot measure persistent path: {item['path']}") from error
    return total


def _remove_materialization_workspace(target, runner, generation: str) -> None:
    root = target.value["state_directory"]
    if not generation.startswith("g") or len(generation) > 32:
        raise RuntimeError("refusing to remove an invalid generation workspace")
    runner.run(["rm", "-rf", f"{root}/generations/{generation}/work"])


def _prepare_generation_volume_ownership(runner, release: dict, volumes: dict[str, str]) -> None:
    runner.run(
        [
            "docker",
            "run",
            "--rm",
            "--user",
            "0:0",
            "--volume",
            f"{volumes['odoo_filestore']}:/var/lib/odoo",
            "--entrypoint",
            "chown",
            release["components"]["distribution"]["digest_reference"],
            "1000:1000",
            "/var/lib/odoo",
            "/var/lib/odoo/filestore",
        ],
    )


def _rollback_after_failure(runner, identity: dict, error: Exception) -> None:
    _report("restore", "rollback", "started", f"activation failed: {error}")
    rollback = runner.run(
        compose_command(identity, ["up", "--detach", "--wait"]),
        check=False,
    )
    if rollback.returncode:
        detail = (rollback.stderr or rollback.stdout).strip()
        raise RuntimeError(
            f"activation failed ({error}); rollback also failed ({detail})",
        ) from error
    _report("restore", "rollback", "completed", "previous generation is running")


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
    if phase != "operation" or status != "started":
        detail = ""
        if "duration_seconds" in details:
            detail = f"{details['duration_seconds']:.3f}s"
        _report(operation, phase, status, detail)


def backup_command(arguments: argparse.Namespace) -> int:
    target = load_target(arguments.target, arguments.targets)
    leave_quiesced = bool(getattr(arguments, "leave_quiesced", False))
    if leave_quiesced and arguments.action != "create":
        raise RuntimeError("--leave-quiesced is valid only for backup create")
    if leave_quiesced and arguments.resume:
        raise RuntimeError("a resumed backup cannot establish writer quiescence")
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
        runtime_images = _validate_runtime_release_images(target, runner, runtime, release)
        with runtime_lock(target, runner, "backup", run_id):
            started = time.monotonic()
            _record_event(target, runner, run_id, "backup", "operation", "started")
            captured = None
            freeze_seconds = 0.0
            if not arguments.resume:
                identity = compose_identity(target, runner)
                writer_services = [
                    target.value["services"][name]
                    for name in BACKUP_WRITER_SERVICE_ROLES
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
                    resume_after_success=not leave_quiesced,
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
                "runtime_images": runtime_images,
                "writers_quiesced": leave_quiesced,
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
        ok = code == 200 if name in {"odoo", "mcp"} else 200 <= code < 500
        endpoints[name] = {"url": url, "status_code": code, "ok": ok}
        if not ok:
            failures.append(f"{name}:http")
    ingress = target.value["ingress"]
    odoo_service = target.value["services"]["odoo"]
    config_probe = (
        "import configparser,json;"
        "c=configparser.ConfigParser();c.read('/etc/odoo/odoo.conf');"
        "o=c['options'];"
        "print(json.dumps({'proxy_mode':o.getboolean('proxy_mode'),"
        "'list_db':o.getboolean('list_db'),'dbfilter':o.get('dbfilter')}))"
    )
    config_result = runner.run(
        compose_command(
            status["compose"],
            ["exec", "--no-TTY", odoo_service, "python", "-c", config_probe],
        ),
        check=False,
    )
    odoo_config = None
    if config_result.returncode:
        failures.append("odoo:config-unreadable")
    else:
        try:
            odoo_config = json.loads(config_result.stdout.strip())
        except json.JSONDecodeError:
            failures.append("odoo:config-invalid")
        else:
            expected_config = {
                "proxy_mode": ingress["proxy_mode"],
                "list_db": ingress["list_db"],
                "dbfilter": ingress["dbfilter"],
            }
            if odoo_config != expected_config:
                failures.append("odoo:config-mismatch")
    websocket_status = {"required": ingress["websocket"], "status_code": None, "ok": True}
    if ingress["websocket"]:
        origin = target.value["endpoints"]["odoo"].rstrip("/")
        websocket_result = runner.run(
            [
                "curl",
                "--silent",
                "--show-error",
                "--include",
                "--no-buffer",
                "--http1.1",
                "--max-time",
                "3",
                "--header",
                "Connection: Upgrade",
                "--header",
                "Upgrade: websocket",
                "--header",
                f"Origin: {origin}",
                "--header",
                "Sec-WebSocket-Key: MDEyMzQ1Njc4OWFiY2RlZg==",
                "--header",
                "Sec-WebSocket-Version: 13",
                origin + "/websocket",
            ],
            check=False,
        )
        first_line = websocket_result.stdout.splitlines()[0] if websocket_result.stdout else ""
        websocket_ok = first_line.startswith("HTTP/") and " 101 " in first_line
        websocket_status = {"required": True, "status_code": 101 if websocket_ok else 0, "ok": websocket_ok}
        if not websocket_ok:
            failures.append("odoo:websocket")
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
        "odoo_config": odoo_config,
        "websocket": websocket_status,
        "ollama": ollama_status,
    }
    print(json.dumps(result, indent=None if arguments.json else 2, sort_keys=True))
    return 0 if not failures else 2


def smoke_command(arguments: argparse.Namespace) -> int:
    target = load_target(arguments.target, arguments.targets)
    runner = target.runner()
    status = inspect_runtime(target, runner)
    identity = status["compose"]
    odoo_query = ODOO_CONTROL_SQL
    paperless_query = PAPERLESS_CONTROL_SQL
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
    failed_queue_keys = (
        "failed_mail",
        "failed_documents",
        "bank_failed",
        "payment_failed",
        "sign_archive_failed",
    )
    if any(odoo[key] for key in failed_queue_keys):
        failures.append("odoo:failed-queue")
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
        f"{generation_root}/sign-secrets:/target/sign-secrets",
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


def _resource_overlay(target) -> str | None:
    relative = target.value["compose"]["resource_overlay"]
    if relative is None:
        return None
    path = (ROOT / relative).resolve()
    if ROOT not in path.parents:
        raise RuntimeError("resource overlay escapes the repository")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise RuntimeError(f"resource overlay is invalid: {relative}") from error
    if not isinstance(value, dict) or set(value) != {"services"}:
        raise RuntimeError("resource overlay must contain only services")
    services = value["services"]
    expected = set(target.value["services"].values())
    if not isinstance(services, dict) or set(services) != expected:
        raise RuntimeError("resource overlay service perimeter differs from the target")
    for name, limits in services.items():
        if not isinstance(limits, dict) or set(limits) != RESOURCE_FIELDS:
            raise RuntimeError(f"resource fields differ for service {name}")
        if not isinstance(limits["cpus"], (int, float)) or limits["cpus"] <= 0:
            raise RuntimeError(f"CPU limit is invalid for service {name}")
        if not isinstance(limits["cpu_shares"], int) or limits["cpu_shares"] < 2:
            raise RuntimeError(f"CPU shares are invalid for service {name}")
        if not isinstance(limits["pids_limit"], int) or limits["pids_limit"] <= 0:
            raise RuntimeError(f"PID limit is invalid for service {name}")
        if (
            not isinstance(limits["mem_swappiness"], int)
            or limits["mem_swappiness"] not in range(0, 101)
        ):
            raise RuntimeError(f"memory swappiness is invalid for service {name}")
        if (
            not isinstance(limits["oom_score_adj"], int)
            or not -1000 <= limits["oom_score_adj"] <= 1000
        ):
            raise RuntimeError(f"OOM score is invalid for service {name}")
        for field in ("mem_limit", "mem_reservation", "memswap_limit"):
            if not isinstance(limits[field], str) or not limits[field]:
                raise RuntimeError(f"{field} is invalid for service {name}")
    return json.dumps(value, indent=2, sort_keys=True) + "\n"


def _generation_overlay(
    volumes: dict[str, str],
    release: dict | None = None,
    available_services: set[str] | None = None,
    ingress: dict | None = None,
    sign_secret_root: str | None = None,
    service_names: dict[str, str] | None = None,
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
            service: {"image": images[component]}
            for component, services in RELEASE_IMAGE_SERVICES.items()
            for service in services
            if available_services is None or service in available_services
        }
        if ingress is not None and "odoo" in value["services"]:
            value["services"]["odoo"]["environment"] = {
                "ODOO_PROXY_MODE": "True" if ingress["proxy_mode"] else "False",
                "ODOO_LIST_DB": "True" if ingress["list_db"] else "False",
                "ODOO_DB_FILTER": ingress["dbfilter"],
            }
    if sign_secret_root is not None:
        if not service_names:
            raise RuntimeError("Sign secret activation requires service names")
        sign_mounts = {
            service_names["sign_ca"]: {
                "env_file": [{"path": f"{sign_secret_root}/step-ca.env", "required": True}],
                "volumes": [f"{sign_secret_root}/step-ca:/home/step"],
            },
            service_names["sign"]: {
                "env_file": [{"path": f"{sign_secret_root}/dss.env", "required": True}],
                "volumes": [f"{sign_secret_root}/dss:/run/usl-sign-dss:ro"],
            },
            service_names["odoo"]: {
                "env_file": [{"path": f"{sign_secret_root}/odoo.env", "required": True}],
                "volumes": [f"{sign_secret_root}/odoo:/run/usl-sign:ro"],
            },
            "init-db": {
                "env_file": [{"path": f"{sign_secret_root}/odoo.env", "required": True}],
                "volumes": [f"{sign_secret_root}/odoo:/run/usl-sign:ro"],
            },
        }
        services = value.setdefault("services", {})
        for service, settings in sign_mounts.items():
            services.setdefault(service, {}).update(settings)
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
    *,
    require_sign_secrets: bool = False,
) -> None:
    embedded = materialized.get("release", {})
    if not isinstance(embedded.get("manifest_sha256"), str) or len(embedded["manifest_sha256"]) != 64:
        raise RuntimeError("cohort has no verified original release identity")
    if not isinstance(embedded.get("commit"), str) or len(embedded["commit"]) != 40:
        raise RuntimeError("cohort has no verified original release commit")
    if release.get("schema") == "usl-release/v2" and embedded.get("manifest_sha256") != release_sha:
        raise RuntimeError("legacy restore release differs from the cohort release")
    if require_sign_secrets and not materialized.get("sign_secrets_restored"):
        raise RuntimeError("production recovery lacks complete Sign secret material")


def _run_candidate_upgrade(target, runner, release, network, volumes, plan) -> None:
    modules = validate_upgrade_plan(plan)["upgrade_modules"]
    if not modules:
        return
    if plan["candidate_release"] != release.get("identity"):
        raise RuntimeError("upgrade plan is not bound to the candidate release")
    database = target.value["databases"]["odoo"]
    runner.run(
        [
            "docker", "run", "--rm", "--network", network,
            "--env-file", target.value["secrets"]["env_file"],
            "--env", f"ODOO_DB_HOST={database['service']}",
            "--env", "ODOO_DB_PORT=5432",
            "--env", f"ODOO_DB_USER={database['user']}",
            "--env", f"ODOO_DB_NAME={database['name']}",
            "--env", "ODOO_MAX_CRON_THREADS=0",
            "--env", "USL_EINVOICE_LIVE_ENABLED=0",
            "--env", "USL_EREPORTING_LIVE_ENABLED=0",
            "--volume", f"{volumes['odoo_filestore']}:/var/lib/odoo",
            release["components"]["distribution"]["digest_reference"],
            "odoo", "--config=/etc/odoo/odoo.conf",
            f"--database={database['name']}",
            f"--update={','.join(modules)}",
            "--stop-after-init", "--no-http", "--max-cron-threads=0",
        ],
    )


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
    release_override = getattr(arguments, "target_release", None) or arguments.release
    release, release_sha, release_raw = _release(source, target_runner, release_override)
    upgrade_plan = None
    signed_plan_evidence = None
    if getattr(arguments, "upgrade_plan", None):
        try:
            plan_value = json.loads(_read_path(target, target_runner, arguments.upgrade_plan))
            if not isinstance(plan_value, dict):
                raise PlanEvidenceError("upgrade plan must be a JSON object")
            if target.value["environment"] == "production":
                upgrade_plan = verify_upgrade_plan(
                    plan_value,
                    Path(target.value["plan_signing"]["public_key"]),
                )
                signed_plan_evidence = plan_value
            elif plan_value.get("schema") == "usl-staging-upgrade-plan-evidence/v1":
                upgrade_plan = verify_upgrade_plan(
                    plan_value,
                    Path(target.value["plan_signing"]["public_key"]),
                )
            else:
                upgrade_plan = validate_upgrade_plan(plan_value)
        except (json.JSONDecodeError, ModuleReleaseError, PlanEvidenceError) as error:
            raise RuntimeError("upgrade plan is invalid") from error
    tool_image = release["components"]["backup-tool"]["digest_reference"]
    generation = arguments.generation or f"g{datetime.now(UTC):%Y%m%dt%H%M}-{arguments.snapshot[:8]}"
    if len(generation) > 32 or not generation.startswith("g"):
        raise RuntimeError("generation name is invalid")
    identity = current["compose"]
    images = _runtime_images(target_runner, identity)
    phase_started = time.monotonic()
    _record_event(target, target_runner, generation, "restore", "image-preparation", "started")
    capacity_before_pull = _require_restore_capacity(target, target_runner, "preflight")
    for image in _release_images(release):
        _ensure_image(target_runner, image)
    candidate_bytes = _measure_candidate_bytes(target, target_runner, tool_image, current)
    capacity_after_pull = _require_restore_capacity(
        target,
        target_runner,
        "image pre-pull",
        candidate_bytes=candidate_bytes,
    )
    _record_event(
        target,
        target_runner,
        generation,
        "restore",
        "image-preparation",
        "completed",
        duration_seconds=round(time.monotonic() - phase_started, 3),
        **capacity_after_pull,
    )
    phase_started = time.monotonic()
    _record_event(target, target_runner, generation, "restore", "materialization", "started")
    generation_root = f"{target.value['state_directory']}/generations/{generation}"
    target_runner.run(["install", "-d", "-m", "0700", generation_root])
    volumes, network = _create_generation_resources(target, target_runner, generation)
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
        _validate_materialized_release(
            materialize_state,
            release,
            release_sha,
            require_sign_secrets=target.value["environment"] == "production",
        )
        snapshot_release = materialize_state["release"]
        candidate_differs = snapshot_release["manifest_sha256"] != release_sha
        if candidate_differs and upgrade_plan is None:
            raise RuntimeError("cross-release restore requires the staging-qualified upgrade plan")
        if upgrade_plan is not None:
            snapshot_identity = snapshot_release.get("identity", snapshot_release["manifest_sha256"])
            if upgrade_plan["active_release"] != snapshot_identity:
                raise RuntimeError("upgrade plan is not bound to the snapshot release")
            _run_candidate_upgrade(target, target_runner, release, network, volumes, upgrade_plan)
        _neutralize_generation(target, target_runner, release, generation, network, volumes)
        _prepare_generation_volume_ownership(target_runner, release, volumes)
    finally:
        for container in database_containers:
            target_runner.run(["docker", "rm", "--force", container], check=False)
    _remove_materialization_workspace(target, target_runner, generation)
    capacity_before_activation = _require_restore_capacity(target, target_runner, "activation")
    _record_event(
        target,
        target_runner,
        generation,
        "restore",
        "materialization",
        "completed",
        duration_seconds=round(time.monotonic() - phase_started, 3),
        **capacity_before_activation,
    )
    release_path = f"{generation_root}/usl-release.json"
    _write_remote(target, target_runner, release_path, release_raw + "\n")
    resource_overlay = _resource_overlay(target)
    resource_path = None
    if resource_overlay is not None:
        resource_path = f"{generation_root}/compose.resources.json"
        _write_remote(target, target_runner, resource_path, resource_overlay, "0644")
    overlay = f"{generation_root}/compose.generation.json"
    _write_remote(
        target,
        target_runner,
        overlay,
        _generation_overlay(
            volumes,
            release,
            set(images),
            target.value["ingress"],
            sign_secret_root=(
                f"{generation_root}/sign-secrets"
                if target.value["environment"] == "production"
                else None
            ),
            service_names=target.value["services"],
        ),
    )
    generated_prefix = target.value["state_directory"] + "/generations/"
    compose_files = [
        path
        for path in identity["compose_files"]
        if not (
            path.startswith(generated_prefix)
            and path.endswith(("/compose.generation.json", "/compose.resources.json"))
        )
    ]
    if resource_path is not None:
        compose_files.append(resource_path)
    compose_files.append(overlay)
    generation_identity = {**identity, "compose_files": compose_files}
    previous = {
        "generation": current["generation"],
        "volumes": {role: item["name"] for role, item in current["volumes"].items()},
        "network": (current["active_state"] or {}).get("network"),
        "release_manifest": (current["active_state"] or {}).get("release_manifest"),
        "snapshot": (current["active_state"] or {}).get("snapshot"),
    }
    phase_started = time.monotonic()
    _record_event(target, target_runner, generation, "restore", "activation", "started")
    try:
        # A stable ingress gateway is intentionally outside this service
        # perimeter. It must keep serving the maintenance response while the
        # stateful cohort is replaced.
        cohort_services = sorted(set(target.value["services"].values()))
        target_runner.run(
            compose_command(identity, ["stop", "--timeout", "60", *cohort_services]),
        )
        target_runner.run(
            compose_command(generation_identity, ["up", "--detach", "--wait"]),
        )
    except Exception as error:
        _rollback_after_failure(target_runner, identity, error)
        raise
    _record_event(
        target,
        target_runner,
        generation,
        "restore",
        "activation",
        "completed",
        duration_seconds=round(time.monotonic() - phase_started, 3),
    )
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
    phase_started = time.monotonic()
    _record_event(target, target_runner, generation, "restore", "validation", "started")
    try:
        health = _gate(health_command, target, arguments.targets)
        smoke = _gate(smoke_command, target, arguments.targets)
        expected_release_sha256 = None
        if signed_plan_evidence is not None:
            expected_release_sha256 = signed_plan_evidence["staging"][
                "release_controls_sha256"
            ]
        try:
            control_validation = validate_restore(
                materialize_state["controls"],
                smoke["controls"],
                expected_release_sha256=expected_release_sha256,
                require_unchanged_release=not candidate_differs,
            )
        except ControlManifestError as error:
            raise RuntimeError(str(error)) from error
    except Exception as error:
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
        _rollback_after_failure(target_runner, identity, error)
        raise
    _record_event(
        target,
        target_runner,
        generation,
        "restore",
        "validation",
        "completed",
        duration_seconds=round(time.monotonic() - phase_started, 3),
    )
    result = {
        "schema": "usl-restore-run/v1",
        "source": source.name,
        "target": target.name,
        "snapshot": arguments.snapshot,
        "generation": generation,
        "materialize": materialize_state,
        "health": health,
        "smoke": smoke,
        "control_validation": control_validation,
        "capacity": {
            "before_pull": capacity_before_pull,
            "after_pull": capacity_after_pull,
            "before_activation": capacity_before_activation,
        },
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
            _report(
                "restore",
                "failure summary",
                "failed",
                f"{type(error).__name__}: {error}",
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


def release_command(arguments: argparse.Namespace) -> int:
    target = load_target(arguments.target, arguments.targets)
    runner = target.runner()
    state_path = f"{target.value['state_directory']}/release-state.json"
    if arguments.action == "status":
        state = runner.run(["cat", state_path], check=False)
        if state.returncode == 0:
            try:
                value = parse_release_state(state.stdout)
            except ReleaseControllerError as error:
                raise RuntimeError(str(error)) from error
        else:
            value = {
                "schema": "usl-release-run/v1",
                "target": target.name,
                "status": "idle",
            }
        print(json.dumps(value, indent=None if arguments.json else 2, sort_keys=True))
        return 0
    if arguments.action == "abort":
        state = runner.run(["cat", state_path], check=False)
        if state.returncode:
            raise RuntimeError("there is no release run to abort")
        try:
            value = abort_release_state(parse_release_state(state.stdout))
        except ReleaseControllerError as error:
            raise RuntimeError(str(error)) from error
        _write_remote(target, runner, state_path, json.dumps(value, indent=2, sort_keys=True) + "\n")
        print(json.dumps(value, indent=None if arguments.json else 2, sort_keys=True))
        return 0
    if arguments.action == "plan":
        if arguments.attest:
            if target.value["environment"] != "staging":
                raise RuntimeError("only staging may attest an upgrade plan")
            if not arguments.upgrade_plan or not arguments.snapshot or not arguments.candidate_release:
                raise RuntimeError("plan attestation requires upgrade plan, snapshot, and candidate release")
            try:
                plan = validate_upgrade_plan(json.loads(_read_path(target, runner, arguments.upgrade_plan)))
            except (json.JSONDecodeError, ModuleReleaseError) as error:
                raise RuntimeError("unsigned staging upgrade plan is invalid") from error
            candidate_raw = _read_path(target, runner, arguments.candidate_release)
            try:
                candidate = validate_release(json.loads(candidate_raw))
            except (ValueError, json.JSONDecodeError) as error:
                raise RuntimeError("candidate release manifest is invalid") from error
            if candidate.get("identity") != plan["candidate_release"]:
                raise RuntimeError("staging plan targets another candidate release")
            runtime = inspect_runtime(target, runner)
            generation = runtime.get("generation")
            if not isinstance(generation, str) or not generation.startswith("g"):
                raise RuntimeError("staging has no admitted generation to attest")
            health = _gate(health_command, target, arguments.targets)
            smoke = _gate(smoke_command, target, arguments.targets)
            evidence = sign_upgrade_plan(
                plan,
                Path(target.value["plan_signing"]["private_key"]),
                snapshot=arguments.snapshot,
                generation=generation,
                health=health,
                smoke=smoke,
            )
            output = arguments.output or arguments.upgrade_plan
            _write_remote(target, runner, str(output), json.dumps(evidence, indent=2, sort_keys=True) + "\n", "0644")
            print(json.dumps({"schema": evidence["schema"], "path": str(output), "plan_sha256": plan["sha256"], "generation": generation, "status": "signed"}, indent=None if arguments.json else 2, sort_keys=True))
            return 0
        current, _current_sha, _current_raw = _release(target, runner, arguments.active_release)
        candidate_raw = _read_path(target, runner, arguments.candidate_release)
        try:
            candidate = validate_release(json.loads(candidate_raw))
        except (ValueError, json.JSONDecodeError) as error:
            raise RuntimeError("candidate release manifest is invalid") from error
        if candidate.get("schema") != "usl-release/v3":
            raise RuntimeError("upgrade planning requires a v3 candidate release")
        runtime = inspect_runtime(target, runner)
        installed_raw = _psql(
            target,
            runner,
            runtime["compose"],
            "odoo",
            "SELECT coalesce(json_agg(name ORDER BY name), '[]'::json) FROM ir_module_module WHERE state = 'installed' AND name LIKE 'usl_%' OR state = 'installed' AND name = 'rebuild_account_migration';",
        )
        installed = set(json.loads(installed_raw))
        try:
            if current.get("schema") == "usl-release/v2":
                plan = derive_legacy_upgrade_plan(
                    candidate,
                    installed,
                    active_identity=hashlib.sha256(_current_raw.encode()).hexdigest(),
                )
            elif current.get("schema") == "usl-release/v3":
                plan = derive_upgrade_plan(current, candidate, installed)
            else:
                raise RuntimeError("active release schema cannot be upgraded")
        except ModuleReleaseError as error:
            raise RuntimeError(str(error)) from error
        output = arguments.output or Path(
            f"{target.value['state_directory']}/plans/{plan['sha256']}.json",
        )
        _write_remote(target, runner, str(output), json.dumps(plan, indent=2, sort_keys=True) + "\n", "0644")
        print(json.dumps({**plan, "path": str(output)}, indent=None if arguments.json else 2, sort_keys=True))
        return 0
    if arguments.action == "reconcile":
        if not arguments.snapshot or not arguments.candidate_release or not arguments.upgrade_plan:
            raise RuntimeError("release reconcile requires snapshot, candidate release, and upgrade plan")
        restore_arguments = argparse.Namespace(
            targets=arguments.targets,
            source=arguments.source,
            target=arguments.target,
            snapshot=arguments.snapshot,
            release=None,
            target_release=arguments.candidate_release,
            upgrade_plan=arguments.upgrade_plan,
            generation=arguments.generation,
            replace=arguments.replace,
            confirm=arguments.confirm,
            json=arguments.json,
        )
        return restore_command(restore_arguments)
    raise RuntimeError(f"unsupported release action: {arguments.action}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--targets", type=Path, default=DEFAULT_TARGETS)
    parser.add_argument("--target")
    commands = parser.add_subparsers(dest="command", required=True)
    runtime = commands.add_parser("runtime")
    runtime.add_argument("action", choices=("status", "start", "stop"))
    runtime.add_argument("--target", dest="command_target")
    runtime.add_argument("--json", action="store_true")
    runtime.set_defaults(handler=runtime_command)
    backup = commands.add_parser("backup")
    backup.add_argument("action", choices=("create", "list", "verify"))
    backup.add_argument("--target", dest="command_target")
    backup.add_argument("--release", type=Path)
    backup.add_argument("--run-id")
    backup.add_argument("--resume")
    backup.add_argument(
        "--leave-quiesced",
        action="store_true",
        help="leave cohort writers stopped after a successful capture",
    )
    backup.add_argument("--snapshot")
    backup.add_argument("--json", action="store_true")
    backup.set_defaults(handler=backup_command)
    health = commands.add_parser("health")
    health.add_argument("--target", dest="command_target")
    health.add_argument("--json", action="store_true")
    health.set_defaults(handler=health_command)
    smoke = commands.add_parser("smoke")
    smoke.add_argument("--target", dest="command_target")
    smoke.add_argument("--json", action="store_true")
    smoke.set_defaults(handler=smoke_command)
    restore = commands.add_parser("restore")
    restore.add_argument("action", choices=("run",))
    restore.add_argument("--source", required=True)
    restore.add_argument("--target", dest="command_target")
    restore.add_argument("--snapshot", required=True)
    restore.add_argument("--release", type=Path)
    restore.add_argument("--target-release", type=Path)
    restore.add_argument("--upgrade-plan", type=Path)
    restore.add_argument("--generation")
    restore.add_argument("--replace", action="store_true")
    restore.add_argument("--confirm")
    restore.add_argument("--json", action="store_true")
    restore.set_defaults(handler=restore_command)
    cleanup = commands.add_parser("cleanup")
    cleanup.add_argument("action", choices=("plan", "apply"))
    cleanup.add_argument("--target", dest="command_target")
    cleanup.add_argument("--confirm")
    cleanup.add_argument("--json", action="store_true")
    cleanup.set_defaults(handler=cleanup_command)
    release = commands.add_parser("release")
    release.add_argument("action", choices=("plan", "reconcile", "status", "abort"))
    release.add_argument("--target", dest="command_target")
    release.add_argument("--source", default="production")
    release.add_argument("--active-release", type=Path)
    release.add_argument("--candidate-release", type=Path)
    release.add_argument("--upgrade-plan", type=Path)
    release.add_argument("--attest", action="store_true")
    release.add_argument("--snapshot")
    release.add_argument("--generation")
    release.add_argument("--output", type=Path)
    release.add_argument("--replace", action="store_true")
    release.add_argument("--confirm")
    release.add_argument("--json", action="store_true")
    release.set_defaults(handler=release_command)
    return parser


def main() -> int:
    arguments = build_parser().parse_args()
    arguments.target = getattr(arguments, "command_target", None) or arguments.target
    if not arguments.target:
        print("usl-stack: --target is required", file=sys.stderr)
        return 2
    try:
        return arguments.handler(arguments)
    except RuntimeError as error:
        print(f"usl-stack: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
