"""Branch-source policy for the two permanent USL release branches."""

from __future__ import annotations


class SourcePolicyError(ValueError):
    pass


def validate(*, event: str, base: str, head: str | None) -> None:
    if base not in {"19-usl", "19-usl-staging"}:
        raise SourcePolicyError(f"unsupported protected base: {base}")
    if event == "merge_group":
        return
    if event != "pull_request" or not head:
        raise SourcePolicyError("release branches require a pull request")
    if base == "19-usl" and head != "19-usl-staging" and not head.startswith("urgent/"):
        raise SourcePolicyError("19-usl accepts only 19-usl-staging or urgent/**")
    if base == "19-usl-staging" and head in {"19-usl", "19-usl-staging"}:
        raise SourcePolicyError("the staging source branch is invalid")
