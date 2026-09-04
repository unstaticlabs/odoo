"""Deterministic evidence for admission to the USL production merge queue."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any


SCHEMA = "usl-production-promotion/v1"
PRODUCTION_BRANCH = "19-usl"
SHA_PATTERN = re.compile(r"^[0-9a-f]{40}$")
EVIDENCE_FIELDS = {
    "schema",
    "event",
    "repository",
    "base_branch",
    "original_pr_number",
    "source_repository",
    "source_branch",
    "source_tree",
    "qualified_git_tree",
    "production_merge_group_tree",
}


class ProductionPromotionError(ValueError):
    pass


def _sha(value: Any, label: str) -> str:
    if not isinstance(value, str) or not SHA_PATTERN.fullmatch(value):
        raise ProductionPromotionError(f"{label} must be a full Git commit SHA")
    return value


def _branch(value: Any) -> str:
    if not isinstance(value, str) or not value:
        raise ProductionPromotionError("source branch is missing")
    if value != "19-usl-staging" and not value.startswith("urgent/"):
        raise ProductionPromotionError(
            "production accepts only 19-usl-staging or urgent/**"
        )
    return value


def _repository(value: Any, expected: str) -> str:
    if not isinstance(value, str) or value != expected:
        raise ProductionPromotionError("production source repository differs")
    return value


def _pr_number(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ProductionPromotionError("original pull request number is invalid")
    return value


def _pull_request(value: Any, *, repository: str, base_branch: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ProductionPromotionError("pull request evidence is not an object")
    base = value.get("base")
    head = value.get("head")
    if not isinstance(base, dict) or base.get("ref") != base_branch:
        raise ProductionPromotionError("pull request does not target production")
    if not isinstance(head, dict):
        raise ProductionPromotionError("pull request head is missing")
    head_repository = head.get("repo")
    if not isinstance(head_repository, dict):
        raise ProductionPromotionError("pull request head repository is missing")
    return {
        "number": _pr_number(value.get("number")),
        "repository": _repository(head_repository.get("full_name"), repository),
        "branch": _branch(head.get("ref")),
        "tree": _sha(head.get("sha"), "source tree"),
    }


def create_pull_request_evidence(
    *,
    repository: str,
    base_branch: str,
    pull_request_number: int,
    source_repository: str,
    source_branch: str,
    source_tree: str,
    qualified_git_tree: str,
) -> dict[str, Any]:
    if base_branch != PRODUCTION_BRANCH:
        raise ProductionPromotionError("production evidence must target 19-usl")
    return {
        "schema": SCHEMA,
        "event": "pull_request",
        "repository": repository,
        "base_branch": base_branch,
        "original_pr_number": _pr_number(pull_request_number),
        "source_repository": _repository(source_repository, repository),
        "source_branch": _branch(source_branch),
        "source_tree": _sha(source_tree, "source tree"),
        "qualified_git_tree": _sha(qualified_git_tree, "qualified Git tree"),
        "production_merge_group_tree": None,
    }


def create_merge_group_evidence(
    *,
    repository: str,
    base_branch: str,
    pull_requests: Any,
    qualified_git_tree: str,
    production_merge_group_tree: str,
) -> dict[str, Any]:
    if base_branch != PRODUCTION_BRANCH:
        raise ProductionPromotionError("production evidence must target 19-usl")
    if not isinstance(pull_requests, list) or len(pull_requests) != 1:
        raise ProductionPromotionError(
            "a production merge group must resolve to exactly one pull request"
        )
    source = _pull_request(
        pull_requests[0],
        repository=repository,
        base_branch=base_branch,
    )
    qualified = _sha(qualified_git_tree, "qualified Git tree")
    merge_group = _sha(production_merge_group_tree, "production merge-group tree")
    if qualified != merge_group:
        raise ProductionPromotionError(
            "qualified Git tree differs from the production merge-group tree"
        )
    return {
        "schema": SCHEMA,
        "event": "merge_group",
        "repository": repository,
        "base_branch": base_branch,
        "original_pr_number": source["number"],
        "source_repository": source["repository"],
        "source_branch": source["branch"],
        "source_tree": source["tree"],
        "qualified_git_tree": qualified,
        "production_merge_group_tree": merge_group,
    }


def load(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ProductionPromotionError(
            f"cannot read production promotion evidence: {path}"
        ) from error
    if not isinstance(value, dict) or set(value) != EVIDENCE_FIELDS:
        raise ProductionPromotionError("production promotion evidence fields differ")
    return value


def verify(evidence: dict[str, Any], expected: dict[str, Any]) -> dict[str, Any]:
    if evidence != expected:
        raise ProductionPromotionError(
            "production promotion evidence does not match this event"
        )
    return evidence
