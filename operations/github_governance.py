"""Validate the administrator-applied GitHub release-branch contract."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


PERMANENT_BRANCHES = {
    "refs/heads/19-usl",
    "refs/heads/19-usl-staging",
}


class GovernanceError(ValueError):
    pass


def load(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise GovernanceError(f"cannot read GitHub ruleset: {path}") from error
    if not isinstance(value, dict):
        raise GovernanceError("GitHub ruleset must be an object")
    return value


def validate(value: dict[str, Any]) -> dict[str, Any]:
    if value.get("name") != "USL Distribution":
        raise GovernanceError("ruleset name differs")
    if value.get("target") != "branch" or value.get("enforcement") != "active":
        raise GovernanceError("ruleset is not an active branch contract")
    included = set(value.get("conditions", {}).get("ref_name", {}).get("include", []))
    if included != PERMANENT_BRANCHES:
        raise GovernanceError("ruleset must target exactly both permanent release branches")
    rules = {item.get("type"): item for item in value.get("rules", []) if isinstance(item, dict)}
    required = {"deletion", "non_fast_forward", "pull_request", "merge_queue", "required_status_checks"}
    if set(rules) != required:
        raise GovernanceError("ruleset protection inventory differs")
    pull_request = rules["pull_request"].get("parameters", {})
    if pull_request.get("allowed_merge_methods") != ["merge"]:
        raise GovernanceError("release branches must use merge commits only")
    if pull_request.get("required_review_thread_resolution") is not True:
        raise GovernanceError("review conversations must be resolved")
    queue = rules["merge_queue"].get("parameters", {})
    if queue.get("merge_method") != "MERGE" or queue.get("grouping_strategy") != "ALLGREEN":
        raise GovernanceError("merge queue policy differs")
    checks = rules["required_status_checks"].get("parameters", {})
    if checks.get("strict_required_status_checks_policy") is not True:
        raise GovernanceError("required checks must be strict")
    contexts = {item.get("context") for item in checks.get("required_status_checks", [])}
    if contexts != {"USL qualification"}:
        raise GovernanceError("stable qualification check is not the sole required context")
    return value
