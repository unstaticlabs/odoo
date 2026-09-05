"""Single operator interface for USL runtime lifecycle and recovery."""

from __future__ import annotations

import argparse
import copy
import hashlib
import io
import json
import os
import re
import sys
import time
from contextlib import contextmanager, redirect_stdout
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import urlsplit

from operations.oidc_admission import CLIENT_PROBE_SCRIPT
from operations.control_manifest import (
    RELEASE_DEFINITIONS_SQL,
    release_definitions_digest,
    ODOO_CONTROL_SQL,
    PAPERLESS_CONTROL_SQL,
    ControlManifestError,
    validate_restore,
)
from operations.cohort import (
    SCHEMA as RECOVERY_COHORT_SCHEMA,
    select_latest_recovery_snapshot,
    validate_manifest as validate_cohort_manifest,
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
    Target,
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
    "renderer": ("usl-document-renderer",),
}
RELEASE_RUNTIME_SERVICES = {
    "distribution": "odoo",
    "paperless": "paperless",
    "sign-dss": "sign",
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
RECOVERY_PROOF_ID = re.compile(r"[a-z0-9][a-z0-9-]{7,23}\Z")
RECOVERY_PROOF_OWNER = "usl-disposable-recovery-proof"
RECOVERY_PROOF_FAILURE_STAGES = (
    "backup-qualified",
    "resources-created",
    "materialized",
    "runtime-started",
    "validated",
    "cleanup",
    "cas-verified",
    "final-write",
)
RECOVERY_PROOF_MAX_SECONDS = 1800
RECOVERY_PROOF_CLEANUP_RESERVE_SECONDS = 120
RECOVERY_PROOF_RUNTIME_ROLES = (
    "odoo", "paperless", "paperless_broker", "paperless_gotenberg",
    "paperless_tika", "mcp", "renderer", "sign", "sign_ca",
)


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


def _container_networks(runner, container: str) -> dict:
    raw = runner.run(
        ["docker", "inspect", container, "--format", "{{json .NetworkSettings.Networks}}"],
    ).stdout
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as error:
        raise RuntimeError("container network inventory is invalid") from error
    if not isinstance(value, dict):
        raise RuntimeError("container network inventory is invalid")
    return value


def _container_identifier(runner, container: str) -> str:
    identifier = runner.run(
        ["docker", "inspect", container, "--format", "{{.Id}}"],
    ).stdout.strip()
    if not re.fullmatch(r"[0-9a-f]{64}", identifier):
        raise RuntimeError("container identity is invalid")
    return identifier


def _network_alias_owners(runner, network: str, alias: str) -> list[str]:
    raw = runner.run(
        ["docker", "network", "inspect", network, "--format", "{{json .Containers}}"],
    ).stdout
    try:
        containers = json.loads(raw)
    except json.JSONDecodeError as error:
        raise RuntimeError("ingress network inventory is invalid") from error
    if not isinstance(containers, dict):
        raise RuntimeError("ingress network inventory is invalid")
    owners = []
    for container in containers:
        networks = _container_networks(runner, container)
        aliases = (networks.get(network) or {}).get("Aliases") or []
        if alias in aliases:
            owners.append(container)
    return sorted(owners)


def _gateway_container(target, runner) -> str | None:
    containers = runner.run(
        [
            "docker", "ps", "-a",
            "--no-trunc",
            "--filter", f"label=com.docker.compose.project={target.project}",
            "--filter", "label=com.docker.compose.service=gateway",
            "--format", "{{.ID}}",
        ],
    ).stdout.splitlines()
    if len(containers) > 1:
        raise RuntimeError("staging gateway container identity is ambiguous")
    return containers[0] if containers else None


def _gateway_service_hash(runner, identity: dict) -> str:
    """Return Compose's exact hash for one rendered gateway identity."""
    lines = runner.run(
        compose_command(identity, ["config", "--hash", "gateway"]),
    ).stdout.splitlines()
    fields = lines[0].split() if len(lines) == 1 else []
    if (
        len(fields) != 2
        or fields[0] != "gateway"
        or not re.fullmatch(r"[0-9a-f]{64}", fields[1])
    ):
        raise RuntimeError("gateway Compose hash evidence is invalid")
    return fields[1]


def _gateway_semantic_contract(target, runner, identity: dict) -> tuple[str, str, str, dict]:
    """Hash the rendered gateway while binding its config mount by content."""
    raw = runner.run(
        compose_command(identity, ["config", "--format", "json"]),
    ).stdout
    try:
        rendered = json.loads(raw)
        gateway = rendered["services"]["gateway"]
        volumes = gateway["volumes"]
    except (json.JSONDecodeError, KeyError, TypeError) as error:
        raise RuntimeError("gateway rendered configuration is invalid") from error
    if not isinstance(gateway, dict) or not isinstance(volumes, list):
        raise RuntimeError("gateway rendered configuration is invalid")
    config_mounts = [
        volume for volume in volumes
        if isinstance(volume, dict)
        and volume.get("target") == "/etc/nginx/usl-gateway.conf"
    ]
    if len(config_mounts) != 1:
        raise RuntimeError("gateway configuration mount identity is ambiguous")
    config_mount = config_mounts[0]
    source = config_mount.get("source")
    if config_mount.get("type") != "bind" or not isinstance(source, str) or not source.startswith("/"):
        raise RuntimeError("gateway configuration mount is invalid")
    digest_fields = runner.run(["sha256sum", "--", source]).stdout.split()
    if len(digest_fields) < 1 or not re.fullmatch(r"[0-9a-f]{64}", digest_fields[0]):
        raise RuntimeError("gateway configuration content digest is invalid")
    normalized = copy.deepcopy(gateway)
    normalized_mount = next(
        volume for volume in normalized["volumes"]
        if volume.get("target") == "/etc/nginx/usl-gateway.conf"
    )
    normalized_mount["source"] = f"sha256:{digest_fields[0]}"
    semantic_hash = hashlib.sha256(json.dumps(
        normalized, sort_keys=True, separators=(",", ":"),
    ).encode()).hexdigest()
    return semantic_hash, digest_fields[0], source, gateway


def _gateway_semantic_hash(target, runner, identity: dict) -> str:
    return _gateway_semantic_contract(target, runner, identity)[0]


def _compose_duration_nanoseconds(value: object) -> int:
    if value in (None, 0):
        return 0
    if not isinstance(value, str):
        raise RuntimeError("gateway healthcheck duration is invalid")
    match = re.fullmatch(r"([0-9]+(?:\.[0-9]+)?)(ns|us|ms|s|m|h)", value)
    if not match:
        raise RuntimeError("gateway healthcheck duration is invalid")
    scale = {
        "ns": 1,
        "us": 1_000,
        "ms": 1_000_000,
        "s": 1_000_000_000,
        "m": 60_000_000_000,
        "h": 3_600_000_000_000,
    }
    return int(float(match.group(1)) * scale[match.group(2)])


def _memory_swappiness_matches(runner, actual: object, expected: object) -> bool:
    """Compare swappiness while respecting the Docker host's cgroup model.

    Docker accepts ``--memory-swappiness`` on a cgroup-v2 host but explicitly
    discards it and reports ``MemorySwappiness: null``.  Treat that exact
    engine-normalized state as equivalent; retain an exact comparison on
    cgroup v1, where the setting is enforceable.
    """
    expected_value = int(expected or 0)
    if actual == expected_value:
        return True
    if actual is not None:
        return False
    cgroup_version = runner.run(
        ["docker", "info", "--format", "{{.CgroupVersion}}"],
    ).stdout.strip()
    if cgroup_version not in {"1", "2"}:
        raise RuntimeError("Docker cgroup capability evidence is invalid")
    return cgroup_version == "2"


def _with_stable_gateway_config(target, runner, identity: dict) -> dict:
    """Pin staging gateway configuration to a persistent content identity."""
    if target.value["environment"] != "staging":
        return identity
    semantic_hash, config_digest, source, _gateway = _gateway_semantic_contract(
        target, runner, identity,
    )
    content = runner.run(["cat", source]).stdout
    if hashlib.sha256(content.encode()).hexdigest() != config_digest:
        raise RuntimeError("gateway configuration changed while it was captured")
    gateway_root = f"{Path(target.value['state_directory']).parents[1]}/gateway/staging"
    config_root = f"{gateway_root}/configs/{config_digest}"
    config_path = f"{config_root}/gateway.conf"
    overlay_path = f"{config_root}/compose.gateway.json"
    runner.run(["install", "-d", "-m", "0755", config_root])
    _write_remote(target, runner, config_path, content, "0444")
    overlay = {
        "services": {
            "gateway": {
                "volumes": [{
                    "type": "bind",
                    "source": config_path,
                    "target": "/etc/nginx/usl-gateway.conf",
                    "read_only": True,
                }],
            },
        },
    }
    _write_remote(
        target, runner, overlay_path,
        json.dumps(overlay, indent=2, sort_keys=True) + "\n", "0444",
    )
    stable = {**identity, "compose_files": [*identity["compose_files"], overlay_path]}
    stable_hash, stable_digest, stable_source, _stable_gateway = _gateway_semantic_contract(
        target, runner, stable,
    )
    if (
        stable_hash != semantic_hash
        or stable_digest != config_digest
        or stable_source != config_path
    ):
        raise RuntimeError("content-addressed gateway configuration differs")
    return stable


def _gateway_labeled_identity(target, runner, labels: dict, canonical_identity: dict) -> dict:
    """Recover the immutable Compose identity that created the running gateway."""
    config_files = [
        item for item in labels.get("com.docker.compose.project.config_files", "").split(",")
        if item
    ]
    env_files = [
        item for item in labels.get("com.docker.compose.project.environment_file", "").split(",")
        if item
    ]
    working_directory = labels.get("com.docker.compose.project.working_dir")
    state_prefix = target.value["state_directory"].rstrip("/") + "/"
    canonical_working = canonical_identity["working_directory"]
    relative_working = target.value["compose"]["canonical"]["working_directory"]
    working_suffix = "/" + relative_working
    canonical_prefix = canonical_working.removesuffix(working_suffix)
    running_prefix = (
        working_directory.removesuffix(working_suffix)
        if isinstance(working_directory, str) and working_directory.endswith(working_suffix)
        else ""
    )
    snapshot_prefix = re.compile(
        r"/var/lib/usl-odoo/gitops-runs/[0-9a-f]{40}\.[A-Za-z0-9._-]+",
    )
    canonical_static = {
        path for path in canonical_identity["compose_files"]
        if path.startswith(canonical_working + "/")
    }
    relative_static = {
        path.removeprefix(canonical_working + "/") for path in canonical_static
    }
    running_static = {
        f"{working_directory}/{path}" for path in relative_static
    }
    gateway_root = f"{Path(target.value['state_directory']).parents[1]}/gateway/staging"
    generation_pattern = GENERATION_NAME.pattern.removesuffix(r"\Z")
    dynamic_patterns = (
        re.compile(re.escape(state_prefix) + r"generations/" + generation_pattern + r"/compose\.(?:generation|resources)\.json"),
        re.compile(re.escape(state_prefix) + r"authorities/mcp-[0-9a-f]{64}\.json"),
        re.compile(re.escape(gateway_root) + r"/configs/[0-9a-f]{64}/compose\.gateway\.json"),
    )
    if (
        not config_files
        or len(config_files) != len(set(config_files))
        or not all(path.startswith("/") for path in config_files)
        or not relative_static
        or not running_static.issubset(config_files)
        or any(
            path not in running_static
            and not any(pattern.fullmatch(path) for pattern in dynamic_patterns)
            for path in config_files
        )
        or env_files != canonical_identity["environment_file"].split(",")
        or working_directory != canonical_working
        and running_prefix != canonical_prefix
        and not snapshot_prefix.fullmatch(running_prefix)
    ):
        raise RuntimeError("running gateway Compose identity is invalid")
    return {
        **canonical_identity,
        "working_directory": working_directory,
        "environment_file": ",".join(env_files),
        "compose_files": config_files,
    }


def _validate_gateway_container(
    target,
    runner,
    container: str,
    canonical_identity: dict,
    generation_identity: dict | None = None,
) -> None:
    try:
        inspected = json.loads(runner.run(
            ["docker", "inspect", container, "--format", "{{json .}}"],
        ).stdout)
        labels = inspected["Config"]["Labels"]
        state = inspected["State"]
        host = inspected["HostConfig"]
        mounts = inspected["Mounts"]
    except json.JSONDecodeError as error:
        raise RuntimeError("staging gateway container evidence is invalid") from error
    except (KeyError, TypeError) as error:
        raise RuntimeError("staging gateway container evidence is invalid") from error
    _gateway_labeled_identity(
        target, runner, labels, canonical_identity,
    )
    running_hash = labels.get("com.docker.compose.config-hash", "")
    if not re.fullmatch(r"[0-9a-f]{64}", running_hash):
        raise RuntimeError("running gateway Compose hash is invalid")
    semantic_hash, config_digest, _canonical_source, canonical_gateway = _gateway_semantic_contract(
        target, runner, canonical_identity,
    )
    canonical_hash = _gateway_service_hash(runner, canonical_identity)
    if generation_identity is not None:
        generation_contract = _gateway_semantic_contract(
            target, runner, generation_identity,
        )
        if generation_contract[0] != semantic_hash:
            raise RuntimeError("generation gateway semantic configuration differs")
        if _gateway_service_hash(runner, generation_identity) != canonical_hash:
            raise RuntimeError("generation gateway Compose hash differs")
    runtime_digest_fields = runner.run([
        "docker", "exec", container, "sha256sum", "--",
        "/etc/nginx/usl-gateway.conf",
    ]).stdout.split()
    if (
        len(runtime_digest_fields) < 1
        or not re.fullmatch(r"[0-9a-f]{64}", runtime_digest_fields[0])
        or runtime_digest_fields[0] != config_digest
    ):
        raise RuntimeError("running gateway configuration content differs")
    config_mounts = [
        mount for mount in mounts
        if isinstance(mount, dict) and mount.get("Destination") == "/etc/nginx/usl-gateway.conf"
    ]
    marker_mounts = [
        mount for mount in mounts
        if isinstance(mount, dict) and mount.get("Destination") == "/run/usl-gateway"
    ]
    expected_marker = f"{Path(target.value['state_directory']).parents[1]}/gateway/staging"
    expected_logging = canonical_gateway.get("logging") or {}
    expected_health = canonical_gateway.get("healthcheck") or {}
    actual_health = inspected["Config"].get("Healthcheck") or {}
    health_matches = (
        actual_health.get("Test") == expected_health.get("test")
        and actual_health.get("Interval", 0)
        == _compose_duration_nanoseconds(expected_health.get("interval"))
        and actual_health.get("Timeout", 0)
        == _compose_duration_nanoseconds(expected_health.get("timeout"))
        and actual_health.get("StartPeriod", 0)
        == _compose_duration_nanoseconds(expected_health.get("start_period"))
        and actual_health.get("Retries", 0) == expected_health.get("retries", 0)
    )
    expected_tmpfs = {}
    for item in canonical_gateway.get("tmpfs") or []:
        if not isinstance(item, str) or ":" not in item:
            raise RuntimeError("canonical gateway tmpfs configuration is invalid")
        destination, options = item.split(":", 1)
        expected_tmpfs[destination] = options
    if (
        inspected["Config"].get("Image") != canonical_gateway.get("image")
        or inspected["Config"].get("Cmd") != canonical_gateway.get("command")
        or canonical_gateway.get("entrypoint") is not None
        and inspected["Config"].get("Entrypoint") != canonical_gateway["entrypoint"]
        or not health_matches
        or host.get("ReadonlyRootfs") is not True
        or (host.get("RestartPolicy") or {}).get("Name") != "unless-stopped"
        or host.get("NanoCpus") != int(float(canonical_gateway.get("cpus", 0)) * 1_000_000_000)
        or host.get("CpuShares") != canonical_gateway.get("cpu_shares", 0)
        or host.get("Memory") != int(canonical_gateway.get("mem_limit", 0))
        or host.get("MemoryReservation") != int(canonical_gateway.get("mem_reservation", 0))
        or host.get("MemorySwap") != int(canonical_gateway.get("memswap_limit", 0))
        or not _memory_swappiness_matches(
            runner,
            host.get("MemorySwappiness"),
            canonical_gateway.get("mem_swappiness", 0),
        )
        or host.get("OomScoreAdj") != canonical_gateway.get("oom_score_adj", 0)
        or host.get("PidsLimit") != canonical_gateway.get("pids_limit")
        or sorted(host.get("SecurityOpt") or [])
        != sorted(canonical_gateway.get("security_opt") or [])
        or (host.get("LogConfig") or {}).get("Type") != expected_logging.get("driver")
        or (host.get("LogConfig") or {}).get("Config") != expected_logging.get("options")
        or (host.get("Tmpfs") or {}) != expected_tmpfs
        or len(config_mounts) != 1
        or config_mounts[0].get("Type") != "bind"
        or config_mounts[0].get("RW") is not False
        or len(marker_mounts) != 1
        or marker_mounts[0].get("Type") != "bind"
        or marker_mounts[0].get("Source") != expected_marker
        or marker_mounts[0].get("RW") is not False
    ):
        raise RuntimeError("running gateway semantic configuration differs")
    networks = _container_networks(runner, container)
    ingress = target.value["external_networks"]["ingress"]
    default_network = target.value["compose"]["default_network"]
    ingress_aliases = (networks.get(ingress) or {}).get("Aliases") or []
    backend_aliases = (networks.get(default_network) or {}).get("Aliases") or []
    if (
        labels.get("com.docker.compose.project") != target.project
        or labels.get("com.docker.compose.service") != "gateway"
        or set(networks) != {ingress, default_network}
        or "odoo-staging" not in ingress_aliases
        or "gateway" not in backend_aliases
        or state.get("Running") is not True
        or state.get("Health", {}).get("Status") != "healthy"
    ):
        raise RuntimeError("staging gateway ownership or health differs")


def _validate_stable_gateway_generation(
    target, runner, canonical_identity: dict, generation_identity: dict,
) -> None:
    """Prove that a release cannot create or reconfigure the staging gateway."""
    if target.value["environment"] != "staging":
        return
    gateway = _gateway_container(target, runner)
    if not gateway:
        raise RuntimeError("staging release requires the stable gateway")
    _validate_gateway_container(
        target, runner, gateway, canonical_identity, generation_identity,
    )


def _probe_staging_gateway_maintenance(target, runner) -> dict:
    endpoint = target.value["endpoints"]["odoo"].rstrip("/")
    if endpoint != "https://odoo-staging.unstaticlabs.com":
        raise RuntimeError("staging public ingress endpoint differs")
    # Cloudflared and its origin connection pool may briefly retain the legacy
    # container after Docker transfers the public network alias.  Admission is
    # still fail-closed, but allow that bounded propagation window instead of
    # treating the first stale origin response as a release failure.
    last_observation = {
        "http_status": None,
        "http_retry_after": False,
        "websocket_status": None,
        "websocket_maintenance": False,
    }
    for attempt in range(30):
        nonce = str(time.time_ns())
        common = [
            "curl", "--silent", "--show-error", "--include", "--http1.1",
            "--max-time", "15", "--header", "Cache-Control: no-cache",
        ]
        http = runner.run(
            [*common, f"{endpoint}/web/health?maintenance_probe={nonce}"],
            check=False,
        )
        websocket = runner.run(
            [
                *common,
                "--header", "Connection: Upgrade",
                "--header", "Upgrade: websocket",
                "--header", f"Origin: {endpoint}",
                "--header", "Sec-WebSocket-Version: 13",
                "--header", "Sec-WebSocket-Key: dXNsLW1haW50ZW5hbmNlIQ==",
                f"{endpoint}/websocket?maintenance_probe={nonce}",
            ],
            check=False,
        )
        http_lines = http.stdout.splitlines()
        websocket_lines = websocket.stdout.splitlines()
        http_status = http_lines[0] if http_lines else ""
        websocket_status = websocket_lines[0] if websocket_lines else ""
        last_observation = {
            "http_status": 503 if " 503 " in http_status else None,
            "http_retry_after": "retry-after: 60" in http.stdout.lower(),
            "websocket_status": 503 if " 503 " in websocket_status else None,
            "websocket_maintenance": '"error":"maintenance"' in websocket.stdout,
        }
        if (
            not http.returncode
            and not websocket.returncode
            and http_lines
            and websocket_lines
            and " 503 " in http_lines[0]
            and last_observation["http_retry_after"]
            and " 503 " in websocket_lines[0]
            and '"error":"maintenance"' in websocket.stdout
        ):
            return {
                "schema": "usl-staging-gateway-maintenance/v1",
                "http_status": 503,
                "websocket_status": 503,
                "status": "passed",
            }
        if attempt < 29:
            time.sleep(2)
    raise RuntimeError(
        "staging gateway maintenance was not admitted over HTTP and WebSocket "
        "within the bounded propagation window; last observation: "
        + json.dumps(last_observation, sort_keys=True),
    )


def _wait_legacy_staging_origin(
    runner, container: str, *, timeout: float = 120.0, interval: float = 2.0,
) -> dict:
    """Wait for the exact restarted legacy Odoo container to become healthy."""
    deadline = time.monotonic() + timeout
    last = {"status": "unknown", "running": False, "health": "unknown"}
    while True:
        result = runner.run(
            ["docker", "inspect", container, "--format", "{{json .State}}"],
            check=False,
        )
        if result.returncode == 0:
            try:
                state = json.loads(result.stdout)
            except json.JSONDecodeError as error:
                raise RuntimeError("legacy staging origin state is invalid") from error
            health = (state.get("Health") or {}).get("Status")
            last = {
                "status": state.get("Status"),
                "running": state.get("Running"),
                "health": health,
            }
            if state.get("Running") is True and health == "healthy":
                return last
            if state.get("Status") in {"dead", "exited", "removing"}:
                raise RuntimeError(
                    "legacy staging origin entered a terminal state: "
                    + json.dumps(last, sort_keys=True),
                )
        if time.monotonic() >= deadline:
            raise RuntimeError(
                "legacy staging origin did not become healthy within "
                f"{int(timeout)} seconds: " + json.dumps(last, sort_keys=True),
            )
        time.sleep(interval)


def _restore_legacy_ingress(target, runner, candidate_identity: dict, legacy: str, aliases: list[str]) -> None:
    # Keep the stable gateway serving maintenance until the legacy origin is
    # genuinely ready. Docker start/restart returning only proves PID startup.
    gateway = _gateway_container(target, runner)
    runner.run(["docker", "start", legacy])
    try:
        _wait_legacy_staging_origin(runner, legacy)
    except Exception as error:
        if gateway:
            _validate_gateway_container(target, runner, gateway, candidate_identity)
            if _network_alias_owners(
                runner, target.value["external_networks"]["ingress"], "odoo-staging",
            ) != [gateway]:
                raise RuntimeError(
                    "legacy staging rollback failed and maintenance gateway lost ingress",
                ) from error
            _probe_staging_gateway_maintenance(target, runner)
        raise RuntimeError(
            "legacy staging rollback origin is not healthy; maintenance gateway remains active",
        ) from error
    if gateway:
        runner.run(
            compose_command(candidate_identity, ["rm", "--stop", "--force", "gateway"]),
        )
    ingress = target.value["external_networks"]["ingress"]
    networks = _container_networks(runner, legacy)
    if ingress not in networks:
        command = ["docker", "network", "connect"]
        for alias in aliases:
            command.extend(("--alias", alias))
        runner.run([*command, ingress, legacy])
    if _network_alias_owners(runner, ingress, "odoo-staging") != [legacy]:
        raise RuntimeError("legacy staging ingress could not be restored")


def _refresh_legacy_staging_origin(target, runner, legacy: str) -> None:
    """Terminate Cloudflared's legacy origin connection after alias transfer."""
    ingress = target.value["external_networks"]["ingress"]
    default_network = target.value["compose"]["default_network"]
    runner.run(["docker", "restart", "--time", "30", legacy])
    _wait_legacy_staging_origin(runner, legacy)
    networks = _container_networks(runner, legacy)
    backend_aliases = (networks.get(default_network) or {}).get("Aliases") or []
    if ingress in networks or "odoo-staging-app" not in backend_aliases:
        raise RuntimeError("legacy staging origin changed networks during gateway handoff")


def _adopt_staging_gateway(target, runner) -> dict:
    """Transfer first-v3 ingress to the stable gateway before writers stop."""
    adoption = target.value["compose"].get("adoption")
    if target.name != "staging" or adoption is None:
        raise RuntimeError("stable gateway adoption is staging-only")
    marker = f"{Path(target.value['state_directory']).parents[1]}/gateway/staging/maintenance"
    if runner.run(["test", "-f", marker], check=False).returncode:
        raise RuntimeError("stable gateway adoption requires the maintenance marker")
    current = inspect_runtime(target, runner)
    identity = current["compose"]
    candidate_identity = _candidate_compose_identity(target, runner, identity)
    legacy_anchor = adoption["legacy_anchor_service"]
    if identity.get("anchor_service") != legacy_anchor:
        gateway = _gateway_container(target, runner)
        if not gateway:
            raise RuntimeError("canonical staging has no stable gateway")
        _validate_gateway_container(target, runner, gateway, candidate_identity)
        maintenance = _probe_staging_gateway_maintenance(target, runner)
        return {**maintenance, "adoption": "already-canonical"}

    _validated_legacy_compose_identity(
        target,
        runner,
        _recorded_compose_identity(identity),
        current["generation"],
    )
    legacy = _container_identifier(runner, identity["container_id"])
    ingress = target.value["external_networks"]["ingress"]
    default_network = target.value["compose"]["default_network"]
    networks = _container_networks(runner, legacy)
    backend_aliases = (networks.get(default_network) or {}).get("Aliases") or []
    ingress_aliases = (networks.get(ingress) or {}).get("Aliases") or []
    expected_ingress_aliases = sorted({f"{target.project}-odoo-1", "odoo", "odoo-staging"})
    if "odoo-staging-app" not in backend_aliases:
        raise RuntimeError("legacy staging backend alias is missing")
    owners = _network_alias_owners(runner, ingress, "odoo-staging")
    already_adopted = legacy not in owners
    if not already_adopted and (owners != [legacy] or sorted(ingress_aliases) != expected_ingress_aliases):
        raise RuntimeError("legacy staging ingress aliases differ from the adoption contract")
    if already_adopted:
        gateway = _gateway_container(target, runner)
        if gateway:
            try:
                _validate_gateway_container(target, runner, gateway, candidate_identity)
                if runner.run(
                    ["docker", "exec", gateway, "test", "-f", "/run/usl-gateway/maintenance"],
                    check=False,
                ).returncode:
                    raise RuntimeError("stable staging gateway did not mount the maintenance marker")
                if owners != [gateway]:
                    raise RuntimeError("stable staging gateway does not uniquely own public ingress")
                if ingress in _container_networks(runner, legacy):
                    raise RuntimeError("legacy staging retained public ingress")
            except RuntimeError:
                # A partial earlier attempt is repaired by the normal path
                # below, whose failure handling restores the legacy ingress.
                pass
            else:
                maintenance = _probe_staging_gateway_maintenance(target, runner)
                return {**maintenance, "adoption": "already-adopted"}
    detached = False
    try:
        if not already_adopted:
            runner.run(["docker", "network", "disconnect", ingress, legacy])
            detached = True
        runner.run(
            compose_command(
                candidate_identity,
                ["up", "--detach", "--wait", "--no-deps", "gateway"],
            ),
        )
        gateway = _gateway_container(target, runner)
        if not gateway:
            raise RuntimeError("stable staging gateway was not created")
        _validate_gateway_container(target, runner, gateway, candidate_identity)
        if runner.run(
            ["docker", "exec", gateway, "test", "-f", "/run/usl-gateway/maintenance"],
            check=False,
        ).returncode:
            raise RuntimeError("stable staging gateway did not mount the maintenance marker")
        if _network_alias_owners(runner, ingress, "odoo-staging") != [gateway]:
            raise RuntimeError("stable staging gateway does not uniquely own public ingress")
        if ingress in _container_networks(runner, legacy):
            raise RuntimeError("legacy staging retained public ingress")
        # Docker alias transfer does not terminate an already pooled HTTP
        # connection.  Restart only the legacy staging Odoo origin after it is
        # detached, so Cloudflared reconnects through the stable gateway while
        # the remaining staging services and every production service stay up.
        _refresh_legacy_staging_origin(target, runner, legacy)
        maintenance = _probe_staging_gateway_maintenance(target, runner)
    except Exception as error:
        if detached or already_adopted:
            try:
                _restore_legacy_ingress(
                    target,
                    runner,
                    candidate_identity,
                    legacy,
                    expected_ingress_aliases,
                )
            except Exception as rollback_error:
                raise RuntimeError(
                    f"gateway adoption failed ({error}); ingress rollback failed "
                    f"({rollback_error})",
                ) from error
        raise
    return {**maintenance, "adoption": "already-adopted" if already_adopted else "adopted"}


def runtime_command(arguments: argparse.Namespace) -> int:
    target = load_target(arguments.target, arguments.targets)
    runner = target.runner()
    if arguments.action == "adopt-gateway":
        with runtime_lock(target, runner, "gateway-adoption", "gateway-adoption"):
            result = _adopt_staging_gateway(target, runner)
    elif arguments.action == "status":
        result = inspect_runtime(target, runner)
    else:
        current = inspect_runtime(target, runner)
        identity = current["compose"]
        if arguments.action == "start":
            identity = _active_generation_identity(target, runner, current)
            runner.run(compose_command(
                identity,
                ["up", "--detach", "--wait", "--no-recreate", "--no-deps", *_compose_services(target, identity)],
            ))
        else:
            runner.run(compose_command(
                identity, ["stop", *_compose_services(target, identity)],
            ))
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


def _read_path(target, runner, path: Path | str) -> str:
    path = Path(path)
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


def _local_restic_mounts(target) -> list[str]:
    # Restic reads and writes through its own container, not the host launcher.
    # Restore also needs write access for repository locks.
    return [
        argument
        for repository in target.value["backup"].values()
        if repository.startswith("/")
        for argument in ("--volume", f"{repository}:{repository}")
    ]


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
        "--env", f"USL_TARGET={target.name}",
        *_local_restic_mounts(target),
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
    if "mcp_secrets" in paths:
        insertion = command.index(image)
        definition = paths["mcp_secrets"]
        if "files" in definition:
            command[insertion:insertion] = [
                argument
                for name, source in sorted(definition["files"].items())
                for argument in ("--volume", f"{source}:/source/mcp-secrets/{name}:ro")
            ]
        else:
            command[insertion:insertion] = [
                "--volume", f"{definition['path']}:/source/mcp-secrets:ro",
            ]
    if "renderer_secrets" in paths:
        insertion = command.index(image)
        command[insertion:insertion] = [
            "--volume", f"{paths['renderer_secrets']['path']}:/source/renderer-secrets:ro",
        ]
    if "paperless_personal_ai_keys" in paths:
        insertion = command.index(image)
        command[insertion:insertion] = [
            "--volume",
            f"{paths['paperless_personal_ai_keys']['path']}:/source/paperless-personal-ai-keys.json:ro",
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


def _operations_image(release: dict) -> str:
    """Use the selected operations runtime, independently of the data's release."""
    image = os.environ.get("USL_OPERATIONS_IMAGE")
    if image is None:
        # The release reader already validates this signed immutable reference.
        return release["components"]["backup-tool"]["digest_reference"]
    if not re.fullmatch(r"ghcr\.io/unstaticlabs/usl-odoo-backup@sha256:[0-9a-f]{64}", image):
        raise RuntimeError("operations image must be an immutable backup-tool reference")
    return image


def _release_images(release: dict, mcp_authority: dict | None = None) -> list[str]:
    """Return Odoo-cohort images needed before a restore can start.

    MCP has an independently admitted release ledger. Its image comes from the
    GitOps Compose generation and must never be downgraded to the MCP build that
    happened to qualify this Odoo release.
    """
    images = {
        _operations_image(release),
        release["components"]["distribution"]["digest_reference"],
        release["components"]["paperless"]["digest_reference"],
        release["components"]["sign-dss"]["digest_reference"],
        release["renderer"]["image"],
    }
    if mcp_authority is not None:
        images.add(mcp_authority["image"])
    return sorted(images)


def _release_image(release: dict, component: str) -> str:
    if component == "renderer":
        return release["renderer"]["image"]
    return release["components"][component]["digest_reference"]


def _independent_mcp_image(
    target, images: dict[str, str], authority: dict | None = None,
) -> str:
    """Return MCP only when Compose agrees with its independent authority."""
    service = target.value["services"]["mcp"]
    image = images.get(service)
    if not isinstance(image, str) or not re.fullmatch(
        r"ghcr\.io/unstaticlabs/odoo-mcp@sha256:[0-9a-f]{64}", image,
    ):
        raise RuntimeError("independently admitted MCP image is missing or mutable")
    if authority is not None and image != authority["image"]:
        raise RuntimeError("GitOps MCP ledger and Compose image differ")
    return image


def _gitops_root() -> tuple[Path, str]:
    """Resolve the immutable GitOps checkout mounted by the fixed launcher."""
    root_value = os.environ.get("USL_RELEASE_GITOPS_ROOT", "")
    commit = os.environ.get("USL_RELEASE_GITOPS_COMMIT", "")
    if not root_value.startswith("/") or not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise RuntimeError("exact GitOps identity is missing")
    root = Path(root_value)
    marker = root / ".usl-gitops-commit"
    if root.is_symlink() or not root.is_dir() or root.resolve() != root:
        raise RuntimeError("exact GitOps root is unsafe")
    if (
        marker.is_symlink()
        or not marker.is_file()
        or marker.resolve() != marker
        or marker.read_text(encoding="ascii").strip() != commit
    ):
        raise RuntimeError("exact GitOps commit marker differs")
    return root, commit


def _load_gitops_json(root: Path, relative: str) -> dict:
    path = root / relative
    try:
        resolved = path.resolve(strict=True)
    except OSError as error:
        raise RuntimeError(f"GitOps release document is unavailable: {relative}") from error
    if path.is_symlink() or not path.is_file() or resolved != path or root not in resolved.parents:
        raise RuntimeError(f"GitOps release document is unsafe: {relative}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise RuntimeError(f"GitOps release document is invalid: {relative}") from error
    if not isinstance(value, dict):
        raise RuntimeError(f"GitOps release document is invalid: {relative}")
    return value


def _mcp_runtime_authority(target) -> dict | None:
    """Validate the independently promoted MCP state from the pinned GitOps tree."""
    if target.value["environment"] == "local":
        return None
    root, gitops_commit = _gitops_root()
    environment = target.value["environment"]
    selected = _load_gitops_json(
        root, f"komodo/releases/usl-odoo-{environment}-mcp.json",
    )
    expected_fields = {
        "schema", "environment", "commit", "compatibility_sha256", "image",
        "oauth_vault_schema", "release_manifest",
    }
    if set(selected) != expected_fields:
        raise RuntimeError("GitOps MCP ledger fields differ")
    if (
        selected.get("schema") != "usl-odoo-mcp-environment-release/v1"
        or selected.get("environment") != environment
    ):
        raise RuntimeError("GitOps MCP ledger targets another environment")
    if (
        not re.fullmatch(r"[0-9a-f]{40}", str(selected.get("commit", "")))
        or not re.fullmatch(
            r"ghcr\.io/unstaticlabs/odoo-mcp@sha256:[0-9a-f]{64}",
            str(selected.get("image", "")),
        )
        or selected.get("oauth_vault_schema") != 1
    ):
        raise RuntimeError("GitOps MCP identity is invalid")
    release_manifest = selected.get("release_manifest")
    compatibility_sha256 = selected.get("compatibility_sha256")
    manifest_relative = f"komodo/releases/usl-odoo-{environment}-mcp-manifest.json"
    if release_manifest is None:
        raise RuntimeError("uncommissioned GitOps MCP cannot be runtime authority")
    else:
        if (
            not re.fullmatch(
                r"ghcr\.io/unstaticlabs/usl-odoo-mcp-release@sha256:[0-9a-f]{64}",
                str(release_manifest),
            )
            or not re.fullmatch(r"[0-9a-f]{64}", str(compatibility_sha256))
        ):
            raise RuntimeError("GitOps MCP release identity is invalid")
        manifest = _load_gitops_json(root, manifest_relative)
        source = manifest.get("source") or {}
        image = manifest.get("image") or {}
        compatibility = manifest.get("compatibility") or {}
        if (
            manifest.get("schema") != "usl-odoo-mcp-oci-release/v2"
            or source.get("repository") != "https://github.com/unstaticlabs/odoo-mcp.git"
            or source.get("ref") != "refs/heads/main"
            or source.get("commit") != selected["commit"]
            or image.get("digest_reference") != selected["image"]
            or compatibility.get("sha256") != compatibility_sha256
            or (compatibility.get("oauth_vault") or {}).get("schema_version") != 1
        ):
            raise RuntimeError("GitOps MCP manifest and ledger differ")
    authority = {**selected, "gitops_commit": gitops_commit}
    authority["sha256"] = hashlib.sha256(json.dumps(
        authority, sort_keys=True, separators=(",", ":"),
    ).encode()).hexdigest()
    return authority


def _validate_candidate_mcp_support(release: dict, compatibility: dict) -> None:
    """Admission policy for a candidate, never a prerequisite to capture its baseline."""
    contract = release.get("mcp_contract") or {}
    version = re.fullmatch(
        r"(?P<major>0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)"
        r"(?:[-+][0-9A-Za-z.-]+)?",
        str(compatibility.get("server_version", "")),
    )
    if (
        contract.get("schema") != "usl-odoo-mcp-support/v1"
        or version is None
        or int(version.group("major")) != contract.get("supported_mcp_major")
        or contract.get("odoo_series") not in compatibility.get("supported_odoo_series", [])
    ):
        raise RuntimeError("GitOps MCP release is incompatible with Odoo")
    for required_name, available_name in (
        ("required_modules", "required_modules"),
        ("required_public_methods", "public_methods"),
        ("required_actions", "actions"),
    ):
        required = compatibility.get(required_name)
        available = contract.get(available_name)
        if (
            not isinstance(required, list)
            or not isinstance(available, list)
            or set(required) - set(available)
        ):
            raise RuntimeError(f"GitOps MCP {required_name} exceed Odoo support")
    required_identity = compatibility.get("required_agent_identity") or {}
    available_identity = contract.get("agent_identity") or {}
    if (
        required_identity.get("method") != available_identity.get("method")
        or required_identity.get("principal_kind") != available_identity.get("principal_kind")
        or not isinstance(required_identity.get("schema_version"), int)
        or not isinstance(available_identity.get("schema_version"), int)
        or required_identity["schema_version"] > available_identity["schema_version"]
        or not isinstance(required_identity.get("fields"), list)
        or not isinstance(available_identity.get("fields"), list)
        or set(required_identity["fields"]) - set(available_identity["fields"])
    ):
        raise RuntimeError("GitOps MCP Agent identity exceeds Odoo support")


def _candidate_mcp_authority(target, release: dict) -> dict | None:
    authority = _mcp_runtime_authority(target)
    if authority is not None:
        root, _commit = _gitops_root()
        manifest = _load_gitops_json(
            root, f"komodo/releases/usl-odoo-{target.value['environment']}-mcp-manifest.json",
        )
        _validate_candidate_mcp_support(release, manifest.get("compatibility") or {})
    return authority


def _with_mcp_runtime_authority(target, runner, identity: dict, authority: dict | None) -> dict:
    """Append the final MCP override so no historical overlay can downgrade it."""
    if authority is None:
        return identity
    authority_prefix = target.value["state_directory"] + "/authorities/"
    base_identity = {
        **identity,
        "compose_files": [
            path for path in identity["compose_files"] if not path.startswith(authority_prefix)
        ],
    }
    services = set(
        runner.run(compose_command(base_identity, ["config", "--services"])).stdout.splitlines(),
    )
    mcp_service = target.value["services"]["mcp"]
    if mcp_service not in services:
        raise RuntimeError("MCP service is absent from Compose")
    overridden = {mcp_service: {"image": authority["image"]}}
    if "odoo-mcp-oauth-init" in services:
        overridden["odoo-mcp-oauth-init"] = {"image": authority["image"]}
    root = f"{target.value['state_directory']}/authorities"
    path = f"{root}/mcp-{authority['sha256']}.json"
    runner.run(["install", "-d", "-m", "0700", root])
    _write_remote(
        target, runner, path,
        json.dumps({"services": overridden}, indent=2, sort_keys=True) + "\n",
        "0444",
    )
    result = {**base_identity, "compose_files": [*base_identity["compose_files"], path]}
    rendered = _runtime_images(runner, result)
    _independent_mcp_image(target, rendered, authority)
    if "odoo-mcp-oauth-init" in overridden and rendered.get("odoo-mcp-oauth-init") != authority["image"]:
        raise RuntimeError("MCP OAuth initializer differs from GitOps authority")
    return result


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
    authority = _mcp_runtime_authority(target)
    if authority is not None:
        service = target.value["services"]["mcp"]
        container = containers.get(service)
        if not container:
            raise RuntimeError(f"release service is not running: {service}")
        actual = runner.run(
            ["docker", "inspect", container, "--format", "{{.Config.Image}}"],
        ).stdout.strip()
        if actual != authority["image"]:
            raise RuntimeError("running MCP image differs from GitOps authority")
        verified["mcp"] = authority["image"]
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
    mcp_authority = _candidate_mcp_authority(target, release)
    candidate_identity = _candidate_compose_identity(target, runner, current["compose"])
    for definition in target.value["storage"]["tiers"].values():
        path = definition["path"]
        if runner.run(["test", "-d", path], check=False).returncode:
            raise RuntimeError(f"release storage path is unavailable: {path}")
    for name in target.value["external_networks"].values():
        if runner.run(["docker", "network", "inspect", name], check=False).returncode:
            raise RuntimeError(f"release external network is unavailable: {name}")
    capacity_before_pull = _require_restore_capacity(target, runner, "prepare")
    for image in _release_images(release, mcp_authority):
        _ensure_image(runner, image)
    tool_image = _operations_image(release)
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
        render_identity = _with_mcp_runtime_authority(
            target, runner, {**release_identity, "compose_files": compose_files}, mcp_authority,
        )
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
        if mcp_authority is not None:
            expected_images[target.value["services"]["mcp"]] = mcp_authority["image"]
            if "odoo-mcp-oauth-init" in rendered_services:
                expected_images["odoo-mcp-oauth-init"] = mcp_authority["image"]
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
        "images": sorted(_release_images(release, mcp_authority)),
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


def _backup_run_receipt(
    value: object,
    *,
    target: str,
    run_id: str | None = None,
    require_quiesced: bool = False,
    expected_writer_services: list[str] | None = None,
) -> dict:
    """Validate an exact qualified cohort produced by ``backup create``."""
    required = {
        "schema", "run_id", "capture", "upload", "qualification", "performance",
        "runtime_images", "writers_quiesced", "writer_interval_complete",
        "writers_stopped_at", "writers_resumed_at", "quiescence", "status", "sha256",
    }
    if not isinstance(value, dict) or set(value) != required:
        raise RuntimeError("backup receipt fields differ")
    if value.get("schema") != "usl-backup-run/v2":
        raise RuntimeError("backup receipt schema differs")
    if value.get("status") != "qualified":
        raise RuntimeError("backup receipt is not qualified")
    if run_id is not None and value.get("run_id") != run_id:
        raise RuntimeError("backup receipt run identity differs")
    capture = value.get("capture")
    upload = value.get("upload")
    qualification = value.get("qualification")
    if not all(isinstance(item, dict) for item in (capture, upload, qualification)):
        raise RuntimeError("backup receipt cohort evidence is incomplete")
    try:
        validate_cohort_manifest(capture)
    except (ValueError, KeyError) as error:
        raise RuntimeError("backup receipt capture manifest is invalid") from error
    state_fields = {
        "schema", "cohort_schema", "run_id", "target", "durable_snapshot_id",
        "cache_snapshot_id", "status",
    }
    snapshot = qualification.get("durable_snapshot_id")
    cache_snapshot = qualification.get("cache_snapshot_id")
    if (
        capture.get("schema") != RECOVERY_COHORT_SCHEMA
        or capture.get("target") != target
        or not re.fullmatch(r"[0-9a-f]{64}", str(capture.get("release", {}).get("identity")))
        or set(upload) != state_fields
        or set(qualification) != state_fields | {"qualified_from_snapshot_id"}
        or upload.get("schema") != "usl-recovery-cohort-state/v1"
        or qualification.get("schema") != "usl-recovery-cohort-state/v1"
        or upload.get("run_id") != value.get("run_id")
        or qualification.get("run_id") != value.get("run_id")
        or upload.get("target") != target
        or qualification.get("target") != target
        or qualification.get("cohort_schema") != RECOVERY_COHORT_SCHEMA
        or qualification.get("status") != "qualified"
        or not re.fullmatch(r"[0-9a-f]{64}", str(snapshot))
        or not re.fullmatch(r"[0-9a-f]{64}", str(cache_snapshot))
        or upload.get("durable_snapshot_id") != qualification.get("qualified_from_snapshot_id")
        or not re.fullmatch(r"[0-9a-f]{64}", str(upload.get("durable_snapshot_id")))
        or upload.get("cache_snapshot_id") != cache_snapshot
    ):
        raise RuntimeError("backup receipt cohort identity differs")
    if not isinstance(value.get("runtime_images"), dict) or not value["runtime_images"]:
        raise RuntimeError("backup receipt runtime image evidence is invalid")
    for image in value["runtime_images"].values():
        if not isinstance(image, str) or "@sha256:" not in image:
            raise RuntimeError("backup receipt runtime image evidence is invalid")
    performance = value.get("performance")
    performance_fields = {
        "capture_pause_seconds", "writer_freeze_seconds", "writer_freeze_sla_seconds",
        "writer_freeze_sla_passed", "upload_seconds", "verification_seconds",
        "total_seconds",
    }
    if not isinstance(performance, dict) or set(performance) != performance_fields:
        raise RuntimeError("backup receipt performance evidence is invalid")
    try:
        created = datetime.fromisoformat(str(capture["created_at"]).replace("Z", "+00:00"))
    except ValueError as error:
        raise RuntimeError("backup receipt capture timestamp is invalid") from error
    if created.tzinfo is None:
        raise RuntimeError("backup receipt capture timestamp is invalid")
    if require_quiesced and (
        value.get("writers_quiesced") is not True
        or value.get("writer_interval_complete") is not False
        or value.get("writers_resumed_at") is not None
        or not isinstance(value.get("writers_stopped_at"), str)
    ):
        raise RuntimeError("staging checkpoint did not leave writers quiesced")
    quiescence = value.get("quiescence")
    if require_quiesced:
        required_quiescence = {
            "schema", "target", "run_id", "baseline_runtime_sha256",
            "writer_services", "prepared_at", "stopped_at", "status", "sha256",
        }
        if (
            not isinstance(quiescence, dict)
            or set(quiescence) != required_quiescence
            or quiescence["schema"] != "usl-backup-quiescence/v2"
            or quiescence["target"] != target
            or quiescence["run_id"] != value["run_id"]
            or quiescence["status"] != "quiesced"
            or not re.fullmatch(r"[0-9a-f]{64}", str(quiescence["baseline_runtime_sha256"]))
            or not isinstance(quiescence["writer_services"], list)
            or not quiescence["writer_services"]
            or expected_writer_services is not None
            and quiescence["writer_services"] != expected_writer_services
            or quiescence["stopped_at"] != value["writers_stopped_at"]
        ):
            raise RuntimeError("backup quiescence receipt is invalid")
        try:
            stopped = datetime.fromisoformat(
                str(quiescence["stopped_at"]).replace("Z", "+00:00"),
            )
        except ValueError as error:
            raise RuntimeError("backup quiescence timestamp is invalid") from error
        if stopped.tzinfo is None:
            raise RuntimeError("backup quiescence timestamp is invalid")
        body = {key: item for key, item in quiescence.items() if key != "sha256"}
        if quiescence["sha256"] != hashlib.sha256(
            json.dumps(body, sort_keys=True, separators=(",", ":")).encode(),
        ).hexdigest():
            raise RuntimeError("backup quiescence receipt digest differs")
    elif quiescence is not None:
        raise RuntimeError("resumed backup unexpectedly claims writer quiescence")
    body = {key: item for key, item in value.items() if key != "sha256"}
    if value["sha256"] != hashlib.sha256(
        json.dumps(body, sort_keys=True, separators=(",", ":")).encode(),
    ).hexdigest():
        raise RuntimeError("backup receipt digest differs")
    return value


def _backup_quiescence_receipt(
    *, target: str, run_id: str, baseline: str, services: list[str], status: str,
    prepared_at: str, stopped_at: str | None,
) -> dict:
    if status not in {"prepared", "quiesced", "resumed"}:
        raise RuntimeError("backup quiescence status is invalid")
    value = {
        "schema": "usl-backup-quiescence/v2",
        "target": target,
        "run_id": run_id,
        "baseline_runtime_sha256": baseline,
        "writer_services": services,
        "prepared_at": prepared_at,
        "stopped_at": stopped_at,
        "status": status,
    }
    value["sha256"] = hashlib.sha256(json.dumps(
        value, sort_keys=True, separators=(",", ":"),
    ).encode()).hexdigest()
    return value


def _validate_backup_quiescence_receipt(
    value: object, *, target: str, run_id: str, services: list[str],
) -> dict:
    required = {
        "schema", "target", "run_id", "baseline_runtime_sha256", "writer_services",
        "prepared_at", "stopped_at", "status", "sha256",
    }
    if not isinstance(value, dict) or set(value) != required:
        raise RuntimeError("backup quiescence receipt fields differ")
    if (
        value["schema"] != "usl-backup-quiescence/v2"
        or value["target"] != target
        or value["run_id"] != run_id
        or value["writer_services"] != services
        or value["status"] not in {"prepared", "quiesced", "resumed"}
        or not re.fullmatch(r"[0-9a-f]{64}", str(value["baseline_runtime_sha256"]))
        or value["status"] == "prepared" and value["stopped_at"] is not None
        or value["status"] != "prepared" and not isinstance(value["stopped_at"], str)
    ):
        raise RuntimeError("backup quiescence receipt identity differs")
    timestamps = {}
    for field in ("prepared_at", "stopped_at"):
        if value[field] is None:
            continue
        try:
            timestamp = datetime.fromisoformat(str(value[field]).replace("Z", "+00:00"))
        except ValueError as error:
            raise RuntimeError("backup quiescence timestamp is invalid") from error
        if timestamp.tzinfo is None:
            raise RuntimeError("backup quiescence timestamp is invalid")
        timestamps[field] = timestamp
    if (
        "stopped_at" in timestamps
        and timestamps["stopped_at"] < timestamps["prepared_at"]
    ):
        raise RuntimeError("backup quiescence phase order is invalid")
    body = {key: item for key, item in value.items() if key != "sha256"}
    if value["sha256"] != hashlib.sha256(json.dumps(
        body, sort_keys=True, separators=(",", ":"),
    ).encode()).hexdigest():
        raise RuntimeError("backup quiescence receipt digest differs")
    return value


def _staging_checkpoint_receipt(
    value: object,
    *,
    target,
    attempt: str,
    release: str,
) -> dict:
    required = {
        "schema", "target", "attempt", "candidate_release", "snapshot",
        "cache_snapshot", "baseline_generation", "baseline_release",
        "baseline_runtime_sha256",
        "upgrade_plan_sha256", "prepare_receipt_sha256",
        "maintenance_receipt_sha256", "resources_sha256", "controls_sha256",
        "checkpointed_at", "status", "sha256",
    }
    if not isinstance(value, dict) or set(value) != required:
        raise RuntimeError("staging checkpoint receipt fields differ")
    if (
        value["schema"] != "usl-staging-checkpoint/v1"
        or value["target"] != target.name
        or target.value["environment"] != "staging"
        or value["attempt"] != attempt
        or value["candidate_release"] != release
        or value["status"] != "checkpointed"
        or not re.fullmatch(r"[0-9a-f]{64}", str(value["snapshot"]))
        or not re.fullmatch(r"[0-9a-f]{64}", str(value["cache_snapshot"]))
        or not re.fullmatch(r"[0-9a-f]{64}", str(value["baseline_release"]))
        or not re.fullmatch(r"[0-9a-f]{64}", str(value["baseline_runtime_sha256"]))
        or any(
            not re.fullmatch(r"[0-9a-f]{64}", str(value[field]))
            for field in (
                "upgrade_plan_sha256", "prepare_receipt_sha256",
                "maintenance_receipt_sha256", "resources_sha256", "controls_sha256",
            )
        )
        or value["baseline_generation"] is not None
        and not GENERATION_NAME.fullmatch(str(value["baseline_generation"]))
    ):
        raise RuntimeError("staging checkpoint receipt identity differs")
    try:
        created = datetime.fromisoformat(str(value["checkpointed_at"]).replace("Z", "+00:00"))
    except ValueError as error:
        raise RuntimeError("staging checkpoint timestamp is invalid") from error
    if created.tzinfo is None:
        raise RuntimeError("staging checkpoint timestamp is invalid")
    body = {key: item for key, item in value.items() if key != "sha256"}
    if value["sha256"] != hashlib.sha256(
        json.dumps(body, sort_keys=True, separators=(",", ":")).encode(),
    ).hexdigest():
        raise RuntimeError("staging checkpoint receipt digest differs")
    return value


def _staging_reset_intent_receipt(value: object, *, target, admission: dict) -> dict:
    """Validate the immutable pre-production intent that authorizes a later reset."""
    required = {
        "schema", "staging_target", "staging_baseline_generation",
        "staging_baseline_release", "production_attempt", "production_release",
        "staging_baseline_runtime_sha256",
        "gitops_commit", "production_prepare_receipt_sha256",
        "production_upgrade_plan_sha256", "created_at", "status", "sha256",
    }
    if not isinstance(value, dict) or set(value) != required:
        raise RuntimeError("staging reset intent fields differ")
    if (
        value["schema"] != "usl-staging-reset-intent/v1"
        or value["staging_target"] != target.name
        or value["production_attempt"] != admission["attempt"]
        or value["production_release"] != admission["release"]
        or value["status"] != "planned"
        or not re.fullmatch(r"[0-9a-f]{64}", str(value["staging_baseline_release"]))
        or not re.fullmatch(
            r"[0-9a-f]{64}", str(value["staging_baseline_runtime_sha256"]),
        )
        or not re.fullmatch(r"[0-9a-f]{40}", str(value["gitops_commit"]))
        or not re.fullmatch(
            r"[0-9a-f]{64}", str(value["production_prepare_receipt_sha256"]),
        )
        or not re.fullmatch(
            r"[0-9a-f]{64}", str(value["production_upgrade_plan_sha256"]),
        )
        or value["staging_baseline_generation"] is not None
        and not GENERATION_NAME.fullmatch(str(value["staging_baseline_generation"]))
    ):
        raise RuntimeError("staging reset intent identity differs")
    try:
        created = datetime.fromisoformat(str(value["created_at"]).replace("Z", "+00:00"))
    except ValueError as error:
        raise RuntimeError("staging reset intent timestamp is invalid") from error
    if created.tzinfo is None:
        raise RuntimeError("staging reset intent timestamp is invalid")
    body = {key: item for key, item in value.items() if key != "sha256"}
    if value["sha256"] != hashlib.sha256(
        json.dumps(body, sort_keys=True, separators=(",", ":")).encode(),
    ).hexdigest():
        raise RuntimeError("staging reset intent digest differs")
    return value


def _staging_reset_deferred_receipt(
    *, target, admission: dict, intent: dict, observed: dict,
    observed_release: str, observed_runtime_sha256: str,
) -> dict:
    """Describe a no-touch reset deferral after staging advances."""
    body = {
        "schema": "usl-staging-reset-deferred/v1",
        "target": target.name,
        "production_attempt": admission["attempt"],
        "production_release": admission["release"],
        "intent_sha256": intent["sha256"],
        "baseline": {
            "generation": intent["staging_baseline_generation"],
            "release": intent["staging_baseline_release"],
            "runtime_sha256": intent["staging_baseline_runtime_sha256"],
        },
        "observed": {
            "generation": observed.get("generation"),
            "release": observed_release,
            "runtime_sha256": observed_runtime_sha256,
        },
        "reason": "staging-advanced",
        "deferred_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "status": "deferred",
    }
    body["sha256"] = hashlib.sha256(json.dumps(
        body, sort_keys=True, separators=(",", ":"),
    ).encode()).hexdigest()
    return body


def _runtime_baseline_sha256(runtime: dict) -> str:
    """Fingerprint every persistent/runtime identity relevant to cutover CAS."""
    compose = runtime.get("compose") or {}
    body = {
        "generation": runtime.get("generation"),
        "active_state": runtime.get("active_state"),
        "volumes": {
            role: {
                "name": item.get("name"),
                "path": item.get("path"),
            }
            for role, item in sorted((runtime.get("volumes") or {}).items())
        },
        "compose": {
            key: compose.get(key)
            for key in (
                "project", "working_directory", "environment_file", "profiles",
                "anchor_service", "compose_files", "gitops_commit",
            )
        },
    }
    return hashlib.sha256(
        json.dumps(body, sort_keys=True, separators=(",", ":")).encode(),
    ).hexdigest()


def _runtime_cas_sha256(target, runner, runtime: dict) -> str:
    """Bind runtime topology to rendered Compose and the exact active release."""
    release, release_sha256, _release_raw = _release(target, runner, None)
    mcp_authority = _mcp_runtime_authority(target)
    identity = _with_mcp_runtime_authority(
        target, runner, runtime["compose"], mcp_authority,
    )
    try:
        rendered = json.loads(runner.run(
            compose_command(identity, ["config", "--format", "json"]),
        ).stdout)
    except (KeyError, json.JSONDecodeError) as error:
        raise RuntimeError("runtime CAS Compose render is invalid") from error
    mcp_runtime_image = None
    if mcp_authority is not None:
        mcp_service = target.value["services"]["mcp"]
        containers = [
            item.get("ID") for item in runtime.get("containers", [])
            if item.get("Service") == mcp_service
        ]
        if len(containers) != 1:
            raise RuntimeError("runtime CAS MCP identity is ambiguous")
        mcp_runtime_image = runner.run([
            "docker", "inspect", containers[0], "--format", "{{.Config.Image}}",
        ]).stdout.strip()
        if mcp_runtime_image != mcp_authority["image"]:
            raise RuntimeError("runtime CAS MCP image differs from GitOps authority")
    body = {
        "runtime_sha256": _runtime_baseline_sha256(runtime),
        "release": release["identity"],
        "release_manifest_sha256": release_sha256,
        "mcp_authority_sha256": None if mcp_authority is None else mcp_authority["sha256"],
        "mcp_runtime_image": mcp_runtime_image,
        "compose_sha256": hashlib.sha256(json.dumps(
            rendered, sort_keys=True, separators=(",", ":"),
        ).encode()).hexdigest(),
    }
    return hashlib.sha256(json.dumps(
        body, sort_keys=True, separators=(",", ":"),
    ).encode()).hexdigest()


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
    elif value.get("schema") == "usl-staging-upgrade-plan-evidence/v2":
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


def _wait_compose_services(target, runner, services: list[str], timeout: int = 120) -> None:
    deadline = time.monotonic() + timeout
    last = ""
    while time.monotonic() < deadline:
        pending = []
        for service in services:
            containers = runner.run(
                [
                    "docker", "ps", "-a",
                    "--filter", f"label=com.docker.compose.project={target.project}",
                    "--filter", f"label=com.docker.compose.service={service}",
                    "--format", "{{.ID}}",
                ],
            ).stdout.splitlines()
            if len(containers) != 1:
                pending.append(f"{service}:ambiguous")
                continue
            try:
                state = json.loads(runner.run(
                    ["docker", "inspect", containers[0], "--format", "{{json .State}}"],
                ).stdout)
            except json.JSONDecodeError as error:
                raise RuntimeError("rollback container state is invalid") from error
            health = state.get("Health", {}).get("Status")
            if state.get("Running") is not True or health not in {None, "healthy"}:
                pending.append(f"{service}:{health or state.get('Status', 'unknown')}")
        if not pending:
            return
        last = ", ".join(pending)
        time.sleep(2)
    raise RuntimeError(f"rollback services did not become ready: {last}")


def _start_rollback_identity(target, runner, identity: dict) -> None:
    """Start a rollback generation without returning legacy ingress to Odoo."""
    identity = _with_mcp_runtime_authority(
        target, runner, identity, _mcp_runtime_authority(target),
    )
    adoption = target.value["compose"].get("adoption")
    if adoption is None or identity.get("anchor_service") != adoption["legacy_anchor_service"]:
        runner.run(compose_command(
            identity,
            ["up", "--detach", "--wait", "--force-recreate", "--no-deps", *_compose_services(target, identity)],
        ))
        return

    gateway = _gateway_container(target, runner)
    if not gateway:
        raise RuntimeError("legacy rollback requires the stable staging gateway")
    candidate_identity = _candidate_compose_identity(target, runner, identity)
    _validate_gateway_container(target, runner, gateway, candidate_identity)
    ingress = target.value["external_networks"]["ingress"]
    if _network_alias_owners(runner, ingress, "odoo-staging") != [gateway]:
        raise RuntimeError("legacy rollback gateway does not uniquely own public ingress")

    services = _compose_services(target, identity)
    runner.run(compose_command(
        identity,
        ["create", "--force-recreate", "--no-deps", *services],
    ))
    legacy = runner.run(
        [
            "docker", "ps", "-a",
            "--no-trunc",
            "--filter", f"label=com.docker.compose.project={target.project}",
            "--filter", f"label=com.docker.compose.service={adoption['legacy_anchor_service']}",
            "--format", "{{.ID}}",
        ],
    ).stdout.splitlines()
    if len(legacy) != 1:
        raise RuntimeError("legacy rollback Odoo identity is ambiguous")
    networks = _container_networks(runner, legacy[0])
    default_network = target.value["compose"]["default_network"]
    if "odoo-staging-app" not in (networks.get(default_network) or {}).get("Aliases", []):
        raise RuntimeError("legacy rollback backend alias is missing")
    if ingress in networks:
        runner.run(["docker", "network", "disconnect", ingress, legacy[0]])
    if _network_alias_owners(runner, ingress, "odoo-staging") != [gateway]:
        raise RuntimeError("legacy rollback retained duplicate public ingress")
    runner.run(compose_command(identity, ["start", *services]))
    _wait_compose_services(target, runner, services)


def _rollback_after_failure(target, runner, identity: dict, error: Exception) -> None:
    _report("restore", "rollback", "started", f"activation failed: {error}")
    try:
        _start_rollback_identity(target, runner, identity)
    except Exception as rollback_error:
        raise RuntimeError(
            f"activation failed ({error}); rollback also failed ({rollback_error})",
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


def _recover_interrupted_backup_lock(
    target,
    runner,
    *,
    run_id: str,
    quiescence: dict,
) -> bool:
    """Remove only an exact, crash-left backup lock under launcher exclusivity.

    The fixed host launcher holds its kernel ``flock`` across every lifecycle
    command.  Consequently, an inner lock observed by ``resume-staging`` cannot
    have a live owner.  This helper still requires the immutable host-side
    quiescence artifact for the same run before removing the lock; callers that
    bypass the launcher fail closed instead of guessing whether an owner lives.
    """
    root = target.value["state_directory"]
    lock = f"{root}/operation.lock"
    owner_result = runner.run(["cat", f"{lock}/owner.json"], check=False)
    if owner_result.returncode:
        present = runner.run(["test", "-d", lock], check=False)
        if present.returncode:
            return False
        raise RuntimeError("operation lock exists without exact owner evidence")
    try:
        owner = json.loads(owner_result.stdout)
    except json.JSONDecodeError as error:
        raise RuntimeError("operation lock owner evidence is invalid") from error
    if (
        not isinstance(owner, dict)
        or set(owner) != {"schema", "target", "operation", "run_id", "started_at"}
        or owner.get("schema") != "usl-operation-lock/v1"
        or owner.get("target") != target.name
        or owner.get("operation") != "backup"
        or owner.get("run_id") != run_id
    ):
        raise RuntimeError("another operation still owns the target")
    try:
        started = datetime.fromisoformat(str(owner["started_at"]).replace("Z", "+00:00"))
    except ValueError as error:
        raise RuntimeError("operation lock timestamp is invalid") from error
    if started.tzinfo is None:
        raise RuntimeError("operation lock timestamp is invalid")

    artifact_root = f"{root}/backup-runs/{run_id}"
    exact_artifact = None
    for filename in ("quiesced.json", "prepared.json"):
        result = runner.run(["cat", f"{artifact_root}/{filename}"], check=False)
        if result.returncode:
            continue
        try:
            candidate = json.loads(result.stdout)
        except json.JSONDecodeError:
            continue
        if candidate.get("sha256") == quiescence.get("sha256"):
            exact_artifact = candidate
            break
    if exact_artifact != quiescence:
        raise RuntimeError("live or unproven backup lock cannot be recovered")
    removed = runner.run(["rm", "-f", f"{lock}/owner.json"], check=False)
    if removed.returncode or runner.run(["rmdir", lock], check=False).returncode:
        raise RuntimeError("interrupted backup lock could not be recovered")
    return True


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
    content = json.dumps(event, sort_keys=True) + "\n"
    program = (
        "import os,pathlib,sys;"
        "p=pathlib.Path(sys.argv[1]);p.parent.mkdir(parents=True,exist_ok=True);"
        "f=p.open('ab');f.write(sys.stdin.buffer.read());f.flush();os.fsync(f.fileno());"
        "f.close();os.chmod(p,0o600)"
    )
    runner.run(["python3", "-c", program, path], input_text=content)
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
    image = _operations_image(release)
    if arguments.action == "prune":
        if target.name != "staging" or not all(
            repository.startswith("/var/lib/usl-odoo/restic/staging/")
            for repository in target.value["backup"].values()
        ):
            raise RuntimeError("backup prune requires local staging repositories")
        run_id = f"retention-{datetime.now(UTC):%Y%m%dt%H%M%S}"
        with runtime_lock(target, runner, "retention", run_id):
            result = _run_cohort(target, runner, image, "retention-apply", [], volumes=runtime["volumes"])
    elif arguments.action == "list":
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
            writer_services = []
            quiescence = None
            baseline_runtime_sha256 = _runtime_cas_sha256(target, runner, runtime)
            if not arguments.resume:
                identity = compose_identity(target, runner)
                identity = _with_mcp_runtime_authority(
                    target, runner, identity, _mcp_runtime_authority(target),
                )
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
                        f"USL_RELEASE_IDENTITY={release['identity']}",
                        "--env",
                        f"USL_RELEASE_MANIFEST_SHA256={release_sha}",
                        "--env",
                        f"USL_RELEASE_MANIFEST_JSON={release_raw}",
                        "--env",
                        f"USL_RUNTIME_IMAGES_JSON={json.dumps(runtime_images, sort_keys=True)}",
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
                prepared_at = datetime.now(UTC).isoformat().replace("+00:00", "Z")
                quiescence_root = f"{target.value['state_directory']}/backup-runs/{run_id}"
                runner.run(["install", "-d", "-m", "0700", quiescence_root])
                prepared_quiescence = _backup_quiescence_receipt(
                    target=target.name,
                    run_id=run_id,
                    baseline=baseline_runtime_sha256,
                    services=writer_services,
                    status="prepared",
                    prepared_at=prepared_at,
                    stopped_at=None,
                )
                _write_remote(
                    target, runner, f"{quiescence_root}/prepared.json",
                    json.dumps(prepared_quiescence, indent=2, sort_keys=True) + "\n", "0444",
                )
                _record_event(target, runner, run_id, "backup", "capture", "started")
                runner.run(compose_command(
                    identity, ["stop", "--timeout", "30", *writer_services],
                ))
                writers_stopped_at = datetime.now(UTC).isoformat().replace("+00:00", "Z")
                stopped_quiescence = _backup_quiescence_receipt(
                    target=target.name,
                    run_id=run_id,
                    baseline=baseline_runtime_sha256,
                    services=writer_services,
                    status="quiesced",
                    prepared_at=prepared_at,
                    stopped_at=writers_stopped_at,
                )
                _write_remote(
                    target, runner, f"{quiescence_root}/quiesced.json",
                    json.dumps(stopped_quiescence, indent=2, sort_keys=True) + "\n", "0444",
                )
                capture_succeeded = False
                try:
                    captured = capture_phase()
                    _write_remote(
                        target,
                        runner,
                        f"{quiescence_root}/capture.json",
                        json.dumps(captured, indent=2, sort_keys=True) + "\n",
                        "0444",
                    )
                    capture_succeeded = True
                finally:
                    if not leave_quiesced or not capture_succeeded:
                        runner.run(compose_command(
                            identity,
                            ["up", "--detach", "--wait", "--no-recreate", *writer_services],
                        ))
                        writers_resumed_at = datetime.now(UTC).isoformat().replace("+00:00", "Z")
                if leave_quiesced:
                    quiescence = stopped_quiescence
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
            else:
                capture_path = (
                    f"{target.value['state_directory']}/backup-runs/{run_id}/capture.json"
                )
                try:
                    captured = json.loads(_read_path(target, runner, Path(capture_path)))
                    validate_cohort_manifest(captured)
                except (json.JSONDecodeError, ValueError, KeyError) as error:
                    raise RuntimeError(
                        "resumed backup lacks its exact captured cohort manifest",
                    ) from error
                if (
                    captured.get("run_id") != run_id
                    or captured.get("target") != target.name
                    or captured.get("release", {}).get("identity") != release["identity"]
                    or captured.get("release", {}).get("manifest_sha256") != release_sha
                ):
                    raise RuntimeError("resumed backup capture identity differs")
            def resume_failed_quiescence() -> None:
                if not leave_quiesced or not writer_services:
                    return
                runner.run(compose_command(
                    identity,
                    ["up", "--detach", "--wait", "--no-recreate", *writer_services],
                ))
                if _runtime_cas_sha256(
                    target, runner, inspect_runtime(target, runner),
                ) != baseline_runtime_sha256:
                    raise RuntimeError("writer recovery changed the backup baseline")
            upload_started = time.monotonic()
            _record_event(target, runner, run_id, "backup", "upload", "started")
            try:
                uploaded = _run_cohort(
                    target,
                    runner,
                    image,
                    "push",
                    ["--run-id", run_id],
                    volumes=runtime["volumes"],
                )
            except Exception:
                resume_failed_quiescence()
                raise
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
            try:
                qualified = _run_cohort(
                    target,
                    runner,
                    image,
                    "qualify",
                    ["--durable-snapshot", uploaded["durable_snapshot_id"]],
                    volumes=runtime["volumes"],
                )
            except Exception:
                resume_failed_quiescence()
                raise
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
                "schema": "usl-backup-run/v2",
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
                "quiescence": quiescence,
                "status": "qualified",
            }
            result["sha256"] = hashlib.sha256(json.dumps(
                result, sort_keys=True, separators=(",", ":"),
            ).encode()).hexdigest()
            receipt_root = f"{target.value['state_directory']}/backup-runs/{run_id}"
            runner.run(["install", "-d", "-m", "0700", receipt_root])
            _write_remote(
                target,
                runner,
                f"{receipt_root}/receipt.json",
                json.dumps(result, indent=2, sort_keys=True) + "\n",
                "0444",
            )
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
        ["curl", "--silent", "--show-error", "--fail", "--max-time", "10",
         "--header", "Host: " + urlsplit(target.value["endpoints"]["mcp"]).netloc, url],
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
    authority = _mcp_runtime_authority(target)
    identity = _with_mcp_runtime_authority(target, runner, identity, authority)
    images = _runtime_images(runner, identity)
    expected_mcp = _independent_mcp_image(target, images, authority)
    service = target.value["services"]["mcp"]
    containers = runner.run([
        "docker", "ps", "--filter", f"label=com.docker.compose.project={target.project}",
        "--filter", f"label=com.docker.compose.service={service}", "--format", "{{.ID}}",
    ]).stdout.splitlines()
    if len(containers) != 1:
        raise RuntimeError("running MCP identity is ambiguous")
    actual = runner.run(
        ["docker", "inspect", containers[0], "--format", "{{.Config.Image}}"],
    ).stdout.strip()
    if actual != expected_mcp:
        raise RuntimeError("running MCP image differs from GitOps authority")
    mcp = _mcp_readiness(target, runner)
    sign = _validate_sign_readiness(_sign_readiness(target, runner, identity))
    return {
        "mcp": mcp,
        "mcp_runtime": {
            "image": expected_mcp,
            "authority_sha256": None if authority is None else authority["sha256"],
        },
        "sign": sign,
    }


def _legacy_staging_baseline(target, runner, runtime: dict, release: dict) -> dict:
    """Prove the one v2 staging baseline without assuming candidate-only schema."""
    if target.value["environment"] != "staging" or release.get("schema") != "usl-release/v2":
        raise RuntimeError("legacy baseline admission requires an active staging v2 release")
    expected = set(_compose_services(target, runtime["compose"]))
    containers = {item.get("Service"): item for item in runtime["containers"]}
    unhealthy = sorted(
        service
        for service in expected
        if service not in containers
        or containers[service].get("State") != "running"
        or containers[service].get("Health") not in {None, "", "healthy"}
    )
    if unhealthy:
        raise RuntimeError("legacy staging services are not healthy: " + ", ".join(unhealthy))
    endpoints = {}
    paths = {"odoo": "/web/health?db_server_status=1", "paperless": "/api/", "mcp": "/healthz"}
    for name, path in paths.items():
        url = target.value["admission_endpoints"][name].rstrip("/") + path
        process = runner.run([
            "curl", "--silent", "--show-error", "--output", "/dev/null",
            "--write-out", "%{http_code}", "--max-time", "10", url,
        ], check=False)
        code = int(process.stdout) if process.returncode == 0 and process.stdout.isdigit() else 0
        ok = code == 200 if name in {"odoo", "mcp"} else 200 <= code < 500
        endpoints[name] = {"url": url, "status_code": code, "ok": ok}
    if not all(item["ok"] for item in endpoints.values()):
        raise RuntimeError("legacy staging baseline endpoint is unavailable")
    query = """
SELECT json_build_object(
  'database', current_database(),
  'companies', (SELECT count(*) FROM res_company),
  'users', (SELECT count(*) FROM res_users),
  'moves', (SELECT count(*) FROM account_move),
  'attachments', (SELECT count(*) FROM ir_attachment),
  'ledger_delta', (SELECT coalesce(sum(balance), 0) FROM account_move_line WHERE parent_state = 'posted')
)::text
"""
    try:
        database = json.loads(_psql(target, runner, runtime["compose"], "odoo", query))
    except (RuntimeError, json.JSONDecodeError) as error:
        raise RuntimeError("legacy staging baseline database probe failed") from error
    if (
        database.get("database") != target.value["databases"]["odoo"]["name"]
        or min(database.get(key, 0) for key in ("companies", "users", "moves", "attachments")) < 1
        or float(database.get("ledger_delta", 1)) != 0
    ):
        raise RuntimeError("legacy staging baseline business controls differ")
    body = {
        "schema": "usl-legacy-staging-baseline/v1",
        "target": target.name,
        "release_schema": release["schema"],
        "release_commit": release["source"]["commit"],
        "generation": runtime.get("generation"),
        "runtime_sha256": _runtime_cas_sha256(target, runner, runtime),
        "endpoints": endpoints,
        "database": database,
        "status": "passed",
    }
    body["sha256"] = hashlib.sha256(
        json.dumps(body, sort_keys=True, separators=(",", ":")).encode(),
    ).hexdigest()
    return body


def _runtime_boundary_gates(target, runner, targets: Path, runtime: dict) -> tuple[dict, dict]:
    """Use the active release's own boundary during rollback validation."""
    release, _release_sha256, _release_raw = _release(target, runner, None)
    if release.get("schema") == "usl-release/v2":
        baseline = _legacy_staging_baseline(target, runner, runtime, release)
        return baseline, baseline
    return _gate(health_command, target, targets), _gate(smoke_command, target, targets)


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
                "--header",
                "Host: " + urlsplit(target.value["endpoints"][name]).netloc,
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
        release_definitions_sha256 = release_definitions_digest(json.loads(
            _psql(target, runner, identity, "odoo", RELEASE_DEFINITIONS_SQL),
        ))
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
        cron_status["lagging_jobs"] = odoo["cron_lag"]
        cron_status["warning"] = "scheduled jobs are late; inspect after workers resume"
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
        "release_definitions_sha256": release_definitions_sha256,
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
    created_volumes: list[str] = []
    created_paths: list[str] = []
    network_created = False
    try:
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
                created_paths.append(device)
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
            created_volumes.append(name)
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
        network_created = True
    except Exception:
        if network_created:
            runner.run(["docker", "network", "rm", network], check=False)
        for name in reversed(created_volumes):
            runner.run(["docker", "volume", "rm", name], check=False)
        for path in reversed(created_paths):
            runner.run(["rmdir", "--", path], check=False)
        raise
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
    network: str | list[str],
    volumes: dict[str, str],
    source_backup_env: str,
    target_environment_file: str | None = None,
    proof_identity: tuple[str, str] | None = None,
) -> list[str]:
    generation_root = f"{target.value['state_directory']}/generations/{generation}"
    command = [
        "docker",
        "run",
        "--rm",
        "--env-file",
        target_environment_file or target.value["secrets"]["env_file"],
        "--env-file",
        source_backup_env,
        "--env",
        f"RESTIC_REPOSITORY={source.value['backup']['durable_repository']}",
        "--env",
        f"USL_BACKUP_CACHE_REPOSITORY={source.value['backup']['cache_repository']}",
        *_local_restic_mounts(source),
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
        f"{generation_root}/mcp-secrets:/target/mcp-secrets",
        "--volume",
        f"{generation_root}/renderer-secrets:/target/renderer-secrets",
        "--volume",
        f"{generation_root}/paperless-secrets:/target/paperless-secrets",
        "--volume",
        f"{generation_root}/sign-secrets:/target/sign-secrets",
        "--volume",
        f"{generation_root}/sign-evidence:/target/sign-evidence",
        image,
        "materialize",
        "--durable-snapshot",
        snapshot,
    ]
    if proof_identity is not None:
        proof_id, container_name = proof_identity
        owned = ["--name", container_name, "--log-driver", "none"]
        for key, value in _recovery_proof_labels(proof_id, role="materializer").items():
            owned.extend(["--label", f"{key}={value}"])
        command[3:3] = owned
    for network_name in reversed([network] if isinstance(network, str) else network):
        command[3:3] = ["--network", network_name]
    return command


def _write_source_backup_environment(source, source_runner, target, target_runner, path: str) -> str:
    """Transfer only repository credentials, never source runtime secrets."""
    allowed = {
        "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "RESTIC_PASSWORD",
        "USL_BACKUP_CACHE_PASSWORD",
    }
    raw = source_runner.run(["cat", source.value["secrets"]["env_file"]]).stdout
    validate_secret_text(raw, source.value["secrets"]["allowed_keys"])
    selected: dict[str, str] = {}
    for line in raw.splitlines():
        if not line or line.lstrip().startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key in allowed:
            selected[key] = value
    if set(selected) != allowed or any(not value for value in selected.values()):
        raise RuntimeError("source backup credential contract is incomplete")
    text = "".join(f"{key}={selected[key]}\n" for key in sorted(selected))
    _write_remote(target, target_runner, path, text)
    return path


def _preserve_staging_environment_state(target, runner, current: dict, volumes: dict[str, str]) -> dict:
    """Keep staging-owned OAuth state when business data is reseeded from production."""
    if target.value["environment"] != "staging":
        raise RuntimeError("staging environment-state preservation is staging-only")
    role = "mcp_oauth"
    source = _volume_source_path(runner, current["volumes"][role]["name"])
    destination = _volume_source_path(runner, volumes[role])
    mcp = target.value["services"]["mcp"]
    identity_program = (
        "import hashlib,json,os,pathlib,stat,sys;"
        "r=pathlib.Path(sys.argv[1]);h=hashlib.sha256();n=0;b=0;"
        "exec(\"for p in sorted(r.rglob('*')):\\n s=p.lstat();rel=p.relative_to(r).as_posix();"
        "h.update((rel+chr(0)+oct(stat.S_IMODE(s.st_mode))+chr(0)).encode());"
        "n+=1;"
        "b+=s.st_size if p.is_file() else 0;"
        "h.update(p.read_bytes()) if p.is_file() else None\");"
        "print(json.dumps({'files':n,'bytes':b,'sha256':h.hexdigest()},sort_keys=True))"
    )
    runner.run(compose_command(current["compose"], ["stop", "--timeout", "30", mcp]))
    try:
        source_identity = json.loads(runner.run(
            ["python3", "-c", identity_program, source],
        ).stdout)
        common = ["-aHAXS", "--numeric-ids", "--sparse", "--delete", "--"]
        runner.run(["rsync", *common, source.rstrip("/") + "/", destination.rstrip("/") + "/"])
        verified = runner.run([
            "rsync", "-aHAXScn", "--numeric-ids", "--sparse", "--delete",
            "--itemize-changes", "--", source.rstrip("/") + "/", destination.rstrip("/") + "/",
        ])
        destination_identity = json.loads(runner.run(
            ["python3", "-c", identity_program, destination],
        ).stdout)
        if verified.stdout.strip() or destination_identity != source_identity:
            raise RuntimeError("staging MCP OAuth preservation differs")
    finally:
        runner.run(compose_command(
            current["compose"], ["up", "--detach", "--wait", "--no-recreate", mcp],
        ))
    return {
        "schema": "usl-staging-environment-state/v1",
        "mcp_oauth": {"source": source_identity, "destination": destination_identity},
        "status": "preserved",
    }


def _write_remote(target, runner, path: str, content: str, mode: str = "0600") -> None:
    program = (
        "import os,pathlib,sys,tempfile;"
        "p=pathlib.Path(sys.argv[1]);p.parent.mkdir(parents=True,exist_ok=True);"
        "fd,t=tempfile.mkstemp(prefix='.'+p.name+'.',dir=p.parent);"
        "exec(\"try:\\n f=os.fdopen(fd,'wb');fd=-1;os.fchmod(f.fileno(),int(sys.argv[2],8));"
        "f.write(sys.stdin.buffer.read());f.flush();os.fsync(f.fileno());f.close();"
        "os.replace(t,p);t=None;d=os.open(p.parent,os.O_RDONLY);os.fsync(d);os.close(d)"
        "\\nfinally:\\n os.close(fd) if fd>=0 else None\\n pathlib.Path(t).unlink(missing_ok=True) if t else None\")"
    )
    runner.run(["python3", "-c", program, path, mode], input_text=content)


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
            "--env-file", target.value["compose"]["canonical"]["environment_file"],
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
            "--env-file", target.value["compose"]["canonical"]["environment_file"],
            "--env-file", target.value["secrets"]["env_file"],
            "--env", f"ODOO_DB_HOST={database['service']}",
            "--env", "ODOO_DB_PORT=5432",
            "--env", f"ODOO_DB_USER={database['user']}",
            "--env", f"ODOO_DB_NAME={database['name']}",
            "--env", "ODOO_MAX_CRON_THREADS=0",
            "--env", "USL_EINVOICE_LIVE_ENABLED=0",
            "--env", "USL_EREPORTING_LIVE_ENABLED=0",
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
    common = {
        "schema", "attempt", "target", "candidate_release", "source", "snapshot",
        "generation", "gitops_commit", "upgrade_plan_sha256", "prepare_receipt_sha256",
        "maintenance_receipt_sha256", "baseline_generation", "operation_bundle_sha256",
        "claimed_at", "status", "sha256",
    }
    v3 = {"operation_kind", "source_receipt_sha256", "baseline_runtime_sha256"}
    if (
        not isinstance(value, dict)
        or value.get("schema") not in {"usl-release-attempt/v2", "usl-release-attempt/v3"}
        or set(value) != common | (v3 if value.get("schema") == "usl-release-attempt/v3" else set())
    ):
        raise RuntimeError("release attempt claim fields differ")
    operation_fields = [
        "target", "attempt", "source", "candidate_release", "snapshot", "generation",
        "gitops_commit", "upgrade_plan_sha256", "prepare_receipt_sha256",
        "maintenance_receipt_sha256",
    ]
    if value["schema"] == "usl-release-attempt/v3":
        operation_fields.extend((
            "operation_kind", "source_receipt_sha256", "baseline_runtime_sha256",
        ))
    operation = {
        key: value[key]
        for key in operation_fields
    }
    if (
        value["target"] != target.name
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
        or value["schema"] == "usl-release-attempt/v3"
        and (
            value["operation_kind"] not in {
                "production-upgrade", "staging-upgrade", "staging-reset-from-production",
            }
            or not re.fullmatch(r"[0-9a-f]{64}", str(value["source_receipt_sha256"]))
            or not re.fullmatch(r"[0-9a-f]{64}", str(value["baseline_runtime_sha256"]))
        )
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
    runtime_evidence_sha256: str | None = None,
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
        "runtime_evidence_sha256": runtime_evidence_sha256,
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
    common = {
        "schema", "target", "attempt", "release", "snapshot", "generation",
        "operation_bundle_sha256",
        "health_sha256", "smoke_sha256", "control_validation_sha256",
        timestamp_key, "status", "sha256",
    }
    actual_schema = value.get("schema") if isinstance(value, dict) else None
    expected_kind = schema.rsplit("/", 1)[0]
    allowed_schemas = {f"{expected_kind}/v1", f"{expected_kind}/v2"}
    required = common | ({"runtime_evidence_sha256"} if actual_schema == f"{expected_kind}/v2" else set())
    if (
        not isinstance(value, dict)
        or actual_schema not in allowed_schemas
        or set(value) != required
    ):
        raise RuntimeError("release boundary receipt fields differ")
    if (
        value["status"] != status
        or value["target"] != target.name
        or value["attempt"] != attempt
        or value["release"] != release
        or not re.fullmatch(r"g[a-zA-Z0-9._-]{1,31}", str(value["generation"]))
        or not re.fullmatch(r"[0-9a-f]{64}", str(value["snapshot"]))
        or not re.fullmatch(r"[0-9a-f]{64}", str(value["operation_bundle_sha256"]))
        or actual_schema == f"{expected_kind}/v2"
        and (
            not re.fullmatch(r"[0-9a-f]{64}", str(value["runtime_evidence_sha256"]))
            if target.value["environment"] == "staging"
            else value["runtime_evidence_sha256"] is not None
        )
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


def _release_runtime_evidence(
    value: object,
    *,
    target,
    attempt: str,
    release: str,
    snapshot: str,
    generation: str,
    operation_kind: str,
) -> dict:
    """Validate the staging-only evidence needed to replay an admission."""
    required = {
        "schema", "target", "attempt", "release", "snapshot", "generation",
        "operation_kind", "auth_compose_admission", "pocket_id_admission",
        "environment_state_preservation", "status", "sha256",
    }
    if not isinstance(value, dict) or set(value) != required:
        raise RuntimeError("release runtime evidence fields differ")
    if (
        target.value["environment"] != "staging"
        or value["schema"] != "usl-release-runtime-evidence/v1"
        or value["target"] != target.name
        or value["attempt"] != attempt
        or value["release"] != release
        or value["snapshot"] != snapshot
        or value["generation"] != generation
        or value["operation_kind"] != operation_kind
        or operation_kind not in {"staging-upgrade", "staging-reset-from-production"}
        or value["status"] != "validated"
    ):
        raise RuntimeError("release runtime evidence identity differs")
    auth = value["auth_compose_admission"]
    pocket = value["pocket_id_admission"]
    auth_common = {
        "schema", "status", "paperless_mode", "canonical_environment",
        "odoo_sso_enabled",
    }
    auth_modes = {
        "oidc": {"paperless_https_url", "paperless_sso_enabled", "paperless_client_isolated"},
        "internal-only": {
            "paperless_internal_url", "paperless_loopback_only",
            "paperless_external_route_absent", "paperless_oidc_disabled",
        },
    }
    pocket_common = {
        "schema", "status", "paperless_mode", "application_completed",
        "provider_enabled", "governed_provider", "client_id_matches",
        "database_secret_absent", "issuer_matches", "base_url_matches",
        "required_group_matches", "scopes_match", "endpoints_match_issuer",
        "odoo_authorization_accepted", "odoo_client_secret_accepted",
    }
    pocket_modes = {
        "oidc": {"paperless_authorization_accepted", "paperless_client_secret_accepted"},
        "internal-only": set(),
    }
    auth_mode = auth.get("paperless_mode") if isinstance(auth, dict) else None
    pocket_mode = pocket.get("paperless_mode") if isinstance(pocket, dict) else None
    if (
        not isinstance(auth, dict)
        or auth.get("schema") != "usl-staging-auth-compose/v1"
        or auth.get("status") != "passed"
        or auth_mode not in auth_modes
        or set(auth) != auth_common | auth_modes[auth_mode]
        or any(
            item is not True
            for key, item in auth.items()
            if key not in {"schema", "status", "paperless_mode"}
        )
        or not isinstance(pocket, dict)
        or pocket.get("schema") != "usl-pocket-id-runtime-admission/v1"
        or pocket.get("status") != "passed"
        or pocket_mode != auth_mode
        or set(pocket) != pocket_common | pocket_modes[pocket_mode]
        or any(
            item is not True
            for key, item in pocket.items()
            if key not in {"schema", "status", "paperless_mode"}
        )
    ):
        raise RuntimeError("release runtime authentication evidence differs")
    preservation = value["environment_state_preservation"]
    if operation_kind == "staging-reset-from-production":
        if (
            not isinstance(preservation, dict)
            or set(preservation) != {"schema", "mcp_oauth", "status"}
            or preservation["schema"] != "usl-staging-environment-state/v1"
            or preservation["status"] != "preserved"
            or not isinstance(preservation["mcp_oauth"], dict)
            or set(preservation["mcp_oauth"]) != {"source", "destination"}
            or preservation["mcp_oauth"]["source"]
            != preservation["mcp_oauth"]["destination"]
            or any(
                not isinstance(identity, dict)
                or set(identity) != {"files", "bytes", "sha256"}
                or not isinstance(identity["files"], int)
                or identity["files"] < 0
                or not isinstance(identity["bytes"], int)
                or identity["bytes"] < 0
                or not re.fullmatch(r"[0-9a-f]{64}", str(identity["sha256"]))
                for identity in preservation["mcp_oauth"].values()
            )
        ):
            raise RuntimeError("release runtime environment evidence differs")
    elif preservation is not None:
        raise RuntimeError("ordinary staging upgrade has unexpected environment preservation")
    body = {key: item for key, item in value.items() if key != "sha256"}
    digest = hashlib.sha256(
        json.dumps(body, sort_keys=True, separators=(",", ":")).encode(),
    ).hexdigest()
    if value["sha256"] != digest:
        raise RuntimeError("release runtime evidence digest differs")
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


def _previous_generation_record(target, current: dict) -> dict:
    """Record rollback resources plus exact validated legacy Compose provenance."""
    active = current["active_state"] or {}
    previous = {
        "generation": current["generation"],
        "volumes": {role: item["name"] for role, item in current["volumes"].items()},
        "network": active.get("network"),
        "release_manifest": active.get("release_manifest"),
        "snapshot": active.get("snapshot"),
    }
    identity = current["compose"]
    adoption = target.value["compose"].get("adoption")
    if adoption is not None and identity.get("anchor_service") == adoption["legacy_anchor_service"]:
        previous["compose"] = _recorded_compose_identity(identity)
    return previous


def _base_compose_identity(target, identity: dict) -> dict:
    generated_prefix = target.value["state_directory"] + "/generations/"
    authority_prefix = target.value["state_directory"] + "/authorities/"
    compose_files = [
        path
        for path in identity["compose_files"]
        if not (
            path.startswith(generated_prefix)
            and path.endswith(("/compose.generation.json", "/compose.resources.json"))
        )
        and not path.startswith(authority_prefix)
    ]
    if not compose_files:
        raise RuntimeError("base Compose identity is unavailable")
    return {**identity, "compose_files": compose_files}


def _recorded_compose_identity(identity: dict) -> dict:
    """Persist only the stable Compose provenance needed for rollback."""
    return {
        field: identity[field]
        for field in (
            "project",
            "working_directory",
            "environment_file",
            "compose_files",
            "profiles",
            "anchor_service",
        )
    }


def _validated_legacy_compose_identity(target, runner, value: dict, generation: str) -> dict:
    """Admit the one staging v2 layout without accepting arbitrary host paths."""
    adoption = target.value["compose"].get("adoption")
    expected_fields = {
        "project",
        "working_directory",
        "environment_file",
        "compose_files",
        "profiles",
        "anchor_service",
    }
    if adoption is None or not isinstance(value, dict) or set(value) != expected_fields:
        raise RuntimeError("legacy rollback Compose identity is invalid")
    if (
        value["project"] != target.project
        or value["anchor_service"] != adoption["legacy_anchor_service"]
        or value["profiles"] != target.value["compose"]["profiles"]
        or value["environment_file"] != adoption["candidate"]["environment_file"]
    ):
        raise RuntimeError("legacy rollback Compose identity differs from its contract")
    state_root = target.value["state_directory"]
    working_directory = value["working_directory"]
    if not re.fullmatch(re.escape(state_root) + r"/validation-[0-9a-f]{8,16}", working_directory):
        raise RuntimeError("legacy rollback working directory is outside the validation perimeter")
    allowed = {
        f"{working_directory}/compose.yaml",
        f"{working_directory}/compose.resources.json",
        f"{working_directory}/usl-staging-proxy-generation.json",
        f"{state_root}/generations/{generation}/compose.resources.json",
        f"{state_root}/generations/{generation}/compose.generation.json",
    }
    compose_files = value["compose_files"]
    if (
        not isinstance(compose_files, list)
        or not compose_files
        or len(compose_files) != len(set(compose_files))
        or set(compose_files) != allowed
    ):
        raise RuntimeError("legacy rollback Compose files are outside the validation perimeter")
    paths = [working_directory, value["environment_file"], *compose_files]
    for path in paths:
        test = ["test", "-d" if path == working_directory else "-f", path]
        if runner.run(test, check=False).returncode:
            raise RuntimeError(f"legacy rollback Compose path is missing: {path}")
        resolved = runner.run(["readlink", "-f", "--", path]).stdout.strip()
        if resolved != path:
            raise RuntimeError(f"legacy rollback Compose path is not direct: {path}")
    services = set(runner.run(compose_command(value, ["config", "--services"])).stdout.splitlines())
    expected_services = set(target.value["services"].values())
    expected_services.remove(target.value["compose"]["anchor_service"])
    expected_services.add(adoption["legacy_anchor_service"])
    if not expected_services.issubset(services) or target.value["compose"]["anchor_service"] in services:
        raise RuntimeError("legacy rollback Compose service perimeter differs")
    return value


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
        root, commit = _gitops_root()
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
        return _with_stable_gateway_config(target, runner, identity)
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
    _remove_owned_compose_service(target, runner, anchor)


def _remove_owned_compose_service(target, runner, service: str) -> None:
    """Remove exactly one allowlisted Compose service after proving ownership."""
    permitted = set(target.value["services"].values())
    adoption = target.value["compose"].get("adoption")
    if adoption is not None:
        permitted.add(adoption["legacy_anchor_service"])
    if service not in permitted:
        raise RuntimeError("refusing to remove a service outside the target perimeter")
    candidates = runner.run(
        [
            "docker", "ps", "-a",
            "--filter", f"label=com.docker.compose.project={target.project}",
            "--filter", f"label=com.docker.compose.service={service}",
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
            or labels.get("com.docker.compose.service") != service
        ):
            raise RuntimeError("Compose service ownership differs")
        runner.run(["docker", "rm", "--force", container])


def _activate_generation(
    target,
    runner,
    current_identity: dict,
    canonical_identity: dict,
    generation_identity: dict,
) -> None:
    """Replace the current cohort while retaining its exact rollback identity."""
    authority = _mcp_runtime_authority(target)
    current_identity = _with_mcp_runtime_authority(
        target, runner, current_identity, authority,
    )
    generation_identity = _with_mcp_runtime_authority(
        target, runner, generation_identity, authority,
    )
    canonical_identity = _with_mcp_runtime_authority(
        target, runner, canonical_identity, authority,
    )
    _validate_stable_gateway_generation(
        target, runner, canonical_identity, generation_identity,
    )
    generation_services = _compose_services(target, generation_identity)
    try:
        runner.run(
            compose_command(
                current_identity,
                ["stop", "--timeout", "60", *_compose_services(target, current_identity)],
            ),
        )
        runner.run(
            compose_command(
                generation_identity,
                ["up", "--detach", "--wait", "--force-recreate", "--no-deps", *generation_services],
            ),
        )
    except Exception as error:
        cleanup_error = None
        try:
            _cleanup_adoption_candidate_anchor(target, runner, current_identity)
        except Exception as cleanup:
            cleanup_error = cleanup
        _rollback_after_failure(target, runner, current_identity, error)
        if cleanup_error is not None:
            raise RuntimeError(
                f"activation failed ({error}); legacy rollback completed but candidate cleanup failed "
                f"({cleanup_error})",
            ) from error
        raise


def _rollback_active_candidate(
    target,
    runner,
    *,
    current: dict,
    current_identity: dict,
    generation_identity: dict,
    active_path: str,
    error: Exception,
) -> None:
    """Restore the exact baseline after any pre-boundary candidate failure."""
    runner.run(
        compose_command(
            generation_identity,
            ["stop", "--timeout", "60", *_compose_services(target, generation_identity)],
        ),
        check=False,
    )
    cleanup_error = None
    try:
        _cleanup_adoption_candidate_anchor(target, runner, current_identity)
    except Exception as cleanup:
        cleanup_error = cleanup
    if current["active_state"] is None:
        runner.run(["rm", "-f", active_path], check=False)
    else:
        _write_remote(
            target,
            runner,
            active_path,
            json.dumps(current["active_state"], indent=2, sort_keys=True) + "\n",
        )
    _rollback_after_failure(target, runner, current_identity, error)
    if cleanup_error is not None:
        raise RuntimeError(
            f"candidate failed ({error}); rollback completed but cleanup failed "
            f"({cleanup_error})",
        ) from error


def _active_generation_identity(target, runner, current: dict) -> dict:
    """Resolve the recorded active generation even when the anchor is still legacy."""
    active = current["active_state"]
    if active is None:
        return _with_mcp_runtime_authority(
            target, runner, current["compose"], _mcp_runtime_authority(target),
        )
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
    return _with_mcp_runtime_authority(
        target, runner, identity, _mcp_runtime_authority(target),
    )


def _previous_generation_identity(target, runner, current: dict) -> tuple[dict, str | None]:
    """Resolve only the one rollback generation recorded by active state."""
    identity = _base_compose_identity(target, current["compose"])
    active = current["active_state"]
    if active is None:
        return _with_mcp_runtime_authority(
            target, runner, identity, _mcp_runtime_authority(target),
        ), None
    previous = active.get("previous")
    required_previous = {
        "generation", "volumes", "network", "release_manifest", "snapshot",
    }
    allowed_previous = required_previous | {"compose"}
    if (
        not isinstance(previous, dict)
        or not required_previous.issubset(previous)
        or not set(previous).issubset(allowed_previous)
    ):
        raise RuntimeError("rollback generation state is incomplete")
    if previous["release_manifest"] is None:
        if previous["network"] is not None or previous["snapshot"] is not None:
            raise RuntimeError("adopted rollback state is inconsistent")
        return _with_mcp_runtime_authority(
            target, runner, identity, _mcp_runtime_authority(target),
        ), None
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
    try:
        previous_release = json.loads(runner.run(["cat", release_manifest]).stdout)
    except json.JSONDecodeError as error:
        raise RuntimeError("rollback release manifest is invalid") from error
    if previous_release.get("schema") == "usl-release/v2":
        if (
            target.value["environment"] != "staging"
            or adoption is None
            or previous_release.get("schema") != adoption["legacy_release_schema"]
            or "compose" not in previous
        ):
            raise RuntimeError("legacy v2 rollback lacks its exact Compose identity")
        identity = _validated_legacy_compose_identity(
            target,
            runner,
            previous["compose"],
            generation,
        )
    elif previous_release.get("schema") != "usl-release/v3":
        raise RuntimeError("rollback release schema is unsupported")
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
    return _with_mcp_runtime_authority(
        target, runner, identity, _mcp_runtime_authority(target),
    ), state


def _staging_abort_neutralization(target, runner, current: dict) -> dict:
    """Re-prove that an admitted staging candidate still has no external writers."""
    if target.value["environment"] != "staging":
        raise RuntimeError("staging neutralization proof is staging-only")
    if target.value["cron_policy"].get("mode") != "neutralized":
        raise RuntimeError("staging cron policy is not neutralized")
    try:
        rendered = json.loads(
            runner.run(compose_command(current["compose"], ["config", "--format", "json"])).stdout,
        )
        services = rendered["services"]
        odoo = services[target.value["services"]["odoo"]]["environment"]
        paperless = services[target.value["services"]["paperless"]]["environment"]
        if not isinstance(odoo, dict) or not isinstance(paperless, dict):
            raise TypeError("service environment is not a mapping")
    except (KeyError, TypeError, json.JSONDecodeError) as error:
        raise RuntimeError("staging neutralization Compose evidence is invalid") from error
    paperless_disabled = {
        name: paperless.get(name) == "disable"
        for name in (
            "PAPERLESS_EMAIL_TASK_CRON",
            "PAPERLESS_EMPTY_TRASH_TASK_CRON",
            "PAPERLESS_SHARE_LINK_BUNDLE_CLEANUP_CRON",
            "PAPERLESS_WORKFLOW_SCHEDULED_TASK_CRON",
        )
    }
    try:
        database = json.loads(_psql(
            target,
            runner,
            current["compose"],
            "odoo",
            """
            SELECT json_build_object(
                'database_neutralized', COALESCE((
                    SELECT lower(value) IN ('1', 'true')
                    FROM ir_config_parameter
                    WHERE key = 'database.is_neutralized'
                ), false),
                'active_crons', (SELECT count(*) FROM ir_cron WHERE active),
                'active_fetchmail', (SELECT count(*) FROM fetchmail_server WHERE active),
                'pending_mail', (
                    SELECT count(*) FROM mail_mail
                    WHERE state IN ('outgoing', 'exception')
                )
            )::text
            """,
        ))
    except (TypeError, json.JSONDecodeError) as error:
        raise RuntimeError("staging neutralization database evidence is invalid") from error
    checks = {
        "deployment_is_staging": odoo.get("USL_DEPLOYMENT_ENV") == "staging",
        "einvoice_disabled": odoo.get("USL_EINVOICE_LIVE_ENABLED") == "0",
        "ereporting_disabled": odoo.get("USL_EREPORTING_LIVE_ENABLED") == "0",
        "database_neutralized": database.get("database_neutralized") is True,
        "active_crons_absent": database.get("active_crons") == 0,
        "active_fetchmail_absent": database.get("active_fetchmail") == 0,
        "pending_mail_absent": database.get("pending_mail") == 0,
        "paperless_external_tasks_disabled": all(paperless_disabled.values()),
    }
    if not all(checks.values()):
        raise RuntimeError(
            "staging admission is no longer neutralized: "
            + ", ".join(key for key, value in checks.items() if not value),
        )
    return {
        "schema": "usl-staging-neutralization/v1",
        **checks,
        "status": "passed",
    }


def _abort_to_previous_generation(
    target,
    runner,
    targets: Path,
    *,
    attempt: str | None = None,
) -> dict:
    """Restore and prove the untouched pre-reopen runtime generation."""
    marker = f"{Path(target.value['state_directory']).parents[1]}/gateway/{target.name}/maintenance"
    if attempt is not None:
        if not isinstance(attempt, str) or not RELEASE_ATTEMPT.fullmatch(attempt):
            raise RuntimeError("release abort attempt identity is invalid")
        try:
            maintenance_marker = json.loads(_read_path(target, runner, Path(marker)))
        except json.JSONDecodeError as error:
            raise RuntimeError("release abort maintenance marker is invalid") from error
        if not isinstance(maintenance_marker, dict):
            raise RuntimeError("release abort maintenance marker is invalid")
        required_marker = {"schema", "target", "attempt", "enabled_at", "sha256"}
        marker_body = {
            key: value for key, value in maintenance_marker.items() if key != "sha256"
        }
        try:
            marker_time = datetime.fromisoformat(
                str(maintenance_marker.get("enabled_at", "")).replace("Z", "+00:00"),
            )
        except ValueError as error:
            raise RuntimeError("release abort maintenance marker is invalid") from error
        if (
            set(maintenance_marker) != required_marker
            or maintenance_marker.get("schema") != "usl-maintenance-marker/v1"
            or maintenance_marker.get("target") != target.name
            or maintenance_marker.get("attempt") != attempt
            or marker_time.tzinfo is None
            or maintenance_marker.get("sha256") != hashlib.sha256(
                json.dumps(marker_body, sort_keys=True, separators=(",", ":")).encode(),
            ).hexdigest()
        ):
            raise RuntimeError("release abort maintenance marker is invalid")
    elif runner.run(["test", "-f", marker], check=False).returncode:
        raise RuntimeError("runtime rollback is allowed only while the gateway is in maintenance")
    current = inspect_runtime(target, runner)
    generation = current.get("generation")
    claim = None
    if attempt is not None:
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
            if (
                claim["schema"] == "usl-release-attempt/v3"
                and _runtime_cas_sha256(target, runner, current) != claim["baseline_runtime_sha256"]
            ):
                raise RuntimeError("rolled-back runtime differs from the claimed baseline")
            # A failed candidate may leave the unchanged baseline quiesced.
            # Resume its existing containers before checking recovery health.
            runner.run(compose_command(
                current["compose"],
                ["up", "--detach", "--wait", "--no-recreate", "--no-deps", *_compose_services(target, current["compose"])],
            ))
            current = inspect_runtime(target, runner)
            health, smoke = _runtime_boundary_gates(target, runner, targets, current)
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
    neutralization = None
    if isinstance(generation, str):
        generation_root = f"{target.value['state_directory']}/generations/{generation}"
        activation_started = runner.run(
            ["test", "-f", f"{generation_root}/activation-started.json"],
            check=False,
        ).returncode == 0
        admission_raw = runner.run(
            ["cat", f"{generation_root}/admission.json"], check=False,
        )
        if target.name == "production":
            if activation_started or admission_raw.returncode == 0:
                raise RuntimeError(
                    "release crossed the forward-only boundary and cannot be rolled back automatically",
                )
        elif activation_started:
            raise RuntimeError("staging has an unexpected production forward-only boundary")
        elif admission_raw.returncode == 0:
            if claim is None or attempt is None:
                raise RuntimeError("staging admission rollback requires an exact release attempt")
            try:
                admission = _validate_release_boundary_receipt(
                    json.loads(admission_raw.stdout),
                    schema="usl-release-admission/v1",
                    status="admitted",
                    target=target,
                    attempt=attempt,
                    release=claim["candidate_release"],
                )
            except json.JSONDecodeError as error:
                raise RuntimeError("staging admission receipt is invalid") from error
            _require_same_attempt_boundary(
                claim,
                admission,
                generation=generation,
                snapshot=claim["snapshot"],
            )
            neutralization = _staging_abort_neutralization(target, runner, current)
    previous_identity, previous_state = _previous_generation_identity(target, runner, current)
    cohort_services = sorted(set(target.value["services"].values()))
    runner.run(
        compose_command(current["compose"], ["stop", "--timeout", "60", *cohort_services]),
        check=False,
    )
    adoption = target.value["compose"].get("adoption")
    if (
        adoption is not None
        and previous_identity.get("anchor_service") == adoption["legacy_anchor_service"]
    ):
        _remove_owned_compose_service(
            target,
            runner,
            target.value["compose"]["anchor_service"],
        )
    try:
        _start_rollback_identity(target, runner, previous_identity)
    except Exception as error:
        raise RuntimeError(f"previous generation did not start: {error}") from error
    active_path = f"{target.value['state_directory']}/active.json"
    if previous_state is None:
        runner.run(["rm", "-f", active_path])
        generation = "adopted"
    else:
        _write_remote(target, runner, active_path, previous_state)
        generation = json.loads(previous_state)["generation"]
    restored_runtime = inspect_runtime(target, runner)
    health, smoke = _runtime_boundary_gates(
        target, runner, targets, restored_runtime,
    )
    result = {
        "schema": "usl-release-abort/v1",
        "target": target.name,
        "attempt": attempt,
        "generation": generation,
        "health": health,
        "smoke": smoke,
        "status": "rolled-back",
    }
    if neutralization is not None:
        result["neutralization"] = neutralization
    return result


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


def _run_candidate_upgrade(
    target,
    runner,
    release,
    network,
    volumes,
    plan,
    candidate_identity=None,
) -> None:
    modules = validate_upgrade_plan(plan)["upgrade_modules"]
    if not modules:
        return
    if plan["candidate_release"] != release.get("identity"):
        raise RuntimeError("upgrade plan is not bound to the candidate release")
    database = target.value["databases"]["odoo"]
    arguments = [
        "odoo", "--config=/etc/odoo/odoo.conf",
        f"--database={database['name']}",
        f"--update={','.join(modules)}",
        "--stop-after-init", "--no-http", "--max-cron-threads=0",
    ]
    if target.value["environment"] == "staging":
        if candidate_identity is None:
            raise RuntimeError("staging upgrade requires the approved runtime identity")
        command = _staging_pocketid_command(
            target,
            release,
            network,
            volumes,
            candidate_identity,
            arguments,
        )
    else:
        command = [
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
            *arguments,
        ]
    runner.run(command)


def _staging_pocketid_runtime_environment(target, candidate_identity: dict) -> str:
    adoption = target.value["compose"].get("adoption")
    if target.value["environment"] != "staging" or adoption is None:
        raise RuntimeError("Pocket ID staging reconciliation requires the staging adoption contract")
    environment_file = candidate_identity.get("environment_file")
    if environment_file != adoption["candidate"]["environment_file"]:
        raise RuntimeError("Pocket ID staging environment differs from the approved runtime file")
    return environment_file


def _validate_staging_auth_compose(target, runner, candidate_identity: dict) -> dict:
    """Fail before materialization unless staging authentication is explicit."""
    if target.value["environment"] != "staging":
        return {"schema": "usl-staging-auth-compose/v1", "status": "not-applicable"}
    _staging_pocketid_runtime_environment(target, candidate_identity)
    rendered = runner.run(
        compose_command(candidate_identity, ["config", "--format", "json"]),
    )
    try:
        compose = json.loads(rendered.stdout)
        services = compose["services"]
        odoo = services[target.value["services"]["odoo"]]["environment"]
        upgrade = services["odoo-upgrade"]["environment"]
        paperless_service = services[target.value["services"]["paperless"]]
        paperless = paperless_service["environment"]
        paperless_preflight = services["paperless-preflight"]["environment"]
    except (KeyError, TypeError, json.JSONDecodeError) as error:
        raise RuntimeError("rendered staging authentication contract is invalid") from error
    issuer = "https://auth.unstaticlabs.com"
    base_url = target.value["endpoints"]["odoo"].rstrip("/")
    paperless_url = str(paperless.get("PAPERLESS_URL", "")).rstrip("/")
    paperless_public_url = str(
        paperless_preflight.get("PAPERLESS_PUBLIC_URL", "")
    ).rstrip("/")
    paperless_public_base = str(
        paperless_preflight.get("PAPERLESS_PUBLIC_BASE_URL", "")
    ).rstrip("/")
    configured_paperless_urls = [
        value for value in (paperless_url, paperless_public_url, paperless_public_base) if value
    ]
    public_paperless = any(
        urlsplit(value).hostname not in {"127.0.0.1", "localhost", "::1"}
        for value in configured_paperless_urls
    )
    checks = {
        "canonical_environment": all(
            environment.get("USL_DEPLOYMENT_ENV") == "staging"
            for environment in (odoo, upgrade, paperless, paperless_preflight)
        ),
        "odoo_sso_enabled": all(
            environment.get("USL_POCKET_ID_ENABLED") == "1"
            and environment.get("USL_POCKET_ID_ISSUER", "").rstrip("/") == issuer
            and environment.get("USL_POCKET_ID_ODOO_BASE_URL", "").rstrip("/") == base_url
            and bool(environment.get("USL_POCKET_ID_CLIENT_ID"))
            and bool(environment.get("USL_POCKET_ID_CLIENT_SECRET"))
            for environment in (odoo, upgrade)
        ),
    }
    paperless_apps = {
        value.strip() for value in str(paperless.get("PAPERLESS_APPS", "")).split(",")
        if value.strip()
    }
    if public_paperless:
        try:
            providers = json.loads(paperless["PAPERLESS_SOCIALACCOUNT_PROVIDERS"])
            provider_list = providers["openid_connect"]["APPS"]
            if not isinstance(provider_list, list) or len(provider_list) != 1:
                raise KeyError("Paperless provider count")
            provider = provider_list[0]
        except (KeyError, TypeError, json.JSONDecodeError) as error:
            raise RuntimeError("rendered public Paperless OIDC contract is invalid") from error
        provider_server = str(
            (provider.get("settings") or {}).get("server_url", "")
        ).rstrip("/")
        checks.update({
            "paperless_https_url": (
                urlsplit(paperless_url).scheme == "https"
                and paperless_url == paperless_public_url == paperless_public_base
            ),
            "paperless_sso_enabled": (
                paperless_preflight.get("PAPERLESS_OIDC_ENABLED") == "1"
                and paperless_preflight.get("PAPERLESS_DISABLE_REGULAR_LOGIN") == "true"
                and paperless.get("PAPERLESS_DISABLE_REGULAR_LOGIN") == "true"
                and paperless.get("PAPERLESS_REDIRECT_LOGIN_TO_SSO") == "true"
                and "allauth.socialaccount.providers.openid_connect" in paperless_apps
            ),
            "paperless_client_isolated": (
                provider.get("provider_id") == "pocket-id"
                and bool(provider.get("client_id"))
                and bool(provider.get("secret"))
                and provider.get("client_id") != odoo.get("USL_POCKET_ID_CLIENT_ID")
                and provider_server == issuer
                and (provider.get("settings") or {}).get("token_auth_method")
                == "client_secret_basic"
            ),
        })
        paperless_mode = "oidc"
    else:
        internal_url = target.value["endpoints"]["paperless"].rstrip("/")
        internal_endpoint = urlsplit(internal_url)
        ports = paperless_service.get("ports") or []
        networks = paperless_service.get("networks") or []
        network_names = set(networks if isinstance(networks, list) else networks)
        rendered_networks = compose.get("networks") or {}
        ingress_name = target.value["external_networks"]["ingress"]
        attached_networks = {
            str((rendered_networks.get(name) or {}).get("name", name))
            for name in network_names
        }
        ingress_route_clear = True
        for service_name, service in services.items():
            service_networks = service.get("networks") or []
            service_network_names = set(
                service_networks if isinstance(service_networks, list) else service_networks
            )
            resolved_service_networks = {
                str((rendered_networks.get(name) or {}).get("name", name))
                for name in service_network_names
            }
            if ingress_name not in service_network_names | resolved_service_networks:
                continue
            for network_name in service_network_names:
                network_options = (
                    service_networks.get(network_name) or {}
                    if isinstance(service_networks, dict)
                    else {}
                )
                aliases = network_options.get("aliases") or []
                if any("paperless" in str(alias).lower() for alias in aliases):
                    ingress_route_clear = False
            image = str(service.get("image", "")).lower()
            if not any(proxy in image for proxy in ("nginx", "caddy", "traefik")):
                continue
            inspected_proxy_config = False
            for volume in service.get("volumes") or []:
                if not isinstance(volume, dict) or volume.get("type") != "bind":
                    continue
                source = Path(str(volume.get("source", "")))
                if source.suffix not in {".conf", ".cfg"}:
                    continue
                if not source.is_absolute():
                    source = Path(candidate_identity["working_directory"]) / source
                inspected_proxy_config = True
                if "paperless" in _read_path(target, runner, source).lower():
                    ingress_route_clear = False
            if not inspected_proxy_config:
                ingress_route_clear = False
        exact_loopback_port = len(ports) == 1 and all(
            str(port.get("host_ip", "")) == "127.0.0.1"
            and str(port.get("published", "")) == str(internal_endpoint.port)
            and str(port.get("target", "")) == "8000"
            for port in ports
            if isinstance(port, dict)
        ) and all(isinstance(port, dict) for port in ports)
        checks.update({
            "paperless_internal_url": (
                internal_endpoint.scheme == "http"
                and internal_endpoint.hostname == "127.0.0.1"
                and paperless_url == paperless_public_url == internal_url
                and not paperless_public_base
            ),
            "paperless_loopback_only": (
                exact_loopback_port
                and ingress_name not in network_names
                and ingress_name not in attached_networks
            ),
            "paperless_external_route_absent": ingress_route_clear,
            "paperless_oidc_disabled": (
                paperless_preflight.get("PAPERLESS_OIDC_ENABLED") == "0"
                and not paperless.get("PAPERLESS_SOCIALACCOUNT_PROVIDERS")
                and not paperless.get("PAPERLESS_REDIRECT_LOGIN_TO_SSO")
                and "allauth.socialaccount.providers.openid_connect" not in paperless_apps
            ),
        })
        paperless_mode = "internal-only"
    if not all(checks.values()):
        raise RuntimeError(
            "staging authentication Compose admission failed: "
            + ", ".join(key for key, value in checks.items() if not value),
        )
    return {
        "schema": "usl-staging-auth-compose/v1",
        "paperless_mode": paperless_mode,
        **checks,
        "status": "passed",
    }


def _staging_pocketid_command(target, release, network, volumes, candidate_identity, arguments):
    database = target.value["databases"]["odoo"]
    runtime_environment = _staging_pocketid_runtime_environment(target, candidate_identity)
    shell = (
        'set -eu; '
        'export USL_POCKET_ID_CLIENT_ID="${POCKET_ID_CLIENT_ID:?}"; '
        'export USL_POCKET_ID_CLIENT_SECRET="${POCKET_ID_CLIENT_SECRET:?}"; '
        'if [ "${PAPERLESS_OIDC_ENABLED:-0}" = 1 ]; then '
        ': "${POCKET_ID_PAPERLESS_CLIENT_ID:?}"; '
        ': "${POCKET_ID_PAPERLESS_CLIENT_SECRET:?}"; '
        ': "${PAPERLESS_PUBLIC_URL:?}"; fi; '
        'exec /usr/local/bin/odoo-entrypoint "$@"'
    )
    return [
        "docker", "run", "--rm", "--interactive", "--network", network,
        "--env-file", target.value["secrets"]["env_file"],
        "--env-file", runtime_environment,
        "--env", f"ODOO_DB_HOST={database['service']}",
        "--env", "ODOO_DB_PORT=5432",
        "--env", f"ODOO_DB_USER={database['user']}",
        "--env", f"ODOO_DB_NAME={database['name']}",
        "--env", "ODOO_MAX_CRON_THREADS=0",
        "--env", "USL_DEPLOYMENT_ENV=staging",
        "--env", "USL_EINVOICE_LIVE_ENABLED=0",
        "--env", "USL_EREPORTING_LIVE_ENABLED=0",
        "--env", "USL_POCKET_ID_ENABLED=1",
        "--env", "USL_POCKET_ID_ISSUER=https://auth.unstaticlabs.com",
        "--env", f"USL_POCKET_ID_ODOO_BASE_URL={target.value['endpoints']['odoo']}",
        "--env", "USL_POCKET_ID_REQUIRED_GROUP=c_suite",
        "--env", "USL_POCKET_ID_SCOPES=openid profile email groups",
        "--volume", f"{volumes['odoo_filestore']}:/var/lib/odoo",
        "--entrypoint", "/bin/sh",
        release["components"]["distribution"]["digest_reference"],
        "-c", shell, "usl-pocketid-reconcile", *arguments,
    ]


def _reconcile_staging_pocketid(target, runner, release, network, volumes, candidate_identity) -> dict:
    """Re-enable and prove the staging OIDC provider after neutralization."""
    if target.value["environment"] != "staging":
        return {"schema": "usl-pocket-id-runtime-admission/v1", "status": "not-applicable"}
    database = target.value["databases"]["odoo"]
    program = r'''import base64
import hashlib
import json
import os
import secrets
from urllib.parse import urlsplit

import requests

provider = env.ref("usl_pocketid.provider_pocketid").sudo()
applied = env["auth.oauth.provider"]._usl_pocketid_apply_environment()
provider.invalidate_recordset()
issuer = os.environ["USL_POCKET_ID_ISSUER"].rstrip("/")
base_url = os.environ["USL_POCKET_ID_ODOO_BASE_URL"].rstrip("/")
expected_scopes = set(os.environ["USL_POCKET_ID_SCOPES"].split())
issuer_url = urlsplit(issuer)
endpoint_fields = ("auth_endpoint", "token_endpoint", "jwks_uri", "usl_end_session_endpoint")
endpoints = [
    urlsplit(provider[field])
    for field in endpoint_fields
    if provider[field]
]



odoo_authorization, odoo_secret = synthetic_client_probe(
    os.environ["USL_POCKET_ID_CLIENT_ID"],
    os.environ["USL_POCKET_ID_CLIENT_SECRET"],
    base_url + "/auth_oauth/signin",
    provider.usl_token_auth_method,
)
paperless_mode = "oidc" if os.environ.get("PAPERLESS_OIDC_ENABLED") == "1" else "internal-only"
checks = {
    "application_completed": applied is True,
    "provider_enabled": provider.enabled is True,
    "governed_provider": provider.usl_pocketid is True,
    "client_id_matches": provider.client_id == os.environ["USL_POCKET_ID_CLIENT_ID"],
    "database_secret_absent": not provider.client_secret,
    "issuer_matches": provider.usl_oidc_issuer.rstrip("/") == issuer,
    "base_url_matches": provider.usl_public_base_url.rstrip("/") == base_url,
    "required_group_matches": provider.usl_required_group == os.environ["USL_POCKET_ID_REQUIRED_GROUP"],
    "scopes_match": set(provider.scope.split()) == expected_scopes,
    "endpoints_match_issuer": all(
        endpoint.scheme == "https"
        and endpoint.hostname == issuer_url.hostname
        and endpoint.port == issuer_url.port
        and not endpoint.username
        and not endpoint.password
        for endpoint in endpoints
    ) and all(provider[field] for field in endpoint_fields[:3]),
    "odoo_authorization_accepted": odoo_authorization,
    "odoo_client_secret_accepted": odoo_secret,
}
if paperless_mode == "oidc":
    paperless_url = os.environ["PAPERLESS_PUBLIC_URL"].rstrip("/")
    paperless_authorization, paperless_secret = synthetic_client_probe(
        os.environ["POCKET_ID_PAPERLESS_CLIENT_ID"],
        os.environ["POCKET_ID_PAPERLESS_CLIENT_SECRET"],
        paperless_url + "/accounts/oidc/pocket-id/login/callback/",
        "client_secret_basic",
    )
    checks.update({
        "paperless_authorization_accepted": paperless_authorization,
        "paperless_client_secret_accepted": paperless_secret,
    })
evidence = {
    "schema": "usl-pocket-id-runtime-admission/v1",
    "status": "passed",
    "paperless_mode": paperless_mode,
    **checks,
}
if not all(checks.values()):
    raise RuntimeError("Pocket ID runtime admission failed: " + ", ".join(
        key for key, value in checks.items() if not value
    ))
print("USL_POCKET_ID_RUNTIME_ADMISSION=" + json.dumps(evidence, sort_keys=True))
'''
    program = CLIENT_PROBE_SCRIPT + program
    result = runner.run(
        _staging_pocketid_command(
            target,
            release,
            network,
            volumes,
            candidate_identity,
            [
                "odoo", "shell", "--config=/etc/odoo/odoo.conf",
                f"--database={database['name']}", "--no-http", "--max-cron-threads=0",
            ],
        ),
        input_text=program,
    )
    prefix = "USL_POCKET_ID_RUNTIME_ADMISSION="
    for line in reversed(result.stdout.splitlines()):
        if not line.startswith(prefix):
            continue
        try:
            evidence = json.loads(line.removeprefix(prefix))
        except json.JSONDecodeError as error:
            raise RuntimeError("Pocket ID runtime admission returned invalid evidence") from error
        common_keys = {
            "schema", "status", "application_completed", "provider_enabled",
            "governed_provider", "client_id_matches", "database_secret_absent",
            "issuer_matches", "base_url_matches", "required_group_matches",
            "scopes_match", "endpoints_match_issuer", "odoo_authorization_accepted",
            "odoo_client_secret_accepted", "paperless_mode",
        }
        paperless_mode = evidence.get("paperless_mode")
        expected_keys = set(common_keys)
        if paperless_mode == "oidc":
            expected_keys.update({
                "paperless_authorization_accepted",
                "paperless_client_secret_accepted",
            })
        elif paperless_mode != "internal-only":
            raise RuntimeError("Pocket ID runtime admission evidence differs")
        if (
            set(evidence) != expected_keys
            or evidence.get("schema") != "usl-pocket-id-runtime-admission/v1"
            or evidence.get("status") != "passed"
            or any(
                evidence[key] is not True
                for key in expected_keys - {"schema", "status", "paperless_mode"}
            )
        ):
            raise RuntimeError("Pocket ID runtime admission evidence differs")
        return evidence
    raise RuntimeError("Pocket ID runtime admission returned no evidence")


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
    network = target.value["compose"]["default_network"]
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
    operation_kind = getattr(arguments, "operation_kind", None)
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
    environment_state_preservation = None
    pocketid_admission = None
    if getattr(arguments, "upgrade_plan", None):
        try:
            plan_value = json.loads(_read_path(target, target_runner, arguments.upgrade_plan))
            upgrade_plan = _validated_release_upgrade_plan(target, plan_value, release)
            if target.value["environment"] == "production":
                signed_plan_evidence = plan_value
        except (json.JSONDecodeError, ModuleReleaseError, PlanEvidenceError) as error:
            raise RuntimeError("upgrade plan is invalid") from error
    tool_image = _operations_image(release)
    generation = arguments.generation or f"g{datetime.now(UTC):%Y%m%dt%H%M}-{arguments.snapshot[:8]}"
    if len(generation) > 32 or not generation.startswith("g"):
        raise RuntimeError("generation name is invalid")
    identity = current["compose"]
    candidate_identity = _candidate_compose_identity(target, target_runner, identity)
    mcp_authority = _candidate_mcp_authority(target, release)
    candidate_identity = _with_mcp_runtime_authority(
        target, target_runner, candidate_identity, mcp_authority,
    )
    auth_compose_admission = _validate_staging_auth_compose(
        target, target_runner, candidate_identity,
    )
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
    target_runner.run(["install", "-d", "-m", "0700", f"{generation_root}/work"])
    volumes, network = _create_generation_resources(target, target_runner, generation)
    with _materialization_cleanup(target, target_runner, generation) as database_containers:
        source_backup_env = _write_source_backup_environment(
            source,
            source.runner(),
            target,
            target_runner,
            f"{generation_root}/work/source-backup.env",
        )
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
        try:
            materialized = target_runner.run(
                _materialize_command(
                    source,
                    target,
                    tool_image,
                    arguments.snapshot,
                    generation,
                    network,
                    volumes,
                    source_backup_env,
                ),
            )
        finally:
            target_runner.run(["rm", "-f", "--", source_backup_env], check=False)
        materialize_state = json.loads(materialized.stdout.splitlines()[-1])
        _validate_materialized_release(
            materialize_state,
            release,
            release_sha,
            require_sign_secrets=target.value["environment"] == "production",
        )
        if source.name != target.name and target.value["environment"] == "staging":
            environment_state_preservation = _preserve_staging_environment_state(
                target, target_runner, current, volumes,
            )
        snapshot_release = materialize_state["release"]
        candidate_differs = snapshot_release["manifest_sha256"] != release_sha
        if candidate_differs and upgrade_plan is None:
            raise RuntimeError("cross-release restore requires the staging-qualified upgrade plan")
        if upgrade_plan is not None:
            snapshot_identity = snapshot_release.get("identity", snapshot_release["manifest_sha256"])
            if upgrade_plan["active_release"] != snapshot_identity:
                raise RuntimeError("upgrade plan is not bound to the snapshot release")
            _run_candidate_upgrade(
                target,
                target_runner,
                release,
                network,
                volumes,
                upgrade_plan,
                candidate_identity,
            )
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
            _neutralize_generation(
                target, target_runner, release, generation, network, volumes,
            )
            pocketid_admission = _reconcile_staging_pocketid(
                target,
                target_runner,
                release,
                network,
                volumes,
                candidate_identity,
            )
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
    generation_identity = _with_mcp_runtime_authority(
        target, target_runner, generation_identity, mcp_authority,
    )
    previous = _previous_generation_record(target, current)
    active_path = f"{target.value['state_directory']}/active.json"

    def rollback_active_candidate(error: Exception) -> None:
        _rollback_active_candidate(
            target,
            target_runner,
            current=current,
            current_identity=identity,
            generation_identity=generation_identity,
            active_path=active_path,
            error=error,
        )
    phase_started = time.monotonic()
    cutover_started = phase_started
    _record_event(target, target_runner, generation, "restore", "activation", "started")
    # A stable ingress gateway is intentionally outside this service
    # perimeter. It must keep serving the maintenance response while the
    # stateful cohort is replaced.
    _activate_generation(
        target, target_runner, identity, candidate_identity, generation_identity,
    )
    activation_seconds = round(time.monotonic() - phase_started, 3)
    try:
        _record_event(
            target,
            target_runner,
            generation,
            "restore",
            "activation",
            "completed",
            duration_seconds=activation_seconds,
        )
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
    except Exception as error:
        rollback_active_candidate(error)
        raise
    phase_started = time.monotonic()
    _record_event(target, target_runner, generation, "restore", "validation", "started")
    try:
        health = _gate(health_command, target, arguments.targets)
        smoke = _gate(smoke_command, target, arguments.targets)
        expected_release_definitions_sha256 = None
        if signed_plan_evidence is not None:
            staging_evidence = signed_plan_evidence.get(
                "staging_evidence", signed_plan_evidence,
            )
            expected_release_definitions_sha256 = staging_evidence["staging"][
                "release_definitions_sha256"
            ]
        try:
            control_validation = validate_restore(
                materialize_state["controls"],
                smoke["controls"],
                require_unchanged_release=not candidate_differs,
            )
        except ControlManifestError as error:
            raise RuntimeError(str(error)) from error
        if expected_release_definitions_sha256 is not None and (
            smoke.get("release_definitions_sha256") != expected_release_definitions_sha256
        ):
            raise RuntimeError("production release definitions differ from staging qualification")
        production_activation = None
        if target.value["environment"] == "production" and attempt is None:
            # The candidate databases now run on the canonical Compose network.
            network = target.value["compose"]["default_network"]
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
                        "up", "--detach", "--wait", "--force-recreate", "--no-deps",
                        *[target.value["services"][role] for role in BACKUP_WRITER_SERVICE_ROLES],
                    ],
                ),
            )
            production_activation["pocket_id_admission"] = _reconcile_production_pocketid(
                target, target_runner, generation_identity,
            )
            health = _gate(health_command, target, arguments.targets)
            smoke = _gate(smoke_command, target, arguments.targets)
    except Exception as error:
        rollback_active_candidate(error)
        raise
    validation_seconds = round(time.monotonic() - phase_started, 3)
    admission_receipt = None
    quarantine_receipt = None
    runtime_evidence = None
    try:
        _record_event(
            target,
            target_runner,
            generation,
            "restore",
            "validation",
            "completed",
            duration_seconds=validation_seconds,
        )
        if attempt is not None:
            operation_bundle_sha256 = getattr(arguments, "operation_bundle_sha256", None)
            if not isinstance(operation_bundle_sha256, str):
                raise RuntimeError("release operation bundle identity is missing")
            production = target.value["environment"] == "production"
            if not production:
                evidence_body = {
                    "schema": "usl-release-runtime-evidence/v1",
                    "target": target.name,
                    "attempt": attempt,
                    "release": release["identity"],
                    "snapshot": arguments.snapshot,
                    "generation": generation,
                    "operation_kind": operation_kind,
                    "auth_compose_admission": auth_compose_admission,
                    "pocket_id_admission": pocketid_admission,
                    "environment_state_preservation": environment_state_preservation,
                    "status": "validated",
                }
                evidence_body["sha256"] = hashlib.sha256(
                    json.dumps(
                        evidence_body, sort_keys=True, separators=(",", ":"),
                    ).encode(),
                ).hexdigest()
                runtime_evidence = _release_runtime_evidence(
                    evidence_body,
                    target=target,
                    attempt=attempt,
                    release=release["identity"],
                    snapshot=arguments.snapshot,
                    generation=generation,
                    operation_kind=operation_kind,
                )
                runtime_evidence_path = f"{generation_root}/runtime-evidence.json"
                _write_remote(
                    target,
                    target_runner,
                    runtime_evidence_path,
                    json.dumps(runtime_evidence, indent=2, sort_keys=True) + "\n",
                    "0444",
                )
            receipt_body = _release_boundary_receipt(
                schema=(
                    "usl-release-quarantine/v2"
                    if production else "usl-release-admission/v2"
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
                runtime_evidence_sha256=(runtime_evidence or {}).get("sha256"),
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
    except Exception as error:
        rollback_active_candidate(error)
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
        "cron_policy_application": cron_policy_application,
        "environment_state_preservation": environment_state_preservation,
        "pocket_id_admission": pocketid_admission,
        "auth_compose_admission": auth_compose_admission,
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
        "runtime_evidence": (
            {"path": f"{generation_root}/runtime-evidence.json", **runtime_evidence}
            if runtime_evidence is not None else None
        ),
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


def _digested_document(value: dict) -> dict:
    document = dict(value)
    document["sha256"] = hashlib.sha256(
        json.dumps(document, sort_keys=True, separators=(",", ":")).encode(),
    ).hexdigest()
    return document


def _validate_digested_document(value: object, *, schema: str, proof_id: str) -> dict:
    if not isinstance(value, dict) or value.get("schema") != schema:
        raise RuntimeError("recovery proof evidence schema differs")
    fields = {
        "usl-disposable-recovery-proof-state/v2": {
            "schema", "proof_id", "source", "phase", "release_identity",
            "release_manifest_sha256", "runtime_sha256", "backup", "started_at",
            "deadline_at", "updated_at", "duration_seconds", "sha256",
        },
        "usl-disposable-recovery-proof/v2": {
            "schema", "proof_id", "source", "release", "backup", "materialization",
            "runtime", "health", "smoke", "durable_state", "reusable_cache",
            "ownership", "cleanup", "isolation", "started_at", "completed_at",
            "duration_seconds", "max_duration_seconds", "status", "sha256",
        },
        "usl-disposable-recovery-proof-failure/v2": {
            "schema", "proof_id", "source", "stage", "error_type", "error_sha256",
            "cleanup", "runtime_sha256", "started_at", "failed_at",
            "duration_seconds", "status", "sha256",
        },
    }.get(schema)
    if fields is None or set(value) != fields:
        raise RuntimeError("recovery proof evidence fields differ")
    if value.get("proof_id") != proof_id:
        raise RuntimeError("recovery proof evidence identity differs")
    digest = value.get("sha256")
    body = {key: item for key, item in value.items() if key != "sha256"}
    if not re.fullmatch(r"[0-9a-f]{64}", str(digest)) or digest != hashlib.sha256(
        json.dumps(body, sort_keys=True, separators=(",", ":")).encode(),
    ).hexdigest():
        raise RuntimeError("recovery proof evidence digest differs")
    return value


def _recovery_proof_timestamp(value: object, field: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError as error:
        raise RuntimeError(f"recovery proof {field} timestamp is invalid") from error
    if parsed.tzinfo is None:
        raise RuntimeError(f"recovery proof {field} timestamp is invalid")
    return parsed


def _validate_recovery_proof_nested(value: object, path: str = "receipt") -> None:
    """Reject malformed status, digest and timestamp leaves at every nesting depth."""
    if isinstance(value, dict):
        for key, item in value.items():
            nested = f"{path}.{key}"
            if key == "status" and item not in {
                "passed", "clean", "healthy", "ready", "materialized", "verified",
                "reused", "internal", "failed", "armed",
            }:
                raise RuntimeError(f"recovery proof nested status is invalid: {nested}")
            if key.endswith("sha256") and not isinstance(item, dict) and not re.fullmatch(
                r"[0-9a-f]{64}", str(item),
            ):
                raise RuntimeError(f"recovery proof nested digest is invalid: {nested}")
            if key.endswith("_at"):
                _recovery_proof_timestamp(item, nested)
            _validate_recovery_proof_nested(item, nested)
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _validate_recovery_proof_nested(item, f"{path}[{index}]")


def _validate_recovery_proof_receipt(value: object, proof_id: str) -> dict:
    receipt = _validate_digested_document(
        value,
        schema="usl-disposable-recovery-proof/v2",
        proof_id=proof_id,
    )
    _validate_recovery_proof_nested(receipt)
    release = receipt["release"]
    backup = receipt["backup"]
    isolation = receipt["isolation"]
    runtime = receipt["runtime"]
    materialization = receipt["materialization"]
    health = receipt["health"]
    smoke = receipt["smoke"]
    durable = receipt["durable_state"]
    reusable_cache = receipt["reusable_cache"]
    cleanup = receipt["cleanup"]
    ownership = receipt["ownership"]
    sample_roles = {
        "odoo_filestore", "paperless_media", "paperless_archive",
        "paperless_thumbnails", "paperless_tantivy", "paperless_vectors",
    }
    cache_roles = {
        "paperless_archive", "paperless_thumbnails", "paperless_tantivy", "paperless_vectors",
    }
    expected_health_checks = {
        *(f"{role}_running" for role in RECOVERY_PROOF_RUNTIME_ROLES),
        "odoo_http", "paperless_http", "gotenberg_http", "tika_http", "paperless_redis",
        "mcp_ready", "mcp_unauthenticated_boundary", "step_ca",
        "mcp_vault_decrypt", "renderer_mtls_health", "sign_cryptographic_transaction",
    }
    samples = durable.get("samples", {}) if isinstance(durable, dict) else {}
    if (
        not isinstance(materialization, dict)
        or set(materialization) != {
            "cohort_schema", "capacity", "controls_sha256", "sign_secrets_restored",
            "mcp_secrets_restored", "renderer_secrets_restored",
            "paperless_personal_ai_keys_restored", "status",
        }
        or materialization.get("cohort_schema") != RECOVERY_COHORT_SCHEMA
        or not isinstance(materialization.get("capacity"), dict)
        or set(materialization["capacity"]) != {
            "schema", "source", "available_bytes", "candidate_bytes", "reserve_bytes",
            "required_bytes", "status",
        }
        or materialization["capacity"].get("schema") != "usl-recovery-proof-capacity/v1"
        or materialization["capacity"].get("status") != "passed"
        or any(
            not isinstance(materialization["capacity"].get(key), int)
            or materialization["capacity"].get(key) < 0
            for key in ("available_bytes", "candidate_bytes", "reserve_bytes", "required_bytes")
        )
        or materialization["capacity"].get("required_bytes")
        != materialization["capacity"].get("candidate_bytes") + materialization["capacity"].get("reserve_bytes")
        or materialization["capacity"].get("available_bytes") < materialization["capacity"].get("required_bytes")
        or not isinstance(health, dict) or set(health) != {"checks", "databases", "checked_at", "status"}
        or health.get("databases") != {"odoo": True, "paperless": True}
        or set(health.get("checks", {})) != expected_health_checks
        or any(item is not True for item in health.get("checks", {}).values())
        or not isinstance(smoke, dict) or set(smoke) != {"runtime", "controls", "status"}
        or not isinstance(smoke.get("runtime"), dict)
        or set(smoke["runtime"]) != {"http", "oauth", "signing", "status"}
        or smoke["runtime"].get("http") != {
            "odoo": "passed", "paperless": "passed", "gotenberg": "passed", "tika": "passed",
        }
        or smoke["runtime"].get("signing") != {
            "step_ca": "passed", "cryptographic_transaction": "passed",
            "renderer_mtls": "passed",
        }
        or set(smoke["runtime"].get("oauth", {})) != {
            "readiness", "unauthenticated_boundary", "schema_version", "active_enrollments",
            "refreshed", "cached", "unavailable", "vault_readability",
        }
        or smoke["runtime"]["oauth"].get("readiness") != "passed"
        or smoke["runtime"]["oauth"].get("unauthenticated_boundary") != "passed"
        or smoke["runtime"]["oauth"].get("unavailable") != 0
        or smoke["runtime"]["oauth"].get("vault_readability") not in {"empty", "decrypted"}
        or any(
            not isinstance(smoke["runtime"]["oauth"].get(key), int)
            or smoke["runtime"]["oauth"].get(key) < 0
            for key in ("active_enrollments", "refreshed", "cached")
        )
        or smoke["runtime"]["oauth"].get("refreshed", -1)
        + smoke["runtime"]["oauth"].get("cached", -1)
        != smoke["runtime"]["oauth"].get("active_enrollments")
        or not isinstance(durable, dict)
        or set(durable) != {"odoo", "paperless", "mcp_oauth", "samples", "checked_at", "status"}
        or set(durable.get("mcp_oauth", {})) != {
            "schema_version", "vault_identity_sha256", "recovered_key_material_sha256",
            "vault_key_binding_sha256", "migration", "readability", "status",
        }
        or durable.get("odoo", {}).get("status") != "passed"
        or not isinstance(durable.get("odoo", {}).get("attachment_records"), int)
        or durable.get("odoo", {}).get("attachment_records") < 1
        or durable.get("paperless", {}).get("status") != "passed"
        or not isinstance(durable.get("paperless", {}).get("document_records"), int)
        or durable.get("paperless", {}).get("document_records") < 1
        or durable.get("mcp_oauth", {}).get("schema_version") != 1
        or set(samples) != sample_roles
        or any(
            not isinstance(sample, dict)
            or set(sample) != {"file_count", "sample_content_sha256", "status"}
            or not isinstance(sample["file_count"], int) or sample["file_count"] < 1
            or sample["status"] != "passed"
            for sample in samples.values()
        )
        or not isinstance(reusable_cache, dict)
        or set(reusable_cache) != {*cache_roles, "status"}
        or any(
            set(reusable_cache[role]) != {
                "file_count", "sample_content_sha256", "capture_identity_sha256", "status",
            }
            for role in cache_roles
        )
    ):
        raise RuntimeError("recovery proof completion receipt nested contract is invalid")
    if (
        receipt["source"] != "production"
        or receipt["status"] != "passed"
        or not isinstance(release, dict)
        or set(release) != {"identity", "manifest_sha256"}
        or not all(re.fullmatch(r"[0-9a-f]{64}", str(value)) for value in release.values())
        or not isinstance(backup, dict)
        or set(backup) != {
            "run_id", "receipt_sha256", "durable_snapshot_id", "cache_snapshot_id",
        }
        or backup["run_id"] != f"proof-{proof_id}"
        or not all(
            re.fullmatch(r"[0-9a-f]{64}", str(backup[key]))
            for key in ("receipt_sha256", "durable_snapshot_id", "cache_snapshot_id")
        )
        or not isinstance(materialization, dict)
        or materialization.get("status") != "materialized"
        or materialization.get("sign_secrets_restored") is not True
        or materialization.get("mcp_secrets_restored") is not True
        or materialization.get("renderer_secrets_restored") is not True
        or materialization.get("paperless_personal_ai_keys_restored") is not True
        or not isinstance(runtime, dict)
        or set(runtime) != {"network", "services", "environment", "quarantine", "status"}
        or runtime["status"] != "passed"
        or runtime["network"] != "internal"
        or set(runtime["services"]) != set(RECOVERY_PROOF_RUNTIME_ROLES)
        or any(
            not isinstance(service, dict)
            or set(service) != {"container_name_sha256", "status"}
            or service["status"] != "ready"
            for service in runtime["services"].values()
        )
        or not isinstance(runtime["environment"], dict)
        or runtime["environment"].get("status") != "passed"
        or set(runtime["environment"].get("environment_sha256", {})) != {
            "database", "odoo", "paperless", "mcp", "dss",
            "better-auth", "credential-encryption-key", "personal-ai",
        }
        or not all(
            re.fullmatch(r"[0-9a-f]{64}", str(item))
            for item in runtime["environment"]["environment_sha256"].values()
        )
        or not isinstance(runtime["quarantine"], dict)
        or set(runtime["quarantine"]) != {
            "candidate_fingerprint", "cron_count", "database_neutralized",
            "fetchmail_count", "status",
        }
        or runtime["quarantine"].get("status") != "passed"
        or runtime["quarantine"].get("database_neutralized") is not True
        or runtime["quarantine"].get("cron_count") != 0
        or runtime["quarantine"].get("fetchmail_count") != 0
        or not isinstance(health, dict) or health.get("status") != "passed"
        or not isinstance(smoke, dict) or smoke.get("status") != "passed"
        or not isinstance(durable, dict) or durable.get("status") != "passed"
        or not isinstance(durable.get("mcp_oauth"), dict)
        or durable["mcp_oauth"].get("migration") != "passed"
        or durable["mcp_oauth"].get("readability") != "passed"
        or durable["mcp_oauth"].get("status") != "passed"
        or not isinstance(reusable_cache, dict) or reusable_cache.get("status") != "reused"
        or any(
            not isinstance(reusable_cache.get(role), dict)
            or reusable_cache[role].get("status") != "passed"
            for role in (
                "paperless_archive", "paperless_thumbnails",
                "paperless_tantivy", "paperless_vectors",
            )
        )
        or not isinstance(cleanup, dict)
        or set(cleanup) != {
            "schema", "containers", "volumes", "networks", "workspaces", "status",
        }
        or cleanup["schema"] != "usl-recovery-proof-cleanup/v1"
        or cleanup["status"] != "clean"
        or not isinstance(ownership, dict)
        or set(ownership) != {"label", "resource_names_sha256"}
        or ownership["label"] != RECOVERY_PROOF_OWNER
        or not isinstance(isolation, dict)
        or isolation != {
            "active_runtime_sha256": isolation.get("active_runtime_sha256"),
            "active_runtime_unchanged": True,
            "gateway_attached": False,
            "host_ports_published": False,
            "external_networks_attached": False,
            "side_effects_neutralized": True,
            "persistent_staging_touched": False,
            "production_secrets_modified": False,
            "production_secrets_sha256": isolation.get("production_secrets_sha256"),
            "runtime_ledger_used_for_restore": False,
            "perimeter_sha256": isolation.get("perimeter_sha256"),
        }
        or not re.fullmatch(r"[0-9a-f]{64}", str(isolation["active_runtime_sha256"]))
        or receipt["max_duration_seconds"] != RECOVERY_PROOF_MAX_SECONDS
        or not isinstance(receipt["duration_seconds"], (int, float))
        or not 0 <= receipt["duration_seconds"] < RECOVERY_PROOF_MAX_SECONDS
    ):
        raise RuntimeError("recovery proof completion receipt is invalid")
    started = _recovery_proof_timestamp(receipt["started_at"], "started_at")
    completed = _recovery_proof_timestamp(receipt["completed_at"], "completed_at")
    nested_times = [
        _recovery_proof_timestamp(health.get("checked_at"), "health.checked_at"),
        _recovery_proof_timestamp(durable.get("checked_at"), "durable_state.checked_at"),
    ]
    if (
        completed < started
        or (completed - started).total_seconds() >= RECOVERY_PROOF_MAX_SECONDS
        or any(not started <= item <= completed for item in nested_times)
    ):
        raise RuntimeError("recovery proof completion duration is invalid")
    return receipt


def _validate_recovery_proof_state(value: object, proof_id: str) -> dict:
    state = _validate_digested_document(
        value,
        schema="usl-disposable-recovery-proof-state/v2",
        proof_id=proof_id,
    )
    if (
        state["source"] != "production"
        or state["phase"] not in {
            "backup-started", "backup-qualified", "resources-created", "materialized",
            "runtime-started", "validated", "finalizing",
        }
        or not re.fullmatch(r"[0-9a-f]{64}", str(state["release_identity"]))
        or not re.fullmatch(r"[0-9a-f]{64}", str(state["release_manifest_sha256"]))
        or not re.fullmatch(r"[0-9a-f]{64}", str(state["runtime_sha256"]))
        or state["backup"] is not None and not isinstance(state["backup"], dict)
    ):
        raise RuntimeError("recovery proof state is invalid")
    started = _recovery_proof_timestamp(state["started_at"], "state.started_at")
    deadline = _recovery_proof_timestamp(state["deadline_at"], "state.deadline_at")
    updated = _recovery_proof_timestamp(state["updated_at"], "state.updated_at")
    if not started <= updated < deadline or not isinstance(
        state["duration_seconds"], (int, float),
    ) or not 0 <= state["duration_seconds"] < RECOVERY_PROOF_MAX_SECONDS:
        raise RuntimeError("recovery proof state timestamp is invalid")
    return state


def _recovery_proof_capacity(target, runner, tool_image: str, runtime: dict) -> dict:
    docker_root = runner.run(
        ["docker", "info", "--format", "{{.DockerRootDir}}"],
    ).stdout.strip()
    if not docker_root.startswith("/"):
        raise RuntimeError("recovery proof Docker storage root is invalid")
    source, available = _filesystem_capacity(runner, docker_root)
    candidate = _measure_candidate_bytes(target, runner, tool_image, runtime)
    candidate_bytes = sum(candidate.values())
    required = candidate_bytes + MINIMUM_FREE_BYTES
    if available < required:
        raise RuntimeError(
            "recovery proof capacity refused on "
            f"{source}: {available / 1024**3:.1f} GiB free, "
            f"{candidate_bytes / 1024**3:.1f} GiB measured cohort, "
            f"{MINIMUM_FREE_BYTES / 1024**3:.1f} GiB reserve",
        )
    return {
        "schema": "usl-recovery-proof-capacity/v1",
        "source": source,
        "available_bytes": available,
        "candidate_bytes": candidate_bytes,
        "reserve_bytes": MINIMUM_FREE_BYTES,
        "required_bytes": required,
        "status": "passed",
    }


def _recovery_proof_root(target, evidence_directory: Path, proof_id: str) -> str:
    if not RECOVERY_PROOF_ID.fullmatch(proof_id):
        raise RuntimeError("recovery proof identity is invalid")
    evidence = str(evidence_directory)
    path = Path(evidence)
    if not path.is_absolute() or ".." in path.parts or path == Path("/"):
        raise RuntimeError("recovery proof evidence directory must be an absolute scoped path")
    protected = [
        Path(target.value["state_directory"]),
        Path(target.value["secrets"]["env_file"]),
        Path(target.value["secrets"]["env_file"]).parent,
        *(Path(item["path"]) for item in target.value["paths"].values()),
    ]
    for protected_path in protected:
        if path == protected_path or path in protected_path.parents or protected_path in path.parents:
            raise RuntimeError(
                "recovery proof evidence directory overlaps environment-owned state",
            )
    for tier in target.value["storage"]["tiers"].values():
        storage_root = Path(tier["path"])
        if path == storage_root or path in storage_root.parents:
            raise RuntimeError("recovery proof evidence directory contains a storage root")
    return str(path / proof_id)


def _prepare_recovery_proof_root(runner, proof_root: str) -> None:
    program = (
        "import os,pathlib,stat,sys;"
        "p=pathlib.Path(sys.argv[1]);"
        "exec(\"for a in [*reversed(p.parents),p]:\\n"
        " if a==pathlib.Path('/') or not a.exists(): continue\\n"
        " s=a.lstat()\\n if stat.S_ISLNK(s.st_mode): raise SystemExit('symlink')\");"
        "p.mkdir(parents=True,exist_ok=True);os.chmod(p,0o700);"
        "s=p.lstat();"
        "assert stat.S_ISDIR(s.st_mode) and stat.S_IMODE(s.st_mode)==0o700 and s.st_uid==os.geteuid()"
    )
    runner.run(["python3", "-c", program, proof_root])


def _recovery_proof_protected_secrets_sha256(target, runner) -> str:
    paths = [
        target.value["secrets"]["env_file"],
        *(
            item["path"] for name, item in sorted(target.value["paths"].items())
            if "secret" in name or name.endswith("_keys")
        ),
    ]
    program = (
        "import hashlib,json,pathlib,stat,sys;h=hashlib.sha256();"
        "exec(\"for root in map(pathlib.Path,sys.argv[1:]):\\n"
        " if not root.exists(): raise SystemExit('missing protected secret')\\n"
        " ps=[root] if root.is_file() else sorted(root.rglob('*'))\\n"
        " for p in ps:\\n  s=p.lstat();h.update(json.dumps([str(p),stat.S_IMODE(s.st_mode),s.st_size],separators=(',',':')).encode())\\n"
        "  h.update(p.read_bytes()) if p.is_file() else None\");print(h.hexdigest())"
    )
    digest = runner.run(["python3", "-c", program, *paths]).stdout.strip()
    if not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise RuntimeError("recovery proof protected secret identity is invalid")
    return digest


def _recovery_proof_names(target, proof_id: str) -> dict:
    token = hashlib.sha256(proof_id.encode()).hexdigest()[:16]
    prefix = f"usl-recovery-proof-{token}"
    return {
        "containers": {
            "odoo_db": f"{prefix}-odoo-db",
            "paperless_db": f"{prefix}-paperless-db",
            "materializer": f"{prefix}-materializer",
            **{
                role: f"{prefix}-{role.replace('_', '-')}"
                for role in RECOVERY_PROOF_RUNTIME_ROLES
            },
        },
        "volumes": {
            role: f"{prefix}-{role.replace('_', '-')}"
            for role in target.value["volumes"]
        },
        "network": f"{prefix}-network",
        "materialization_network": f"{prefix}-materialization-egress",
    }


def _recovery_proof_labels(proof_id: str, *, role: str) -> dict[str, str]:
    return {
        "com.unstaticlabs.recovery-proof.owner": RECOVERY_PROOF_OWNER,
        "com.unstaticlabs.recovery-proof.id": proof_id,
        "com.unstaticlabs.recovery-proof.source": "production",
        "com.unstaticlabs.recovery-proof.role": role,
    }


def _docker_labels(runner, resource_type: str, name: str) -> dict[str, str] | None:
    command = ["docker"]
    if resource_type != "container":
        command.append(resource_type)
    command.extend(["inspect", name, "--format", "{{json .Config.Labels}}"])
    if resource_type in {"volume", "network"}:
        command[-1] = "{{json .Labels}}"
    result = runner.run(command, check=False)
    if result.returncode:
        return None
    try:
        labels = json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise RuntimeError(f"recovery proof {resource_type} labels are invalid") from error
    if not isinstance(labels, dict) or not all(
        isinstance(key, str) and isinstance(value, str) for key, value in labels.items()
    ):
        raise RuntimeError(f"recovery proof {resource_type} labels are invalid")
    return labels


def _require_recovery_proof_owner(
    runner,
    resource_type: str,
    name: str,
    proof_id: str,
    role: str,
) -> bool:
    labels = _docker_labels(runner, resource_type, name)
    if labels is None:
        return False
    expected = _recovery_proof_labels(proof_id, role=role)
    if any(labels.get(key) != value for key, value in expected.items()):
        raise RuntimeError(
            f"refusing foreign {resource_type} at recovery proof perimeter: {name}",
        )
    return True


def _cleanup_recovery_proof_resources(target, runner, proof_id: str, proof_root: str) -> dict:
    names = _recovery_proof_names(target, proof_id)
    removed = {"containers": [], "volumes": [], "networks": [], "workspaces": []}
    for role, name in names["containers"].items():
        if _require_recovery_proof_owner(runner, "container", name, proof_id, role):
            result = runner.run(["docker", "rm", "--force", name], check=False)
            if result.returncode:
                raise RuntimeError(f"recovery proof container cleanup failed: {name}")
            removed["containers"].append(name)
    for role, name in names["volumes"].items():
        if _require_recovery_proof_owner(runner, "volume", name, proof_id, role):
            result = runner.run(["docker", "volume", "rm", name], check=False)
            if result.returncode:
                raise RuntimeError(f"recovery proof volume cleanup failed: {name}")
            removed["volumes"].append(name)
    network = names["network"]
    if _require_recovery_proof_owner(runner, "network", network, proof_id, "private-network"):
        result = runner.run(["docker", "network", "rm", network], check=False)
        if result.returncode:
            raise RuntimeError(f"recovery proof network cleanup failed: {network}")
        removed["networks"].append(network)
    egress = names["materialization_network"]
    if _require_recovery_proof_owner(runner, "network", egress, proof_id, "materialization-egress"):
        result = runner.run(["docker", "network", "rm", egress], check=False)
        if result.returncode:
            raise RuntimeError(f"recovery proof network cleanup failed: {egress}")
        removed["networks"].append(egress)
    generation = f"gproof-{hashlib.sha256(proof_id.encode()).hexdigest()[:16]}"
    workspace = f"{proof_root}/generations/{generation}"
    if not workspace.startswith(proof_root.rstrip("/") + "/generations/gproof-"):
        raise RuntimeError("recovery proof workspace escaped its evidence root")
    if runner.run(["test", "-e", workspace], check=False).returncode == 0:
        if runner.run(["test", "-L", workspace], check=False).returncode == 0:
            raise RuntimeError("recovery proof workspace is a symlink")
        runner.run(["rm", "-rf", "--", workspace])
        removed["workspaces"].append(workspace)
    label = f"com.unstaticlabs.recovery-proof.id={proof_id}"
    owner_label = f"com.unstaticlabs.recovery-proof.owner={RECOVERY_PROOF_OWNER}"
    survivors = {}
    for resource_type, command in {
        "containers": ["docker", "ps", "--all", "--filter", f"label={owner_label}", "--filter", f"label={label}", "--format", "{{.Names}}"],
        "volumes": ["docker", "volume", "ls", "--filter", f"label={owner_label}", "--filter", f"label={label}", "--format", "{{.Name}}"],
        "networks": ["docker", "network", "ls", "--filter", f"label={owner_label}", "--filter", f"label={label}", "--format", "{{.Name}}"],
    }.items():
        names_left = [item for item in runner.run(command).stdout.splitlines() if item]
        if names_left:
            survivors[resource_type] = names_left
    if survivors:
        raise RuntimeError("recovery proof owned resources survived cleanup")
    return {
        "schema": "usl-recovery-proof-cleanup/v1",
        **removed,
        "status": "clean",
    }


def _create_recovery_proof_resources(target, runner, proof_id: str) -> dict:
    names = _recovery_proof_names(target, proof_id)
    created: list[tuple[str, str, str]] = []
    try:
        for role, name in names["volumes"].items():
            if _docker_labels(runner, "volume", name) is not None:
                raise RuntimeError(f"recovery proof volume already exists: {name}")
            command = ["docker", "volume", "create"]
            for key, value in _recovery_proof_labels(proof_id, role=role).items():
                command.extend(["--label", f"{key}={value}"])
            command.append(name)
            runner.run(command)
            created.append(("volume", role, name))
        network = names["network"]
        if _docker_labels(runner, "network", network) is not None:
            raise RuntimeError(f"recovery proof network already exists: {network}")
        command = ["docker", "network", "create"]
        for key, value in _recovery_proof_labels(
            proof_id, role="private-network",
        ).items():
            command.extend(["--label", f"{key}={value}"])
        command.extend(["--internal", network])
        runner.run(command)
        created.append(("network", "private-network", network))
    except Exception:
        for resource_type, _role, name in reversed(created):
            command = ["docker"]
            command.append(resource_type)
            command.extend(["rm", name])
            runner.run(command, check=False)
        raise
    return names


def _create_recovery_proof_materialization_network(runner, proof_id: str, names: dict) -> str:
    network = names["materialization_network"]
    if _docker_labels(runner, "network", network) is not None:
        raise RuntimeError(f"recovery proof materialization network already exists: {network}")
    command = ["docker", "network", "create"]
    for key, value in _recovery_proof_labels(proof_id, role="materialization-egress").items():
        command.extend(["--label", f"{key}={value}"])
    command.append(network)
    runner.run(command)
    return network


def _start_recovery_proof_database(
    target,
    runner,
    proof_id: str,
    names: dict,
    images: dict[str, str],
    database_key: str,
    runtime_env: str,
) -> str:
    role = f"{database_key}_db"
    database = target.value["databases"][database_key]
    service_role = "odoo_db" if database_key == "odoo" else "paperless_db"
    alias = database["service"]
    command = [
        "docker", "run", "--detach", "--name", names["containers"][role],
        "--log-driver", "none",
        "--security-opt", "no-new-privileges:true",
    ]
    for key, value in _recovery_proof_labels(proof_id, role=role).items():
        command.extend(["--label", f"{key}={value}"])
    command.extend([
        "--network", names["network"], "--network-alias", alias,
        "--env-file", runtime_env,
        "--env", f"POSTGRES_USER={database['user']}",
        "--env", "POSTGRES_DB=postgres",
        "--volume", f"{names['volumes'][database_key + '_postgres']}:/var/lib/postgresql/data",
        "--entrypoint", "/bin/bash", images[target.value["services"][service_role]],
        "-ceu",
        f'export POSTGRES_PASSWORD="${database["password_key"]}"; '
        "exec /usr/local/bin/docker-entrypoint.sh postgres",
    ])
    runner.run(command)
    name = names["containers"][role]
    for _attempt in range(60):
        ready = runner.run(
            [
                "docker", "exec", name, "pg_isready", "--username", database["user"],
                "--dbname", "postgres",
            ],
            check=False,
        )
        if ready.returncode == 0:
            return name
        time.sleep(1)
    raise RuntimeError(f"recovery proof database did not become ready: {database_key}")


def _recovery_proof_runtime_config(runner, identity: dict) -> dict:
    try:
        rendered = json.loads(
            runner.run(compose_command(identity, ["config", "--format", "json"])).stdout,
        )
    except json.JSONDecodeError as error:
        raise RuntimeError("recovery proof Compose configuration is invalid") from error
    if not isinstance(rendered.get("services"), dict):
        raise RuntimeError("recovery proof Compose service configuration is invalid")
    return rendered


def _recovery_proof_environment(
    target,
    runner,
    proof_root: str,
    rendered: dict,
    mcp_secrets_sha256: str,
    paperless_keys_sha256: str,
) -> tuple[dict[str, str], dict]:
    """Write least-privilege, disposable service environments outside the receipt tree."""
    proof_id = proof_root.rstrip("/").rsplit("/", 1)[-1]
    workspace = f"{proof_root}/generations/gproof-{hashlib.sha256(proof_id.encode()).hexdigest()[:16]}"
    services = rendered["services"]
    selected: dict[str, dict[str, str]] = {}
    allow = {
        "database": {"ODOO_DB_PASSWORD", "PAPERLESS_DBPASS"},
        "odoo": {
            "ODOO_ADMIN_PASSWORD", "ODOO_DB_PASSWORD", "ODOO_ADDONS_PATH",
            "USL_USER_DOCS_PATH", "USL_RELEASE_COMMIT",
        },
        "paperless": {
            "PAPERLESS_DBPASS", "PAPERLESS_SECRET_KEY", "PAPERLESS_TIME_ZONE",
            "PAPERLESS_OCR_LANGUAGE", "PAPERLESS_APPS", "USL_PERSONAL_AI_MASTER_KEYS_PATH",
        },
        "mcp": {
            "MCP_TARGET_CONCURRENCY", "MCP_MAX_REQUEST_BYTES", "MCP_MAX_RESPONSE_BYTES",
        },
        "dss": {
            key for key in services[target.value["services"]["sign"]].get("environment", {})
            if key.endswith("_PASSWORD")
        },
    }
    database_environment = services[target.value["services"]["odoo"]].get("environment", {})
    database_environment = {
        **database_environment,
        **services[target.value["services"]["paperless"]].get("environment", {}),
    }
    selected["database"] = {
        key: str(value) for key, value in database_environment.items() if key in allow["database"]
    }
    paperless_dbpass = services[target.value["services"]["paperless"]].get(
        "environment", {},
    ).get("PAPERLESS_DBPASS")
    if not paperless_dbpass:
        raise RuntimeError("recovery proof Paperless database password is missing")
    selected["database"]["PAPERLESS_DB_PASSWORD"] = str(paperless_dbpass)
    selected["odoo"] = {
        key: str(value)
        for key, value in services[target.value["services"]["odoo"]].get("environment", {}).items()
        if key in allow["odoo"]
        or key.startswith("USL_SIGN_") and not key.endswith("PASSWORD")
        or key.startswith("USL_DOCUMENT_RENDERER_")
    }
    selected["odoo"].update({
        "ODOO_DB_HOST": target.value["databases"]["odoo"]["service"],
        "ODOO_DB_PORT": "5432",
        "ODOO_DB_USER": target.value["databases"]["odoo"]["user"],
        "ODOO_DB_NAME": target.value["databases"]["odoo"]["name"],
        "ODOO_DB_FILTER": f"^{target.value['databases']['odoo']['name']}$",
        "ODOO_HTTP_INTERFACE": "0.0.0.0",
        "ODOO_LIST_DB": "False",
        "ODOO_PROXY_MODE": "False",
        "ODOO_WORKERS": "0",
        "ODOO_MAX_CRON_THREADS": "0",
        "ODOO_SMTP_SERVER": "127.0.0.1",
        "ODOO_SMTP_PORT": "1",
        "ODOO_SMTP_USER": "",
        "ODOO_SMTP_PASSWORD": "",
        "USL_DEPLOYMENT_ENV": "recovery-proof",
        "USL_EINVOICE_LIVE_ENABLED": "0",
        "USL_EREPORTING_LIVE_ENABLED": "0",
        "USL_POCKET_ID_ENABLED": "0",
        "USL_SIGN_ADDONS_PATH": "/opt/odoo/custom-addons",
        "PAPERLESS_INTERNAL_URL": "http://paperless-webserver:8000",
    })
    selected["paperless"] = {
        key: str(value)
        for key, value in services[target.value["services"]["paperless"]].get("environment", {}).items()
        if key in allow["paperless"] or key.startswith("PAPERLESS_")
    }
    selected["paperless"].update({
        "PAPERLESS_REDIS": "redis://paperless-broker:6379",
        "PAPERLESS_DBHOST": target.value["databases"]["paperless"]["service"],
        "PAPERLESS_DBNAME": target.value["databases"]["paperless"]["name"],
        "PAPERLESS_DBUSER": target.value["databases"]["paperless"]["user"],
        "PAPERLESS_URL": "http://paperless-webserver:8000",
        "PAPERLESS_PUBLIC_URL": "http://paperless-webserver:8000",
        "PAPERLESS_PUBLIC_BASE_URL": "http://paperless-webserver:8000",
        "PAPERLESS_ALLOWED_HOSTS": "paperless-webserver",
        "PAPERLESS_CORS_ALLOWED_HOSTS": "http://paperless-webserver:8000",
        "PAPERLESS_CSRF_TRUSTED_ORIGINS": "http://paperless-webserver:8000",
        "PAPERLESS_AI_ENABLED": "false",
        "PAPERLESS_AI_LLM_EMBEDDING_ENDPOINT": "http://127.0.0.1:1",
        "PAPERLESS_USL_DEFER_SEMANTIC_INDEX": "true",
        "PAPERLESS_OIDC_ENABLED": "0",
        "PAPERLESS_DISABLE_REGULAR_LOGIN": "false",
        "PAPERLESS_APPS": "paperless_personal_ai",
        "PAPERLESS_EMAIL_TASK_CRON": "disable",
        "PAPERLESS_TRAIN_TASK_CRON": "disable",
        "PAPERLESS_SANITY_TASK_CRON": "disable",
        "PAPERLESS_EMPTY_TRASH_TASK_CRON": "disable",
        "PAPERLESS_WORKFLOW_SCHEDULED_TASK_CRON": "disable",
        "PAPERLESS_SHARE_LINK_BUNDLE_CLEANUP_CRON": "disable",
        "PAPERLESS_LLM_INDEX_TASK_CRON": "disable",
    })
    selected["mcp"] = {
        key: str(value)
        for key, value in services[target.value["services"]["mcp"]].get("environment", {}).items()
        if key in allow["mcp"]
    }
    selected["mcp"].update({
        "MCP_HOST": "0.0.0.0", "MCP_PORT": "3000",
        "MCP_PUBLIC_ORIGIN": "http://odoo-mcp:3000",
        "MCP_ALLOWED_HOSTS": "odoo-mcp", "MCP_HEALTHCHECK_HOST": "odoo-mcp",
        "MCP_ALLOWED_ORIGINS": "http://odoo-mcp:3000",
        "MCP_ALLOW_LOCAL_HTTP_ODOO": "true",
        "ODOO_PUBLIC_ORIGIN": "http://odoo:8069",
        "ODOO_INTERNAL_ORIGIN": "http://odoo:8069",
        "ODOO_DATABASE": target.value["databases"]["odoo"]["name"],
        "MCP_OAUTH_ENABLED": "true", "MCP_OAUTH_DATABASE": "/data/oauth.sqlite",
        "BETTER_AUTH_SECRET_FILE": "/run/secrets/better-auth.secret",
        "MCP_CREDENTIAL_ENCRYPTION_KEY_FILE": "/run/secrets/credential-encryption-key.secret",
        "MCP_OAUTH_TRUSTED_ORIGINS": "http://odoo-mcp:3000",
    })
    selected["dss"] = {
        key: str(value)
        for key, value in services[target.value["services"]["sign"]].get("environment", {}).items()
        if key in allow["dss"] or key.startswith("USL_DSS_") and not key.endswith("_URL")
    }
    selected["dss"].update({
        "USL_DSS_PORT": "8443", "USL_DSS_LOTL_URL": "", "USL_DSS_OJ_URL": "",
        "USL_DSS_TSA_URL": "", "USL_DSS_PLATFORM_KEYSTORE": "/run/usl-sign-dss/platform.p12",
        "USL_DSS_MANIFEST_KEYSTORE": "/run/usl-sign-dss/manifest.p12",
        "USL_DSS_TLS_KEYSTORE": "/run/usl-sign-dss/server.p12",
        "USL_DSS_CLIENT_TRUSTSTORE": "/run/usl-sign-dss/client-trust.p12",
        "USL_DSS_LOCAL_TRUSTSTORE": "/run/usl-sign-dss/local-trust.p12",
    })
    paths: dict[str, str] = {}
    digests: dict[str, str] = {}
    for role, environment in selected.items():
        if any("\n" in key or "\n" in value for key, value in environment.items()):
            raise RuntimeError("recovery proof environment contains a newline")
        text = "".join(f"{key}={environment[key]}\n" for key in sorted(environment))
        path = f"{workspace}/{role}.env"
        _write_remote(target, runner, path, text)
        paths[role] = path
        digests[role] = hashlib.sha256(text.encode()).hexdigest()
    if not re.fullmatch(r"[0-9a-f]{64}", str(mcp_secrets_sha256)):
        raise RuntimeError("recovery proof MCP secret identity is invalid")
    paths["better-auth"] = f"{workspace}/mcp-secrets/better-auth.secret"
    paths["credential-encryption-key"] = (
        f"{workspace}/mcp-secrets/credential-encryption-key.secret"
    )
    digests["better-auth"] = mcp_secrets_sha256
    digests["credential-encryption-key"] = mcp_secrets_sha256
    if not re.fullmatch(r"[0-9a-f]{64}", str(paperless_keys_sha256)):
        raise RuntimeError("recovery proof Paperless keyring identity is invalid")
    paths["personal-ai"] = f"{workspace}/paperless-secrets/personal-ai-keys.json"
    digests["personal-ai"] = paperless_keys_sha256
    return paths, {"environment_sha256": digests, "status": "passed"}


def _recovery_proof_query(target, runner, container: str, database_key: str, query: str) -> dict:
    database = target.value["databases"][database_key]
    result = runner.run([
        "docker", "exec", container, "psql", "--username", database["user"],
        "--dbname", database["name"], "--tuples-only", "--no-align",
        "--command", query,
    ])
    try:
        value = json.loads(result.stdout.strip())
    except json.JSONDecodeError as error:
        raise RuntimeError(f"recovery proof {database_key} control query is invalid") from error
    if not isinstance(value, dict):
        raise RuntimeError(f"recovery proof {database_key} control query is invalid")
    return value


def _run_recovery_proof_container(
    runner,
    proof_id: str,
    names: dict,
    role: str,
    image: str,
    *,
    alias: str,
    arguments: list[str] | None = None,
    env_file: str | list[str] | None = None,
    environment: dict[str, str] | None = None,
    volumes: list[str] | None = None,
    cap_add: list[str] | None = None,
) -> str:
    command = [
        "docker", "run", "--detach", "--name", names["containers"][role],
        "--log-driver", "none",
        "--security-opt", "no-new-privileges:true",
    ]
    for key, value in _recovery_proof_labels(proof_id, role=role).items():
        command.extend(["--label", f"{key}={value}"])
    command.extend(["--network", names["network"], "--network-alias", alias])
    if env_file:
        for path in [env_file] if isinstance(env_file, str) else env_file:
            command.extend(["--env-file", path])
    for key, value in sorted((environment or {}).items()):
        command.extend(["--env", f"{key}={value}"])
    for volume in volumes or []:
        command.extend(["--volume", volume])
    for capability in cap_add or []:
        command.extend(["--cap-add", capability])
    command.append(image)
    command.extend(arguments or [])
    runner.run(command)
    return names["containers"][role]


def _start_recovery_proof_runtime(
    target,
    runner,
    proof_id: str,
    proof_root: str,
    names: dict,
    release: dict,
    images: dict[str, str],
    env: dict[str, str],
) -> dict:
    """Start only restored services on the proof's Docker-internal network."""
    mcp_image = _independent_mcp_image(target, images)
    generation = f"gproof-{hashlib.sha256(proof_id.encode()).hexdigest()[:16]}"
    secrets = f"{proof_root}/generations/{generation}/sign-secrets"
    renderer_secrets = f"{proof_root}/generations/{generation}/renderer-secrets"
    volumes = names["volumes"]
    started: dict[str, str] = {}
    started["paperless_broker"] = _run_recovery_proof_container(
        runner, proof_id, names, "paperless_broker",
        images[target.value["services"]["paperless_broker"]], alias="paperless-broker",
        arguments=["valkey-server", "--save", "", "--appendonly", "no"],
    )
    started["paperless_gotenberg"] = _run_recovery_proof_container(
        runner, proof_id, names, "paperless_gotenberg",
        images[target.value["services"]["paperless_gotenberg"]], alias="paperless-gotenberg",
        arguments=["gotenberg", "--chromium-disable-javascript=true"],
    )
    started["paperless_tika"] = _run_recovery_proof_container(
        runner, proof_id, names, "paperless_tika",
        images[target.value["services"]["paperless_tika"]], alias="paperless-tika",
    )
    started["sign_ca"] = _run_recovery_proof_container(
        runner, proof_id, names, "sign_ca", images[target.value["services"]["sign_ca"]],
        alias="usl-sign-step-ca",
        arguments=[
            "step-ca", "/home/step/config/ca.json", "--password-file", "/home/step/password",
        ],
        volumes=[f"{secrets}/step-ca:/home/step"],
        cap_add=["NET_BIND_SERVICE"],
    )
    started["sign"] = _run_recovery_proof_container(
        runner, proof_id, names, "sign", release["components"]["sign-dss"]["digest_reference"],
        alias="usl-sign-dss", env_file=f"{secrets}/dss.env",
        environment={
            "USL_DSS_PORT": "8443", "USL_DSS_LOTL_URL": "", "USL_DSS_OJ_URL": "",
            "USL_DSS_TSA_URL": "",
        },
        volumes=[f"{secrets}/dss:/run/usl-sign-dss:ro"],
    )
    started["renderer"] = _run_recovery_proof_container(
        runner, proof_id, names, "renderer", release["renderer"]["image"],
        alias="usl-document-renderer",
        environment={
            "USL_RENDERER_TLS_CERT": "/run/renderer/server.crt",
            "USL_RENDERER_TLS_KEY": "/run/renderer/server.key",
            "USL_RENDERER_TLS_CLIENT_CA": "/run/renderer/client-ca.crt",
            "USL_RENDER_CONCURRENCY": "1", "USL_RENDER_TIMEOUT_SECONDS": "30",
        },
        volumes=[
            f"{renderer_secrets}/renderer.crt:/run/renderer/server.crt:ro",
            f"{renderer_secrets}/renderer.key:/run/renderer/server.key:ro",
            f"{renderer_secrets}/ca.crt:/run/renderer/client-ca.crt:ro",
        ],
    )
    started["odoo"] = _run_recovery_proof_container(
        runner, proof_id, names, "odoo", release["components"]["distribution"]["digest_reference"],
        alias="odoo", env_file=[f"{secrets}/odoo.env", env["odoo"]],
        volumes=[
            f"{volumes['odoo_filestore']}:/var/lib/odoo",
            f"{secrets}/odoo:/run/usl-sign:ro",
            f"{renderer_secrets}/ca.crt:/run/secrets/document-renderer/ca.crt:ro",
            f"{renderer_secrets}/odoo.crt:/run/secrets/document-renderer/odoo.crt:ro",
            f"{renderer_secrets}/odoo.key:/run/secrets/document-renderer/odoo.key:ro",
        ],
    )
    started["paperless"] = _run_recovery_proof_container(
        runner, proof_id, names, "paperless", release["components"]["paperless"]["digest_reference"],
        alias="paperless-webserver", env_file=env["paperless"],
        volumes=[
            f"{volumes['paperless_data']}:/usr/src/paperless/data",
            f"{volumes['paperless_media']}:/usr/src/paperless/media",
            f"{volumes['paperless_export']}:/usr/src/paperless/export",
            f"{volumes['paperless_consume']}:/usr/src/paperless/consume",
            f"{volumes['paperless_trash']}:/usr/src/paperless/trash",
            f"{env['personal-ai']}:/run/secrets/usl_personal_ai_master_keys:ro",
        ],
    )
    mcp_common = {
        "alias": "odoo-mcp", "env_file": env["mcp"],
        "volumes": [
            f"{volumes['mcp_oauth']}:/data",
            f"{env['better-auth']}:/run/secrets/better-auth.secret:ro",
            f"{env['credential-encryption-key']}:/run/secrets/credential-encryption-key.secret:ro",
        ],
    }
    runner.run([
        "docker", "run", "--rm", "--network", names["network"],
        "--env-file", env["mcp"],
        "--volume", f"{volumes['mcp_oauth']}:/data",
        "--volume", f"{env['better-auth']}:/run/secrets/better-auth.secret:ro",
        "--volume", f"{env['credential-encryption-key']}:/run/secrets/credential-encryption-key.secret:ro",
        mcp_image, "node", "dist/auth/cli.js", "migrate",
    ])
    started["mcp"] = _run_recovery_proof_container(
        runner, proof_id, names, "mcp", mcp_image, **mcp_common,
    )
    if set(started) != set(RECOVERY_PROOF_RUNTIME_ROLES):
        raise RuntimeError("recovery proof runtime service perimeter differs")
    return {
        "network": "internal",
        "services": {
            role: {
                "container_name_sha256": hashlib.sha256(name.encode()).hexdigest(),
                "status": "ready",
            }
            for role, name in started.items()
        },
        "status": "passed",
    }


def _recovery_proof_runtime_health_once(target, runner, names: dict, release: dict) -> tuple[dict, dict]:
    checks: dict[str, bool] = {}
    for role in RECOVERY_PROOF_RUNTIME_ROLES:
        state = runner.run([
            "docker", "inspect", names["containers"][role],
            "--format", "{{.State.Running}}",
        ], check=False)
        checks[f"{role}_running"] = state.returncode == 0 and state.stdout.strip() == "true"
    commands = {
        "odoo_http": [
            "python", "-c",
            "import urllib.request;"
            "urllib.request.urlopen('http://odoo:8069/web/health?db_server_status=1',"
            "timeout=10).read()",
        ],
        "paperless_http": [
            "python", "-c",
            "import urllib.request;urllib.request.urlopen('http://paperless-webserver:8000/api/',timeout=10).read()",
        ],
        "gotenberg_http": [
            "python", "-c",
            "import urllib.request;urllib.request.urlopen('http://paperless-gotenberg:3000/health',timeout=10).read()",
        ],
        "tika_http": [
            "python", "-c",
            "import urllib.request;urllib.request.urlopen('http://paperless-tika:9998/version',timeout=10).read()",
        ],
    }
    for name, command in commands.items():
        result = runner.run([
            "docker", "run", "--rm", "--network", names["network"], "--entrypoint",
            command[0], release["components"]["distribution"]["digest_reference"], *command[1:],
        ], check=False)
        checks[name] = result.returncode == 0
    checks["paperless_redis"] = runner.run([
        "docker", "exec", names["containers"]["paperless_broker"], "redis-cli", "ping",
    ], check=False).stdout.strip() == "PONG"
    mcp_ready = runner.run([
        "docker", "exec", names["containers"]["mcp"], "node", "-e",
        "require('http').get({host:'127.0.0.1',port:3000,path:'/readyz',"
        "headers:{host:'odoo-mcp'}},r=>{let b='';r.on('data',c=>b+=c);"
        "r.on('end',()=>{if(r.statusCode!==200)process.exit(1);process.stdout.write(b)})})"
        ".on('error',()=>process.exit(1))",
    ], check=False)
    try:
        mcp_readiness = _validate_mcp_readiness(
            json.loads(mcp_ready.stdout), require_oauth=True,
        )
    except (json.JSONDecodeError, RuntimeError):
        mcp_readiness = None
    checks["mcp_ready"] = mcp_ready.returncode == 0 and mcp_readiness is not None
    checks["mcp_unauthenticated_boundary"] = runner.run([
        "docker", "exec", names["containers"]["mcp"], "node", "-e",
        "require('http').get({host:'127.0.0.1',port:3000,path:'/mcp',"
        "headers:{host:'odoo-mcp'}},r=>process.exit(r.statusCode===401?0:1))"
        ".on('error',()=>process.exit(1))",
    ], check=False).returncode == 0
    active_vault = runner.run([
        "docker", "exec", names["containers"]["mcp"], "node", "-e",
        "const D=require('better-sqlite3');const d=new D('/data/oauth.sqlite',{readonly:true});"
        "const n=d.prepare('SELECT count(*) n FROM odoo_enrollment WHERE grant_expires_at > ?')"
        ".get(Math.floor(Date.now()/1000)).n;process.stdout.write(String(n))",
    ], check=False)
    prepared_vault = runner.run([
        "docker", "exec", names["containers"]["mcp"], "node", "dist/auth/cli.js", "prepare",
    ], check=False)
    match = re.fullmatch(
        r"OAuth schema is current; access snapshots: ([0-9]+) refreshed, "
        r"([0-9]+) cached, ([0-9]+) unavailable\.\n?",
        prepared_vault.stdout,
    )
    try:
        active_count = int(active_vault.stdout.strip())
        refreshed, cached, unavailable = (int(item) for item in match.groups()) if match else (-1, -1, -1)
    except ValueError:
        active_count, refreshed, cached, unavailable = -1, -1, -1, -1
    checks["mcp_vault_decrypt"] = (
        active_vault.returncode == 0 and prepared_vault.returncode == 0
        and active_count >= 0 and unavailable == 0 and refreshed + cached == active_count
    )
    checks["step_ca"] = runner.run([
        "docker", "exec", names["containers"]["sign_ca"], "step", "ca", "health",
        "--ca-url", "https://localhost:9000", "--root", "/home/step/certs/root_ca.crt",
    ], check=False).returncode == 0
    renderer = runner.run([
        "docker", "exec", names["containers"]["odoo"], "python", "-c",
        "import requests;u='https://usl-document-renderer:8443/health';"
        "r=requests.get(u,cert=('/run/secrets/document-renderer/odoo.crt','/run/secrets/document-renderer/odoo.key'),"
        "verify='/run/secrets/document-renderer/ca.crt',timeout=15);r.raise_for_status();"
        "v=r.json();assert v.get('status')=='ok' and v.get('template_revision')==__import__('sys').argv[1]",
        release["renderer"]["commit"],
    ], check=False)
    checks["renderer_mtls_health"] = renderer.returncode == 0
    if not all(checks.values()):
        raise RuntimeError(
            "recovery proof runtime health failed: "
            + ", ".join(name for name, passed in checks.items() if not passed),
        )
    sign_transaction = runner.run(
        ["docker", "exec", "--interactive", names["containers"]["odoo"], "python", "-"],
        check=False,
        input_text=(ROOT / "scripts/sign-services-smoke.py").read_text(encoding="utf-8"),
    )
    checks["sign_cryptographic_transaction"] = (
        sign_transaction.returncode == 0
        and "alteration checks passed" in sign_transaction.stdout
    )
    if not all(checks.values()):
        raise RuntimeError(
            "recovery proof runtime health failed: "
            + ", ".join(name for name, passed in checks.items() if not passed),
        )
    health = {"checks": checks, "checked_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"), "status": "passed"}
    smoke = {
        "http": {
            "odoo": "passed", "paperless": "passed",
            "gotenberg": "passed", "tika": "passed",
        },
        "oauth": {
            "readiness": "passed", "unauthenticated_boundary": "passed",
            "schema_version": mcp_readiness["oauth"]["schema_version"],
            "active_enrollments": active_count, "refreshed": refreshed, "cached": cached,
            "unavailable": unavailable,
            "vault_readability": "empty" if active_count == 0 else "decrypted",
        },
        "signing": {
            "step_ca": "passed", "cryptographic_transaction": "passed",
            "renderer_mtls": "passed",
        },
        "status": "passed",
    }
    return health, smoke


def _recovery_proof_runtime_health(
    target,
    runner,
    names: dict,
    release: dict,
    *,
    started: float,
    deadline_at: str,
) -> tuple[dict, dict]:
    last_error: RuntimeError | None = None
    for attempt in range(60):
        _require_recovery_proof_deadline(started, deadline_at)
        try:
            return _recovery_proof_runtime_health_once(target, runner, names, release)
        except RuntimeError as error:
            last_error = error
            if attempt < 59:
                time.sleep(5)
    raise RuntimeError("recovery proof runtime did not become ready") from last_error


def _recovery_proof_isolation(runner, names: dict) -> str:
    network = runner.run([
        "docker", "network", "inspect", names["network"],
        "--format", "{{json .Internal}}",
    ])
    try:
        internal = json.loads(network.stdout)
    except json.JSONDecodeError as error:
        raise RuntimeError("recovery proof network isolation evidence is invalid") from error
    perimeter: dict[str, dict] = {}
    for role, name in names["containers"].items():
        if role == "materializer":
            if _docker_labels(runner, "container", name) is not None:
                raise RuntimeError("recovery proof materializer survived into runtime phase")
            continue
        result = runner.run([
            "docker", "inspect", name, "--format",
            "{{json .HostConfig.PortBindings}}|{{json .NetworkSettings.Networks}}",
        ])
        try:
            raw_ports, raw_networks = result.stdout.strip().split("|", 1)
            ports = json.loads(raw_ports)
            networks = json.loads(raw_networks)
        except (ValueError, json.JSONDecodeError) as error:
            raise RuntimeError("recovery proof container isolation evidence is invalid") from error
        if ports not in (None, {}) or not isinstance(networks, dict) or set(networks) != {names["network"]}:
            raise RuntimeError(f"recovery proof container escaped its private perimeter: {role}")
        perimeter[role] = {"networks": sorted(networks), "ports": {}}
    if internal is not True:
        raise RuntimeError("recovery proof network is not Docker-internal")
    return hashlib.sha256(json.dumps(
        {"internal": internal, "containers": perimeter},
        sort_keys=True, separators=(",", ":"),
    ).encode()).hexdigest()


def _recovery_proof_durable_state(
    target, runner, names: dict, release: dict, backup: dict, proof_root: str,
    images: dict[str, str],
) -> tuple[dict, dict]:
    odoo = _recovery_proof_query(
        target, runner, names["containers"]["odoo_db"], "odoo",
        """
        SELECT json_build_object(
            'records', (SELECT count(*) FROM ir_attachment WHERE store_fname IS NOT NULL),
            'sample', COALESCE((SELECT min(store_fname) FROM ir_attachment WHERE store_fname IS NOT NULL), '')
        )::text
        """,
    )
    paperless = _recovery_proof_query(
        target, runner, names["containers"]["paperless_db"], "paperless",
        "SELECT json_build_object('documents', (SELECT count(*) FROM documents_document))::text",
    )
    sample = str(odoo.get("sample", ""))
    if sample and not re.fullmatch(r"[0-9a-f]{2}/[0-9a-f]{38}", sample):
        raise RuntimeError("recovery proof Odoo filestore sample path is unsafe")
    file_checks = {
        "odoo_filestore": (
            f"/sample/filestore/{target.value['databases']['odoo']['name']}/{sample}"
            if sample else "/sample/filestore"
        ),
        "paperless_media": "/sample/documents/originals",
        "paperless_archive": "/sample/documents/archive",
        "paperless_thumbnails": "/sample/documents/thumbnails",
        "paperless_tantivy": "/sample/index",
        "paperless_vectors": "/sample/llm_index",
    }
    volume_for = {
        "odoo_filestore": names["volumes"]["odoo_filestore"],
        "paperless_media": names["volumes"]["paperless_media"],
        "paperless_archive": names["volumes"]["paperless_media"],
        "paperless_thumbnails": names["volumes"]["paperless_media"],
        "paperless_tantivy": names["volumes"]["paperless_data"],
        "paperless_vectors": names["volumes"]["paperless_data"],
    }
    samples: dict[str, dict] = {}
    for role, path in file_checks.items():
        result = runner.run([
            "docker", "run", "--rm", "--network", "none",
            "--volume", f"{volume_for[role]}:/sample:ro", "--entrypoint", "/bin/sh",
            release["components"]["paperless"]["digest_reference"], "-ec",
            'p="$1"; [ -e "$p" ]; n=$(find "$p" -type f 2>/dev/null | sort | head -n 1); '
            '[ -n "$n" ]; c=$(find "$p" -type f 2>/dev/null | wc -l | tr -d " "); '
            'printf "%s:" "$c"; sha256sum "$n" | cut -d" " -f1', "proof", path,
        ], check=False)
        if result.returncode:
            raise RuntimeError(f"recovery proof durable sample is missing: {role}")
        try:
            count_text, digest = result.stdout.strip().split(":", 1)
            count = int(count_text)
        except (ValueError, TypeError) as error:
            raise RuntimeError(f"recovery proof durable sample count is invalid: {role}") from error
        if count < 1:
            raise RuntimeError(f"recovery proof durable sample is empty: {role}")
        if not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise RuntimeError(f"recovery proof durable sample digest is invalid: {role}")
        samples[role] = {"file_count": count, "sample_content_sha256": digest, "status": "passed"}
    oauth = runner.run([
        "docker", "run", "--rm", "--network", "none",
        "--volume", f"{names['volumes']['mcp_oauth']}:/data:ro",
        _independent_mcp_image(target, images),
        "node", "-e",
        "const D=require('better-sqlite3');const d=new D('/data/oauth.sqlite',{readonly:true});"
        "const ok=d.pragma('integrity_check',{simple:true});const v=d.pragma('schema_version',{simple:true});"
        "if(ok!=='ok'||!Number.isInteger(v)||v<1)process.exit(1);process.stdout.write(String(v))",
    ], check=False)
    if oauth.returncode or not oauth.stdout.strip().isdigit():
        raise RuntimeError("recovery proof MCP OAuth database integrity failed")
    try:
        oauth_identity = backup["capture"]["resources"]["mcp_oauth"]["identity"]["sha256"]
        backup["capture"]["resources"]["mcp_secrets"]["identity"]["sha256"]
    except (KeyError, TypeError) as error:
        raise RuntimeError("recovery proof MCP OAuth key binding is incomplete") from error
    generation = f"gproof-{hashlib.sha256(proof_root.rstrip('/').rsplit('/', 1)[-1].encode()).hexdigest()[:16]}"
    key_path = f"{proof_root}/generations/{generation}/mcp-secrets/credential-encryption-key.secret"
    key_identity = runner.run(["sha256sum", key_path]).stdout.split()[0]
    if not re.fullmatch(r"[0-9a-f]{64}", key_identity):
        raise RuntimeError("recovery proof MCP recovered key digest is invalid")
    vault_binding = hashlib.sha256(f"{oauth_identity}:{key_identity}".encode()).hexdigest()
    durable = {
        "odoo": {"attachment_records": int(odoo.get("records", -1)), "status": "passed"},
        "paperless": {"document_records": int(paperless.get("documents", -1)), "status": "passed"},
        "mcp_oauth": {
            "schema_version": int(oauth.stdout.strip()),
            "vault_identity_sha256": oauth_identity,
            "recovered_key_material_sha256": key_identity,
            "vault_key_binding_sha256": vault_binding,
            "migration": "passed", "readability": "passed", "status": "passed",
        },
        "samples": samples,
        "checked_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "status": "passed",
    }
    cache = {
        role: {
            **samples[role],
            "capture_identity_sha256": hashlib.sha256(json.dumps(
                target.value["volumes"][
                    "paperless_media" if role in {"paperless_archive", "paperless_thumbnails"}
                    else "paperless_data"
                ], sort_keys=True, separators=(",", ":"),
            ).encode()).hexdigest(),
        }
        for role in ("paperless_archive", "paperless_thumbnails", "paperless_tantivy", "paperless_vectors")
    }
    cache["status"] = "reused"
    return durable, cache


def _require_recovery_proof_deadline(started: float, deadline_at: str | None = None) -> float:
    elapsed = time.monotonic() - started
    if elapsed >= RECOVERY_PROOF_MAX_SECONDS or deadline_at is not None and (
        datetime.now(UTC) >= _recovery_proof_timestamp(deadline_at, "deadline_at")
    ):
        raise RuntimeError("recovery proof exceeded its 1800-second hard deadline")
    return round(elapsed, 3)


def _write_recovery_proof_failure(
    target,
    runner,
    proof_root: str,
    proof_id: str,
    stage: str,
    error: BaseException,
    cleanup: dict,
    runtime_sha: str,
    started_at: str,
    started_monotonic: float,
) -> dict:
    return _write_recovery_proof_evidence(
        target, runner, f"{proof_root}/failure.json",
        {
            "schema": "usl-disposable-recovery-proof-failure/v2",
            "proof_id": proof_id, "source": "production", "stage": stage,
            "error_type": type(error).__name__,
            "error_sha256": hashlib.sha256(str(error).encode()).hexdigest(),
            "cleanup": cleanup, "runtime_sha256": runtime_sha,
            "started_at": started_at,
            "failed_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            "duration_seconds": round(time.monotonic() - started_monotonic, 3),
            "status": "failed",
        },
    )


def _write_recovery_proof_evidence(target, runner, path: str, value: dict, mode: str = "0600") -> dict:
    document = _digested_document(value)
    content = json.dumps(document, indent=2, sort_keys=True) + "\n"
    _write_remote(target, runner, path, content, mode)
    persisted = runner.run(["cat", path]).stdout
    if persisted != content:
        raise RuntimeError("recovery proof durable evidence read-back differs")
    return document


def _read_recovery_proof_evidence(target, runner, path: str) -> dict | None:
    result = runner.run(["cat", path], check=False)
    if result.returncode:
        return None
    try:
        value = json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise RuntimeError("recovery proof evidence is invalid JSON") from error
    if not isinstance(value, dict):
        raise RuntimeError("recovery proof evidence is not an object")
    return value


def _run_recovery_proof_backup(arguments, release: dict) -> dict:
    output = io.StringIO()
    backup_arguments = argparse.Namespace(
        target=arguments.target,
        targets=arguments.targets,
        action="create",
        release=arguments.release,
        run_id=None if arguments.resume_backup else f"proof-{arguments.proof_id}",
        resume=f"proof-{arguments.proof_id}" if arguments.resume_backup else None,
        leave_quiesced=False,
        snapshot=None,
        json=True,
    )
    with redirect_stdout(output):
        status = backup_command(backup_arguments)
    if status:
        raise RuntimeError("recovery proof backup did not complete")
    try:
        receipt = json.loads(output.getvalue())
    except json.JSONDecodeError as error:
        raise RuntimeError("recovery proof backup receipt is invalid") from error
    receipt = _backup_run_receipt(
        receipt,
        target="production",
        run_id=f"proof-{arguments.proof_id}",
    )
    capture_release = receipt["capture"]["release"]
    if (
        capture_release.get("identity") != release["identity"]
        or capture_release.get("manifest_sha256")
        != hashlib.sha256(
            json.dumps(release, sort_keys=True, separators=(",", ":")).encode(),
        ).hexdigest()
    ):
        raise RuntimeError("recovery proof backup differs from the active release")
    return receipt


def _recover_recovery_proof_backup(target, runner, run_id: str, runtime_sha: str) -> bool:
    root = f"{target.value['state_directory']}/backup-runs/{run_id}"
    quiescence = None
    for filename in ("quiesced.json", "prepared.json"):
        result = runner.run(["cat", f"{root}/{filename}"], check=False)
        if not result.returncode:
            try:
                quiescence = json.loads(result.stdout)
            except json.JSONDecodeError as error:
                raise RuntimeError("interrupted recovery proof quiescence receipt is invalid") from error
            break
    if quiescence is None:
        raise RuntimeError(
            "interrupted recovery proof backup has no quiescence receipt",
        )
    services = [target.value["services"][name] for name in BACKUP_WRITER_SERVICE_ROLES]
    quiescence = _validate_backup_quiescence_receipt(
        quiescence,
        target="production",
        run_id=run_id,
        services=services,
    )
    if (
        quiescence["status"] not in {"prepared", "quiesced"}
        or quiescence["baseline_runtime_sha256"] != runtime_sha
    ):
        raise RuntimeError("interrupted recovery proof backup identity differs")
    _recover_interrupted_backup_lock(
        target,
        runner,
        run_id=run_id,
        quiescence=quiescence,
    )
    identity = compose_identity(target, runner)
    runner.run(compose_command(
        identity,
        ["up", "--detach", "--wait", "--no-recreate", *services],
    ))
    if _runtime_cas_sha256(target, runner, inspect_runtime(target, runner)) != runtime_sha:
        raise RuntimeError("recovery proof writer resumption changed the production baseline")
    if quiescence["status"] == "prepared":
        return False
    captured_result = runner.run(["cat", f"{root}/capture.json"], check=False)
    if captured_result.returncode:
        return False
    try:
        captured = json.loads(captured_result.stdout)
        validate_cohort_manifest(captured)
    except (json.JSONDecodeError, ValueError, KeyError) as error:
        raise RuntimeError("interrupted recovery proof captured cohort is invalid") from error
    if captured.get("run_id") != run_id or captured.get("target") != "production":
        raise RuntimeError("interrupted recovery proof capture identity differs")
    return True


def recovery_proof_command(arguments: argparse.Namespace) -> int:
    command_started_monotonic = time.monotonic()
    target = load_target(arguments.target, arguments.targets)
    _recovery_proof_root(target, arguments.evidence_directory, arguments.proof_id)
    runner = target.runner()
    lock_root = str(arguments.evidence_directory / ".locks")
    _prepare_recovery_proof_root(runner, lock_root)
    lock = f"{lock_root}/{arguments.proof_id}.lock"
    with runner.advisory_lock(lock):
        return _recovery_proof_command_locked(arguments, command_started_monotonic)


def _recovery_proof_command_locked(
    arguments: argparse.Namespace,
    started_monotonic: float | None = None,
) -> int:
    started_monotonic = started_monotonic or time.monotonic()
    target = load_target(arguments.target, arguments.targets)
    if target.name != "production" or target.value["environment"] != "production":
        raise RuntimeError("daily recovery proof is production-source only")
    proof_id = arguments.proof_id
    proof_root = _recovery_proof_root(target, arguments.evidence_directory, proof_id)
    runner = target.runner()
    runner.deadline_monotonic = (
        started_monotonic + RECOVERY_PROOF_MAX_SECONDS - RECOVERY_PROOF_CLEANUP_RESERVE_SECONDS
    )
    _prepare_recovery_proof_root(runner, proof_root)
    receipt_path = f"{proof_root}/receipt.json"
    state_path = f"{proof_root}/state.json"
    existing_receipt = _read_recovery_proof_evidence(target, runner, receipt_path)
    if existing_receipt is not None:
        receipt = _validate_recovery_proof_receipt(existing_receipt, proof_id)
        print(json.dumps(receipt, indent=None if arguments.json else 2, sort_keys=True))
        return 0

    protected_secrets_sha = _recovery_proof_protected_secrets_sha256(target, runner)
    current = inspect_runtime(target, runner)
    release, release_sha, _release_raw = _release(target, runner, arguments.release)
    observed_runtime_sha = _runtime_cas_sha256(target, runner, current)
    runtime_sha = observed_runtime_sha
    existing_state = _read_recovery_proof_evidence(target, runner, state_path)
    backup = None
    started_at = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    deadline_at = (
        datetime.now(UTC) + timedelta(seconds=RECOVERY_PROOF_MAX_SECONDS)
    ).isoformat().replace("+00:00", "Z")
    if existing_state is not None:
        state = _validate_recovery_proof_state(existing_state, proof_id)
        backup_interrupted = state.get("phase") == "backup-started" and state.get("backup") is None
        if (
            state.get("source") != "production"
            or state.get("release_identity") != release["identity"]
            or state.get("release_manifest_sha256") != release_sha
            or not backup_interrupted and state.get("runtime_sha256") != observed_runtime_sha
        ):
            raise RuntimeError("recovery proof retry baseline differs")
        runtime_sha = state["runtime_sha256"]
        backup = state.get("backup")
        arguments.resume_backup = backup_interrupted
        started_at = state["started_at"]
        deadline_at = state["deadline_at"]
        remaining = (_recovery_proof_timestamp(deadline_at, "deadline_at") - datetime.now(UTC)).total_seconds()
        runner.deadline_monotonic = time.monotonic() + max(
            0.0, remaining - RECOVERY_PROOF_CLEANUP_RESERVE_SECONDS,
        )
        if datetime.now(UTC) >= _recovery_proof_timestamp(deadline_at, "deadline_at"):
            error = RuntimeError("recovery proof retry exceeded its 1800-second hard deadline")
            recovery_runner = target.runner()
            recovery_runner.deadline_monotonic = (
                time.monotonic() + RECOVERY_PROOF_CLEANUP_RESERVE_SECONDS
            )
            cleanup = _cleanup_recovery_proof_resources(
                target, recovery_runner, proof_id, proof_root,
            )
            _write_recovery_proof_failure(
                target, recovery_runner, proof_root, proof_id, "cleanup", error, cleanup,
                runtime_sha, started_at, started_monotonic,
            )
            raise error
    else:
        arguments.resume_backup = False

    try:
        cleanup = _cleanup_recovery_proof_resources(target, runner, proof_id, proof_root)
    except Exception as error:
        recovery_runner = target.runner()
        recovery_runner.deadline_monotonic = time.monotonic() + RECOVERY_PROOF_CLEANUP_RESERVE_SECONDS
        cleanup = {
            "schema": "usl-recovery-proof-cleanup/v1", "status": "failed",
            "error_sha256": hashlib.sha256(str(error).encode()).hexdigest(),
        }
        _write_recovery_proof_failure(
            target, recovery_runner, proof_root, proof_id, "cleanup", error, cleanup,
            runtime_sha, started_at, started_monotonic,
        )
        raise
    def write_state(phase: str, backup_receipt: dict | None) -> dict:
        elapsed = _require_recovery_proof_deadline(started_monotonic, deadline_at)
        return _write_recovery_proof_evidence(
            target,
            runner,
            state_path,
            {
                "schema": "usl-disposable-recovery-proof-state/v2",
                "proof_id": proof_id,
                "source": "production",
                "phase": phase,
                "release_identity": release["identity"],
                "release_manifest_sha256": release_sha,
                "runtime_sha256": runtime_sha,
                "backup": backup_receipt,
                "started_at": started_at,
                "deadline_at": deadline_at,
                "updated_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
                "duration_seconds": elapsed,
            },
        )

    stage = "initialized"
    names = None
    materialized = None
    control_validation = None
    runtime = None
    runtime_environment = None
    health = None
    smoke = None
    durable_state = None
    cache_roles = None
    perimeter_sha = None
    failure_armed = False
    try:
        if backup is None:
            backup_run_id = f"proof-{proof_id}"
            persisted_backup = None
            if arguments.resume_backup:
                persisted_backup = _read_recovery_proof_evidence(
                    target,
                    runner,
                    f"{target.value['state_directory']}/backup-runs/"
                    f"{backup_run_id}/receipt.json",
                )
            if persisted_backup is not None:
                backup = _backup_run_receipt(
                    persisted_backup,
                    target="production",
                    run_id=backup_run_id,
                )
            else:
                if arguments.resume_backup:
                    arguments.resume_backup = _recover_recovery_proof_backup(
                        target, runner, backup_run_id, runtime_sha,
                    )
                write_state("backup-started", None)
                backup = _run_recovery_proof_backup(arguments, release)
        else:
            backup = _backup_run_receipt(
                backup,
                target="production",
                run_id=f"proof-{proof_id}",
            )
        stage = "backup-qualified"
        write_state(stage, backup)
        if arguments.failure_after == stage:
            raise RuntimeError(f"injected recovery proof failure after {stage}")

        current_after_backup = inspect_runtime(target, runner)
        if _runtime_cas_sha256(target, runner, current_after_backup) != runtime_sha:
            raise RuntimeError("production runtime changed while taking recovery proof backup")
        identity = current_after_backup["compose"]
        identity = _with_mcp_runtime_authority(
            target, runner, identity, _mcp_runtime_authority(target),
        )
        rendered = _recovery_proof_runtime_config(runner, identity)
        images = {name: value["image"] for name, value in rendered["services"].items()}
        tool_image = _operations_image(release)
        capacity = _recovery_proof_capacity(
            target, runner, tool_image, current_after_backup,
        )
        names = _create_recovery_proof_resources(target, runner, proof_id)
        generation = f"gproof-{hashlib.sha256(proof_id.encode()).hexdigest()[:16]}"
        generation_root = f"{proof_root}/generations/{generation}"
        runner.run(["install", "-d", "-m", "0700", f"{generation_root}/work"])
        env, runtime_environment = _recovery_proof_environment(
            target, runner, proof_root, rendered,
            backup["capture"]["resources"]["mcp_secrets"]["identity"]["sha256"],
            backup["capture"]["resources"]["paperless_personal_ai_keys"]["identity"]["sha256"],
        )
        proof_value = copy.deepcopy(target.value)
        proof_value["state_directory"] = proof_root
        proof_target = Target(target.path, proof_value)
        _start_recovery_proof_database(
            target, runner, proof_id, names, images, "odoo", env["database"],
        )
        _start_recovery_proof_database(
            target, runner, proof_id, names, images, "paperless", env["database"],
        )
        stage = "resources-created"
        write_state(stage, backup)
        if arguments.failure_after == stage:
            raise RuntimeError(f"injected recovery proof failure after {stage}")

        source_backup_env = _write_source_backup_environment(
            target,
            runner,
            proof_target,
            runner,
            f"{generation_root}/work/source-backup.env",
        )
        materialization_network = _create_recovery_proof_materialization_network(
            runner, proof_id, names,
        )
        try:
            materialize_result = runner.run(
                _materialize_command(
                    target,
                    proof_target,
                    tool_image,
                    backup["qualification"]["durable_snapshot_id"],
                    generation,
                    [materialization_network, names["network"]],
                    names["volumes"],
                    source_backup_env,
                    env["database"],
                    (proof_id, names["containers"]["materializer"]),
                ),
            )
        finally:
            runner.run(["rm", "-f", "--", source_backup_env], check=False)
            if _require_recovery_proof_owner(
                runner, "network", materialization_network, proof_id, "materialization-egress",
            ):
                removed = runner.run(
                    ["docker", "network", "rm", materialization_network], check=False,
                )
                if removed.returncode:
                    raise RuntimeError("recovery proof materialization network cleanup failed")
        try:
            materialized = json.loads(materialize_result.stdout.splitlines()[-1])
        except (IndexError, json.JSONDecodeError) as error:
            raise RuntimeError("recovery proof materialization evidence is invalid") from error
        _validate_materialized_release(
            materialized,
            release,
            release_sha,
            require_sign_secrets=True,
        )
        if (
            materialized.get("durable_snapshot_id")
            != backup["qualification"]["durable_snapshot_id"]
            or materialized.get("cache_snapshot_id")
            != backup["upload"]["cache_snapshot_id"]
            or materialized.get("run_id") != backup["run_id"]
            or materialized.get("release", {}).get("identity") != release["identity"]
            or materialized.get("release", {}).get("manifest_sha256") != release_sha
            or materialized.get("transformations") != []
            or materialized.get("mcp_secrets_restored") is not True
            or materialized.get("renderer_secrets_restored") is not True
            or materialized.get("paperless_personal_ai_keys_restored") is not True
        ):
            raise RuntimeError("recovery proof is not an exact same-release materialization")
        for role in ("better-auth", "credential-encryption-key", "personal-ai"):
            digest = runner.run(["sha256sum", env[role]]).stdout.split()[0]
            if not re.fullmatch(r"[0-9a-f]{64}", digest):
                raise RuntimeError(f"recovery proof restored MCP secret digest is invalid: {role}")
            runtime_environment["environment_sha256"][role] = digest
        restored_dss = runner.run([
            "sha256sum", f"{generation_root}/sign-secrets/dss.env",
        ]).stdout.split()[0]
        restored_odoo = runner.run([
            "sha256sum", f"{generation_root}/sign-secrets/odoo.env",
        ]).stdout.split()[0]
        if not all(re.fullmatch(r"[0-9a-f]{64}", item) for item in (restored_dss, restored_odoo)):
            raise RuntimeError("recovery proof restored Sign environment digest is invalid")
        runtime_environment["environment_sha256"]["dss"] = restored_dss
        runtime_environment["environment_sha256"]["odoo"] = hashlib.sha256(
            f"{runtime_environment['environment_sha256']['odoo']}:{restored_odoo}".encode(),
        ).hexdigest()
        stage = "materialized"
        write_state(stage, backup)
        if arguments.failure_after == stage:
            raise RuntimeError(f"injected recovery proof failure after {stage}")

        quarantine = _run_production_boundary_script(
            target, runner, release, names["network"], names["volumes"],
            "production_quarantine.py", release["identity"], "USL_PRODUCTION_QUARANTINE=",
            environment_file=env["database"],
        )
        runtime = _start_recovery_proof_runtime(
            target, runner, proof_id, proof_root, names, release, images, env,
        )
        runtime["environment"] = runtime_environment
        runtime["quarantine"] = quarantine
        perimeter_sha = _recovery_proof_isolation(runner, names)
        stage = "runtime-started"
        write_state(stage, backup)
        if arguments.failure_after == stage:
            raise RuntimeError(f"injected recovery proof failure after {stage}")

        database_health = {}
        for database_key, container in names["containers"].items():
            if database_key not in {"odoo_db", "paperless_db"}:
                continue
            logical_database_key = database_key.removesuffix("_db")
            database = target.value["databases"][logical_database_key]
            ready = runner.run([
                "docker", "exec", container, "pg_isready", "--username", database["user"],
                "--dbname", database["name"],
            ], check=False)
            database_health[logical_database_key] = ready.returncode == 0
        if not all(database_health.values()):
            raise RuntimeError("recovery proof restored database is not ready")
        restored_controls = {
            "odoo": _recovery_proof_query(
                target, runner, names["containers"]["odoo_db"], "odoo", ODOO_CONTROL_SQL,
            ),
            "paperless": _recovery_proof_query(
                target,
                runner,
                names["containers"]["paperless_db"],
                "paperless",
                PAPERLESS_CONTROL_SQL,
            ),
        }
        try:
            control_validation = validate_restore(
                materialized["controls"],
                restored_controls,
                require_unchanged_release=True,
            )
        except ControlManifestError as error:
            raise RuntimeError(str(error)) from error
        cache_role_names = (
            "paperless_archive",
            "paperless_thumbnails",
            "paperless_tantivy",
            "paperless_vectors",
        )
        try:
            captured_cache = {
                role: backup["capture"]["resources"][role]["identity"]
                for role in cache_role_names
            }
        except (KeyError, TypeError) as error:
            raise RuntimeError("recovery proof reusable cache evidence is incomplete") from error
        if not all(
            backup["capture"]["resources"][role].get("class") == "cache"
            for role in cache_role_names
        ):
            raise RuntimeError("recovery proof reusable cache classification differs")
        health, smoke = _recovery_proof_runtime_health(
            target, runner, names, release,
            started=started_monotonic, deadline_at=deadline_at,
        )
        health["databases"] = database_health
        durable_state, cache_roles = _recovery_proof_durable_state(
            target, runner, names, release, backup, proof_root, images,
        )
        for role, identity in captured_cache.items():
            cache_roles[role]["capture_identity_sha256"] = hashlib.sha256(json.dumps(
                identity, sort_keys=True, separators=(",", ":"),
            ).encode()).hexdigest()
        stage = "validated"
        write_state(stage, backup)
        if arguments.failure_after == stage:
            raise RuntimeError(f"injected recovery proof failure after {stage}")
    except BaseException as error:
        recovery_runner = target.runner()
        recovery_runner.deadline_monotonic = time.monotonic() + max(
            0.0,
            (_recovery_proof_timestamp(deadline_at, "deadline_at") - datetime.now(UTC)).total_seconds(),
        )
        try:
            cleanup = _cleanup_recovery_proof_resources(
                target, recovery_runner, proof_id, proof_root,
            )
        except Exception as cleanup_error:
            cleanup = {
                "schema": "usl-recovery-proof-cleanup/v1", "status": "failed",
                "error_sha256": hashlib.sha256(str(cleanup_error).encode()).hexdigest(),
            }
        try:
            _write_recovery_proof_failure(
                target, recovery_runner, proof_root, proof_id, stage, error, cleanup,
                runtime_sha, started_at, started_monotonic,
            )
        except Exception:
            if not failure_armed:
                raise
        raise

    stage = "finalizing"
    runner.deadline_monotonic = time.monotonic() + max(
        0.0,
        (_recovery_proof_timestamp(deadline_at, "deadline_at") - datetime.now(UTC)).total_seconds(),
    )
    final_failure_runner = target.runner()
    final_failure_runner.deadline_monotonic = time.monotonic() + RECOVERY_PROOF_CLEANUP_RESERVE_SECONDS
    try:
        _write_recovery_proof_evidence(
            target, runner, f"{proof_root}/failure.json",
            {
                "schema": "usl-disposable-recovery-proof-failure/v2",
                "proof_id": proof_id, "source": "production", "stage": stage,
                "error_type": "FinalizationPending",
                "error_sha256": hashlib.sha256(b"finalization pending").hexdigest(),
                "cleanup": cleanup, "runtime_sha256": runtime_sha,
                "started_at": started_at,
                "failed_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
                "duration_seconds": _require_recovery_proof_deadline(started_monotonic, deadline_at),
                "status": "armed",
            },
        )
        failure_armed = True
        write_state(stage, backup)
    except Exception as error:
        _write_recovery_proof_failure(
            target, final_failure_runner, proof_root, proof_id, "final-write", error, cleanup,
            runtime_sha, started_at, started_monotonic,
        )
        raise
    try:
        cleanup = _cleanup_recovery_proof_resources(target, runner, proof_id, proof_root)
    except Exception as error:
        cleanup = {
            "schema": "usl-recovery-proof-cleanup/v1", "status": "failed",
            "error_sha256": hashlib.sha256(str(error).encode()).hexdigest(),
        }
        _write_recovery_proof_failure(
            target, final_failure_runner, proof_root, proof_id, "cleanup", error, cleanup,
            runtime_sha, started_at, started_monotonic,
        )
        raise
    if arguments.failure_after == "cleanup":
        error = RuntimeError("injected recovery proof failure after cleanup")
        _write_recovery_proof_failure(
            target, final_failure_runner, proof_root, proof_id, "cleanup", error, cleanup,
            runtime_sha, started_at, started_monotonic,
        )
        raise error
    try:
        final_runtime = inspect_runtime(target, runner)
        if _runtime_cas_sha256(target, runner, final_runtime) != runtime_sha:
            raise RuntimeError("production runtime changed during disposable recovery proof")
        if _recovery_proof_protected_secrets_sha256(target, runner) != protected_secrets_sha:
            raise RuntimeError("production protected secrets changed during recovery proof")
    except Exception as error:
        _write_recovery_proof_failure(
            target, final_failure_runner, proof_root, proof_id, "runtime-cas", error, cleanup,
            runtime_sha, started_at, started_monotonic,
        )
        raise
    if arguments.failure_after == "cas-verified":
        error = RuntimeError("injected recovery proof failure after cas-verified")
        _write_recovery_proof_failure(
            target, final_failure_runner, proof_root, proof_id, "cas-verified", error, cleanup,
            runtime_sha, started_at, started_monotonic,
        )
        raise error
    duration_seconds = _require_recovery_proof_deadline(started_monotonic, deadline_at)
    if arguments.failure_after == "final-write":
        error = RuntimeError("injected recovery proof failure at final-write")
        _write_recovery_proof_failure(
            target, final_failure_runner, proof_root, proof_id, "final-write", error, cleanup,
            runtime_sha, started_at, started_monotonic,
        )
        raise error
    try:
        receipt = _write_recovery_proof_evidence(
        target,
        runner,
        receipt_path,
        {
            "schema": "usl-disposable-recovery-proof/v2",
            "proof_id": proof_id,
            "source": "production",
            "release": {
                "identity": release["identity"],
                "manifest_sha256": release_sha,
            },
            "backup": {
                "run_id": backup["run_id"],
                "receipt_sha256": backup["sha256"],
                "durable_snapshot_id": backup["qualification"]["durable_snapshot_id"],
                "cache_snapshot_id": backup["upload"]["cache_snapshot_id"],
            },
            "materialization": {
                "cohort_schema": materialized["cohort_schema"],
                "capacity": capacity,
                "controls_sha256": hashlib.sha256(json.dumps(
                    materialized["controls"], sort_keys=True, separators=(",", ":"),
                ).encode()).hexdigest(),
                "sign_secrets_restored": materialized["sign_secrets_restored"],
                "mcp_secrets_restored": materialized["mcp_secrets_restored"],
                "renderer_secrets_restored": materialized["renderer_secrets_restored"],
                "paperless_personal_ai_keys_restored": materialized["paperless_personal_ai_keys_restored"],
                "status": materialized["status"],
            },
            "runtime": runtime,
            "health": health,
            "smoke": {"runtime": smoke, "controls": control_validation, "status": "passed"},
            "durable_state": durable_state,
            "reusable_cache": cache_roles,
            "ownership": {
                "label": RECOVERY_PROOF_OWNER,
                "resource_names_sha256": hashlib.sha256(json.dumps(
                    names, sort_keys=True, separators=(",", ":"),
                ).encode()).hexdigest(),
            },
            "cleanup": cleanup,
            "isolation": {
                "active_runtime_sha256": runtime_sha,
                "active_runtime_unchanged": True,
                "gateway_attached": False,
                "host_ports_published": False,
                "external_networks_attached": False,
                "side_effects_neutralized": True,
                "persistent_staging_touched": False,
                "production_secrets_modified": False,
                "production_secrets_sha256": protected_secrets_sha,
                "runtime_ledger_used_for_restore": False,
                "perimeter_sha256": perimeter_sha,
            },
            "started_at": started_at,
            "completed_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            "duration_seconds": duration_seconds,
            "max_duration_seconds": RECOVERY_PROOF_MAX_SECONDS,
            "status": "passed",
        },
            "0444",
        )
        persisted = _validate_recovery_proof_receipt(
            _read_recovery_proof_evidence(target, runner, receipt_path), proof_id,
        )
        if persisted != receipt:
            raise RuntimeError("recovery proof final receipt read-back differs")
    except Exception as error:
        _write_recovery_proof_failure(
            target, final_failure_runner, proof_root, proof_id, "final-write", error, cleanup,
            runtime_sha, started_at, started_monotonic,
        )
        raise
    runner.run(["rm", "-f", "--", f"{proof_root}/failure.json"])
    print(json.dumps(receipt, indent=None if arguments.json else 2, sort_keys=True))
    return 0


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
    # Compose may reuse sidecars across rollback; their config labels still
    # reference candidate files even when the database has returned to baseline.
    prefix = f"{target.value['state_directory']}/generations/"
    suffix = "/compose.generation.json"
    for container in current.get("containers", []):
        if container.get("State") != "running" or not container.get("ID"):
            continue
        labels = json.loads(runner.run([
            "docker", "inspect", container["ID"], "--format", "{{json .Config.Labels}}",
        ]).stdout)
        for path in labels.get("com.docker.compose.project.config_files", "").split(","):
            if path.startswith(prefix) and path.endswith(suffix):
                generation = path.removeprefix(prefix).removesuffix(suffix)
                if not GENERATION_NAME.fullmatch(generation):
                    raise RuntimeError("running service generation label is invalid")
                protected_generations.add(generation)
                active.update(generation_volume_names(target, generation).values())
                protected_networks.add(f"{target.project}-{generation}-recovery")
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
            return {
                "name": name,
                "generation": generation,
                "database_path": database_path,
            }
        if options not in (None, {}) or mountpoint != managed_mountpoint or storage_label is not None:
            raise RuntimeError(f"cleanup legacy database volume differs: {name}")
        return {"name": name, "generation": generation, "database_path": None}
    if storage_label not in (None, tier):
        raise RuntimeError(f"cleanup volume labels differ: {name}")
    if options not in (None, {}) or mountpoint != managed_mountpoint:
        raise RuntimeError(f"cleanup managed volume differs: {name}")
    return {"name": name, "generation": generation, "database_path": None}


def _validated_cleanup_network(target, runner, name: str) -> dict[str, str]:
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
    return {"name": name, "generation": generation}


def _validated_cleanup_containers(
    target,
    runner,
    volumes: list[dict],
    networks: list[dict],
    protected_volumes: set[str],
    protected_networks: set[str],
) -> list[str]:
    """Select only Compose containers bound to validated stale generations."""
    candidates = {item["name"]: item["generation"] for item in volumes}
    candidate_networks = {item["name"]: item["generation"] for item in networks}
    candidate_generations = set(candidates.values()) | set(candidate_networks.values())
    attached = runner.run([
        "docker", "ps", "-a", "--no-trunc", "--filter",
        f"label=com.docker.compose.project={target.project}", "--format", "{{.ID}}",
    ])
    identifiers = {line for line in attached.stdout.splitlines() if line}
    validated = []
    for identifier in sorted(identifiers):
        try:
            container = json.loads(runner.run(
                ["docker", "inspect", identifier, "--format", "{{json .}}"],
            ).stdout)
        except json.JSONDecodeError as error:
            raise RuntimeError("cleanup candidate container inspection is invalid") from error
        labels = (container.get("Config") or {}).get("Labels")
        mounts = container.get("Mounts")
        if not isinstance(labels, dict) or not isinstance(mounts, list):
            raise RuntimeError("cleanup candidate container inspection is invalid")
        mounted = {
            item.get("Name") for item in mounts
            if isinstance(item, dict) and item.get("Type") == "volume"
        }
        attached_networks = set((container.get("NetworkSettings") or {}).get("Networks") or {})
        config_files = {
            item for item in labels.get(
                "com.docker.compose.project.config_files", "",
            ).split(",") if item
        }
        prefix = f"{target.value['state_directory']}/generations/"
        suffix = "/compose.generation.json"
        generations = {
            path.removeprefix(prefix).removesuffix(suffix)
            for path in config_files
            if path.startswith(prefix) and path.endswith(suffix)
        }
        selected = generations & candidate_generations
        if not selected:
            continue
        if labels.get("com.docker.compose.service") == "gateway":
            raise RuntimeError("cleanup refuses the stable gateway")
        generation = next(iter(selected)) if len(selected) == 1 else None
        expected_overlay = f"{prefix}{generation}{suffix}"
        if (
            container.get("Id") != identifier
            or labels.get("com.docker.compose.project") != target.project
            or not labels.get("com.docker.compose.service")
            or generations != {generation}
            or expected_overlay not in config_files
            or mounted & protected_volumes
            or attached_networks & protected_networks
        ):
            raise RuntimeError("cleanup candidate container ownership differs")
        validated.append(identifier)
    return validated


def _validated_cleanup_resources(
    target, runner, inventory: dict,
) -> tuple[list[str], list[dict], list[str]]:
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
    containers = _validated_cleanup_containers(
        target,
        runner,
        volumes,
        networks,
        set(inventory["protected_volumes"]),
        set(inventory["protected_networks"]),
    )
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
    return containers, volumes, networks


def _delete_cleanup_resources(
    runner,
    containers: list[str],
    volumes: list[dict],
    networks: list[dict],
    workspaces: list[str],
) -> None:
    for identifier in containers:
        runner.run(["docker", "rm", "--force", identifier])
    for item in volumes:
        runner.run(["docker", "volume", "rm", item["name"]])
        if item["database_path"] is not None:
            runner.run(
                ["find", item["database_path"], "-xdev", "-mindepth", "1", "-delete"],
            )
            runner.run(["rmdir", "--", item["database_path"]])
    for name in networks:
        runner.run(["docker", "network", "rm", name["name"]])
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
                retention_image = _operations_image(release)
                retention_plan = _run_cohort(
                    target, runner, retention_image, "retention-plan", [],
                    volumes=current["volumes"],
                )
            containers, volumes, networks = _validated_cleanup_resources(
                target, runner, inventory,
            )
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
                runner, containers, volumes, networks, inventory["delete_workspaces"],
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
            retention_image = _operations_image(release)
            retention_plan = _run_cohort(
                target, runner, retention_image, "retention-plan", [],
                volumes=current["volumes"],
            )
        plan = _cleanup_plan(target, inventory, retention_plan)
        plan["status"] = "planned"
    print(json.dumps(plan, indent=None if arguments.json else 2, sort_keys=True))
    return 0


def _reconcile_production_pocketid(target, runner, identity) -> dict:
    """Restore environment-owned identity after production neutralization."""
    container = runner.run(compose_command(
        identity, ["ps", "--quiet", target.value["services"]["odoo"]],
    )).stdout.strip()
    if not container or "\n" in container:
        raise RuntimeError("production identity requires one running Odoo container")
    program = r"""
import base64
import hashlib
import json
import os
import secrets

import requests

applied = env["auth.oauth.provider"]._usl_pocketid_apply_environment()
provider = env.ref("usl_pocketid.provider_pocketid").sudo()
if not applied or not provider.enabled:
    raise RuntimeError("Production Pocket ID is not enabled")
if provider.client_id != os.environ["USL_POCKET_ID_CLIENT_ID"]:
    raise RuntimeError("Production Pocket ID client differs from runtime")
if provider.usl_public_base_url.rstrip("/") != os.environ["USL_POCKET_ID_ODOO_BASE_URL"].rstrip("/"):
    raise RuntimeError("Production Pocket ID callback origin differs from runtime")




authorization, client_secret = synthetic_client_probe(
    os.environ['USL_POCKET_ID_CLIENT_ID'], os.environ['USL_POCKET_ID_CLIENT_SECRET'],
    os.environ['USL_POCKET_ID_ODOO_BASE_URL'].rstrip('/') + '/auth_oauth/signin',
    provider.usl_token_auth_method,
)
if not authorization or not client_secret:
    raise RuntimeError('Production Pocket ID client admission failed')

env.cr.commit()
print("USL_PRODUCTION_POCKET_ID=" + json.dumps({
    "client_id": provider.client_id,
    "issuer": provider.usl_oidc_issuer,
    "odoo_base_url": provider.usl_public_base_url,
    "enabled": provider.enabled,
    "authorization_accepted": authorization,
    "client_secret_accepted": client_secret,
    "status": "passed",
}, sort_keys=True))
"""
    program = CLIENT_PROBE_SCRIPT + program
    result = runner.run([
        "docker", "exec", "--interactive", container,
        "odoo", "shell", "--config=/etc/odoo/odoo.conf",
        f"--database={target.value['databases']['odoo']['name']}",
        "--no-http", "--max-cron-threads=0",
    ], input_text=program)
    for line in reversed(result.stdout.splitlines()):
        if line.startswith("USL_PRODUCTION_POCKET_ID="):
            value = json.loads(line.split("=", 1)[1])
            if value.get("status") == "passed" and value.get("enabled") is True:
                return value
    raise RuntimeError("production Pocket ID returned no admission evidence")


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
    if quarantine["schema"].endswith("/v1"):
        raise RuntimeError("legacy v1 quarantine is audit-only and cannot be activated")
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
    network = target.value["compose"]["default_network"]
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
    activation["side_effect_admission"] = _admit_production_side_effects(
        target, runner, release, network, volumes,
    )
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
                "up", "--detach", "--wait", "--force-recreate", "--no-deps",
                *[target.value["services"][role] for role in BACKUP_WRITER_SERVICE_ROLES],
            ],
        ),
    )
    activation["pocket_id_admission"] = _reconcile_production_pocketid(target, runner, identity)
    health = _gate(health_command, target, arguments.targets)
    smoke = _gate(smoke_command, target, arguments.targets)
    receipt = {
        **{key: quarantine[key] for key in (
            "target", "attempt", "release", "snapshot", "generation",
            "control_validation_sha256", "operation_bundle_sha256",
            "runtime_evidence_sha256",
        )},
        "schema": "usl-release-admission/v2",
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
    if arguments.action == "baseline-check":
        runtime = inspect_runtime(target, runner)
        release, _release_sha256, _release_raw = _release(target, runner, None)
        value = _legacy_staging_baseline(target, runner, runtime, release)
        print(json.dumps(value, indent=None if arguments.json else 2, sort_keys=True))
        return 0
    if arguments.action == "resume-staging":
        if target.value["environment"] != "staging" or not (
            arguments.backup_receipt or arguments.quiescence_receipt
        ):
            raise RuntimeError("staging writer resume requires exact quiescence evidence")
        attempt = str(arguments.attempt_id or "")
        services = [target.value["services"][role] for role in BACKUP_WRITER_SERVICE_ROLES]
        try:
            if arguments.quiescence_receipt:
                quiescence = _validate_backup_quiescence_receipt(
                    json.loads(_read_path(target, runner, arguments.quiescence_receipt)),
                    target=target.name, run_id=attempt, services=services,
                )
                backup_sha256 = None
            else:
                backup = _backup_run_receipt(
                    json.loads(_read_path(target, runner, arguments.backup_receipt)),
                    target=target.name,
                    run_id=attempt,
                    require_quiesced=True,
                    expected_writer_services=services,
                )
                quiescence = backup["quiescence"]
                backup_sha256 = backup["sha256"]
        except json.JSONDecodeError as error:
            raise RuntimeError("staging writer resume receipt is invalid") from error
        _recover_interrupted_backup_lock(
            target, runner, run_id=attempt, quiescence=quiescence,
        )
        with runtime_lock(target, runner, "resume-staging", attempt):
            runtime = inspect_runtime(target, runner)
            baseline = quiescence["baseline_runtime_sha256"]
            if _runtime_cas_sha256(target, runner, runtime) != baseline:
                raise RuntimeError("staging writer resume baseline differs")
            services = quiescence["writer_services"]
            runner.run(compose_command(
                runtime["compose"],
                ["up", "--detach", "--wait", "--no-recreate", *services],
            ))
            resumed = inspect_runtime(target, runner)
            if _runtime_cas_sha256(target, runner, resumed) != baseline:
                raise RuntimeError("staging writer resume changed the runtime baseline")
        value = {
            "schema": "usl-staging-writer-resume/v1",
            "target": target.name,
            "attempt": attempt,
            "backup_receipt_sha256": backup_sha256,
            "quiescence_receipt_sha256": quiescence["sha256"],
            "baseline_runtime_sha256": baseline,
            "services": services,
            "resumed_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            "status": "resumed",
        }
        value["sha256"] = hashlib.sha256(json.dumps(
            value, sort_keys=True, separators=(",", ":"),
        ).encode()).hexdigest()
        print(json.dumps(value, indent=None if arguments.json else 2, sort_keys=True))
        return 0
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
    if arguments.action == "staging-reset-intent":
        if target.value["environment"] != "staging":
            raise RuntimeError("staging reset intent is staging-only")
        if not arguments.candidate_release or not arguments.prepare_receipt:
            raise RuntimeError(
                "staging reset intent requires production candidate and prepare receipt",
            )
        try:
            production_release = validate_release(json.loads(
                _read_path(target, runner, arguments.candidate_release),
            ))
        except (json.JSONDecodeError, ReleaseManifestError) as error:
            raise RuntimeError("staging reset intent candidate is invalid") from error
        production_attempt = _release_attempt(
            arguments.attempt_id, production_release["identity"],
        )
        try:
            production_prepare = _prepare_receipt(
                json.loads(_read_path(target, runner, arguments.prepare_receipt)),
                target="production",
                attempt=production_attempt,
                release=production_release["identity"],
            )
        except json.JSONDecodeError as error:
            raise RuntimeError("staging reset intent preparation is invalid") from error
        if production_prepare["gitops_commit"] is None:
            raise RuntimeError("staging reset intent has no immutable GitOps commit")
        staging_runtime = inspect_runtime(target, runner)
        staging_release, _staging_sha, _staging_raw = _release(target, runner, None)
        value = {
            "schema": "usl-staging-reset-intent/v1",
            "staging_target": target.name,
            "staging_baseline_generation": staging_runtime.get("generation"),
            "staging_baseline_release": staging_release["identity"],
            "staging_baseline_runtime_sha256": _runtime_cas_sha256(
                target, runner, staging_runtime,
            ),
            "production_attempt": production_attempt,
            "production_release": production_release["identity"],
            "gitops_commit": production_prepare["gitops_commit"],
            "production_prepare_receipt_sha256": production_prepare["sha256"],
            "production_upgrade_plan_sha256": production_prepare["upgrade_plan_sha256"],
            "created_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            "status": "planned",
        }
        value["sha256"] = hashlib.sha256(json.dumps(
            value, sort_keys=True, separators=(",", ":"),
        ).encode()).hexdigest()
        output = arguments.output or Path(
            f"{target.value['state_directory']}/reset-intents/{production_attempt}.json",
        )
        runner.run([
            "install", "-d", "-m", "0700", str(Path(str(output)).parent),
        ])
        existing = runner.run(["cat", str(output)], check=False)
        if existing.returncode == 0:
            try:
                existing_value = _staging_reset_intent_receipt(
                    json.loads(existing.stdout),
                    target=target,
                    admission={
                        "attempt": production_attempt,
                        "release": production_release["identity"],
                    },
                )
            except json.JSONDecodeError as error:
                raise RuntimeError("existing staging reset intent is invalid") from error
            stable = set(value) - {"created_at", "sha256"}
            if any(existing_value.get(key) != value[key] for key in stable):
                raise RuntimeError("existing staging reset intent differs")
            print(json.dumps(
                {"path": str(output), **existing_value},
                indent=None if arguments.json else 2, sort_keys=True,
            ))
            return 0
        _write_remote(
            target, runner, str(output),
            json.dumps(value, indent=2, sort_keys=True) + "\n", "0444",
        )
        print(json.dumps(
            {"path": str(output), **value},
            indent=None if arguments.json else 2,
            sort_keys=True,
        ))
        return 0
    if arguments.action == "staging-checkpoint":
        if target.value["environment"] != "staging":
            raise RuntimeError("staging checkpoint is staging-only")
        if not all((
            arguments.candidate_release,
            arguments.upgrade_plan,
            arguments.prepare_receipt,
            arguments.maintenance_receipt,
            arguments.backup_receipt,
        )):
            raise RuntimeError(
                "staging checkpoint requires candidate, plan, prepare, maintenance, and backup receipts",
            )
        try:
            release = validate_release(json.loads(
                _read_path(target, runner, arguments.candidate_release),
            ))
            plan = _validated_release_upgrade_plan(
                target,
                json.loads(_read_path(target, runner, arguments.upgrade_plan)),
                release,
            )
        except (json.JSONDecodeError, ReleaseManifestError, ModuleReleaseError, PlanEvidenceError) as error:
            raise RuntimeError("staging checkpoint release evidence is invalid") from error
        attempt = _release_attempt(arguments.attempt_id, release["identity"])
        try:
            prepared = _prepare_receipt(
                json.loads(_read_path(target, runner, arguments.prepare_receipt)),
                target=target.name,
                attempt=attempt,
                release=release["identity"],
            )
            maintenance = _maintenance_receipt(
                json.loads(_read_path(target, runner, arguments.maintenance_receipt)),
                target=target.name,
                attempt=attempt,
                required_endpoints=_required_maintenance_endpoints(target),
            )
            backup = _backup_run_receipt(
                json.loads(_read_path(target, runner, arguments.backup_receipt)),
                target=target.name,
                run_id=attempt,
                require_quiesced=True,
                expected_writer_services=[
                    target.value["services"][role]
                    for role in BACKUP_WRITER_SERVICE_ROLES
                ],
            )
        except json.JSONDecodeError as error:
            raise RuntimeError("staging checkpoint input receipt is invalid") from error
        if prepared["upgrade_plan_sha256"] != plan["sha256"]:
            raise RuntimeError("staging checkpoint plan differs from preparation")
        prepared_at = datetime.fromisoformat(prepared["prepared_at"].replace("Z", "+00:00"))
        maintenance_at = datetime.fromisoformat(maintenance["observed_at"].replace("Z", "+00:00"))
        capture_at = datetime.fromisoformat(backup["capture"]["created_at"].replace("Z", "+00:00"))
        if maintenance_at < prepared_at or capture_at < maintenance_at:
            raise RuntimeError("staging checkpoint phase ordering differs")
        active_release, _active_sha, _active_raw = _release(target, runner, None)
        if backup["capture"]["release"]["identity"] != active_release["identity"]:
            raise RuntimeError("staging checkpoint does not describe the active release")
        runtime = inspect_runtime(target, runner)
        image = _operations_image(active_release)
        verified = _run_cohort(
            target,
            runner,
            image,
            "verify",
            ["--durable-snapshot", backup["qualification"]["durable_snapshot_id"]],
            volumes=runtime["volumes"],
        )
        if (
            verified.get("status") != "verified"
            or verified.get("target") != "staging"
            or verified.get("cohort_schema") != RECOVERY_COHORT_SCHEMA
            or verified.get("durable_snapshot_id")
            != backup["qualification"]["durable_snapshot_id"]
            or verified.get("cache_snapshot_id")
            != backup["qualification"]["cache_snapshot_id"]
        ):
            raise RuntimeError("staging checkpoint verification differs")
        value = {
            "schema": "usl-staging-checkpoint/v1",
            "target": target.name,
            "attempt": attempt,
            "candidate_release": release["identity"],
            "snapshot": verified["durable_snapshot_id"],
            "cache_snapshot": verified["cache_snapshot_id"],
            "baseline_generation": runtime.get("generation"),
            "baseline_release": active_release["identity"],
            "baseline_runtime_sha256": _runtime_cas_sha256(target, runner, runtime),
            "upgrade_plan_sha256": plan["sha256"],
            "prepare_receipt_sha256": prepared["sha256"],
            "maintenance_receipt_sha256": maintenance["sha256"],
            "resources_sha256": hashlib.sha256(json.dumps(
                backup["capture"]["resources"], sort_keys=True, separators=(",", ":"),
            ).encode()).hexdigest(),
            "controls_sha256": hashlib.sha256(json.dumps(
                backup["capture"]["controls"], sort_keys=True, separators=(",", ":"),
            ).encode()).hexdigest(),
            "checkpointed_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            "status": "checkpointed",
        }
        if (
            backup["quiescence"]["baseline_runtime_sha256"]
            != value["baseline_runtime_sha256"]
        ):
            raise RuntimeError("staging checkpoint baseline differs from quiescence")
        value["sha256"] = hashlib.sha256(json.dumps(
            value, sort_keys=True, separators=(",", ":"),
        ).encode()).hexdigest()
        checkpoint_root = f"{target.value['state_directory']}/checkpoints/{attempt}"
        runner.run([
            "install", "-d", "-m", "0700",
            f"{target.value['state_directory']}/checkpoints",
        ])
        path = f"{checkpoint_root}/receipt.json"
        if runner.run(["mkdir", checkpoint_root], check=False).returncode:
            existing = runner.run(["cat", path], check=False)
            if existing.returncode:
                raise RuntimeError("staging checkpoint attempt is incomplete")
            try:
                existing_value = _staging_checkpoint_receipt(
                    json.loads(existing.stdout), target=target, attempt=attempt,
                    release=release["identity"],
                )
            except json.JSONDecodeError as error:
                raise RuntimeError("existing staging checkpoint is invalid") from error
            stable = set(value) - {"checkpointed_at", "sha256"}
            if any(existing_value.get(key) != value[key] for key in stable):
                raise RuntimeError("existing staging checkpoint differs")
            print(json.dumps(
                {"path": path, **existing_value},
                indent=None if arguments.json else 2, sort_keys=True,
            ))
            return 0
        _write_remote(
            target, runner, path,
            json.dumps(value, indent=2, sort_keys=True) + "\n", "0444",
        )
        print(json.dumps(
            {"path": path, **value}, indent=None if arguments.json else 2, sort_keys=True,
        ))
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
        run_id = f"abort-{datetime.now(UTC):%Y%m%dt%H%M%S}"
        with runtime_lock(target, runner, "release-abort", run_id):
            rollback = _abort_to_previous_generation(
                target,
                runner,
                arguments.targets,
                attempt=arguments.attempt_id,
            )
            # Recovery has proved the baseline. Preserve the failed claim as history
            # and allow the same immutable release inputs to be retried.
            attempt_root = f"{target.value['state_directory']}/attempts/{arguments.attempt_id}"
            archived_root = f"{target.value['state_directory']}/aborted-attempts/{arguments.attempt_id}-{time.time_ns()}"
            runner.run(["install", "-d", "-m", "0700", str(Path(archived_root).parent)])
            runner.run(["mv", "--", attempt_root, archived_root])
        if controller_state is not None:
            _write_remote(
                target,
                runner,
                state_path,
                json.dumps(controller_state, indent=2, sort_keys=True) + "\n",
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
    operation_kind = None
    source_receipt_sha256 = None
    if arguments.action == "reconcile-staging":
        if target.value["environment"] != "staging":
            raise RuntimeError("ordinary staging reconciliation is staging-only")
        if arguments.source not in (None, "staging"):
            raise RuntimeError("ordinary staging reconciliation must preserve staging data")
        if not arguments.checkpoint_receipt or not arguments.candidate_release:
            raise RuntimeError("ordinary staging reconciliation requires its exact checkpoint")
        try:
            release_for_checkpoint = validate_release(json.loads(
                _read_path(target, runner, arguments.candidate_release),
            ))
            checkpoint = _staging_checkpoint_receipt(
                json.loads(_read_path(target, runner, arguments.checkpoint_receipt)),
                target=target,
                attempt=_release_attempt(
                    arguments.attempt_id, release_for_checkpoint["identity"],
                ),
                release=release_for_checkpoint["identity"],
            )
        except (json.JSONDecodeError, ReleaseManifestError) as error:
            raise RuntimeError("staging checkpoint receipt is invalid") from error
        current = inspect_runtime(target, runner)
        if current.get("generation") != checkpoint["baseline_generation"]:
            raise RuntimeError("staging changed after its checkpoint")
        if _runtime_cas_sha256(target, runner, current) != checkpoint["baseline_runtime_sha256"]:
            raise RuntimeError("staging runtime changed after its checkpoint")
        arguments.source = "staging"
        arguments.snapshot = checkpoint["snapshot"]
        operation_kind = "staging-upgrade"
        source_receipt_sha256 = checkpoint["sha256"]
    elif arguments.action == "staging-reset-from-production":
        if target.value["environment"] != "staging":
            raise RuntimeError("production-derived staging reset is staging-only")
        if (
            not arguments.production_admission_receipt
            or not arguments.backup_receipt
            or not arguments.staging_reset_intent
            or not arguments.production_claim
        ):
            raise RuntimeError(
                "production-derived staging reset requires production admission, backup, and intent",
            )
        production = load_target("production", arguments.targets)
        try:
            admission_value = json.loads(_read_path(target, runner, arguments.production_admission_receipt))
            admission = _validate_release_boundary_receipt(
                admission_value,
                schema="usl-release-admission/v1",
                status="admitted",
                target=production,
                attempt=str(admission_value.get("attempt", "")),
                release=str(admission_value.get("release", "")),
            )
            if admission["schema"].endswith("/v1"):
                raise RuntimeError(
                    "legacy v1 production admission cannot authorize a staging reset",
                )
            backup = _backup_run_receipt(
                json.loads(_read_path(target, runner, arguments.backup_receipt)),
                target="production",
            )
            reset_intent = _staging_reset_intent_receipt(
                json.loads(_read_path(target, runner, arguments.staging_reset_intent)),
                target=target,
                admission=admission,
            )
            claim_value = json.loads(_read_path(target, runner, arguments.production_claim))
            production_claim = _release_attempt_claim(
                claim_value,
                target=production,
                attempt=admission["attempt"],
                release=admission["release"],
            )
        except json.JSONDecodeError as error:
            raise RuntimeError("production-derived staging reset evidence is invalid") from error
        observed_staging = inspect_runtime(target, runner)
        observed_staging_release, _observed_sha, _observed_raw = _release(
            target, runner, None,
        )
        observed_runtime_sha256 = _runtime_cas_sha256(target, runner, observed_staging)
        if (
            observed_staging.get("generation") != reset_intent["staging_baseline_generation"]
            or observed_staging_release["identity"]
            != reset_intent["staging_baseline_release"]
            or observed_runtime_sha256 != reset_intent["staging_baseline_runtime_sha256"]
        ):
            deferred = _staging_reset_deferred_receipt(
                target=target,
                admission=admission,
                intent=reset_intent,
                observed=observed_staging,
                observed_release=observed_staging_release["identity"],
                observed_runtime_sha256=observed_runtime_sha256,
            )
            print(json.dumps(
                deferred, indent=None if arguments.json else 2, sort_keys=True,
            ))
            return 0
        if (
            production_claim.get("operation_kind") != "production-upgrade"
            or production_claim["source"] != production.name
            or production_claim["operation_bundle_sha256"]
            != admission["operation_bundle_sha256"]
            or production_claim["snapshot"] != admission["snapshot"]
            or production_claim["generation"] != admission["generation"]
            or production_claim["gitops_commit"] != reset_intent["gitops_commit"]
            or production_claim["prepare_receipt_sha256"]
            != reset_intent["production_prepare_receipt_sha256"]
            or production_claim["upgrade_plan_sha256"]
            != reset_intent["production_upgrade_plan_sha256"]
        ):
            raise RuntimeError("production staging-reset evidence is cross-wired")
        admitted_at = datetime.fromisoformat(admission["admitted_at"].replace("Z", "+00:00"))
        captured_at = datetime.fromisoformat(backup["capture"]["created_at"].replace("Z", "+00:00"))
        if captured_at <= admitted_at:
            raise RuntimeError("staging reset backup predates production admission")
        if backup["capture"]["release"]["identity"] != admission["release"]:
            raise RuntimeError("staging reset backup is not from the admitted production release")
        production_runner = production.runner()
        production_release, _production_sha, _production_raw = _release(
            production, production_runner, None,
        )
        if production_release["identity"] != admission["release"]:
            raise RuntimeError("production changed after the accepted backup")
        production_runtime = inspect_runtime(production, production_runner)
        verified = _run_cohort(
            production,
            production_runner,
            _operations_image(production_release),
            "verify",
            ["--durable-snapshot", backup["qualification"]["durable_snapshot_id"]],
            volumes=production_runtime["volumes"],
        )
        _validate_recovery_selection(
            backup["qualification"]["durable_snapshot_id"], verified,
        )
        if verified.get("cache_snapshot_id") != backup["qualification"]["cache_snapshot_id"]:
            raise RuntimeError("staging reset reusable cache verification differs")
        arguments.source = "production"
        arguments.snapshot = backup["qualification"]["durable_snapshot_id"]
        operation_kind = "staging-reset-from-production"
        source_receipt_sha256 = hashlib.sha256(json.dumps(
            {
                "admission": admission["sha256"],
                "backup": backup,
                "intent": reset_intent["sha256"],
                "claim": production_claim["sha256"],
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()).hexdigest()
    elif arguments.action == "reconcile":
        if target.value["environment"] != "production":
            raise RuntimeError(
                "use reconcile-staging for ordinary staging upgrades or "
                "staging-reset-from-production after production admission",
            )
        if arguments.source not in (None, "production"):
            raise RuntimeError("production reconciliation requires a production snapshot")
        if not arguments.backup_receipt:
            raise RuntimeError("production reconciliation requires its exact qualified backup")
        try:
            production_backup = _backup_run_receipt(
                json.loads(_read_path(target, runner, arguments.backup_receipt)),
                target="production",
                require_quiesced=True,
                expected_writer_services=[
                    target.value["services"][role]
                    for role in BACKUP_WRITER_SERVICE_ROLES
                ],
            )
        except json.JSONDecodeError as error:
            raise RuntimeError("production reconciliation backup receipt is invalid") from error
        if arguments.snapshot != production_backup["qualification"]["durable_snapshot_id"]:
            raise RuntimeError("production reconciliation snapshot differs from backup receipt")
        active_release, _active_sha, _active_raw = _release(target, runner, None)
        if production_backup["capture"]["release"]["identity"] != active_release["identity"]:
            raise RuntimeError("production backup does not describe the active release")
        runtime = inspect_runtime(target, runner)
        verified = _run_cohort(
            target,
            runner,
            _operations_image(active_release),
            "verify",
            ["--durable-snapshot", arguments.snapshot],
            volumes=runtime["volumes"],
        )
        _validate_recovery_selection(arguments.snapshot, verified)
        arguments.source = "production"
        operation_kind = "production-upgrade"
        source_receipt_sha256 = production_backup["sha256"]
    if operation_kind is not None:
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
            "operation_kind": operation_kind,
            "source_receipt_sha256": source_receipt_sha256,
            "baseline_runtime_sha256": _runtime_cas_sha256(target, runner, baseline),
        }
        operation_bundle_sha256 = hashlib.sha256(
            json.dumps(operation, sort_keys=True, separators=(",", ":")).encode(),
        ).hexdigest()
        claim = {
            "schema": "usl-release-attempt/v3",
            **operation,
            "baseline_generation": baseline.get("generation"),
            "operation_bundle_sha256": operation_bundle_sha256,
            "claimed_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            "status": "claimed",
        }
        claim["sha256"] = hashlib.sha256(
            json.dumps(claim, sort_keys=True, separators=(",", ":")).encode(),
        ).hexdigest()
        runner.run(["install", "-d", "-m", "0700", str(Path(attempt_root).parent)])
        if runner.run(["mkdir", attempt_root], check=False).returncode != 0:
            existing_raw = runner.run(["cat", attempt_path], check=False)
            if existing_raw.returncode:
                raise RuntimeError("release attempt is incomplete; abort before retry")
            try:
                existing_claim = _release_attempt_claim(
                    json.loads(existing_raw.stdout), target=target, attempt=attempt,
                    release=release_value["identity"],
                )
            except json.JSONDecodeError as error:
                raise RuntimeError("existing release attempt is invalid") from error
            stable = set(claim) - {"claimed_at", "sha256"}
            if any(existing_claim.get(key) != claim[key] for key in stable):
                raise RuntimeError("existing release attempt differs")
            active = inspect_runtime(target, runner)
            if active.get("generation") != generation:
                raise RuntimeError("release attempt is incomplete; abort before retry")
            production = target.value["environment"] == "production"
            receipt_name = "quarantine" if production else "admission"
            receipt_raw = runner.run([
                "cat", f"{target.value['state_directory']}/generations/{generation}/{receipt_name}.json",
            ], check=False)
            if receipt_raw.returncode:
                raise RuntimeError("release attempt has no durable boundary receipt")
            receipt = _validate_release_boundary_receipt(
                json.loads(receipt_raw.stdout),
                schema=("usl-release-quarantine/v2" if production else "usl-release-admission/v2"),
                status=("quarantined" if production else "admitted"),
                target=target, attempt=attempt, release=release_value["identity"],
            )
            if receipt["schema"].endswith("/v1"):
                raise RuntimeError(
                    "legacy v1 boundary is audit-only and cannot replay reconciliation",
                )
            _require_same_attempt_boundary(
                existing_claim, receipt, generation=generation, snapshot=arguments.snapshot,
            )
            runtime_evidence = None
            if production:
                if receipt["runtime_evidence_sha256"] is not None:
                    raise RuntimeError("production boundary has staging runtime evidence")
            else:
                runtime_evidence_path = (
                    f"{target.value['state_directory']}/generations/{generation}/"
                    "runtime-evidence.json"
                )
                try:
                    runtime_evidence = _release_runtime_evidence(
                        json.loads(_read_path(target, runner, Path(runtime_evidence_path))),
                        target=target,
                        attempt=attempt,
                        release=release_value["identity"],
                        snapshot=arguments.snapshot,
                        generation=generation,
                        operation_kind=operation_kind,
                    )
                except json.JSONDecodeError as error:
                    raise RuntimeError("release runtime evidence is invalid") from error
                if receipt["runtime_evidence_sha256"] != runtime_evidence["sha256"]:
                    raise RuntimeError("release boundary runtime evidence differs")
            value = {
                "schema": "usl-release-reconcile-replay/v1",
                "target": target.name,
                "attempt": attempt,
                "operation_kind": operation_kind,
                "claim": {"path": attempt_path, **existing_claim},
                receipt_name: {
                    "path": f"{target.value['state_directory']}/generations/{generation}/{receipt_name}.json",
                    **receipt,
                },
                "runtime_evidence": (
                    {"path": runtime_evidence_path, **runtime_evidence}
                    if runtime_evidence is not None else None
                ),
                "status": "already-" + receipt["status"],
            }
            if runtime_evidence is not None:
                value.update({
                    "auth_compose_admission": runtime_evidence["auth_compose_admission"],
                    "pocket_id_admission": runtime_evidence["pocket_id_admission"],
                    "environment_state_preservation": runtime_evidence[
                        "environment_state_preservation"
                    ],
                })
            print(json.dumps(value, indent=None if arguments.json else 2, sort_keys=True))
            return 0
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
            operation_kind=operation_kind,
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
    runtime.add_argument("action", choices=("status", "start", "stop", "adopt-gateway"))
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
    backup.add_argument("action", choices=("create", "list", "select", "verify", "prune"))
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
    recovery_proof = commands.add_parser("recovery-proof")
    recovery_proof.add_argument("action", choices=("run",))
    recovery_proof.add_argument("--target", dest="command_target", required=True)
    recovery_proof.add_argument("--proof-id", required=True)
    recovery_proof.add_argument("--evidence-directory", type=Path, required=True)
    recovery_proof.add_argument("--release", type=Path)
    recovery_proof.add_argument(
        "--failure-after",
        choices=RECOVERY_PROOF_FAILURE_STAGES,
        help="inject one deterministic failure after the named durable phase",
    )
    recovery_proof.add_argument("--json", action="store_true")
    recovery_proof.set_defaults(handler=recovery_proof_command)
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
    release.add_argument(
        "action",
        choices=(
            "prepare", "plan", "staging-checkpoint", "reconcile", "baseline-check",
            "staging-reset-intent",
            "resume-staging",
            "reconcile-staging", "staging-reset-from-production",
            "activate", "status", "abort", "notify",
        ),
    )
    release.add_argument("--target", dest="command_target")
    release.add_argument("--source")
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
    release.add_argument("--backup-receipt", type=Path)
    release.add_argument("--quiescence-receipt", type=Path)
    release.add_argument("--checkpoint-receipt", type=Path)
    release.add_argument("--production-admission-receipt", type=Path)
    release.add_argument("--production-claim", type=Path)
    release.add_argument("--staging-reset-intent", type=Path)
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
