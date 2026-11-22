"""Read-only projection and deterministic selection of native release runs.

Fixed-launcher v3 is owned by GitOps' usl-odoo-release-launcher.py. This
reader validates its complete checksummed envelope before omitting private
stage values from the public status response. It grants no release authority.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime

from operations.release_controller import ReleaseControllerError, parse as parse_legacy
from operations.runtime import (
    RuntimeError,
    read_active_release,
    read_active_state,
    release_run_inventory_command,
    staging_progress_probe,
)

FIXED_SCHEMA = "usl-fixed-release-run/v3"
STAGING_SCHEMA = "usl-staging-release-progress/v1"
STAGING_PHASES = {
    "wake", "resolve", "plan", "prepare", "maintenance", "backup",
    "checkpoint", "upgrade", "attestation", "reopen", "record", "verify",
}
STAGING_ADMITTED = ("admitted", "admitted_follow_up_deferred")
RUN_NAME = re.compile(r"release-([0-9a-f]{64})\.json\Z")
STAGES = (
    "resolve", "prepare", "preflight", "maintenance", "quiesce",
    "pre-release-snapshot", "candidate-materialization", "candidate-upgrade",
    "candidate-qualification", "candidate-snapshot", "activation", "route-switch",
    "production-admission", "post-admission-backup", "reopen", "notify",
    "staging-refresh", "retention", "record",
)


def _digest(value: object) -> str:
    canonical = json.dumps(value, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def _optional_read(runner, path: str) -> str | None:
    result = runner.run(["cat", path], check=False)
    if not result.returncode:
        return result.stdout
    if runner.run(["test", "!", "-e", path], check=False).returncode == 0:
        return None
    raise RuntimeError("release status state cannot be read")


def _fixed(text: str, *, target: str, release: str) -> dict:
    try:
        value = json.loads(text)
    except json.JSONDecodeError as error:
        raise RuntimeError("fixed release run is invalid JSON") from error
    if not isinstance(value, dict):
        raise RuntimeError("fixed release run is not an object")
    body = {key: item for key, item in value.items() if key != "checksum"}
    if value.get("checksum") != _digest(body):
        raise RuntimeError("fixed release run checksum differs")
    required = {
        "schema", "target", "release", "attempt", "status", "phase",
        "completed", "evidence", "started_at",
    }
    if not required <= set(body) or set(body) - required - {
        "failure", "recovery", "completed_at", "failure_context",
    }:
        raise RuntimeError("fixed release run fields differ")
    if (
        body["schema"] != FIXED_SCHEMA or body["target"] != target or body["release"] != release
        or not re.fullmatch(r"[a-z0-9][a-z0-9._-]{7,63}", str(body["attempt"]))
        or body["attempt"] == release
        or body["status"] not in ("running", "failed", "admitted", "recovered")
        or body["phase"] not in STAGES
    ):
        raise RuntimeError("fixed release run identity differs")
    completed = body["completed"]
    if not isinstance(completed, list) or completed != list(STAGES[:len(completed)]):
        raise RuntimeError("fixed release run stages are out of order")
    evidence = body["evidence"]
    if not isinstance(evidence, dict) or set(evidence) != set(completed):
        raise RuntimeError("fixed release run evidence differs")
    for item in evidence.values():
        if (
            not isinstance(item, dict) or set(item) != {"sha256", "value"}
            or item["sha256"] != _digest(item["value"])
        ):
            raise RuntimeError("fixed release run evidence digest differs")
    if body["status"] in ("failed", "recovered"):
        failure = body.get("failure")
        if not isinstance(failure, dict) or set(failure) != {"stage", "type", "occurred_at"}:
            raise RuntimeError("fixed release run failure differs")
        if (
            failure["stage"] not in STAGES
            or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,127}", str(failure["type"]))
        ):
            raise RuntimeError("fixed release run failure metadata is invalid")
        activated = (
            "activation" in completed or failure["stage"] in STAGES[STAGES.index("activation"):]
            or failure["type"] == "MaintenanceContinuityLost"
        )
        allowed = ("forward-fix-only",) if activated else (
            "rollback-previous-generation", "rolled-back-and-reopened",
        )
        if body.get("recovery") not in allowed:
            raise RuntimeError("fixed release run recovery boundary differs")
    elif "failure" in body or "recovery" in body:
        raise RuntimeError("non-failed fixed release run contains failure evidence")
    if body["status"] == "admitted" and completed != list(STAGES):
        raise RuntimeError("admitted fixed release run is incomplete")
    if body["status"] == "recovered" and "completed_at" not in body:
        raise RuntimeError("recovered fixed release run has no terminal timestamp")
    timestamps = [body["started_at"]]
    if "completed_at" in body:
        timestamps.append(body["completed_at"])
    if "failure" in body:
        timestamps.append(body["failure"]["occurred_at"])
    for timestamp in timestamps:
        try:
            if (
                not isinstance(timestamp, str) or len(timestamp) > 40
                or datetime.fromisoformat(timestamp).tzinfo is None
            ):
                raise ValueError
        except ValueError as error:
            raise RuntimeError("fixed release run timestamp is invalid") from error
    context = body.get("failure_context")
    if "failure_context" in body and (
        not isinstance(context, dict) or set(context) != {"phase", "code"}
        or context["phase"] not in STAGES
        or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_.-]{0,95}", str(context["code"]))
    ):
        raise RuntimeError("fixed release run failure context is invalid")
    return value


def _project(value: dict) -> dict:
    keys = (
        "target", "release", "attempt", "status", "phase", "completed",
        "started_at", "completed_at", "recovery",
    )
    result = {
        "schema": "usl-release-status/v1", "source_schema": FIXED_SCHEMA,
        "source_checksum": value["checksum"],
        **{key: value[key] for key in keys if key in value},
        "evidence_sha256": {stage: item["sha256"] for stage, item in value["evidence"].items()},
    }
    for key in ("failure", "failure_context"):
        if key in value:
            result[key] = value[key]
    return result


def _staging_progress(text: str, root: str, runner) -> dict:
    try:
        value = json.loads(text)
    except json.JSONDecodeError as error:
        raise RuntimeError("staging progress is invalid JSON") from error
    if not isinstance(value, dict):
        raise RuntimeError("staging progress is not an object")
    body = {key: item for key, item in value.items() if key != "checksum"}
    if value.get("checksum") != _digest(body):
        raise RuntimeError("staging progress checksum differs")
    expected = {
        "schema", "target", "release", "desired_state", "phase", "status",
        "updated_at", "token",
    }
    if (
        set(body) != expected
        or body["schema"] != STAGING_SCHEMA or body["target"] != "staging"
        or any(not re.fullmatch(r"[a-f0-9]{64}", str(body[key])) for key in ("release", "desired_state"))
        or not isinstance(body["phase"], str) or body["phase"] not in STAGING_PHASES
        or body["status"] not in ("running", "failed", *STAGING_ADMITTED)
        or not isinstance(body["token"], str) or not re.fullmatch(r"[a-f0-9]{32}", body["token"])
        or (body["status"] in STAGING_ADMITTED and body["phase"] != "record")
    ):
        raise RuntimeError("staging progress identity differs")
    timestamp = body["updated_at"]
    try:
        if not isinstance(timestamp, str) or len(timestamp) > 40 or datetime.fromisoformat(timestamp).tzinfo is None:
            raise ValueError
    except ValueError as error:
        raise RuntimeError("staging progress timestamp is invalid") from error

    status = body["status"]
    probe = runner.run(staging_progress_probe(root, body["token"]), check=False)
    if probe.stderr or probe.returncode not in (0, 1, 2):
        raise RuntimeError("staging progress lock cannot be inspected")
    if probe.returncode == 2:
        return {"schema": "usl-release-run/v1", "target": "staging", "status": "unobserved"}
    if probe.returncode == 1 and status == "running":
        status = "interrupted"
    return {
        "schema": "usl-release-status/v1", "source_schema": STAGING_SCHEMA,
        "source_checksum": value["checksum"], "status": status,
        "operation": "staging-upgrade",
        **{key: body[key] for key in ("target", "release", "desired_state", "phase", "updated_at")},
    }


def observe(target, runner) -> dict:
    """Select a running run, or the active release; never guess by timestamp."""
    root = target.value["state_directory"]
    if target.name == "staging":
        path = f"{root}/staging-progress.json"
        progress = _optional_read(runner, path)
        if progress is not None:
            result = _staging_progress(progress, root, runner)
            if result["status"] in ("interrupted", "unobserved"):
                # The launcher can publish its terminal result between the file and process reads.
                current = _optional_read(runner, path)
                if current is not None and current != progress:
                    return _staging_progress(current, root, runner)
            return result
    legacy_text = _optional_read(runner, f"{root}/release-state.json")
    legacy = None
    if legacy_text is not None:
        try:
            legacy = parse_legacy(legacy_text)
        except ReleaseControllerError as error:
            raise RuntimeError(str(error)) from error
        if legacy["target"] != target.name:
            raise RuntimeError("legacy release state belongs to another target")
    fixed = []
    if target.name in {"staging", "production"}:
        runs_root = f"{root}/runs"
        listing = runner.run(release_run_inventory_command(runs_root), check=False)
        if listing.returncode:
            if runner.run(["test", "!", "-e", runs_root], check=False).returncode:
                raise RuntimeError("release run inventory cannot be inspected")
        else:
            for line in listing.stdout.splitlines():
                parts = line.split("\t")
                if len(parts) != 2:
                    raise RuntimeError("release run inventory is invalid")
                match = RUN_NAME.fullmatch(parts[0])
                if not match:  # Failed-attempt archives and sidecars are history.
                    continue
                if parts[1] != "f":
                    raise RuntimeError("fixed release run is not a regular file")
                text = _optional_read(runner, f"{runs_root}/{parts[0]}")
                if text is None:
                    raise RuntimeError("fixed release run disappeared during observation")
                fixed.append(_fixed(text, target=target.name, release=match[1]))
    running = [
        value for value in [*fixed, legacy]
        if value is not None and value["status"] == "running"
    ]
    if len(running) > 1:
        raise RuntimeError("release status is ambiguous: multiple running runs")
    if running:
        return _project(running[0]) if running[0]["schema"] == FIXED_SCHEMA else running[0]
    if fixed:
        active = read_active_state(target, runner)
        if active is None:
            # Distinguish a bootstrap without active.json from an unreadable file.
            _optional_read(runner, f"{root}/active.json")
        if active is not None:
            identity = read_active_release(target, runner, active)["identity"]
            for value in fixed:
                if value["release"] == identity:
                    return _project(value)
            if legacy is not None and legacy["release"] == identity:
                return legacy
        raise RuntimeError("release status is ambiguous: no run matches the active release")
    return legacy or {
        "schema": "usl-release-run/v1",
        "target": target.name,
        "status": "unobserved" if target.name == "staging" else "idle",
    }
