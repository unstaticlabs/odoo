"""Persistent, resumable state machine for coordinated USL releases."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable


SCHEMA = "usl-release-run/v1"
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
    body = {key: item for key, item in value.items() if key != "checksum"}
    value = {**body, "checksum": _digest(body)}
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as stream:
        json.dump(value, stream, indent=2, sort_keys=True)
        stream.write("\n")
        temporary = Path(stream.name)
    os.replace(temporary, path)


def load(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ReleaseControllerError(f"cannot read release state: {path}") from error
    if not isinstance(value, dict) or value.get("schema") != SCHEMA:
        raise ReleaseControllerError("release state schema is invalid")
    checksum = value.get("checksum")
    body = {key: item for key, item in value.items() if key != "checksum"}
    if checksum != _digest(body):
        raise ReleaseControllerError("release state checksum differs")
    return value


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
                "forward-fix-only" if "reopen" in completed else "rollback-previous-generation"
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
