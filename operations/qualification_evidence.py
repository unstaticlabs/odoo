"""Small, exact evidence binding expensive PR qualification to a Git tree."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any


SCHEMA = "usl-qualification-evidence/v1"
SHA = re.compile(r"^[0-9a-f]{40}$")
TREE = re.compile(r"^[0-9a-f]{40}$")
MODES = {"all", "focused", "skipped"}
FIELDS = {
    "schema", "repository", "event", "pull_request", "source_ref", "source_sha",
    "base_ref", "base_sha", "qualified_commit", "qualified_tree", "database_mode",
    "workflow_run_id", "results", "sha256",
}


class QualificationEvidenceError(ValueError):
    pass


def _digest(value: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def create(
    *,
    repository: str,
    event: str,
    pull_request: int | None,
    source_ref: str,
    source_sha: str,
    base_ref: str,
    base_sha: str,
    qualified_commit: str,
    qualified_tree: str,
    database_mode: str,
    workflow_run_id: int,
    results: dict[str, str],
) -> dict[str, Any]:
    body = {
        "schema": SCHEMA,
        "repository": repository,
        "event": event,
        "pull_request": pull_request,
        "source_ref": source_ref,
        "source_sha": source_sha,
        "base_ref": base_ref,
        "base_sha": base_sha,
        "qualified_commit": qualified_commit,
        "qualified_tree": qualified_tree,
        "database_mode": database_mode,
        "workflow_run_id": workflow_run_id,
        "results": results,
    }
    value = {**body, "sha256": _digest(body)}
    validate(value)
    return value


def validate(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != FIELDS:
        raise QualificationEvidenceError("qualification evidence fields differ")
    body = {key: item for key, item in value.items() if key != "sha256"}
    if value["schema"] != SCHEMA or value["sha256"] != _digest(body):
        raise QualificationEvidenceError("qualification evidence digest differs")
    if not isinstance(value["repository"], str) or "/" not in value["repository"]:
        raise QualificationEvidenceError("qualification repository is invalid")
    if value["event"] not in {"push", "pull_request", "merge_group"}:
        raise QualificationEvidenceError("qualification event is invalid")
    if value["pull_request"] is not None and (
        isinstance(value["pull_request"], bool)
        or not isinstance(value["pull_request"], int)
        or value["pull_request"] <= 0
    ):
        raise QualificationEvidenceError("qualification pull request is invalid")
    for field in ("source_ref", "base_ref"):
        if not isinstance(value[field], str) or not value[field]:
            raise QualificationEvidenceError(f"qualification {field} is invalid")
    for field in ("source_sha", "base_sha", "qualified_commit"):
        if not SHA.fullmatch(str(value[field])):
            raise QualificationEvidenceError(f"qualification {field} is invalid")
    if not TREE.fullmatch(str(value["qualified_tree"])):
        raise QualificationEvidenceError("qualification tree is invalid")
    if value["database_mode"] not in MODES:
        raise QualificationEvidenceError("qualification database mode is invalid")
    if not isinstance(value["workflow_run_id"], int) or value["workflow_run_id"] <= 0:
        raise QualificationEvidenceError("qualification workflow run is invalid")
    results = value["results"]
    if not isinstance(results, dict) or set(results) != {"compatibility", "database", "source_policy"}:
        raise QualificationEvidenceError("qualification result inventory differs")
    if any(item not in {"success", "skipped"} for item in results.values()):
        raise QualificationEvidenceError("qualification contains a failed result")
    return value


def load(path: Path) -> dict[str, Any]:
    try:
        return validate(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError) as error:
        raise QualificationEvidenceError(f"cannot load qualification evidence: {path}") from error


def verify_production_pr(
    value: dict[str, Any], *, repository: str, pull_request: int,
    source_ref: str, source_sha: str, base_sha: str, qualified_tree: str,
) -> dict[str, Any]:
    validate(value)
    expected = {
        "repository": repository,
        "event": "pull_request",
        "pull_request": pull_request,
        "source_ref": source_ref,
        "source_sha": source_sha,
        "base_ref": "19-usl",
        "base_sha": base_sha,
        "qualified_tree": qualified_tree,
        "database_mode": "all",
    }
    for key, item in expected.items():
        if value.get(key) != item:
            raise QualificationEvidenceError(f"production qualification {key} differs")
    if value["results"] != {
        "compatibility": "success", "database": "success", "source_policy": "success",
    }:
        raise QualificationEvidenceError("production qualification results differ")
    return value


def verify_merge_group(
    value: dict[str, Any], *, repository: str, pull_request: int,
    source_ref: str, source_sha: str, qualified_tree: str,
) -> dict[str, Any]:
    validate(value)
    expected = {
        "repository": repository,
        "event": "pull_request",
        "pull_request": pull_request,
        "source_ref": source_ref,
        "source_sha": source_sha,
        "base_ref": "19-usl",
        "qualified_tree": qualified_tree,
        "database_mode": "all",
    }
    for key, item in expected.items():
        if value.get(key) != item:
            raise QualificationEvidenceError(f"merge-group qualification {key} differs")
    if value["results"] != {
        "compatibility": "success", "database": "success", "source_policy": "success",
    }:
        raise QualificationEvidenceError("merge-group qualification results differ")
    return value
