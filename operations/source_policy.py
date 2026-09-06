"""Branch-source policy for the two permanent USL release branches."""

from __future__ import annotations

from typing import Any


class SourcePolicyError(ValueError):
    pass


def validate(
    *,
    event: str,
    base: str,
    head: str | None,
    head_repository: str | None = None,
    expected_repository: str | None = None,
) -> None:
    if base not in {"19-usl", "19-usl-staging"}:
        raise SourcePolicyError(f"unsupported protected base: {base}")
    if event == "push":
        return
    if event == "merge_group":
        raise SourcePolicyError("merge groups require their associated pull request")
    if event != "pull_request" or not head:
        raise SourcePolicyError("release branches require a pull request")
    if base == "19-usl":
        if not head_repository or not expected_repository:
            raise SourcePolicyError("production pull requests require repository identity")
        if head_repository != expected_repository:
            raise SourcePolicyError("production pull requests must originate in the protected repository")
        if head != "19-usl-staging" and not head.startswith("urgent/"):
            raise SourcePolicyError("19-usl accepts only 19-usl-staging or urgent/**")
    if base == "19-usl-staging" and head in {"19-usl", "19-usl-staging"}:
        raise SourcePolicyError("the staging source branch is invalid")


def validate_merge_group(
    *,
    base: str,
    pull_requests: Any,
    expected_repository: str,
) -> dict[str, Any]:
    """Validate the one pull request represented by a production merge group."""
    if base != "19-usl":
        raise SourcePolicyError("merge-group source policy is production-only")
    if not isinstance(pull_requests, list) or len(pull_requests) != 1:
        raise SourcePolicyError("a production merge group must contain exactly one pull request")
    pull_request = pull_requests[0]
    if not isinstance(pull_request, dict):
        raise SourcePolicyError("merge-group pull request is invalid")
    base_value = pull_request.get("base") or {}
    head_value = pull_request.get("head") or {}
    head_repository = head_value.get("repo") or {}
    if base_value.get("ref") != base:
        raise SourcePolicyError("merge-group pull request does not target production")
    validate(
        event="pull_request",
        base=base,
        head=head_value.get("ref"),
        head_repository=head_repository.get("full_name"),
        expected_repository=expected_repository,
    )
    return pull_request
