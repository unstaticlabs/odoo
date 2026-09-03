"""Persistent, resumable state machine for coordinated USL releases."""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable


SCHEMA = "usl-release-run/v1"
RUN_ID = re.compile(r"[a-z0-9][a-z0-9._-]{7,95}\Z")
TARGET = re.compile(r"[a-z][a-z0-9-]{1,31}\Z")
RELEASE = re.compile(r"[0-9a-f]{64}\Z")
STATUSES = frozenset({"running", "failed", "admitted", "aborted"})
STAGES = (
    "resolve",
    "preflight",
    "maintenance",
    "quiesce",
    "pre-release-snapshot",
    "candidate-materialization",
    "candidate-upgrade",
    "candidate-qualification",
    "candidate-snapshot",
    "route-switch",
    "production-admission",
    "reopen",
    "notify",
    "staging-refresh",
    "retention",
    "record",
)


class ReleaseControllerError(RuntimeError):
    """A run state or transition is invalid."""


def _digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _write(path: Path, value: dict[str, Any]) -> None:
    value = prepare(value)
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as stream:
        json.dump(value, stream, indent=2, sort_keys=True)
        stream.write("\n")
        temporary = Path(stream.name)
    os.replace(temporary, path)


def prepare(value: dict[str, Any]) -> dict[str, Any]:
    """Validate and checksum one state before durable or remote storage."""
    body = {key: item for key, item in value.items() if key != "checksum"}
    _validate(body)
    return {**body, "checksum": _digest(body)}


def _validate(value: object) -> dict[str, Any]:
    if not isinstance(value, dict) or value.get("schema") != SCHEMA:
        raise ReleaseControllerError("release state schema is invalid")
    required = {
        "schema",
        "run_id",
        "target",
        "release",
        "status",
        "completed",
        "phase",
        "evidence",
        "started_at",
    }
    optional = {"failure", "recovery", "completed_at", "aborted_at"}
    if not required <= set(value) or set(value) - required - optional:
        raise ReleaseControllerError("release state fields differ")
    if not RUN_ID.fullmatch(str(value["run_id"])):
        raise ReleaseControllerError("release run identity is invalid")
    if not TARGET.fullmatch(str(value["target"])):
        raise ReleaseControllerError("release target is invalid")
    if not RELEASE.fullmatch(str(value["release"])):
        raise ReleaseControllerError("release identity is invalid")
    if value["status"] not in STATUSES or value["phase"] not in STAGES:
        raise ReleaseControllerError("release status or phase is invalid")
    completed = value["completed"]
    if (
        not isinstance(completed, list)
        or completed != list(STAGES[: len(completed)])
        or len(completed) > len(STAGES)
    ):
        raise ReleaseControllerError("completed stage order is invalid")
    evidence = value["evidence"]
    if not isinstance(evidence, dict) or set(evidence) != set(completed):
        raise ReleaseControllerError("release evidence stages differ")
    for stage, item in evidence.items():
        if (
            not isinstance(item, dict)
            or set(item) != {"sha256", "value"}
            or item["sha256"] != _digest(item["value"])
        ):
            raise ReleaseControllerError(f"release evidence is invalid: {stage}")
    if value["status"] == "failed":
        if set(value.get("failure", {})) != {"stage", "type", "occurred_at"}:
            raise ReleaseControllerError("failed release has invalid failure evidence")
        expected_recovery = (
            "forward-fix-only"
            if "reopen" in completed
            else "rollback-previous-generation"
        )
        if value.get("recovery") != expected_recovery:
            raise ReleaseControllerError("failed release recovery boundary differs")
    elif "failure" in value or "recovery" in value:
        raise ReleaseControllerError("non-failed release contains failure state")
    if value["status"] == "admitted" and completed != list(STAGES):
        raise ReleaseControllerError("admitted release has incomplete stages")
    return value


def parse(text: str) -> dict[str, Any]:
    try:
        value = json.loads(text)
    except json.JSONDecodeError as error:
        raise ReleaseControllerError("release state is not valid JSON") from error
    if not isinstance(value, dict):
        raise ReleaseControllerError("release state schema is invalid")
    checksum = value.get("checksum")
    body = {key: item for key, item in value.items() if key != "checksum"}
    if checksum != _digest(body):
        raise ReleaseControllerError("release state checksum differs")
    _validate(body)
    return value


def load(path: Path) -> dict[str, Any]:
    try:
        return parse(path.read_text(encoding="utf-8"))
    except OSError as error:
        raise ReleaseControllerError(f"cannot read release state: {path}") from error


def create(path: Path, *, run_id: str, target: str, release: str) -> dict[str, Any]:
    if path.exists():
        existing = load(path)
        if existing["run_id"] != run_id or existing["release"] != release:
            raise ReleaseControllerError("another release run owns this target")
        return existing
    value = {
        "schema": SCHEMA,
        "run_id": run_id,
        "target": target,
        "release": release,
        "status": "running",
        "completed": [],
        "phase": STAGES[0],
        "evidence": {},
        "started_at": datetime.now(UTC).isoformat(),
    }
    _write(path, value)
    return load(path)


def abort(value: dict[str, Any]) -> dict[str, Any]:
    """Mark a pre-reopen run aborted while preserving its checksum contract."""
    state = _validate({key: item for key, item in value.items() if key != "checksum"})
    if state["status"] in {"admitted", "aborted"}:
        raise ReleaseControllerError(f"release run is {state['status']}")
    if "reopen" in state["completed"]:
        raise ReleaseControllerError(
            "an already reopened release requires a forward fix",
        )
    state = {
        **state,
        "status": "aborted",
        "aborted_at": datetime.now(UTC).isoformat(),
    }
    state.pop("failure", None)
    state.pop("recovery", None)
    return prepare(state)


def run(
    path: Path,
    handlers: dict[str, Callable[[], dict[str, Any]]],
    *,
    stop_after: str | None = None,
) -> dict[str, Any]:
    state = load(path)
    if state["status"] not in {"running", "failed"}:
        raise ReleaseControllerError(f"release run is {state['status']}")
    unknown = set(handlers) - set(STAGES)
    missing = set(STAGES) - set(handlers)
    if unknown or missing:
        raise ReleaseControllerError(
            f"stage handlers differ: missing={sorted(missing)}, unknown={sorted(unknown)}",
        )
    completed = list(state["completed"])
    for stage in STAGES:
        if stage in completed:
            continue
        expected_index = len(completed)
        if STAGES[expected_index] != stage:
            raise ReleaseControllerError("completed stage order is invalid")
        state["phase"] = stage
        state["status"] = "running"
        _write(path, state)
        try:
            evidence = handlers[stage]()
        except Exception as error:
            state["status"] = "failed"
            state["failure"] = {
                "stage": stage,
                "type": type(error).__name__,
                "occurred_at": datetime.now(UTC).isoformat(),
            }
            state["recovery"] = (
                "forward-fix-only"
                if "reopen" in completed
                else "rollback-previous-generation"
            )
            _write(path, state)
            raise
        if not isinstance(evidence, dict):
            raise ReleaseControllerError(f"stage {stage} returned non-object evidence")
        state.pop("failure", None)
        state.pop("recovery", None)
        state["evidence"][stage] = {
            "sha256": _digest(evidence),
            "value": evidence,
        }
        completed.append(stage)
        state["completed"] = completed
        _write(path, state)
        if stop_after == stage:
            return load(path)
    state["status"] = "admitted"
    state["completed_at"] = datetime.now(UTC).isoformat()
    _write(path, state)
    return load(path)
