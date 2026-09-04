"""Single operator interface for USL runtime lifecycle and recovery."""

from __future__ import annotations

import argparse
import base64
import hashlib
import io
import json
import os
import re
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
from operations.cohort import (
    SCHEMA as RECOVERY_COHORT_SCHEMA,
    select_latest_recovery_snapshot,
)
from operations.cron_policy import (
    INVENTORY_SQL as CRON_INVENTORY_SQL,
    CronPolicyError,
    parse as parse_cron_policy,
    render_odoo_apply_script,
    validate_runtime as validate_cron_runtime,
)
from operations.release_controller import (
    ReleaseControllerError,
    abort as abort_release_state,
    parse as parse_release_state,
)
from operations.release_manifest import ReleaseManifestError, validate as validate_release
from operations.module_release import (
    ModuleReleaseError,
    derive_legacy_upgrade_plan,
    derive_upgrade_plan,
    validate_upgrade_plan,
)
from operations.plan_evidence import (
    PROMOTION_SCHEMA,
    PlanEvidenceError,
    promote as promote_upgrade_plan,
    sign as sign_upgrade_plan,
    verify as verify_upgrade_plan,
    verify_promotion as verify_upgrade_plan_promotion,
)
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
VOLUME_RUNTIME_SERVICE_KEYS = {
    "odoo_postgres": "odoo_db",
    "odoo_filestore": "odoo",
    "paperless_postgres": "paperless_db",
    "paperless_broker": "paperless_broker",
    "paperless_media": "paperless",
    "paperless_data": "paperless",
    "paperless_trash": "paperless",
    "paperless_consume": "paperless",
    "paperless_export": "paperless",
    "mcp_oauth": "mcp",
}
RELEASE_IMAGE_SERVICES = {
    "distribution": ("odoo", "odoo-upgrade"),
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
GENERATION_NAME = re.compile(r"g[a-z0-9][a-z0-9-]{0,30}\Z")
RELEASE_ATTEMPT = re.compile(r"[a-z0-9][a-z0-9._-]{7,63}\Z")


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
        current = inspect_runtime(target, runner)
        identity = current["compose"]
        if arguments.action == "start":
            identity = _active_generation_identity(target, runner, current)
            runner.run(compose_command(identity, ["up", "--detach", "--wait"]))
        else:
            runner.run(compose_command(identity, ["stop"]))
        result = inspect_runtime(target, runner)
    print(json.dumps(result, indent=None if arguments.json else 2, sort_keys=True))
    return 0


def _volume_inspect(runner, name: str) -> dict:
    raw = runner.run(
        ["docker", "volume", "inspect", name, "--format", "{{json .}}"],
    ).stdout
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as error:
        raise RuntimeError(f"volume inspection is invalid: {name}") from error
    if not isinstance(value, dict):
        raise RuntimeError(f"volume inspection is invalid: {name}")
    return value


def _volume_source_path(runner, name: str) -> str:
    value = _volume_inspect(runner, name)
    options = value.get("Options") or {}
    path = options.get("device") if options.get("type") == "none" and options.get("o") == "bind" else value.get("Mountpoint")
    if not isinstance(path, str) or not path.startswith("/"):
        raise RuntimeError(f"volume source path is invalid: {name}")
    return path


def _storage_inventory(target, runner, runtime: dict) -> tuple[list[dict], dict[str, int]]:
    inventory = []
    totals = {tier: 0 for tier in target.value["storage"]["tiers"]}
    for role, definition in sorted(runtime["volumes"].items()):
        source = _volume_source_path(runner, definition["name"])
        measured = runner.run(["du", "-sb", "--", source]).stdout.split()
        try:
            size = int(measured[0])
        except (IndexError, ValueError) as error:
            raise RuntimeError(f"cannot measure persistent volume: {definition['name']}") from error
        totals[definition["tier"]] += size
        inventory.append(
            {
                "role": role,
                "name": definition["name"],
                "tier": definition["tier"],
                "source": source,
                "bytes": size,
            },
        )
    return inventory, totals


def _storage_status(target, runner, runtime: dict) -> dict:
    failures = []
    tiers = {}
    for tier, definition in sorted(target.value["storage"]["tiers"].items()):
        probe = runner.run(
            ["findmnt", "--target", definition["path"], "--noheadings", "--output", "SOURCE,FSTYPE,UUID,TARGET"],
            check=False,
        )
        fields = probe.stdout.split()
        if probe.returncode or len(fields) < 4:
            failures.append(f"storage tier is not mounted: {tier} ({definition['path']})")
            tiers[tier] = {"path": definition["path"], "status": "missing"}
            continue
        tiers[tier] = {
            "path": definition["path"],
            "source": fields[0],
            "fstype": fields[1],
            "uuid": fields[2],
            "mountpoint": fields[3],
            "status": "mounted",
        }
    if tiers.get("bulk", {}).get("source") == tiers.get("database", {}).get("source"):
        failures.append("bulk and database tiers resolve to the same filesystem")
    generation = runtime["generation"]
    running = {
        item.get("Service"): item
        for item in runtime.get("containers", [])
        if item.get("State") == "running" and item.get("Service") and item.get("Name")
    }
    volumes = {}
    for role, definition in sorted(runtime["volumes"].items()):
        value = _volume_inspect(runner, definition["name"])
        options = value.get("Options") or {}
        actual = _volume_source_path(runner, definition["name"])
        status = "valid"
        if definition["tier"] == "database":
            if generation == "adopted":
                status = "legacy"
                failures.append(f"database volume is not generation-backed: {role}")
            else:
                expected = generation_volume_path(target, generation, role)
                if options != {"device": expected, "o": "bind", "type": "none"}:
                    status = "wrong-device"
                    failures.append(f"database volume is not bound to its generation path: {role}")
        service_key = VOLUME_RUNTIME_SERVICE_KEYS[role]
        service = target.value["services"][service_key]
        container = running.get(service)
        runtime_status = "not-running"
        if container is not None:
            inspected = runner.run(
                ["docker", "inspect", container["Name"], "--format", "{{json .Mounts}}"],
                check=False,
            )
            try:
                mounts = json.loads(inspected.stdout) if inspected.returncode == 0 else None
            except json.JSONDecodeError:
                mounts = None
            mounted_names = {
                item.get("Name")
                for item in mounts or []
                if isinstance(item, dict) and item.get("Type") == "volume"
            }
            if not isinstance(mounts, list):
                runtime_status = "inspect-failed"
                failures.append(f"running service mount inspection failed: {service}")
            elif definition["name"] not in mounted_names:
                runtime_status = "wrong-runtime-volume"
                failures.append(
                    f"running service does not mount the active volume: {service}/{role}",
                )
            else:
                runtime_status = "valid"
        volumes[role] = {
            "name": definition["name"],
            "tier": definition["tier"],
            "source": actual,
            "status": status,
            "runtime_service": service,
            "runtime_status": runtime_status,
        }
    docker_root = runner.run(["docker", "info", "--format", "{{.DockerRootDir}}"], check=False).stdout.strip()
    containerd_root = ""
    if target.value["environment"] != "local":
        containerd_config = runner.run(["containerd", "config", "dump"], check=False)
        match = re.search(r"(?m)^root\s*=\s*['\"]([^'\"]+)['\"]\s*$", containerd_config.stdout)
        containerd_root = match.group(1) if match else ""
        if containerd_config.returncode or containerd_root != "/srv/storage/containerd":
            failures.append(f"containerd root differs: {containerd_root or 'unavailable'}")
    if target.value["environment"] != "local" and docker_root != "/srv/storage/docker":
        failures.append(f"Docker root differs: {docker_root or 'unavailable'}")
    return {
        "schema": "usl-storage-status/v1",
        "target": target.name,
        "generation": generation,
        "docker_root": docker_root,
        "containerd_root": containerd_root,
        "tiers": tiers,
        "volumes": volumes,
        "failures": failures,
        "status": "passed" if not failures else "failed",
    }


def _write_adopt_generation(
    target,
    runner,
    identity: dict,
    generation: str,
    volumes: dict[str, str],
    network: str,
    snapshot: str,
    source_release_manifest: str,
) -> str:
    generation_root = f"{target.value['state_directory']}/generations/{generation}"
    runner.run(["install", "-d", "-m", "0700", "--", generation_root])
    release_raw = runner.run(["cat", source_release_manifest]).stdout
    try:
        release = json.loads(release_raw)
    except json.JSONDecodeError as error:
        raise RuntimeError("adopted release manifest is invalid JSON") from error
    release_path = f"{generation_root}/usl-release.json"
    _write_remote(target, runner, release_path, json.dumps(release, indent=2, sort_keys=True) + "\n")
    resource_path = None
    resource = _resource_overlay(target)
    if resource is not None:
        resource_path = f"{generation_root}/compose.resources.json"
        _write_remote(target, runner, resource_path, resource, "0644")
    services = set(_runtime_images(runner, identity))
    overlay_path = f"{generation_root}/compose.generation.json"
    _write_remote(
        target,
        runner,
        overlay_path,
        _generation_overlay(
            volumes,
            release,
            services,
            target.value["ingress"],
            service_names=target.value["services"],
        ),
        "0644",
    )
    return release_path


def storage_command(arguments: argparse.Namespace) -> int:
    target = load_target(arguments.target, arguments.targets)
    runner = target.runner()
    runtime = inspect_runtime(target, runner)
    if arguments.action == "status":
        result = _storage_status(target, runner, runtime)
        print(json.dumps(result, indent=None if arguments.json else 2, sort_keys=True))
        return 0 if result["status"] == "passed" else 2
    inventory, totals = _storage_inventory(target, runner, runtime)
    capacity = _require_restore_capacity(
        target,
        runner,
        "storage-adoption",
        candidate_bytes={tier: size * 2 for tier, size in totals.items()},
    )
    plan = {
        "schema": "usl-storage-adoption-plan/v1",
        "target": target.name,
        "source_generation": runtime["generation"],
        "active_generation": arguments.generation,
        "rollback_generation": arguments.rollback_generation,
        "snapshot": arguments.snapshot,
        "volumes": inventory,
        "candidate_bytes": totals,
        "materialization_bytes": {tier: size * 2 for tier, size in totals.items()},
        "capacity": capacity,
        "confirmation": (
            f"{target.name}:{arguments.generation}:{arguments.rollback_generation}:{arguments.snapshot}"
            if arguments.generation and arguments.rollback_generation and arguments.snapshot
            else None
        ),
        "status": "planned",
    }
    if arguments.action == "plan":
        print(json.dumps(plan, indent=None if arguments.json else 2, sort_keys=True))
        return 0
    if not all((arguments.generation, arguments.rollback_generation, arguments.snapshot)):
        raise RuntimeError("storage adopt requires both generations and a snapshot")
    if arguments.generation == arguments.rollback_generation:
        raise RuntimeError("active and rollback generations must differ")
    if not re.fullmatch(r"[0-9a-f]{64}", arguments.snapshot):
        raise RuntimeError("storage adoption snapshot is invalid")
    if arguments.confirm != plan["confirmation"]:
        raise RuntimeError("storage adopt requires the exact target/generations/snapshot confirmation")
    marker = f"{Path(target.value['state_directory']).parents[1]}/gateway/{target.name}/maintenance"
    if runner.run(["test", "-f", marker], check=False).returncode:
        raise RuntimeError("storage adoption requires the persistent maintenance marker")
    cohort = set(target.value["services"].values())
    running = sorted(
        item.get("Service") for item in runtime["containers"]
        if item.get("Service") in cohort and item.get("State") == "running"
    )
    if running:
        raise RuntimeError(f"storage adoption requires stopped cohort services: {running}")
    identity = runtime["compose"]
    source_release_manifest = (
        (runtime.get("active_state") or {}).get("release_manifest")
        or target.value["release_manifest"]
    )
    created = {}
    releases = {}
    networks = {}
    for generation in (arguments.rollback_generation, arguments.generation):
        volumes, network = _create_generation_resources(target, runner, generation)
        created[generation] = volumes
        networks[generation] = network
        for role, source in runtime["volumes"].items():
            source_path = _volume_source_path(runner, source["name"])
            destination_path = _volume_source_path(runner, volumes[role])
            runner.run(
                [
                    "rsync", "-aHAXS", "--numeric-ids", "--sparse", "--",
                    source_path.rstrip("/") + "/", destination_path.rstrip("/") + "/",
                ],
            )
            verified = runner.run(
                [
                    "rsync", "-aHAXScn", "--numeric-ids", "--sparse", "--itemize-changes", "--",
                    source_path.rstrip("/") + "/", destination_path.rstrip("/") + "/",
                ],
            )
            if verified.stdout.strip():
                raise RuntimeError(f"storage adoption copy differs: {generation}/{role}")
        releases[generation] = _write_adopt_generation(
            target,
            runner,
            identity,
            generation,
            volumes,
            network,
            arguments.snapshot,
            source_release_manifest,
        )
    previous = {
        "generation": arguments.rollback_generation,
        "volumes": created[arguments.rollback_generation],
        "network": networks[arguments.rollback_generation],
        "release_manifest": releases[arguments.rollback_generation],
        "snapshot": arguments.snapshot,
    }
    active_path = f"{target.value['state_directory']}/active.json"
    _write_remote(
        target,
        runner,
        active_path,
        _active_generation_state(
            target,
            arguments.generation,
            created[arguments.generation],
            networks[arguments.generation],
            arguments.snapshot,
            releases[arguments.generation],
            previous,
        ),
    )
    result = {
        **plan,
        "active_volumes": created[arguments.generation],
        "rollback_volumes": created[arguments.rollback_generation],
        "status": "adopted",
    }
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


def _prepare_secret_contract(target, runner) -> str:
    """Require a complete, direct and owner-protected release secret file."""
    path = _secret_file(target, runner)
    resolved = runner.run(["readlink", "-f", "--", path]).stdout.strip()
    if resolved != path:
        raise RuntimeError("release secret file path is not direct")
    metadata = runner.run(["stat", "-c", "%F|%a", "--", path]).stdout.strip()
    try:
        kind, raw_mode = metadata.split("|", 1)
        mode = int(raw_mode, 8)
    except (ValueError, TypeError) as error:
        raise RuntimeError("release secret file metadata is invalid") from error
    if kind != "regular file" or mode & 0o077:
        raise RuntimeError("release secret file permissions are unsafe")
    text = runner.run(["cat", path]).stdout
    found = validate_secret_text(text, target.value["secrets"]["allowed_keys"])
    if found != sorted(target.value["secrets"]["allowed_keys"]):
        raise RuntimeError("release secret file is incomplete")
    for raw in text.splitlines():
        line = raw.strip()
        if line and not line.startswith("#") and not line.split("=", 1)[1]:
            raise RuntimeError("release secret file contains an empty value")
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


def _filesystem_capacity(runner, path: str) -> tuple[str, int]:
    result = runner.run(["df", "--output=source,avail", "--block-size=1", path])
    try:
        fields = result.stdout.splitlines()[-1].split()
        return fields[0], int(fields[-1])
    except (IndexError, ValueError) as error:
        raise RuntimeError(f"disk capacity probe returned invalid output for {path}") from error


def _require_restore_capacity(
    target,
    runner,
    phase: str,
    *,
    candidate_bytes: dict[str, int] | None = None,
) -> dict:
    candidate_bytes = candidate_bytes or {}
    filesystems: dict[str, dict] = {}
    for tier, definition in target.value["storage"]["tiers"].items():
        source, available = _filesystem_capacity(runner, definition["path"])
        item = filesystems.setdefault(
            source,
            {
                "source": source,
                "tiers": [],
                "paths": [],
                "available_bytes": available,
                "candidate_bytes": 0,
                "reserve_bytes": 0,
            },
        )
        item["tiers"].append(tier)
        item["paths"].append(definition["path"])
        item["available_bytes"] = min(item["available_bytes"], available)
        item["candidate_bytes"] += candidate_bytes.get(tier, 0)
        item["reserve_bytes"] = max(item["reserve_bytes"], definition["reserve_bytes"])
    warning = False
    for source, item in filesystems.items():
        available = item["available_bytes"]
        warning = warning or available < CAPACITY_WARNING_BYTES
        if available < MINIMUM_FREE_BYTES:
            raise RuntimeError(
                f"restore {phase} refused on {source}: {_capacity_detail(available)}",
            )
        required = item["candidate_bytes"] + item["reserve_bytes"]
        item["required_bytes"] = required
        item["tiers"].sort()
        item["paths"].sort()
        if available < required:
            deficit = required - available
            raise RuntimeError(
                "restore capacity refused on "
                f"{source}: {available / 1024**3:.1f} GiB free, "
                f"{item['candidate_bytes'] / 1024**3:.1f} GiB measured candidate, "
                f"{item['reserve_bytes'] / 1024**3:.1f} GiB reserve, "
                f"{deficit / 1024**3:.1f} GiB deficit",
            )
        _report(
            "restore",
            phase,
            "capacity checked",
            f"{source} ({', '.join(item['tiers'])}): {_capacity_detail(available)}",
        )
    return {
        "schema": "usl-storage-capacity/v2",
        "filesystems": {source: filesystems[source] for source in sorted(filesystems)},
        "warning": warning,
    }


def _measure_candidate_bytes(target, runner, tool_image: str, runtime: dict) -> dict[str, int]:
    """Measure the additional persistent state a fresh generation must hold.

    Existing active and rollback generations are already reflected in free
    space, so they must not be added again. The estimate intentionally sums
    allocated file bytes rather than Docker volume metadata.
    """
    totals = {tier: 0 for tier in target.value["storage"]["tiers"]}
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
            totals[item["tier"]] += int(measured[0])
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
            totals[item["tier"]] += int(result.stdout.split()[0])
        except (IndexError, ValueError) as error:
            raise RuntimeError(f"cannot measure persistent path: {item['path']}") from error
    return totals


def _prepare_release_candidate(
    target,
    runner,
    release: dict,
    current: dict,
    *,
    upgrade_plan_sha256: str | None = None,
) -> dict:
    """Validate and cache a candidate without changing application runtime state."""
    _prepare_secret_contract(target, runner)
    candidate_identity = _candidate_compose_identity(target, runner, current["compose"])
    for definition in target.value["storage"]["tiers"].values():
        path = definition["path"]
        if runner.run(["test", "-d", path], check=False).returncode:
            raise RuntimeError(f"release storage path is unavailable: {path}")
    for name in target.value["external_networks"].values():
        if runner.run(["docker", "network", "inspect", name], check=False).returncode:
            raise RuntimeError(f"release external network is unavailable: {name}")
    capacity_before_pull = _require_restore_capacity(target, runner, "prepare")
    for image in _release_images(release):
        _ensure_image(runner, image)
    tool_image = release["components"]["backup-tool"]["digest_reference"]
    candidate_bytes = _measure_candidate_bytes(target, runner, tool_image, current)
    capacity_after_pull = _require_restore_capacity(
        target, runner, "prepare image pull", candidate_bytes=candidate_bytes,
    )

    release_identity = {
        **candidate_identity,
        "profiles": sorted(set(candidate_identity.get("profiles", [])) | {"release"}),
    }
    services = set(
        runner.run(compose_command(release_identity, ["config", "--services"])).stdout.splitlines(),
    )
    temporary = runner.run(["mktemp", "-d", "/tmp/usl-release-prepare.XXXXXX"]).stdout.strip()
    if not re.fullmatch(r"/tmp/usl-release-prepare\.[A-Za-z0-9]{6}", temporary):
        raise RuntimeError("release Compose render workspace is invalid")
    try:
        resource_path = f"{temporary}/compose.resources.json"
        generation_path = f"{temporary}/compose.generation.json"
        resource = _resource_overlay(target)
        compose_files = list(candidate_identity["compose_files"])
        if resource is not None:
            _write_remote(target, runner, resource_path, resource, "0600")
            compose_files.append(resource_path)
        current_volumes = {role: item["name"] for role, item in current["volumes"].items()}
        _write_remote(
            target,
            runner,
            generation_path,
            _generation_overlay(
                current_volumes,
                release,
                services,
                target.value["ingress"],
                sign_secret_root=(
                    target.value["paths"]["sign_secrets"]["path"]
                    if target.value["environment"] == "production"
                    else None
                ),
                service_names=target.value["services"],
                quarantine=target.value["environment"] == "production",
            ),
            "0600",
        )
        compose_files.append(generation_path)
        render_identity = {**release_identity, "compose_files": compose_files}
        rendered = json.loads(
            runner.run(compose_command(render_identity, ["config", "--format", "json"])).stdout,
        )
        rendered_services = rendered.get("services")
        if not isinstance(rendered_services, dict):
            raise RuntimeError("release Compose render has no services")
        expected_images = {
            service: _release_image(release, component)
            for component, component_services in RELEASE_IMAGE_SERVICES.items()
            for service in component_services
            if service in rendered_services
        }
        mismatched = {
            name: rendered_services[name].get("image")
            for name, image in expected_images.items()
            if rendered_services[name].get("image") != image
        }
        if mismatched:
            raise RuntimeError("release Compose image render differs: " + json.dumps(mismatched, sort_keys=True))
        canonical = json.dumps(rendered, sort_keys=True, separators=(",", ":"))
    finally:
        runner.run(["rm", "-rf", "--", temporary], check=False)
    return {
        "schema": "usl-release-prepare/v1",
        "target": target.name,
        "release": release["identity"],
        "gitops_commit": candidate_identity.get("gitops_commit"),
        "upgrade_plan_sha256": upgrade_plan_sha256,
        "compose_sha256": hashlib.sha256(canonical.encode()).hexdigest(),
        "services": sorted(rendered_services),
        "images": sorted(_release_images(release)),
        "capacity": {
            "before_pull": capacity_before_pull,
            "after_pull": capacity_after_pull,
        },
        "runtime_changed": False,
        "status": "prepared",
    }


def _release_attempt(value: str | None, release_identity: str) -> str:
    if not isinstance(value, str) or not RELEASE_ATTEMPT.fullmatch(value):
        raise RuntimeError("release attempt identity is missing or invalid")
    if value == release_identity:
        raise RuntimeError("release attempt must be distinct from the desired release")
    return value


def _required_maintenance_endpoints(target) -> set[str]:
    endpoints = target.value["endpoints"]
    required = {
        endpoints["odoo"].rstrip("/") + "/web/health",
        endpoints["odoo"].rstrip("/") + "/websocket",
    }
    for service, path in (("paperless", "/api/schema/"), ("mcp", "/readyz")):
        origin = endpoints[service]
        if origin.startswith("https://"):
            required.add(origin.rstrip("/") + path)
    return required


def _maintenance_receipt(
    value: object,
    *,
    target: str,
    attempt: str,
    required_endpoints: set[str] | None = None,
) -> dict:
    expected = {"schema", "target", "attempt", "observed_at", "endpoints", "status", "sha256"}
    if not isinstance(value, dict) or set(value) != expected:
        raise RuntimeError("maintenance receipt fields differ")
    if (
        value["schema"] != "usl-maintenance-admission/v1"
        or value["target"] != target
        or value["attempt"] != attempt
        or value["status"] != "closed"
    ):
        raise RuntimeError("maintenance receipt identity differs")
    try:
        observed = datetime.fromisoformat(str(value["observed_at"]).replace("Z", "+00:00"))
    except ValueError as error:
        raise RuntimeError("maintenance receipt timestamp is invalid") from error
    if observed.tzinfo is None:
        raise RuntimeError("maintenance receipt timestamp has no timezone")
    endpoints = value["endpoints"]
    if not isinstance(endpoints, dict) or not endpoints:
        raise RuntimeError("maintenance receipt has no endpoints")
    if required_endpoints is not None and set(endpoints) != required_endpoints:
        raise RuntimeError("maintenance receipt endpoint coverage differs")
    for url, evidence in endpoints.items():
        if (
            not isinstance(url, str)
            or not url.startswith("https://")
            or not isinstance(evidence, dict)
            or set(evidence) != {"status_code", "body_sha256"}
            or evidence["status_code"] != 503
            or not re.fullmatch(r"[0-9a-f]{64}", str(evidence["body_sha256"]))
        ):
            raise RuntimeError("maintenance endpoint evidence is invalid")
    body = {key: item for key, item in value.items() if key != "sha256"}
    if value["sha256"] != hashlib.sha256(
        json.dumps(body, sort_keys=True, separators=(",", ":")).encode(),
    ).hexdigest():
        raise RuntimeError("maintenance receipt digest differs")
    return value


def _prepare_receipt(value: object, *, target: str, attempt: str, release: str) -> dict:
    expected = {
        "schema", "target", "release", "attempt", "prepared_at", "compose_sha256",
        "gitops_commit", "upgrade_plan_sha256", "services", "images", "capacity",
        "runtime_changed", "status", "sha256",
    }
    if not isinstance(value, dict) or set(value) != expected:
        raise RuntimeError("release prepare receipt fields differ")
    if (
        value["schema"] != "usl-release-prepare/v1"
        or value["target"] != target
        or value["release"] != release
        or value["attempt"] != attempt
        or value["runtime_changed"] is not False
        or value["status"] != "prepared"
        or not re.fullmatch(r"[0-9a-f]{64}", str(value["compose_sha256"]))
        or value["gitops_commit"] is not None
        and not re.fullmatch(r"[0-9a-f]{40}", str(value["gitops_commit"]))
        or value["upgrade_plan_sha256"] is not None
        and not re.fullmatch(r"[0-9a-f]{64}", str(value["upgrade_plan_sha256"]))
    ):
        raise RuntimeError("release prepare receipt identity differs")
    try:
        prepared_at = datetime.fromisoformat(str(value["prepared_at"]).replace("Z", "+00:00"))
    except ValueError as error:
        raise RuntimeError("release prepare receipt timestamp is invalid") from error
    if prepared_at.tzinfo is None:
        raise RuntimeError("release prepare receipt timestamp has no timezone")
    body = {key: item for key, item in value.items() if key != "sha256"}
    if value["sha256"] != hashlib.sha256(
        json.dumps(body, sort_keys=True, separators=(",", ":")).encode(),
    ).hexdigest():
        raise RuntimeError("release prepare receipt digest differs")
    return value


def _require_same_preparation(current: dict, receipt: dict) -> None:
    for field in (
        "target", "release", "gitops_commit", "upgrade_plan_sha256", "compose_sha256", "services", "images",
        "runtime_changed", "status",
    ):
        if current.get(field) != receipt.get(field):
            raise RuntimeError(f"release preparation changed after maintenance: {field}")


def _validated_release_upgrade_plan(target, value: object, release: dict) -> dict:
    if not isinstance(value, dict):
        raise PlanEvidenceError("upgrade plan must be a JSON object")
    if target.value["environment"] == "production":
        if value.get("schema") != PROMOTION_SCHEMA:
            raise PlanEvidenceError(
                "production requires a staging-signed production promotion",
            )
        plan = verify_upgrade_plan_promotion(
            value,
            Path(target.value["plan_signing"]["public_key"]),
            release,
        )
    elif value.get("schema") == "usl-staging-upgrade-plan-evidence/v1":
        plan = verify_upgrade_plan(
            value,
            Path(target.value["plan_signing"]["public_key"]),
        )
    else:
        plan = validate_upgrade_plan(value)
    if plan["candidate_release"] != release["identity"]:
        raise ModuleReleaseError("upgrade plan targets another candidate")
    return plan


def _remove_materialization_workspace(target, runner, generation: str) -> None:
    root = target.value["state_directory"]
    if not GENERATION_NAME.fullmatch(str(generation)):
        raise RuntimeError("refusing to remove an invalid generation workspace")
    runner.run(["rm", "-rf", "--", f"{root}/generations/{generation}/work"])


@contextmanager
def _materialization_cleanup(target, runner, generation: str):
    database_containers: list[str] = []
    try:
        yield database_containers
    finally:
        for container in database_containers:
            runner.run(["docker", "rm", "--force", container], check=False)
        _remove_materialization_workspace(target, runner, generation)


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
    elif arguments.action == "select":
        if target.name != "production":
            raise RuntimeError("backup selection is production-only")
        inventory = _run_cohort(
            target, runner, image, "list", [], volumes=runtime["volumes"],
        )
        snapshot = select_latest_recovery_snapshot(inventory.get("snapshots"))
        verified = _run_cohort(
            target,
            runner,
            image,
            "verify",
            ["--durable-snapshot", snapshot],
            volumes=runtime["volumes"],
        )
        _validate_recovery_selection(snapshot, verified)
        result = {
            "schema": "usl-recovery-selection/v1",
            "snapshot": snapshot,
            "verification": verified,
            "status": "selected",
        }
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
            writers_stopped_at = None
            writers_resumed_at = None
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
                writers_stopped_at = datetime.now(UTC).isoformat().replace("+00:00", "Z")
                _record_event(target, runner, run_id, "backup", "capture", "started")
                captured = with_writers_paused(
                    runner,
                    identity,
                    writer_services,
                    capture_phase,
                    resume_after_success=not leave_quiesced,
                )
                freeze_seconds = round(time.monotonic() - freeze_started, 3)
                if not leave_quiesced:
                    writers_resumed_at = datetime.now(UTC).isoformat().replace("+00:00", "Z")
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
                    "capture_pause_seconds": freeze_seconds,
                    "writer_freeze_seconds": (
                        freeze_seconds if not leave_quiesced and not arguments.resume else None
                    ),
                    "writer_freeze_sla_seconds": 120,
                    "writer_freeze_sla_passed": (
                        freeze_seconds <= 120
                        if not leave_quiesced and not arguments.resume
                        else None
                    ),
                    "upload_seconds": upload_seconds,
                    "verification_seconds": verify_seconds,
                    "total_seconds": total_seconds,
                },
                "runtime_images": runtime_images,
                "writers_quiesced": leave_quiesced,
                "writer_interval_complete": not leave_quiesced and not arguments.resume,
                "writers_stopped_at": writers_stopped_at,
                "writers_resumed_at": writers_resumed_at,
                "status": "qualified",
            }
    print(json.dumps(result, indent=None if arguments.json else 2, sort_keys=True))
    return 0


def _validate_recovery_selection(snapshot: str, verified: dict) -> None:
    """Bind recovery admission to the exact complete production cohort."""
    if (
        verified.get("status") != "verified"
        or verified.get("durable_snapshot_id") != snapshot
        or verified.get("target") != "production"
        or verified.get("cohort_schema") != RECOVERY_COHORT_SCHEMA
    ):
        raise RuntimeError("selected production recovery snapshot verification differs")


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


def _validate_mcp_readiness(value: object, *, require_oauth: bool) -> dict:
    if not isinstance(value, dict) or value.get("schema") != "usl-odoo-mcp-readiness/v1":
        raise RuntimeError("MCP readiness evidence schema differs")
    version = value.get("server_version")
    if not isinstance(version, str) or not re.fullmatch(r"1\.[0-9]+\.[0-9]+", version):
        raise RuntimeError("MCP readiness reports an unsupported server version")
    oauth = value.get("oauth")
    if not isinstance(oauth, dict) or oauth.get("schema_version") != 1:
        raise RuntimeError("MCP readiness reports an unsupported OAuth-vault schema")
    allowed_oauth = {"ready"} if require_oauth else {"ready", "disabled"}
    if oauth.get("status") not in allowed_oauth:
        raise RuntimeError("MCP OAuth vault is not ready")
    if value.get("status") != "ready" or not isinstance(value.get("targets"), int) or value["targets"] < 1:
        raise RuntimeError("MCP runtime is not ready")
    return {
        "schema": value["schema"],
        "server_version": version,
        "oauth": {
            "schema_version": oauth["schema_version"],
            "status": oauth["status"],
        },
        "targets": value["targets"],
        "status": value["status"],
    }


def _mcp_readiness(target, runner) -> dict:
    url = target.value["admission_endpoints"]["mcp"].rstrip("/") + "/readyz"
    result = runner.run(
        ["curl", "--silent", "--show-error", "--fail", "--max-time", "10", url],
        check=False,
    )
    if result.returncode:
        raise RuntimeError("MCP readiness endpoint is unavailable")
    try:
        value = json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise RuntimeError("MCP readiness endpoint did not return JSON") from error
    return _validate_mcp_readiness(
        value,
        require_oauth=target.value["environment"] in {"production", "staging"},
    )


def _sign_readiness(target, runner, identity) -> dict:
    # This probe performs only trusted health reads. It records public trust
    # material identities, never provisioner keys, keystore values or secrets.
    program = (
        "import hashlib,json,os,pathlib,ssl,urllib.request;"
        "ca_url=os.environ['USL_SIGN_STEP_CA_URL'].rstrip('/');"
        "ca_bundle=os.environ['USL_SIGN_STEP_CA_CA_BUNDLE'];"
        "ca_ctx=ssl.create_default_context(cafile=ca_bundle);"
        "ca=json.load(urllib.request.urlopen(ca_url+'/health',context=ca_ctx,timeout=10));"
        "dss_url=os.environ['USL_SIGN_DSS_URL'].rstrip('/');"
        "dss_bundle=os.environ['USL_SIGN_DSS_CA_BUNDLE'];"
        "dss_ctx=ssl.create_default_context(cafile=dss_bundle);"
        "dss_ctx.load_cert_chain(os.environ['USL_SIGN_DSS_CLIENT_CERT'],os.environ['USL_SIGN_DSS_CLIENT_KEY']);"
        "req=urllib.request.Request(dss_url+'/v1/health',data=b'{}',headers={'Content-Type':'application/json','Accept':'application/json'});"
        "dss=json.load(urllib.request.urlopen(req,context=dss_ctx,timeout=10));"
        "assert ca.get('status')=='ok' and dss.get('ok') is True and dss.get('engineVersion')=='6.4';"
        "print(json.dumps({'schema':'usl-sign-readiness/v1','status':'ready',"
        "'step_ca':{'status':'ok','trust_sha256':hashlib.sha256(pathlib.Path(ca_bundle).read_bytes()).hexdigest()},"
        "'dss':{'status':'ok','engine_version':dss['engineVersion'],'trust_sha256':hashlib.sha256(pathlib.Path(dss_bundle).read_bytes()).hexdigest()}}))"
    )
    return _python_probe(target, runner, identity, "odoo", program)


def _validate_sign_readiness(value: object) -> dict:
    if (
        not isinstance(value, dict)
        or value.get("schema") != "usl-sign-readiness/v1"
        or value.get("status") != "ready"
        or not isinstance(value.get("step_ca"), dict)
        or not isinstance(value.get("dss"), dict)
        or value["step_ca"].get("status") != "ok"
        or value["dss"].get("status") != "ok"
        or value["dss"].get("engine_version") != "6.4"
    ):
        raise RuntimeError("Sign readiness evidence differs")
    for service in ("step_ca", "dss"):
        if not re.fullmatch(r"[0-9a-f]{64}", str(value[service].get("trust_sha256", ""))):
            raise RuntimeError("Sign public trust identity is invalid")
    return value


def _runtime_admission_evidence(target, runner, identity) -> dict:
    mcp = _mcp_readiness(target, runner)
    sign = _validate_sign_readiness(_sign_readiness(target, runner, identity))
    return {"mcp": mcp, "sign": sign}


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
    admission = target.value["admission_endpoints"]
    for name in ("odoo", "paperless", "mcp"):
        origin = admission[name]
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
        origin = admission["odoo_websocket"].rstrip("/")
        public_origin = target.value["endpoints"]["odoo"].rstrip("/")
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
                f"Origin: {public_origin}",
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
    try:
        service_evidence = _runtime_admission_evidence(target, runner, identity)
    except RuntimeError as error:
        failures.append("cohort:readiness-evidence")
        service_evidence = {"error": str(error)}
    result = {
        "schema": "usl-runtime-health/v1",
        "target": target.name,
        "status": "passed" if not failures else "failed",
        "failures": failures,
        "endpoints": endpoints,
        "configured_endpoints": target.value["endpoints"],
        "odoo_config": odoo_config,
        "websocket": websocket_status,
        "ollama": ollama_status,
        "services": service_evidence,
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
        cron_inventory = json.loads(
            _psql(target, runner, identity, "odoo", CRON_INVENTORY_SQL),
        )
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
        service_evidence = _runtime_admission_evidence(target, runner, identity)
    except (RuntimeError, json.JSONDecodeError) as error:
        raise RuntimeError(f"read-only database smoke failed: {error}") from error
    failures = []
    cron_target = target.value["cron_policy"]
    try:
        cron_policy = (
            None
            if cron_target["mode"] == "unmanaged"
            else parse_cron_policy(_read_path(target, runner, Path(cron_target["path"])))
        )
        cron_status = validate_cron_runtime(
            cron_policy,
            mode=cron_target["mode"],
            gates=cron_target["gates"],
            installed=cron_inventory["installed"],
            active=cron_inventory["active"],
            invalid_identity_count=cron_inventory["invalid_identity_count"],
        )
    except (CronPolicyError, KeyError, TypeError) as error:
        cron_status = {"status": "failed", "error": str(error)}
        failures.append("odoo:cron-policy")
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
    if odoo.get("cron_lag"):
        failures.append("odoo:cron-lag")
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
        "cron_policy": cron_status,
        "services": service_evidence,
        "storage": {"odoo": odoo_storage, "paperless": paperless_storage},
    }
    print(json.dumps(result, indent=None if arguments.json else 2, sort_keys=True))
    return 0 if not failures else 2


def generation_volume_names(target, generation: str) -> dict[str, str]:
    return {
        role: f"{target.project}-{generation}-{role.replace('_', '-')}"
        for role in target.value["volumes"]
    }


def generation_volume_path(target, generation: str, role: str) -> str:
    if not GENERATION_NAME.fullmatch(str(generation)):
        raise RuntimeError("generation name is invalid")
    if role not in target.value["volumes"]:
        raise RuntimeError("generation volume role is invalid")
    definition = target.value["volumes"][role]
    if definition["tier"] != "database":
        raise RuntimeError("only database-tier volumes have host paths")
    root = Path(target.value["storage"]["tiers"]["database"]["path"])
    generation_root = root / "usl-odoo" / target.name / "generations" / generation
    candidate = generation_root / role
    if candidate.parent != generation_root:
        raise RuntimeError("generation volume path escaped its generation root")
    return str(candidate)


def _create_generation_resources(target, runner, generation: str) -> tuple[dict[str, str], str]:
    volumes = generation_volume_names(target, generation)
    network = f"{target.project}-{generation}-recovery"
    for role, name in volumes.items():
        probe = runner.run(["docker", "volume", "inspect", name], check=False)
        if probe.returncode == 0:
            raise RuntimeError(f"generation volume already exists: {name}")
        tier = target.value["volumes"][role]["tier"]
        command = [
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
            "--label",
            f"com.unstaticlabs.runtime.storage-tier={tier}",
        ]
        if tier == "database":
            device = generation_volume_path(target, generation, role)
            runner.run(["install", "-d", "-m", "0700", "--", device])
            command.extend(
                [
                    "--driver",
                    "local",
                    "--opt",
                    "type=none",
                    "--opt",
                    "o=bind",
                    "--opt",
                    f"device={device}",
                ],
            )
        command.append(name)
        runner.run(command)
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
    if (
        target.name == "staging"
        and target.value["environment"] == "staging"
        and target.value["services"]["odoo"] == "odoo-staging"
        and isinstance(services, dict)
        and "odoo" in services
        and "odoo-staging" not in services
    ):
        services["odoo-staging"] = services.pop("odoo")
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
    quarantine: bool = False,
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
            (service_names or {}).get("odoo", "odoo") if service == "odoo" else service: {
                "image": images[component]
            }
            for component, services in RELEASE_IMAGE_SERVICES.items()
            for service in services
            if available_services is None
            or service in available_services
            or (
                service == "odoo"
                and (service_names or {}).get("odoo") in available_services
            )
        }
        odoo_service = (service_names or {}).get("odoo", "odoo")
        if ingress is not None and odoo_service in value["services"]:
            value["services"][odoo_service]["environment"] = {
                "ODOO_PROXY_MODE": "True" if ingress["proxy_mode"] else "False",
                "ODOO_LIST_DB": "True" if ingress["list_db"] else "False",
                "ODOO_DB_FILTER": ingress["dbfilter"],
            }
        if quarantine:
            if ingress is None:
                raise RuntimeError("production quarantine requires ingress configuration")
            odoo_environment = value["services"][odoo_service].setdefault("environment", {})
            odoo_environment.update({
                "ODOO_MAX_CRON_THREADS": "0",
                "ODOO_SMTP_SERVER": "127.0.0.1",
                "ODOO_SMTP_PORT": "1",
                "USL_EINVOICE_LIVE_ENABLED": "0",
                "USL_EREPORTING_LIVE_ENABLED": "0",
                "USL_PRODUCTION_CANDIDATE_QUARANTINED": "1",
            })
            value["services"][odoo_service].setdefault("labels", {})[
                "com.unstaticlabs.runtime.side-effects"
            ] = "quarantined"
            if "paperless-webserver" in value["services"]:
                value["services"]["paperless-webserver"].setdefault("environment", {}).update({
                    "PAPERLESS_EMAIL_TASK_CRON": "disable",
                    "PAPERLESS_EMPTY_TRASH_TASK_CRON": "disable",
                    "PAPERLESS_LLM_INDEX_TASK_CRON": "disable",
                    "PAPERLESS_SANITY_TASK_CRON": "disable",
                    "PAPERLESS_SHARE_LINK_BUNDLE_CLEANUP_CRON": "disable",
                    "PAPERLESS_TRAIN_TASK_CRON": "disable",
                    "PAPERLESS_WORKFLOW_SCHEDULED_TASK_CRON": "disable",
                })
                value["services"]["paperless-webserver"].setdefault("labels", {})[
                    "com.unstaticlabs.runtime.side-effects"
                ] = "quarantined"
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
            "odoo-upgrade": {
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


def _run_production_boundary_script(
    target, runner, release: dict, network: str, volumes: dict[str, str],
    script: str, fingerprint: str, prefix: str,
) -> dict:
    if target.value["environment"] != "production":
        raise RuntimeError("production boundary scripts are production-only")
    database = target.value["databases"]["odoo"]
    result = runner.run(
        [
            "docker", "run", "--rm", "--interactive", "--network", network,
            "--env-file", target.value["secrets"]["env_file"],
            "--env", f"ODOO_DB_HOST={database['service']}",
            "--env", "ODOO_DB_PORT=5432",
            "--env", f"ODOO_DB_USER={database['user']}",
            "--env", f"ODOO_DB_NAME={database['name']}",
            "--env", "ODOO_MAX_CRON_THREADS=0",
            "--env", "USL_EINVOICE_LIVE_ENABLED=0",
            "--env", "USL_EREPORTING_LIVE_ENABLED=0",
            "--env", f"USL_PRODUCTION_ACTIVATION_CONFIRM={fingerprint}",
            "--env", "USL_PRODUCTION_INBOUND_MAIL_ENABLED=" + (
                "1" if target.value["cron_policy"]["gates"].get("inbound_mail") else "0"
            ),
            "--volume", f"{volumes['odoo_filestore']}:/var/lib/odoo",
            release["components"]["distribution"]["digest_reference"],
            "odoo", "shell", "--config=/etc/odoo/odoo.conf",
            f"--database={database['name']}", "--no-http", "--max-cron-threads=0",
        ],
        input_text=(ROOT / "scripts" / "odoo" / script).read_text(encoding="utf-8"),
    )
    for line in reversed(result.stdout.splitlines()):
        if line.startswith(prefix):
            value = json.loads(line.removeprefix(prefix))
            if value.get("status") != "passed" or value.get("candidate_fingerprint") != fingerprint:
                raise RuntimeError(f"{script} returned invalid evidence")
            return value
    raise RuntimeError(f"{script} returned no evidence")


def _admit_production_side_effects(target, runner, release, network, volumes) -> dict:
    database = target.value["databases"]["odoo"]
    result = runner.run(
        [
            "docker", "run", "--rm", "--interactive", "--network", network,
            "--env-file", target.value["secrets"]["env_file"],
            "--env", f"ODOO_DB_HOST={database['service']}",
            "--env", "ODOO_DB_PORT=5432",
            "--env", f"ODOO_DB_USER={database['user']}",
            "--env", f"ODOO_DB_NAME={database['name']}",
            "--env", "ODOO_MAX_CRON_THREADS=0",
            "--env", "USL_PRODUCTION_SIDE_EFFECT_MODE=admitted",
            "--env", "USL_PRODUCTION_CRON_GATES_JSON=" + json.dumps(
                target.value["cron_policy"]["gates"], sort_keys=True,
            ),
            "--volume", f"{volumes['odoo_filestore']}:/var/lib/odoo",
            release["components"]["distribution"]["digest_reference"],
            "odoo", "shell", "--config=/etc/odoo/odoo.conf",
            f"--database={database['name']}", "--no-http", "--max-cron-threads=0",
        ],
        input_text=(ROOT / "scripts" / "odoo" / "production_side_effect_boundary.py").read_text(encoding="utf-8"),
    )
    try:
        value = json.loads(result.stdout.splitlines()[-1])
    except (IndexError, json.JSONDecodeError) as error:
        raise RuntimeError("production side-effect admission returned invalid evidence") from error
    if value.get("status") != "passed" or value.get("mode") != "admitted":
        raise RuntimeError("production side-effect admission did not pass")
    return value


def _release_attempt_claim(value: object, *, target, attempt: str, release: str) -> dict:
    """Validate the immutable, attempt-scoped operation bundle."""
    expected = {
        "schema", "attempt", "target", "candidate_release", "source", "snapshot",
        "generation", "gitops_commit", "upgrade_plan_sha256", "prepare_receipt_sha256",
        "maintenance_receipt_sha256", "baseline_generation", "operation_bundle_sha256",
        "claimed_at", "status", "sha256",
    }
    if not isinstance(value, dict) or set(value) != expected:
        raise RuntimeError("release attempt claim fields differ")
    operation = {
        key: value[key]
        for key in (
            "target", "attempt", "source", "candidate_release", "snapshot", "generation",
            "gitops_commit", "upgrade_plan_sha256", "prepare_receipt_sha256",
            "maintenance_receipt_sha256",
        )
    }
    if (
        value["schema"] != "usl-release-attempt/v2"
        or value["target"] != target.name
        or value["attempt"] != attempt
        or value["candidate_release"] != release
        or not re.fullmatch(r"[0-9a-f]{64}", str(value["candidate_release"]))
        or value["status"] != "claimed"
        or not re.fullmatch(r"[0-9a-f]{64}", str(value["snapshot"]))
        or not re.fullmatch(r"g[a-zA-Z0-9._-]{1,31}", str(value["generation"]))
        or (
            not re.fullmatch(r"[0-9a-f]{40}", str(value["gitops_commit"]))
            if target.value["compose"].get("canonical") is not None
            else value["gitops_commit"] is not None
        )
        or not re.fullmatch(r"[a-z][a-z0-9-]{1,31}", str(value["source"]))
        or not all(
            re.fullmatch(r"[0-9a-f]{64}", str(value[field]))
            for field in (
                "upgrade_plan_sha256", "prepare_receipt_sha256",
                "maintenance_receipt_sha256", "operation_bundle_sha256",
            )
        )
        or value["operation_bundle_sha256"] != hashlib.sha256(
            json.dumps(operation, sort_keys=True, separators=(",", ":")).encode(),
        ).hexdigest()
        or value["baseline_generation"] is not None
        and not re.fullmatch(r"g[a-zA-Z0-9._-]{1,31}", str(value["baseline_generation"]))
    ):
        raise RuntimeError("release attempt claim identity differs")
    try:
        claimed_at = datetime.fromisoformat(str(value["claimed_at"]).replace("Z", "+00:00"))
    except ValueError as error:
        raise RuntimeError("release attempt claim timestamp is invalid") from error
    if claimed_at.tzinfo is None:
        raise RuntimeError("release attempt claim timestamp is invalid")
    digest = hashlib.sha256(
        json.dumps(
            {key: item for key, item in value.items() if key != "sha256"},
            sort_keys=True,
            separators=(",", ":"),
        ).encode(),
    ).hexdigest()
    if value["sha256"] != digest:
        raise RuntimeError("release attempt claim digest differs")
    return value


def _require_same_attempt_boundary(
    claim: dict,
    receipt: dict,
    *,
    generation: str,
    snapshot: str,
) -> None:
    if (
        receipt["operation_bundle_sha256"] != claim["operation_bundle_sha256"]
        or receipt["generation"] != generation
        or receipt["snapshot"] != snapshot
        or claim["generation"] != generation
        or claim["snapshot"] != snapshot
    ):
        raise RuntimeError("release boundary operation bundle differs")


def _forward_only_receipt(
    *,
    target,
    attempt: str,
    release: str,
    snapshot: str,
    generation: str,
    operation_bundle_sha256: str,
) -> dict:
    body = {
        "schema": "usl-release-forward-only/v1",
        "target": target.name,
        "attempt": attempt,
        "release": release,
        "snapshot": snapshot,
        "generation": generation,
        "operation_bundle_sha256": operation_bundle_sha256,
        "crossed_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "status": "forward-only",
    }
    body["sha256"] = hashlib.sha256(
        json.dumps(body, sort_keys=True, separators=(",", ":")).encode(),
    ).hexdigest()
    return body


def _validate_forward_only_receipt(
    value: object,
    *,
    target,
    attempt: str,
    release: str,
    snapshot: str,
    generation: str,
    operation_bundle_sha256: str,
) -> dict:
    expected = {
        "schema", "target", "attempt", "release", "snapshot", "generation",
        "operation_bundle_sha256", "crossed_at", "status", "sha256",
    }
    if not isinstance(value, dict) or set(value) != expected:
        raise RuntimeError("release forward-only receipt fields differ")
    if (
        value["schema"] != "usl-release-forward-only/v1"
        or value["target"] != target.name
        or value["attempt"] != attempt
        or value["release"] != release
        or value["snapshot"] != snapshot
        or value["generation"] != generation
        or value["operation_bundle_sha256"] != operation_bundle_sha256
        or value["status"] != "forward-only"
    ):
        raise RuntimeError("release forward-only receipt identity differs")
    try:
        crossed_at = datetime.fromisoformat(str(value["crossed_at"]).replace("Z", "+00:00"))
    except ValueError as error:
        raise RuntimeError("release forward-only timestamp is invalid") from error
    if crossed_at.tzinfo is None:
        raise RuntimeError("release forward-only timestamp is invalid")
    digest = hashlib.sha256(
        json.dumps(
            {key: item for key, item in value.items() if key != "sha256"},
            sort_keys=True,
            separators=(",", ":"),
        ).encode(),
    ).hexdigest()
    if value["sha256"] != digest:
        raise RuntimeError("release forward-only receipt digest differs")
    return value


def _release_boundary_receipt(
    *,
    schema: str,
    status: str,
    target,
    attempt: str,
    release: str,
    snapshot: str,
    generation: str,
    health: dict,
    smoke: dict,
    control_validation: dict,
    operation_bundle_sha256: str,
) -> dict:
    """Bind a runtime boundary to the exact candidate and its validation."""
    timestamp_key = "admitted_at" if status == "admitted" else "quarantined_at"
    body = {
        "schema": schema,
        "target": target.name,
        "attempt": attempt,
        "release": release,
        "snapshot": snapshot,
        "generation": generation,
        "operation_bundle_sha256": operation_bundle_sha256,
        "health_sha256": hashlib.sha256(
            json.dumps(health, sort_keys=True, separators=(",", ":")).encode(),
        ).hexdigest(),
        "smoke_sha256": hashlib.sha256(
            json.dumps(smoke, sort_keys=True, separators=(",", ":")).encode(),
        ).hexdigest(),
        "control_validation_sha256": hashlib.sha256(
            json.dumps(control_validation, sort_keys=True, separators=(",", ":")).encode(),
        ).hexdigest(),
        timestamp_key: datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "status": status,
    }
    body["sha256"] = hashlib.sha256(
        json.dumps(body, sort_keys=True, separators=(",", ":")).encode(),
    ).hexdigest()
    return body


def _validate_release_boundary_receipt(
    value: object,
    *,
    schema: str,
    status: str,
    target,
    attempt: str,
    release: str,
) -> dict:
    timestamp_key = "admitted_at" if status == "admitted" else "quarantined_at"
    required = {
        "schema", "target", "attempt", "release", "snapshot", "generation",
        "operation_bundle_sha256",
        "health_sha256", "smoke_sha256", "control_validation_sha256",
        timestamp_key, "status", "sha256",
    }
    if not isinstance(value, dict) or set(value) != required:
        raise RuntimeError("release boundary receipt fields differ")
    if (
        value["schema"] != schema
        or value["status"] != status
        or value["target"] != target.name
        or value["attempt"] != attempt
        or value["release"] != release
        or not re.fullmatch(r"g[a-zA-Z0-9._-]{1,31}", str(value["generation"]))
        or not re.fullmatch(r"[0-9a-f]{64}", str(value["snapshot"]))
        or not re.fullmatch(r"[0-9a-f]{64}", str(value["operation_bundle_sha256"]))
    ):
        raise RuntimeError("release boundary receipt identity differs")
    digest = hashlib.sha256(
        json.dumps(
            {key: item for key, item in value.items() if key != "sha256"},
            sort_keys=True,
            separators=(",", ":"),
        ).encode(),
    ).hexdigest()
    if value["sha256"] != digest:
        raise RuntimeError("release boundary receipt digest differs")
    return value


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


def _base_compose_identity(target, identity: dict) -> dict:
    generated_prefix = target.value["state_directory"] + "/generations/"
    compose_files = [
        path
        for path in identity["compose_files"]
        if not (
            path.startswith(generated_prefix)
            and path.endswith(("/compose.generation.json", "/compose.resources.json"))
        )
    ]
    if not compose_files:
        raise RuntimeError("base Compose identity is unavailable")
    return {**identity, "compose_files": compose_files}


def _compose_services(target, identity: dict) -> list[str]:
    """Return target services using the captured runtime anchor when it is legacy."""
    services = set(target.value["services"].values())
    anchor = target.value["compose"]["anchor_service"]
    captured = identity.get("anchor_service", anchor)
    if captured != anchor:
        services.remove(anchor)
        services.add(captured)
    return sorted(services)


def _candidate_compose_identity(target, runner, current_identity: dict) -> dict:
    """Resolve the canonical base independently from a permitted legacy runtime."""
    anchor = target.value["compose"]["anchor_service"]
    canonical = target.value["compose"].get("canonical")
    if canonical is not None:
        root_value = os.environ.get("USL_RELEASE_GITOPS_ROOT", "")
        commit = os.environ.get("USL_RELEASE_GITOPS_COMMIT", "")
        if not root_value.startswith("/") or not re.fullmatch(r"[0-9a-f]{40}", commit):
            raise RuntimeError("exact GitOps Compose identity is missing")
        root = Path(root_value)
        if root.is_symlink() or not root.is_dir() or root.resolve() != root:
            raise RuntimeError("exact GitOps root is unsafe")
        marker = root / ".usl-gitops-commit"
        if (
            marker.is_symlink()
            or not marker.is_file()
            or marker.read_text(encoding="ascii").strip() != commit
        ):
            raise RuntimeError("exact GitOps commit marker differs")
        working = root / canonical["working_directory"]
        files = [working / item for item in canonical["compose_files"]]
        environment = Path(canonical["environment_file"])
        identity = {
            "project": target.project,
            "working_directory": str(working),
            "environment_file": str(environment),
            "compose_files": [str(item) for item in files],
            "profiles": target.value["compose"]["profiles"],
            "anchor_service": anchor,
            "gitops_commit": commit,
        }
        approved_root = root.resolve()
        for path in (working, *files):
            try:
                resolved = path.resolve(strict=True)
            except OSError as error:
                raise RuntimeError(f"exact GitOps Compose path is unavailable: {path}") from error
            if resolved != path or (resolved != approved_root and approved_root not in resolved.parents):
                raise RuntimeError(f"exact GitOps Compose path is unsafe: {path}")
        if not environment.is_file() or environment.is_symlink():
            raise RuntimeError("exact Compose environment file is unavailable")
        services = set(
            runner.run(compose_command(identity, ["config", "--services"])).stdout.splitlines(),
        )
        if not set(target.value["services"].values()).issubset(services):
            raise RuntimeError("exact GitOps Compose service identity differs")
        return identity
    if current_identity.get("anchor_service", anchor) == anchor:
        return _base_compose_identity(target, current_identity)
    adoption = target.value["compose"].get("adoption")
    if adoption is None:
        raise RuntimeError("legacy runtime has no candidate Compose contract")
    candidate = adoption["candidate"]
    identity = {
        "project": target.project,
        "working_directory": candidate["working_directory"],
        "environment_file": candidate["environment_file"],
        "compose_files": list(candidate["compose_files"]),
        "profiles": target.value["compose"]["profiles"],
        "anchor_service": anchor,
    }
    if runner.run(["test", "-d", identity["working_directory"]], check=False).returncode:
        raise RuntimeError("canonical Compose working directory is missing")
    paths = [
        identity["working_directory"],
        *identity["compose_files"],
        identity["environment_file"],
    ]
    for path in paths:
        if path != identity["working_directory"] and runner.run(
            ["test", "-f", path], check=False,
        ).returncode:
            raise RuntimeError(f"canonical Compose identity file is missing: {path}")
        resolved = runner.run(["readlink", "-f", "--", path]).stdout.strip()
        if resolved != path:
            raise RuntimeError(f"canonical Compose identity path is not direct: {path}")
    services = set(
        runner.run(compose_command(identity, ["config", "--services"])).stdout.splitlines(),
    )
    legacy_anchor = adoption["legacy_anchor_service"]
    expected = set(target.value["services"].values())
    if not expected.issubset(services) or legacy_anchor in services:
        raise RuntimeError("canonical Compose service identity differs")
    return identity


def _cleanup_adoption_candidate_anchor(target, runner, current_identity: dict) -> None:
    """Remove only a failed first-adoption anchor so the v2 runtime stays retryable."""
    anchor = target.value["compose"]["anchor_service"]
    if current_identity.get("anchor_service", anchor) == anchor:
        return
    candidates = runner.run(
        [
            "docker", "ps", "-a",
            "--filter", f"label=com.docker.compose.project={target.project}",
            "--filter", f"label=com.docker.compose.service={anchor}",
            "--format", "{{.ID}}",
        ],
    ).stdout.splitlines()
    for container in candidates:
        labels = json.loads(
            runner.run(
                [
                    "docker", "inspect", container,
                    "--format", "{{json .Config.Labels}}",
                ],
            ).stdout,
        )
        if (
            labels.get("com.docker.compose.project") != target.project
            or labels.get("com.docker.compose.service") != anchor
        ):
            raise RuntimeError("failed candidate anchor ownership differs")
        runner.run(["docker", "rm", "--force", container])


def _activate_generation(
    target,
    runner,
    current_identity: dict,
    generation_identity: dict,
) -> None:
    """Replace the current cohort while retaining its exact rollback identity."""
    try:
        runner.run(
            compose_command(
                current_identity,
                ["stop", "--timeout", "60", *_compose_services(target, current_identity)],
            ),
        )
        runner.run(
            compose_command(generation_identity, ["up", "--detach", "--wait"]),
        )
    except Exception as error:
        cleanup_error = None
        try:
            _cleanup_adoption_candidate_anchor(target, runner, current_identity)
        except Exception as cleanup:
            cleanup_error = cleanup
        _rollback_after_failure(runner, current_identity, error)
        if cleanup_error is not None:
            raise RuntimeError(
                f"activation failed ({error}); legacy rollback completed but candidate cleanup failed "
                f"({cleanup_error})",
            ) from error
        raise


def _active_generation_identity(target, runner, current: dict) -> dict:
    """Resolve the recorded active generation even when the anchor is still legacy."""
    active = current["active_state"]
    if active is None:
        return current["compose"]
    identity = _base_compose_identity(target, current["compose"])
    generation = active["generation"]
    generation_root = f"{target.value['state_directory']}/generations/{generation}"
    release_manifest = f"{generation_root}/usl-release.json"
    if active["release_manifest"] != release_manifest:
        raise RuntimeError("active release manifest path is invalid")
    overlay = f"{generation_root}/compose.generation.json"
    required = [release_manifest, overlay]
    if target.value["compose"]["resource_overlay"] is not None:
        resource = f"{generation_root}/compose.resources.json"
        required.append(resource)
        identity["compose_files"].append(resource)
    identity["compose_files"].append(overlay)
    for path in required:
        if runner.run(["test", "-f", path], check=False).returncode:
            raise RuntimeError(f"active generation file is missing: {path}")
    return identity


def _previous_generation_identity(target, runner, current: dict) -> tuple[dict, str | None]:
    """Resolve only the one rollback generation recorded by active state."""
    identity = _base_compose_identity(target, current["compose"])
    active = current["active_state"]
    if active is None:
        return identity, None
    previous = active.get("previous")
    if not isinstance(previous, dict) or set(previous) != {
        "generation", "volumes", "network", "release_manifest", "snapshot",
    }:
        raise RuntimeError("rollback generation state is incomplete")
    if previous["release_manifest"] is None:
        if previous["network"] is not None or previous["snapshot"] is not None:
            raise RuntimeError("adopted rollback state is inconsistent")
        return identity, None
    generation = previous["generation"]
    if not isinstance(generation, str) or not generation.startswith("g") or len(generation) > 32:
        raise RuntimeError("rollback generation name is invalid")
    volumes = previous["volumes"]
    if (
        not isinstance(volumes, dict)
        or set(volumes) != set(target.value["volumes"])
        or not all(isinstance(value, str) and value for value in volumes.values())
    ):
        raise RuntimeError("rollback generation volume perimeter differs")
    if not isinstance(previous["network"], str) or not previous["network"]:
        raise RuntimeError("rollback generation network is invalid")
    if not re.fullmatch(r"[0-9a-f]{64}", str(previous["snapshot"])):
        raise RuntimeError("rollback generation snapshot is invalid")
    generation_root = f"{target.value['state_directory']}/generations/{generation}"
    release_manifest = f"{generation_root}/usl-release.json"
    if previous["release_manifest"] != release_manifest:
        raise RuntimeError("rollback release manifest path is invalid")
    overlay = f"{generation_root}/compose.generation.json"
    required = [release_manifest, overlay]
    if target.value["compose"]["resource_overlay"] is not None:
        resource = f"{generation_root}/compose.resources.json"
        required.append(resource)
        identity["compose_files"].append(resource)
    identity["compose_files"].append(overlay)
    for path in required:
        if runner.run(["test", "-f", path], check=False).returncode:
            raise RuntimeError(f"rollback generation file is missing: {path}")
    adoption = target.value["compose"].get("adoption")
    if adoption is not None:
        try:
            previous_release = json.loads(runner.run(["cat", release_manifest]).stdout)
        except json.JSONDecodeError as error:
            raise RuntimeError("rollback release manifest is invalid") from error
        if previous_release.get("schema") != "usl-release/v3":
            raise RuntimeError("automatic rollback to the preserved legacy v2 topology is disabled")
        try:
            validate_release(previous_release)
        except ReleaseManifestError as error:
            raise RuntimeError("rollback release manifest is invalid") from error
    state = _active_generation_state(
        target,
        generation,
        volumes,
        previous["network"],
        previous["snapshot"],
        release_manifest,
        {},
    )
    return identity, state


def _abort_to_previous_generation(
    target,
    runner,
    targets: Path,
    *,
    attempt: str | None = None,
) -> dict:
    """Restore and prove the untouched pre-reopen runtime generation."""
    marker = f"{Path(target.value['state_directory']).parents[1]}/gateway/{target.name}/maintenance"
    if runner.run(["test", "-f", marker], check=False).returncode:
        raise RuntimeError("runtime rollback is allowed only while the gateway is in maintenance")
    current = inspect_runtime(target, runner)
    generation = current.get("generation")
    claim = None
    if attempt is not None:
        if not isinstance(attempt, str) or not RELEASE_ATTEMPT.fullmatch(attempt):
            raise RuntimeError("release abort attempt identity is invalid")
        claim_path = f"{target.value['state_directory']}/attempts/{attempt}/claim.json"
        try:
            claim_value = json.loads(_read_path(target, runner, Path(claim_path)))
            claim = _release_attempt_claim(
                claim_value,
                target=target,
                attempt=attempt,
                release=str(claim_value.get("candidate_release", "")),
            )
        except json.JSONDecodeError as error:
            raise RuntimeError("release abort attempt claim is invalid") from error
        if generation == claim["baseline_generation"]:
            health = _gate(health_command, target, targets)
            smoke = _gate(smoke_command, target, targets)
            return {
                "schema": "usl-release-abort/v1",
                "target": target.name,
                "attempt": attempt,
                "generation": generation or "adopted",
                "health": health,
                "smoke": smoke,
                "status": "already-rolled-back",
            }
        if generation != claim["generation"]:
            raise RuntimeError("release abort runtime is outside the claimed attempt")
        active = current.get("active_state") or {}
        if (
            active.get("snapshot") != claim["snapshot"]
            or active.get("release_manifest")
            != f"{target.value['state_directory']}/generations/{generation}/usl-release.json"
        ):
            raise RuntimeError("release abort candidate identity differs from its claim")
    if isinstance(generation, str):
        generation_root = f"{target.value['state_directory']}/generations/{generation}"
        for boundary in ("activation-started.json", "admission.json"):
            if runner.run(
                ["test", "-f", f"{generation_root}/{boundary}"],
                check=False,
            ).returncode == 0:
                raise RuntimeError(
                    "release crossed the forward-only boundary and cannot be rolled back automatically",
                )
    previous_identity, previous_state = _previous_generation_identity(target, runner, current)
    cohort_services = sorted(set(target.value["services"].values()))
    runner.run(
        compose_command(current["compose"], ["stop", "--timeout", "60", *cohort_services]),
        check=False,
    )
    rollback = runner.run(
        compose_command(previous_identity, ["up", "--detach", "--wait"]),
        check=False,
    )
    if rollback.returncode:
        detail = (rollback.stderr or rollback.stdout).strip()
        raise RuntimeError(f"previous generation did not start: {detail}")
    active_path = f"{target.value['state_directory']}/active.json"
    if previous_state is None:
        runner.run(["rm", "-f", active_path])
        generation = "adopted"
    else:
        _write_remote(target, runner, active_path, previous_state)
        generation = json.loads(previous_state)["generation"]
    health = _gate(health_command, target, targets)
    smoke = _gate(smoke_command, target, targets)
    return {
        "schema": "usl-release-abort/v1",
        "target": target.name,
        "attempt": attempt,
        "generation": generation,
        "health": health,
        "smoke": smoke,
        "status": "rolled-back",
    }


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


def _apply_generation_cron_policy(target, runner, release, network, volumes) -> dict:
    """Converge the isolated candidate before any production worker can run."""
    cron_target = target.value["cron_policy"]
    mode = cron_target["mode"]
    if mode == "unmanaged":
        return {"schema": "usl-cron-policy-application/v1", "status": "unmanaged"}
    policy = parse_cron_policy(_read_path(target, runner, Path(cron_target["path"])))
    program = render_odoo_apply_script(policy, mode=mode, gates=cron_target["gates"])
    database = target.value["databases"]["odoo"]
    result = runner.run(
        [
            "docker", "run", "--rm", "--interactive", "--network", network,
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
            "odoo", "shell", "--config=/etc/odoo/odoo.conf",
            f"--database={database['name']}", "--no-http", "--max-cron-threads=0",
        ],
        input_text=program,
    )
    prefix = "USL_CRON_POLICY_RESULT="
    for line in reversed(result.stdout.splitlines()):
        if line.startswith(prefix):
            try:
                applied = json.loads(line.removeprefix(prefix))
            except json.JSONDecodeError as error:
                raise RuntimeError("candidate cron policy returned invalid evidence") from error
            if applied.get("status") != "applied":
                raise RuntimeError("candidate cron policy did not converge")
            return applied
    raise RuntimeError("candidate cron policy returned no evidence")


def _notify_release(target, runner, release_id: str) -> dict:
    """Post one persistent OdooBot note in the distribution update channel."""
    if target.value["environment"] != "production":
        raise RuntimeError("release notifications are production-only")
    if not re.fullmatch(r"[0-9a-f]{64}", release_id):
        raise RuntimeError("release notification identity is invalid")
    runtime = inspect_runtime(target, runner)
    release, _release_sha, _release_raw = _release(target, runner, None)
    if release.get("identity") != release_id:
        raise RuntimeError("release notification does not match the active release")
    active = runtime.get("active_state")
    if active is not None and active.get("release_manifest"):
        active_release = json.loads(_read_path(target, runner, active["release_manifest"]))
        if active_release.get("identity") != release_id:
            raise RuntimeError("active generation differs from the release notification")
    network = active["network"] if active else target.value["compose"]["default_network"]
    volumes = runtime["volumes"]
    database = target.value["databases"]["odoo"]
    program = """
import json
from markupsafe import Markup, escape
from odoo import fields
release_id = env.context.get("usl_release_notification_id")
notes = json.loads(env.context.get("usl_release_notification_notes"))
evidence_url = env.context.get("usl_release_notification_evidence_url")
channel = env.ref("usl_home.channel_distribution_updates").sudo()
odoobot = env.ref("base.partner_root").sudo()
external_message_id = "<usl-release-%s@unstaticlabs.com>" % release_id
message = env["mail.message"].sudo().search([
    ("model", "=", channel._name),
    ("res_id", "=", channel.id),
    ("message_id", "=", external_message_id),
], limit=1)
status = "already_posted"
if not message:
    items = Markup("").join(
        Markup("<li>%s</li>") % escape(item)
        for item in notes["changes"]
    )
    action = Markup("")
    if notes.get("action_required"):
        action = Markup("<p><strong>Action required:</strong> %s</p>") % escape(
            notes["action_required"]
        )
    body = (
        Markup("<h3>%s</h3><p>%s</p><ul>%s</ul>%s")
        % (escape(notes["title"]), escape(notes["summary"]), items, action)
        + Markup(
            "<p>Deployed %s · release <code>%s</code> · "
            '<a href="%s">technical evidence</a></p>'
        )
        % (
            escape(fields.Datetime.now()),
            escape(release_id[:12]),
            escape(evidence_url),
        )
    )
    message = channel.message_post(
        author_id=odoobot.id,
        body=body,
        email_add_signature=False,
        message_id=external_message_id,
        message_type="comment",
        subtype_xmlid="mail.mt_comment",
    )
    status = "posted"
print("USL_RELEASE_NOTIFICATION_RESULT=" + json.dumps({
    "channel": "usl_home.channel_distribution_updates",
    "message_id": message.id,
    "release": release_id,
    "status": status,
}, sort_keys=True))
"""
    result = runner.run(
        [
            "docker", "run", "--rm", "--interactive", "--network", network,
            "--env-file", target.value["secrets"]["env_file"],
            "--env", f"ODOO_DB_HOST={database['service']}",
            "--env", "ODOO_DB_PORT=5432",
            "--env", f"ODOO_DB_USER={database['user']}",
            "--env", f"ODOO_DB_NAME={database['name']}",
            "--env", "ODOO_MAX_CRON_THREADS=0",
            "--env", "USL_EINVOICE_LIVE_ENABLED=0",
            "--env", "USL_EREPORTING_LIVE_ENABLED=0",
            "--volume", f"{volumes['odoo_filestore']['name']}:/var/lib/odoo",
            release["components"]["distribution"]["digest_reference"],
            "odoo", "shell", "--config=/etc/odoo/odoo.conf",
            f"--database={database['name']}", "--no-http", "--max-cron-threads=0",
        ],
        input_text=(
            "env = env(context=dict(env.context, usl_release_notification_id="
            + repr(release_id)
            + ", usl_release_notification_notes="
            + repr(json.dumps(release["release_notes"], sort_keys=True))
            + ", usl_release_notification_evidence_url="
            + repr(release["build"]["workflow_url"])
            + "))\n"
            + program
        ),
    )
    prefix = "USL_RELEASE_NOTIFICATION_RESULT="
    for line in reversed(result.stdout.splitlines()):
        if line.startswith(prefix):
            try:
                value = json.loads(line.removeprefix(prefix))
            except json.JSONDecodeError as error:
                raise RuntimeError("release notification returned invalid evidence") from error
            if (
                value.get("status") not in {"posted", "already_posted"}
                or value.get("release") != release_id
                or value.get("channel") != "usl_home.channel_distribution_updates"
                or not isinstance(value.get("message_id"), int)
            ):
                raise RuntimeError("release notification evidence differs")
            return value
    raise RuntimeError("release notification returned no evidence")


def _restore_unlocked(arguments: argparse.Namespace) -> int:
    restore_started = time.monotonic()
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
    attempt = getattr(arguments, "attempt_id", None)
    if attempt is not None:
        attempt = _release_attempt(attempt, release.get("identity", ""))
    maintenance = getattr(arguments, "maintenance_receipt", None)
    prepared_before_downtime = getattr(arguments, "prepare_receipt", None)
    if (
        target.value["environment"] == "production"
        and getattr(arguments, "upgrade_plan", None)
        and (attempt is None or maintenance is None or prepared_before_downtime is None)
    ):
        raise RuntimeError(
            "production candidate restore requires attempt, preparation, and maintenance evidence",
        )
    if attempt is not None:
        prepared_before_downtime = _prepare_receipt(
            prepared_before_downtime,
            target=target.name,
            attempt=attempt,
            release=release["identity"],
        )
        maintenance = _maintenance_receipt(
            maintenance,
            target=target.name,
            attempt=attempt,
            required_endpoints=_required_maintenance_endpoints(target),
        )
    upgrade_plan = None
    signed_plan_evidence = None
    cron_policy_application = None
    if getattr(arguments, "upgrade_plan", None):
        try:
            plan_value = json.loads(_read_path(target, target_runner, arguments.upgrade_plan))
            upgrade_plan = _validated_release_upgrade_plan(target, plan_value, release)
            if target.value["environment"] == "production":
                signed_plan_evidence = plan_value
        except (json.JSONDecodeError, ModuleReleaseError, PlanEvidenceError) as error:
            raise RuntimeError("upgrade plan is invalid") from error
    tool_image = release["components"]["backup-tool"]["digest_reference"]
    generation = arguments.generation or f"g{datetime.now(UTC):%Y%m%dt%H%M}-{arguments.snapshot[:8]}"
    if len(generation) > 32 or not generation.startswith("g"):
        raise RuntimeError("generation name is invalid")
    identity = current["compose"]
    candidate_identity = _candidate_compose_identity(target, target_runner, identity)
    images = _runtime_images(target_runner, candidate_identity)
    phase_started = time.monotonic()
    _record_event(target, target_runner, generation, "restore", "image-preparation", "started")
    preparation = _prepare_release_candidate(
        target,
        target_runner,
        release,
        current,
        upgrade_plan_sha256=(upgrade_plan or {}).get("sha256"),
    )
    if prepared_before_downtime is not None:
        _require_same_preparation(preparation, prepared_before_downtime)
    capacity_before_pull = preparation["capacity"]["before_pull"]
    capacity_after_pull = preparation["capacity"]["after_pull"]
    preparation_seconds = round(time.monotonic() - phase_started, 3)
    _record_event(
        target,
        target_runner,
        generation,
        "restore",
        "image-preparation",
        "completed",
        duration_seconds=preparation_seconds,
        **capacity_after_pull,
    )
    phase_started = time.monotonic()
    _record_event(target, target_runner, generation, "restore", "materialization", "started")
    generation_root = f"{target.value['state_directory']}/generations/{generation}"
    target_runner.run(["install", "-d", "-m", "0700", generation_root])
    volumes, network = _create_generation_resources(target, target_runner, generation)
    with _materialization_cleanup(target, target_runner, generation) as database_containers:
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
        if target.value["environment"] == "production":
            _run_production_boundary_script(
                target,
                target_runner,
                release,
                network,
                volumes,
                "production_quarantine.py",
                release["identity"],
                "USL_PRODUCTION_QUARANTINE=",
            )
        else:
            _neutralize_generation(target, target_runner, release, generation, network, volumes)
        cron_policy_application = _apply_generation_cron_policy(
            target,
            target_runner,
            release,
            network,
            volumes,
        )
        _prepare_generation_volume_ownership(target_runner, release, volumes)
    capacity_before_activation = _require_restore_capacity(target, target_runner, "activation")
    materialization_seconds = round(time.monotonic() - phase_started, 3)
    _record_event(
        target,
        target_runner,
        generation,
        "restore",
        "materialization",
        "completed",
        duration_seconds=materialization_seconds,
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
            set(images) | {"odoo-upgrade"},
            target.value["ingress"],
            sign_secret_root=(
                f"{generation_root}/sign-secrets"
                if target.value["environment"] == "production"
                else None
            ),
            service_names=target.value["services"],
            quarantine=target.value["environment"] == "production",
        ),
    )
    compose_files = list(candidate_identity["compose_files"])
    if resource_path is not None:
        compose_files.append(resource_path)
    compose_files.append(overlay)
    generation_identity = {
        **candidate_identity,
        "anchor_service": target.value["compose"]["anchor_service"],
        "compose_files": compose_files,
    }
    previous = {
        "generation": current["generation"],
        "volumes": {role: item["name"] for role, item in current["volumes"].items()},
        "network": (current["active_state"] or {}).get("network"),
        "release_manifest": (current["active_state"] or {}).get("release_manifest"),
        "snapshot": (current["active_state"] or {}).get("snapshot"),
    }
    phase_started = time.monotonic()
    cutover_started = phase_started
    _record_event(target, target_runner, generation, "restore", "activation", "started")
    # A stable ingress gateway is intentionally outside this service
    # perimeter. It must keep serving the maintenance response while the
    # stateful cohort is replaced.
    _activate_generation(target, target_runner, identity, generation_identity)
    activation_seconds = round(time.monotonic() - phase_started, 3)
    _record_event(
        target,
        target_runner,
        generation,
        "restore",
        "activation",
        "completed",
        duration_seconds=activation_seconds,
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
            staging_evidence = signed_plan_evidence.get(
                "staging_evidence", signed_plan_evidence,
            )
            expected_release_sha256 = staging_evidence["staging"][
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
        production_activation = None
        if target.value["environment"] == "production" and attempt is None:
            production_activation = _run_production_boundary_script(
                target,
                target_runner,
                release,
                network,
                volumes,
                "production_activate.py",
                release["identity"],
                "USL_PRODUCTION_ACTIVATION=",
            )
            production_activation["side_effect_admission"] = _admit_production_side_effects(
                target, target_runner, release, network, volumes,
            )
            _write_remote(
                target,
                target_runner,
                overlay,
                _generation_overlay(
                    volumes,
                    release,
                    set(images) | {"odoo-upgrade"},
                    target.value["ingress"],
                    sign_secret_root=f"{generation_root}/sign-secrets",
                    service_names=target.value["services"],
                ),
            )
            target_runner.run(
                compose_command(
                    generation_identity,
                    [
                        "up", "--detach", "--wait", "--force-recreate",
                        target.value["services"]["odoo"],
                        target.value["services"]["paperless"],
                    ],
                ),
            )
            health = _gate(health_command, target, arguments.targets)
            smoke = _gate(smoke_command, target, arguments.targets)
    except Exception as error:
        target_runner.run(compose_command(generation_identity, ["stop", "--timeout", "60"]), check=False)
        cleanup_error = None
        try:
            _cleanup_adoption_candidate_anchor(target, target_runner, identity)
        except Exception as cleanup:
            cleanup_error = cleanup
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
        if cleanup_error is not None:
            raise RuntimeError(
                f"validation failed ({error}); legacy rollback completed but candidate cleanup failed "
                f"({cleanup_error})",
            ) from error
        raise
    validation_seconds = round(time.monotonic() - phase_started, 3)
    _record_event(
        target,
        target_runner,
        generation,
        "restore",
        "validation",
        "completed",
        duration_seconds=validation_seconds,
    )
    admission_receipt = None
    quarantine_receipt = None
    if attempt is not None:
        operation_bundle_sha256 = getattr(arguments, "operation_bundle_sha256", None)
        if not isinstance(operation_bundle_sha256, str):
            raise RuntimeError("release operation bundle identity is missing")
        production = target.value["environment"] == "production"
        receipt_body = _release_boundary_receipt(
            schema=(
                "usl-release-quarantine/v1"
                if production else "usl-release-admission/v1"
            ),
            status="quarantined" if production else "admitted",
            target=target,
            attempt=attempt,
            release=release["identity"],
            snapshot=arguments.snapshot,
            generation=generation,
            health=health,
            smoke=smoke,
            control_validation=control_validation,
            operation_bundle_sha256=operation_bundle_sha256,
        )
        receipt_path = f"{generation_root}/{'quarantine' if production else 'admission'}.json"
        _write_remote(
            target,
            target_runner,
            receipt_path,
            json.dumps(receipt_body, indent=2, sort_keys=True) + "\n",
            "0444",
        )
        if production:
            quarantine_receipt = {"path": receipt_path, **receipt_body}
        else:
            admission_receipt = {"path": receipt_path, **receipt_body}
    result = {
        "schema": "usl-restore-run/v1",
        "source": source.name,
        "target": target.name,
        "snapshot": arguments.snapshot,
        "generation": generation,
        "materialize": materialize_state,
        "health": health,
        "smoke": smoke,
        "cron_policy_application": cron_policy_application,
        "control_validation": control_validation,
        "production_activation": production_activation,
        "capacity": {
            "before_pull": capacity_before_pull,
            "after_pull": capacity_after_pull,
            "before_activation": capacity_before_activation,
        },
        "preparation": preparation,
        "attempt": attempt,
        "admission": admission_receipt,
        "quarantine": quarantine_receipt,
        "performance": {
            "preparation_seconds": preparation_seconds,
            "materialization_seconds": materialization_seconds,
            "activation_seconds": activation_seconds,
            "validation_seconds": validation_seconds,
            "candidate_cutover_seconds": round(time.monotonic() - cutover_started, 3),
            "total_seconds": round(time.monotonic() - restore_started, 3),
            "maintenance_seconds": None,
            "maintenance_elapsed_at_admission_seconds": (
                round((
                    datetime.now(UTC)
                    - datetime.fromisoformat(maintenance["observed_at"].replace("Z", "+00:00"))
                ).total_seconds(), 3)
                if maintenance is not None else None
            ),
            "maintenance_interval_complete": False,
            "maintenance_interval_owned_by": "release-controller",
        },
        "maintenance": maintenance,
        "prepared_before_downtime": prepared_before_downtime,
        "status": (
            "quarantined"
            if target.value["environment"] == "production" else "activated"
        ),
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


def _cleanup_workspaces(target, runner, protected_generations: set[str]) -> list[str]:
    state_root = target.value["state_directory"]
    generations_root = f"{state_root}/generations"
    if runner.run(["test", "-L", state_root], check=False).returncode == 0:
        raise RuntimeError("cleanup state root must not be a symlink")
    root_probe = runner.run(
        ["find", generations_root, "-mindepth", "0", "-maxdepth", "0", "-printf", "%y\\n"],
        check=False,
    )
    if root_probe.returncode:
        absent = runner.run(["test", "!", "-e", generations_root], check=False)
        not_symlink = runner.run(["test", "!", "-L", generations_root], check=False)
        if absent.returncode == 0 and not_symlink.returncode == 0:
            return []
        raise RuntimeError("cleanup generations root cannot be inspected")
    if root_probe.stdout.strip() != "d":
        raise RuntimeError("cleanup generations root is not a directory")
    generations = runner.run(
        [
            "find", generations_root, "-mindepth", "1", "-maxdepth", "1",
            "-printf", "%f\\t%y\\n",
        ],
    )
    known_generations: set[str] = set()
    for line in generations.stdout.splitlines():
        try:
            generation, kind = line.split("\t", 1)
        except ValueError as error:
            raise RuntimeError("cleanup generation inventory is invalid") from error
        if not GENERATION_NAME.fullmatch(generation) or kind != "d":
            raise RuntimeError(f"cleanup generation directory is invalid: {generation}")
        known_generations.add(generation)
    workspaces = runner.run(
        [
            "find", generations_root, "-mindepth", "2", "-maxdepth", "2",
            "-name", "work", "-printf", "%P\\t%y\\n",
        ],
    )
    candidates = []
    for line in workspaces.stdout.splitlines():
        try:
            relative, kind = line.split("\t", 1)
            generation, leaf = relative.split("/", 1)
        except ValueError as error:
            raise RuntimeError("cleanup workspace inventory is invalid") from error
        if leaf != "work" or generation not in known_generations or kind != "d":
            raise RuntimeError(f"cleanup workspace is invalid: {relative}")
        if generation not in protected_generations:
            candidates.append(f"{generations_root}/{generation}/work")
    return sorted(candidates)


def _cleanup_inventory(target, runner, current: dict) -> dict:
    active = {item["name"] for item in current["volumes"].values()}
    state_path = f"{target.value['state_directory']}/active.json"
    state_result = runner.run(["cat", state_path], check=False)
    previous: set[str] = set()
    protected_networks: set[str] = set()
    protected_generations = {current["generation"]} if current.get("generation") else set()
    if state_result.returncode == 0:
        try:
            state = json.loads(state_result.stdout)
        except json.JSONDecodeError as error:
            raise RuntimeError("cleanup active state is invalid") from error
        previous = set((state.get("previous") or {}).get("volumes", {}).values())
        if state.get("network"):
            protected_networks.add(state["network"])
        previous_network = (state.get("previous") or {}).get("network")
        if previous_network:
            protected_networks.add(previous_network)
        previous_generation = (state.get("previous") or {}).get("generation")
        if previous_generation:
            protected_generations.add(previous_generation)
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
    return {
        "protected_volumes": sorted(active | previous),
        "protected_networks": sorted(protected_networks),
        "protected_generations": sorted(protected_generations),
        "delete_volumes": candidates,
        "delete_networks": network_candidates,
        "delete_workspaces": _cleanup_workspaces(target, runner, protected_generations),
    }


def _validated_cleanup_volume(target, runner, name: str, docker_root: str) -> dict:
    volume = _volume_inspect(runner, name)
    labels = volume.get("Labels")
    if not isinstance(labels, dict):
        raise RuntimeError(f"cleanup candidate labels are invalid: {name}")
    generation = labels.get("com.unstaticlabs.runtime.generation")
    role = labels.get("com.unstaticlabs.runtime.role")
    if (
        volume.get("Name") != name
        or volume.get("Driver") != "local"
        or labels.get("com.unstaticlabs.runtime.project") != target.project
        or labels.get("com.unstaticlabs.runtime.target") != target.name
        or not isinstance(generation, str)
        or not GENERATION_NAME.fullmatch(generation)
        or role not in target.value["volumes"]
    ):
        raise RuntimeError(f"cleanup candidate identity differs: {name}")
    expected_name = generation_volume_names(target, generation)[role]
    if name != expected_name:
        raise RuntimeError(f"cleanup candidate name differs: {name}")
    tier = target.value["volumes"][role]["tier"]
    storage_label = labels.get("com.unstaticlabs.runtime.storage-tier")
    options = volume.get("Options")
    mountpoint = volume.get("Mountpoint")
    managed_mountpoint = f"{docker_root}/volumes/{name}/_data"
    if tier == "database":
        database_path = generation_volume_path(target, generation, role)
        bind_options = {"device": database_path, "o": "bind", "type": "none"}
        if options == bind_options:
            if storage_label != tier:
                raise RuntimeError(f"cleanup database volume labels differ: {name}")
            path_probe = runner.run(
                ["find", database_path, "-mindepth", "0", "-maxdepth", "0", "-printf", "%y\\n"],
                check=False,
            )
            if path_probe.returncode or path_probe.stdout.strip() != "d":
                raise RuntimeError(f"cleanup database volume path is invalid: {name}")
            return {"name": name, "database_path": database_path}
        if options not in (None, {}) or mountpoint != managed_mountpoint or storage_label is not None:
            raise RuntimeError(f"cleanup legacy database volume differs: {name}")
        return {"name": name, "database_path": None}
    if storage_label not in (None, tier):
        raise RuntimeError(f"cleanup volume labels differ: {name}")
    if options not in (None, {}) or mountpoint != managed_mountpoint:
        raise RuntimeError(f"cleanup managed volume differs: {name}")
    return {"name": name, "database_path": None}


def _validated_cleanup_network(target, runner, name: str) -> str:
    raw = runner.run(
        ["docker", "network", "inspect", name, "--format", "{{json .}}"],
    ).stdout
    try:
        network = json.loads(raw)
    except json.JSONDecodeError as error:
        raise RuntimeError(f"network inspection is invalid: {name}") from error
    if not isinstance(network, dict) or not isinstance(network.get("Labels"), dict):
        raise RuntimeError(f"network inspection is invalid: {name}")
    labels = network["Labels"]
    generation = labels.get("com.unstaticlabs.runtime.generation")
    expected_name = (
        f"{target.project}-{generation}-recovery"
        if isinstance(generation, str) and GENERATION_NAME.fullmatch(generation)
        else None
    )
    if (
        network.get("Name") != name
        or network.get("Driver") != "bridge"
        or labels.get("com.unstaticlabs.runtime.project") != target.project
        or labels.get("com.unstaticlabs.runtime.target") != target.name
        or name != expected_name
    ):
        raise RuntimeError(f"cleanup network identity differs: {name}")
    return name


def _validated_cleanup_resources(target, runner, inventory: dict) -> tuple[list[dict], list[str]]:
    docker_root = runner.run(
        ["docker", "info", "--format", "{{.DockerRootDir}}"],
    ).stdout.strip().rstrip("/")
    if not docker_root.startswith("/") or docker_root == "/" or str(Path(docker_root)) != docker_root:
        raise RuntimeError("Docker root directory is invalid")
    volumes = [
        _validated_cleanup_volume(target, runner, name, docker_root)
        for name in inventory["delete_volumes"]
    ]
    networks = [
        _validated_cleanup_network(target, runner, name)
        for name in inventory["delete_networks"]
    ]
    generations_root = f"{target.value['state_directory']}/generations"
    for workspace in inventory["delete_workspaces"]:
        relative = workspace.removeprefix(generations_root + "/")
        parts = relative.split("/")
        if (
            workspace == relative
            or len(parts) != 2
            or not GENERATION_NAME.fullmatch(parts[0])
            or parts[1] != "work"
        ):
            raise RuntimeError(f"cleanup workspace path is invalid: {workspace}")
        probe = runner.run(
            ["find", workspace, "-mindepth", "0", "-maxdepth", "0", "-printf", "%y\\n"],
            check=False,
        )
        if probe.returncode or probe.stdout.strip() != "d":
            raise RuntimeError(f"cleanup workspace became invalid: {workspace}")
    return volumes, networks


def _delete_cleanup_resources(
    runner, volumes: list[dict], networks: list[str], workspaces: list[str],
) -> None:
    for item in volumes:
        runner.run(["docker", "volume", "rm", item["name"]])
        if item["database_path"] is not None:
            runner.run(
                ["find", item["database_path"], "-xdev", "-mindepth", "1", "-delete"],
            )
            runner.run(["rmdir", "--", item["database_path"]])
    for name in networks:
        runner.run(["docker", "network", "rm", name])
    for workspace in workspaces:
        runner.run(["rm", "-rf", "--", workspace])


def _cleanup_plan(target, inventory: dict, retention_plan: dict | None) -> dict:
    return {
        "schema": "usl-cleanup-plan/v1",
        "target": target.name,
        **inventory,
        "backup_retention": retention_plan,
    }


def cleanup_command(arguments: argparse.Namespace) -> int:
    target = load_target(arguments.target, arguments.targets)
    runner = target.runner()
    retention_image = None
    if arguments.action == "apply":
        if arguments.confirm != target.name:
            raise RuntimeError("cleanup apply requires exact --confirm")
        run_id = f"cleanup-{datetime.now(UTC):%Y%m%dt%H%M%S}"
        with runtime_lock(target, runner, "cleanup", run_id):
            current = inspect_runtime(target, runner)
            inventory = _cleanup_inventory(target, runner, current)
            retention_plan = None
            if target.value["environment"] == "production" and not getattr(
                arguments, "runtime_only", False,
            ):
                release, _release_sha, _release_raw = _release(target, runner, None)
                _secret_file(target, runner)
                retention_image = release["components"]["backup-tool"]["digest_reference"]
                retention_plan = _run_cohort(
                    target, runner, retention_image, "retention-plan", [],
                    volumes=current["volumes"],
                )
            volumes, networks = _validated_cleanup_resources(target, runner, inventory)
            if any(item["database_path"] is not None for item in volumes):
                database_source, _available = _filesystem_capacity(
                    runner, target.value["storage"]["tiers"]["database"]["path"],
                )
                bulk_source, _available = _filesystem_capacity(
                    runner, target.value["storage"]["tiers"]["bulk"]["path"],
                )
                if database_source == bulk_source:
                    raise RuntimeError("cleanup database tier is not local NVMe")
            _delete_cleanup_resources(
                runner, volumes, networks, inventory["delete_workspaces"],
            )
            if retention_image is not None:
                retention_plan = _run_cohort(
                    target,
                    runner,
                    retention_image,
                    "retention-apply",
                    [],
                    volumes=current["volumes"],
                )
            plan = _cleanup_plan(target, inventory, retention_plan)
        plan["status"] = "applied"
    else:
        current = inspect_runtime(target, runner)
        inventory = _cleanup_inventory(target, runner, current)
        retention_plan = None
        if target.value["environment"] == "production" and not getattr(
            arguments, "runtime_only", False,
        ):
            release, _release_sha, _release_raw = _release(target, runner, None)
            _secret_file(target, runner)
            retention_image = release["components"]["backup-tool"]["digest_reference"]
            retention_plan = _run_cohort(
                target, runner, retention_image, "retention-plan", [],
                volumes=current["volumes"],
            )
        plan = _cleanup_plan(target, inventory, retention_plan)
        plan["status"] = "planned"
    print(json.dumps(plan, indent=None if arguments.json else 2, sort_keys=True))
    return 0


def _activate_quarantined_release(target, runner, arguments, release: dict) -> dict:
    """Cross the production side-effect boundary for one exact generation."""
    if target.value["environment"] != "production":
        raise RuntimeError("release activation is production-only")
    attempt = _release_attempt(arguments.attempt_id, release["identity"])
    if not arguments.quarantine_receipt or not arguments.maintenance_receipt:
        raise RuntimeError(
            "release activation requires quarantine and maintenance receipts",
        )
    runtime = inspect_runtime(target, runner)
    active = runtime.get("active_state")
    generation = runtime.get("generation")
    if not isinstance(active, dict) or not isinstance(generation, str):
        raise RuntimeError("production has no quarantined candidate generation")
    if active.get("snapshot") != arguments.snapshot:
        raise RuntimeError("production activation snapshot differs")
    active_release = validate_release(json.loads(
        _read_path(target, runner, Path(active.get("release_manifest", ""))),
    ))
    if active_release.get("identity") != release["identity"]:
        raise RuntimeError("production activation release differs")
    generation_root = f"{target.value['state_directory']}/generations/{generation}"
    attempt_path = f"{target.value['state_directory']}/attempts/{attempt}/claim.json"
    try:
        claim = _release_attempt_claim(
            json.loads(_read_path(target, runner, Path(attempt_path))),
            target=target,
            attempt=attempt,
            release=release["identity"],
        )
    except json.JSONDecodeError as error:
        raise RuntimeError("release attempt claim is invalid") from error
    quarantine_path = f"{generation_root}/quarantine.json"
    if str(arguments.quarantine_receipt) != quarantine_path:
        raise RuntimeError("production quarantine receipt path differs")
    quarantine = _validate_release_boundary_receipt(
        json.loads(_read_path(target, runner, arguments.quarantine_receipt)),
        schema="usl-release-quarantine/v1",
        status="quarantined",
        target=target,
        attempt=attempt,
        release=release["identity"],
    )
    _require_same_attempt_boundary(
        claim,
        quarantine,
        generation=generation,
        snapshot=arguments.snapshot,
    )
    maintenance = _maintenance_receipt(
        json.loads(_read_path(target, runner, arguments.maintenance_receipt)),
        target=target.name,
        attempt=attempt,
        required_endpoints=_required_maintenance_endpoints(target),
    )
    if datetime.fromisoformat(maintenance["observed_at"].replace("Z", "+00:00")) > datetime.fromisoformat(
        quarantine["quarantined_at"].replace("Z", "+00:00"),
    ):
        raise RuntimeError("production quarantine predates maintenance admission")

    admission_path = f"{generation_root}/admission.json"
    forward_path = f"{generation_root}/activation-started.json"
    existing = runner.run(["cat", admission_path], check=False)
    if existing.returncode == 0:
        admission = _validate_release_boundary_receipt(
            json.loads(existing.stdout),
            schema="usl-release-admission/v1",
            status="admitted",
            target=target,
            attempt=attempt,
            release=release["identity"],
        )
        _require_same_attempt_boundary(
            claim,
            admission,
            generation=generation,
            snapshot=arguments.snapshot,
        )
        try:
            forward = _validate_forward_only_receipt(
                json.loads(_read_path(target, runner, Path(forward_path))),
                target=target,
                attempt=attempt,
                release=release["identity"],
                snapshot=arguments.snapshot,
                generation=generation,
                operation_bundle_sha256=claim["operation_bundle_sha256"],
            )
        except json.JSONDecodeError as error:
            raise RuntimeError("release forward-only receipt is invalid") from error
        return {
            "schema": "usl-production-release-activation/v1",
            "target": target.name,
            "attempt": attempt,
            "release": release["identity"],
            "generation": generation,
            "forward_only": {"path": forward_path, **forward},
            "admission": {"path": admission_path, **admission},
            "status": "already-activated",
        }

    identity = runtime["compose"]
    overlay = f"{generation_root}/compose.generation.json"
    if overlay not in identity["compose_files"]:
        raise RuntimeError("production generation overlay is unavailable")
    volumes = {role: item["name"] for role, item in runtime["volumes"].items()}
    network = active.get("network")
    if not isinstance(network, str):
        raise RuntimeError("production candidate network is unavailable")
    side_effect_admission = _admit_production_side_effects(
        target, runner, release, network, volumes,
    )
    existing_forward = runner.run(["cat", forward_path], check=False)
    if existing_forward.returncode == 0:
        try:
            forward = _validate_forward_only_receipt(
                json.loads(existing_forward.stdout),
                target=target,
                attempt=attempt,
                release=release["identity"],
                snapshot=arguments.snapshot,
                generation=generation,
                operation_bundle_sha256=claim["operation_bundle_sha256"],
            )
        except json.JSONDecodeError as error:
            raise RuntimeError("release forward-only receipt is invalid") from error
    else:
        forward = _forward_only_receipt(
            target=target,
            attempt=attempt,
            release=release["identity"],
            snapshot=arguments.snapshot,
            generation=generation,
            operation_bundle_sha256=claim["operation_bundle_sha256"],
        )
        _write_remote(
            target,
            runner,
            forward_path,
            json.dumps(forward, indent=2, sort_keys=True) + "\n",
            "0444",
        )
    activation = _run_production_boundary_script(
        target,
        runner,
        release,
        network,
        volumes,
        "production_activate.py",
        release["identity"],
        "USL_PRODUCTION_ACTIVATION=",
    )
    activation["side_effect_admission"] = side_effect_admission
    images = _runtime_images(runner, identity)
    _write_remote(
        target,
        runner,
        overlay,
        _generation_overlay(
            volumes,
            release,
            set(images) | {"odoo-upgrade"},
            target.value["ingress"],
            sign_secret_root=f"{generation_root}/sign-secrets",
            service_names=target.value["services"],
        ),
    )
    runner.run(
        compose_command(
            identity,
            [
                "up", "--detach", "--wait", "--force-recreate",
                target.value["services"]["odoo"],
                target.value["services"]["paperless"],
            ],
        ),
    )
    health = _gate(health_command, target, arguments.targets)
    smoke = _gate(smoke_command, target, arguments.targets)
    receipt = {
        **{key: quarantine[key] for key in (
            "target", "attempt", "release", "snapshot", "generation",
            "control_validation_sha256", "operation_bundle_sha256",
        )},
        "schema": "usl-release-admission/v1",
        "health_sha256": hashlib.sha256(
            json.dumps(health, sort_keys=True, separators=(",", ":")).encode(),
        ).hexdigest(),
        "smoke_sha256": hashlib.sha256(
            json.dumps(smoke, sort_keys=True, separators=(",", ":")).encode(),
        ).hexdigest(),
        "admitted_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "status": "admitted",
    }
    receipt["sha256"] = hashlib.sha256(
        json.dumps(receipt, sort_keys=True, separators=(",", ":")).encode(),
    ).hexdigest()
    _write_remote(
        target, runner, admission_path,
        json.dumps(receipt, indent=2, sort_keys=True) + "\n", "0444",
    )
    return {
        "schema": "usl-production-release-activation/v1",
        "target": target.name,
        "attempt": attempt,
        "release": release["identity"],
        "generation": generation,
        "forward_only": {"path": forward_path, **forward},
        "production_activation": activation,
        "health": health,
        "smoke": smoke,
        "admission": {"path": admission_path, **receipt},
        "status": "activated",
    }


def release_command(arguments: argparse.Namespace) -> int:
    target = load_target(arguments.target, arguments.targets)
    runner = target.runner()
    state_path = f"{target.value['state_directory']}/release-state.json"
    if arguments.action == "prepare":
        if not arguments.candidate_release:
            raise RuntimeError("release prepare requires a candidate release")
        try:
            release = validate_release(json.loads(
                _read_path(target, runner, arguments.candidate_release),
            ))
        except (json.JSONDecodeError, ReleaseManifestError) as error:
            raise RuntimeError("candidate release manifest is invalid") from error
        if release.get("schema") != "usl-release/v3":
            raise RuntimeError("release prepare requires a v3 candidate")
        attempt = _release_attempt(arguments.attempt_id, release["identity"])
        if not arguments.upgrade_plan:
            raise RuntimeError("release prepare requires an exact upgrade plan")
        try:
            plan_value = json.loads(_read_path(target, runner, arguments.upgrade_plan))
            prepared_plan = _validated_release_upgrade_plan(target, plan_value, release)
        except (json.JSONDecodeError, ModuleReleaseError, PlanEvidenceError) as error:
            raise RuntimeError("release prepare upgrade plan is invalid") from error
        current = inspect_runtime(target, runner)
        value = _prepare_release_candidate(
            target,
            runner,
            release,
            current,
            upgrade_plan_sha256=prepared_plan["sha256"],
        )
        value["attempt"] = attempt
        value["prepared_at"] = datetime.now(UTC).isoformat().replace("+00:00", "Z")
        value["sha256"] = hashlib.sha256(
            json.dumps(value, sort_keys=True, separators=(",", ":")).encode(),
        ).hexdigest()
        print(json.dumps(value, indent=None if arguments.json else 2, sort_keys=True))
        return 0
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
        if not arguments.attempt_id:
            raise RuntimeError("release abort requires the exact attempt identity")
        state = runner.run(["cat", state_path], check=False)
        controller_state = None
        if state.returncode == 0:
            try:
                controller_state = abort_release_state(parse_release_state(state.stdout))
            except ReleaseControllerError as error:
                raise RuntimeError(str(error)) from error
            _write_remote(
                target,
                runner,
                state_path,
                json.dumps(controller_state, indent=2, sort_keys=True) + "\n",
            )
        run_id = f"abort-{datetime.now(UTC):%Y%m%dt%H%M%S}"
        with runtime_lock(target, runner, "release-abort", run_id):
            rollback = _abort_to_previous_generation(
                target,
                runner,
                arguments.targets,
                attempt=arguments.attempt_id,
            )
        value = {**rollback, "controller_state": controller_state}
        print(json.dumps(value, indent=None if arguments.json else 2, sort_keys=True))
        return 0
    if arguments.action == "notify":
        value = _notify_release(target, runner, arguments.release_id or "")
        print(json.dumps(value, indent=None if arguments.json else 2, sort_keys=True))
        return 0
    if arguments.action == "activate":
        if not arguments.snapshot or not arguments.candidate_release:
            raise RuntimeError(
                "release activation requires snapshot and candidate release",
            )
        try:
            release = validate_release(json.loads(
                _read_path(target, runner, arguments.candidate_release),
            ))
        except (json.JSONDecodeError, ReleaseManifestError) as error:
            raise RuntimeError("release activation candidate is invalid") from error
        value = _activate_quarantined_release(target, runner, arguments, release)
        print(json.dumps(value, indent=None if arguments.json else 2, sort_keys=True))
        return 0
    if arguments.action == "plan":
        if arguments.promote:
            if target.value["environment"] != "staging":
                raise RuntimeError("only staging may sign a production plan promotion")
            if not arguments.upgrade_plan or not arguments.staging_release or not arguments.candidate_release:
                raise RuntimeError(
                    "plan promotion requires staging evidence and both release manifests",
                )
            try:
                evidence = json.loads(_read_path(target, runner, arguments.upgrade_plan))
                staging_release = validate_release(json.loads(
                    _read_path(target, runner, arguments.staging_release),
                ))
                production_release = validate_release(json.loads(
                    _read_path(target, runner, arguments.candidate_release),
                ))
                promoted = promote_upgrade_plan(
                    evidence,
                    staging_release,
                    production_release,
                    Path(target.value["plan_signing"]["private_key"]),
                    Path(target.value["plan_signing"]["public_key"]),
                )
            except (json.JSONDecodeError, ReleaseManifestError, PlanEvidenceError) as error:
                raise RuntimeError("production plan promotion is invalid") from error
            output = arguments.output or arguments.upgrade_plan
            _write_remote(
                target,
                runner,
                str(output),
                json.dumps(promoted, indent=2, sort_keys=True) + "\n",
                "0644",
            )
            print(json.dumps({
                "schema": promoted["schema"],
                "path": str(output),
                "staging_release": staging_release["identity"],
                "production_release": production_release["identity"],
                "status": "signed",
            }, indent=None if arguments.json else 2, sort_keys=True))
            return 0
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
        try:
            release_value = validate_release(json.loads(
                _read_path(target, runner, arguments.candidate_release),
            ))
        except (json.JSONDecodeError, ReleaseManifestError) as error:
            raise RuntimeError("release reconcile candidate is invalid") from error
        attempt = _release_attempt(arguments.attempt_id, release_value["identity"])
        if not arguments.prepare_receipt:
            raise RuntimeError("release reconcile requires pre-downtime preparation evidence")
        try:
            prepare_receipt = _prepare_receipt(
                json.loads(_read_path(target, runner, arguments.prepare_receipt)),
                target=target.name,
                attempt=attempt,
                release=release_value["identity"],
            )
        except json.JSONDecodeError as error:
            raise RuntimeError("release prepare receipt is invalid") from error
        if not arguments.maintenance_receipt:
            raise RuntimeError("release reconcile requires observed maintenance evidence")
        try:
            maintenance_receipt = _maintenance_receipt(
                json.loads(_read_path(target, runner, arguments.maintenance_receipt)),
                target=target.name,
                attempt=attempt,
                required_endpoints=_required_maintenance_endpoints(target),
            )
        except json.JSONDecodeError as error:
            raise RuntimeError("maintenance receipt is invalid") from error
        prepared_at = datetime.fromisoformat(prepare_receipt["prepared_at"].replace("Z", "+00:00"))
        maintenance_at = datetime.fromisoformat(maintenance_receipt["observed_at"].replace("Z", "+00:00"))
        if maintenance_at < prepared_at:
            raise RuntimeError("maintenance was observed before release preparation")
        try:
            plan_value = json.loads(_read_path(target, runner, arguments.upgrade_plan))
            exact_plan = _validated_release_upgrade_plan(target, plan_value, release_value)
        except (json.JSONDecodeError, ModuleReleaseError, PlanEvidenceError) as error:
            raise RuntimeError("release reconcile upgrade plan is invalid") from error
        if prepare_receipt["upgrade_plan_sha256"] != exact_plan["sha256"]:
            raise RuntimeError("release upgrade plan changed after preparation")
        generation = arguments.generation or (
            "g" + datetime.now(UTC).strftime("%Y%m%dt%H%M-")
            + hashlib.sha256(attempt.encode()).hexdigest()[:8]
        )
        attempt_root = f"{target.value['state_directory']}/attempts/{attempt}"
        if runner.run(["mkdir", attempt_root], check=False).returncode != 0:
            raise RuntimeError(
                "release attempt was already consumed; recover and use a fresh attempt",
            )
        attempt_path = f"{attempt_root}/claim.json"
        baseline = inspect_runtime(target, runner)
        operation = {
            "target": target.name,
            "attempt": attempt,
            "source": arguments.source,
            "candidate_release": release_value["identity"],
            "snapshot": arguments.snapshot,
            "generation": generation,
            "gitops_commit": prepare_receipt["gitops_commit"],
            "upgrade_plan_sha256": exact_plan["sha256"],
            "prepare_receipt_sha256": prepare_receipt["sha256"],
            "maintenance_receipt_sha256": maintenance_receipt["sha256"],
        }
        operation_bundle_sha256 = hashlib.sha256(
            json.dumps(operation, sort_keys=True, separators=(",", ":")).encode(),
        ).hexdigest()
        claim = {
            "schema": "usl-release-attempt/v2",
            **operation,
            "baseline_generation": baseline.get("generation"),
            "operation_bundle_sha256": operation_bundle_sha256,
            "claimed_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            "status": "claimed",
        }
        claim["sha256"] = hashlib.sha256(
            json.dumps(claim, sort_keys=True, separators=(",", ":")).encode(),
        ).hexdigest()
        _write_remote(
            target, runner, attempt_path,
            json.dumps(claim, indent=2, sort_keys=True) + "\n", "0444",
        )
        restore_arguments = argparse.Namespace(
            targets=arguments.targets,
            source=arguments.source,
            target=arguments.target,
            snapshot=arguments.snapshot,
            release=None,
            target_release=arguments.candidate_release,
            upgrade_plan=arguments.upgrade_plan,
            generation=generation,
            attempt_id=attempt,
            maintenance_receipt=maintenance_receipt,
            prepare_receipt=prepare_receipt,
            operation_bundle_sha256=operation_bundle_sha256,
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
    storage = commands.add_parser("storage")
    storage.add_argument("action", choices=("plan", "adopt", "status"))
    storage.add_argument("--target", dest="command_target")
    storage.add_argument("--generation")
    storage.add_argument("--rollback-generation")
    storage.add_argument("--snapshot")
    storage.add_argument("--confirm")
    storage.add_argument("--json", action="store_true")
    storage.set_defaults(handler=storage_command)
    backup = commands.add_parser("backup")
    backup.add_argument("action", choices=("create", "list", "select", "verify"))
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
    cleanup.add_argument(
        "--runtime-only",
        action="store_true",
        help="remove only obsolete generation-owned Docker resources",
    )
    cleanup.add_argument("--json", action="store_true")
    cleanup.set_defaults(handler=cleanup_command)
    release = commands.add_parser("release")
    release.add_argument("action", choices=("prepare", "plan", "reconcile", "activate", "status", "abort", "notify"))
    release.add_argument("--target", dest="command_target")
    release.add_argument("--source", default="production")
    release.add_argument("--active-release", type=Path)
    release.add_argument("--candidate-release", type=Path)
    release.add_argument("--upgrade-plan", type=Path)
    release.add_argument("--attest", action="store_true")
    release.add_argument("--promote", action="store_true")
    release.add_argument("--staging-release", type=Path)
    release.add_argument("--snapshot")
    release.add_argument("--generation")
    release.add_argument("--output", type=Path)
    release.add_argument("--replace", action="store_true")
    release.add_argument("--confirm")
    release.add_argument("--release-id")
    release.add_argument("--attempt-id")
    release.add_argument("--maintenance-receipt", type=Path)
    release.add_argument("--prepare-receipt", type=Path)
    release.add_argument("--quarantine-receipt", type=Path)
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
