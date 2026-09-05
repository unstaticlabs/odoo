"""Validate the administrator-applied GitHub release-branch contract."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


STAGING_BRANCH = {"refs/heads/19-usl-staging"}
PRODUCTION_BRANCH = {"refs/heads/19-usl"}
PROTECTION_RULES = {
    "deletion",
    "non_fast_forward",
    "pull_request",
    "merge_queue",
    "required_status_checks",
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


def validate_unattended_merge(rules: dict[str, dict[str, Any]]) -> None:
    pull_request = rules["pull_request"].get("parameters", {})
    if pull_request.get("required_approving_review_count") != 0:
        raise GovernanceError("release branches intentionally require zero approving reviews")
    if pull_request.get("required_reviewers") != []:
        raise GovernanceError("release branches must not require named reviewers")
    if pull_request.get("require_code_owner_review") is not False:
        raise GovernanceError("release branches must not require code-owner approval")
    if pull_request.get("require_last_push_approval") is not False:
        raise GovernanceError("release branches must not require last-push approval")
    if pull_request.get("allowed_merge_methods") != ["merge"]:
        raise GovernanceError("release branches must use merge commits only")
    if pull_request.get("required_review_thread_resolution") is not True:
        raise GovernanceError("review conversations must be resolved")


def validate_merge_queue(
    rules: dict[str, dict[str, Any]],
    *,
    max_entries_to_merge: int,
    wait_minutes: int,
) -> None:
    queue = rules["merge_queue"].get("parameters", {})
    if queue.get("merge_method") != "MERGE" or queue.get("grouping_strategy") != "ALLGREEN":
        raise GovernanceError("merge queue policy differs")
    if queue.get("max_entries_to_merge") != max_entries_to_merge:
        raise GovernanceError("merge queue batch size differs")
    if queue.get("min_entries_to_merge_wait_minutes") != wait_minutes:
        raise GovernanceError("merge queue wait differs")


def validate(value: dict[str, Any]) -> dict[str, Any]:
    if value.get("name") != "USL Distribution — Staging":
        raise GovernanceError("staging ruleset name differs")
    if value.get("target") != "branch" or value.get("enforcement") != "active":
        raise GovernanceError("staging ruleset is not an active branch contract")
    included = set(value.get("conditions", {}).get("ref_name", {}).get("include", []))
    if included != STAGING_BRANCH:
        raise GovernanceError("staging ruleset must target only 19-usl-staging")
    rules = {item.get("type"): item for item in value.get("rules", []) if isinstance(item, dict)}
    if set(rules) != PROTECTION_RULES:
        raise GovernanceError("staging protection inventory differs")
    validate_unattended_merge(rules)
    validate_merge_queue(rules, max_entries_to_merge=5, wait_minutes=5)
    checks = rules["required_status_checks"].get("parameters", {})
    if checks.get("strict_required_status_checks_policy") is not False:
        raise GovernanceError("staging qualification check must not require a branch refresh")
    contexts = {item.get("context") for item in checks.get("required_status_checks", [])}
    if contexts != {"USL qualification"}:
        raise GovernanceError("stable qualification check is not the sole required context")
    return value


def validate_production(value: dict[str, Any]) -> dict[str, Any]:
    if value.get("name") != "USL Distribution — Production":
        raise GovernanceError("production ruleset name differs")
    if value.get("target") != "branch" or value.get("enforcement") != "active":
        raise GovernanceError("production ruleset is not an active branch contract")
    included = set(value.get("conditions", {}).get("ref_name", {}).get("include", []))
    if included != PRODUCTION_BRANCH:
        raise GovernanceError("production ruleset must target only 19-usl")
    rules = {item.get("type"): item for item in value.get("rules", []) if isinstance(item, dict)}
    if set(rules) != PROTECTION_RULES:
        raise GovernanceError("production protection inventory differs")
    validate_unattended_merge(rules)
    validate_merge_queue(rules, max_entries_to_merge=1, wait_minutes=0)
    checks = rules["required_status_checks"].get("parameters", {})
    if checks.get("strict_required_status_checks_policy") is not True:
        raise GovernanceError("production required checks must be strict")
    contexts = {item.get("context") for item in checks.get("required_status_checks", [])}
    if contexts != {
        "USL source policy",
        "USL compatibility",
        "USL production promotion",
    }:
        raise GovernanceError("production source, compatibility and promotion checks differ")
    return value
