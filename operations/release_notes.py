"""Build user-facing release notes from the pull requests merged in a push.

A push to ``19-usl`` is a production release and a push to ``19-usl-staging``
is a staging release. The changelog lists every pull request merged in the
pushed commit range ``before..sha``. The promotion pull request itself
(``19-usl-staging`` into ``19-usl``) is excluded; the pull requests it carries
are included because their commits are part of the range.

The generator uses only ``gh api`` and the standard library. When the range
has no pull request, or when GitHub is unreachable, it writes the reviewed
fallback notes so a release never blocks on its changelog.
"""

from __future__ import annotations

import argparse
import datetime as _datetime
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = "usl-release-notes/v2"
TYPE_ORDER = ("feat", "fix", "perf", "refactor", "docs", "chore", "ci", "build", "test")
OTHER_TYPE = "other"
RELEASE_BRANCHES = frozenset({"19-usl", "19-usl-staging"})
ACTION_REQUIRED_LABEL = "action-required"
FALLBACK_COMMIT_COUNT = 20
MAXIMUM_CHANGES = 100
COMPARE_PAGE_SIZE = 250
GRAPHQL_BATCH_SIZE = 50
COMMIT = re.compile(r"[0-9a-f]{40}\Z")
REPOSITORY = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+\Z")
CONVENTIONAL_TITLE = re.compile(
    r"(?P<type>[a-z]+)(?:\((?P<scope>[^()]+)\))?!?:\s*(?P<title>\S.*)\Z",
    re.DOTALL,
)
DEFAULT_FALLBACK = ROOT / "operations" / "release-notes.json"

Api = Callable[[list[str]], Any]


class ReleaseNotesError(RuntimeError):
    """GitHub did not give a usable answer."""


def gh_api(arguments: list[str]) -> Any:
    """Run ``gh api`` and return the parsed JSON response."""
    try:
        process = subprocess.run(
            ["gh", "api", *arguments],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )
    except OSError as error:
        raise ReleaseNotesError(f"gh is not available: {error}") from error
    if process.returncode:
        detail = process.stderr.strip() or process.stdout.strip()
        raise ReleaseNotesError(f"gh api {arguments[0]} failed: {detail}")
    try:
        return json.loads(process.stdout)
    except json.JSONDecodeError as error:
        raise ReleaseNotesError(f"gh api {arguments[0]} returned invalid JSON") from error


def _valid_commit(value: str | None) -> str | None:
    """Return a usable 40-character commit, or ``None`` for an absent base."""
    if not value:
        return None
    value = value.strip().lower()
    if not COMMIT.fullmatch(value) or set(value) == {"0"}:
        return None
    return value


def range_commits(repository: str, before: str | None, sha: str, api: Api = gh_api) -> list[str]:
    """List the commits in ``before..sha`` oldest first.

    Without a usable ``before`` (first push, force push, manual dispatch) the
    last ``FALLBACK_COMMIT_COUNT`` commits reachable from ``sha`` are used.
    """
    base = _valid_commit(before)
    if base is not None and base != sha:
        commits: list[str] = []
        page = 1
        try:
            while True:
                response = api([
                    f"repos/{repository}/compare/{base}...{sha}"
                    f"?per_page={COMPARE_PAGE_SIZE}&page={page}",
                ])
                batch = [item["sha"] for item in response.get("commits", [])]
                commits.extend(batch)
                total = response.get("total_commits", len(commits))
                if not batch or len(commits) >= total:
                    break
                page += 1
        except ReleaseNotesError as error:
            # The base is gone after a force push; a shorter history is
            # better than no changelog.
            print(f"release-notes: {error}; using the last commits", file=sys.stderr)
            commits = []
        if commits:
            return commits
    response = api([f"repos/{repository}/commits?sha={sha}&per_page={FALLBACK_COMMIT_COUNT}"])
    return [item["sha"] for item in reversed(response)]


def _graphql_query(commits: list[str]) -> str:
    for commit in commits:
        if not COMMIT.fullmatch(commit):
            raise ReleaseNotesError(f"commit identity is invalid: {commit}")
    fields = (
        "number title url merged mergedAt baseRefName headRefName "
        "author { login } labels(first: 20) { nodes { name } } "
        "baseRepository { nameWithOwner }"
    )
    objects = "\n".join(
        f'c{index}: object(oid: "{commit}") {{ ... on Commit {{ oid '
        f"associatedPullRequests(first: 10) {{ nodes {{ {fields} }} }} }} }}"
        for index, commit in enumerate(commits)
    )
    return (
        "query($owner: String!, $name: String!) {\n"
        "repository(owner: $owner, name: $name) {\n"
        f"{objects}\n}}\n}}"
    )


def associated_pull_requests(repository: str, commits: list[str], api: Api = gh_api) -> list[dict]:
    """Return the pull requests associated with the commits, in commit order."""
    owner, name = repository.split("/", 1)
    seen: dict[int, dict] = {}
    for start in range(0, len(commits), GRAPHQL_BATCH_SIZE):
        batch = commits[start:start + GRAPHQL_BATCH_SIZE]
        response = api([
            "graphql",
            "-F", f"owner={owner}",
            "-F", f"name={name}",
            "-f", f"query={_graphql_query(batch)}",
        ])
        if response.get("errors"):
            raise ReleaseNotesError(
                "GitHub GraphQL errors: "
                + "; ".join(str(error.get("message")) for error in response["errors"])
            )
        objects = (response.get("data") or {}).get("repository") or {}
        for index in range(len(batch)):
            commit = objects.get(f"c{index}") or {}
            for node in (commit.get("associatedPullRequests") or {}).get("nodes") or []:
                number = node.get("number")
                if isinstance(number, int) and number not in seen:
                    seen[number] = node
    return list(seen.values())


def is_promotion(node: dict) -> bool:
    """A promotion merges one release branch into the other."""
    return (
        node.get("headRefName") in RELEASE_BRANCHES
        and node.get("baseRefName") in RELEASE_BRANCHES
    )


def select_pull_requests(repository: str, nodes: list[dict]) -> list[dict]:
    """Keep merged pull requests of this repository, without promotions."""
    selected = []
    for node in nodes:
        if not node.get("merged"):
            continue
        if (node.get("baseRepository") or {}).get("nameWithOwner") != repository:
            continue
        if is_promotion(node):
            continue
        selected.append(node)
    return selected


def parse_title(title: str) -> tuple[str, str | None, str]:
    """Split a Conventional Commit title into type, scope and subject."""
    match = CONVENTIONAL_TITLE.fullmatch(title.strip())
    if match is None:
        return OTHER_TYPE, None, title.strip()
    kind = match.group("type")
    if kind not in TYPE_ORDER:
        return OTHER_TYPE, None, title.strip()
    scope = match.group("scope")
    return kind, scope.strip() if scope else None, match.group("title").strip()


def _change(node: dict) -> dict[str, Any]:
    kind, scope, title = parse_title(str(node.get("title") or ""))
    author = (node.get("author") or {}).get("login")
    return {
        "type": kind,
        "scope": scope,
        "title": title or f"Pull request #{node['number']}",
        "number": node["number"],
        "url": node["url"],
        "author": author if isinstance(author, str) and author else None,
    }


def _type_rank(change: dict) -> int:
    return TYPE_ORDER.index(change["type"]) if change["type"] in TYPE_ORDER else len(TYPE_ORDER)


def _areas(changes: list[dict]) -> str:
    scopes = sorted({change["scope"] for change in changes if change["scope"]})
    if not scopes:
        return ""
    if len(scopes) == 1:
        return f", in {scopes[0]}"
    return ", in " + ", ".join(scopes[:-1]) + f" and {scopes[-1]}"


def build_notes(pull_requests: list[dict], *, date: _datetime.date) -> dict[str, Any] | None:
    """Build ``usl-release-notes/v2`` notes; ``None`` when nothing was merged."""
    if not pull_requests:
        return None
    ordered = sorted(
        pull_requests,
        key=lambda node: (str(node.get("mergedAt") or ""), node["number"]),
    )
    changes = sorted((_change(node) for node in ordered), key=_type_rank)
    if len(changes) > MAXIMUM_CHANGES:
        print(
            f"release-notes: keeping {MAXIMUM_CHANGES} of {len(changes)} changes",
            file=sys.stderr,
        )
        changes = changes[:MAXIMUM_CHANGES]
    action_required = [
        _change(node)["title"]
        for node in ordered
        if any(
            label.get("name") == ACTION_REQUIRED_LABEL
            for label in (node.get("labels") or {}).get("nodes") or []
        )
    ]
    count = len(changes)
    noun = "change" if count == 1 else "changes"
    return {
        "schema": SCHEMA,
        "title": f"USL Distribution release {date.isoformat()}",
        "summary": f"{count} {noun} since the previous release{_areas(changes)}.",
        "changes": changes,
        "action_required": "; ".join(action_required)[:500] or None,
    }


def generate(
    repository: str,
    before: str | None,
    sha: str,
    *,
    date: _datetime.date,
    api: Api = gh_api,
) -> dict[str, Any] | None:
    """Return the v2 notes for the pushed range, or ``None`` without changes."""
    if not REPOSITORY.fullmatch(repository):
        raise ReleaseNotesError(f"repository is invalid: {repository}")
    if not COMMIT.fullmatch(sha):
        raise ReleaseNotesError(f"release commit is invalid: {sha}")
    commits = range_commits(repository, before, sha, api)
    nodes = associated_pull_requests(repository, commits, api)
    return build_notes(select_pull_requests(repository, nodes), date=date)


def _write(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as stream:
        json.dump(value, stream, indent=2, sort_keys=True)
        stream.write("\n")
        temporary = Path(stream.name)
    os.replace(temporary, path)


def parser() -> argparse.ArgumentParser:
    command = argparse.ArgumentParser(
        prog="scripts/release-notes",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    command.add_argument("--repository", required=True, help="GitHub owner/name")
    command.add_argument("--sha", required=True, help="the pushed release commit")
    command.add_argument(
        "--before",
        default=None,
        help="the previous branch tip (github.event.before); empty or zero on a first push",
    )
    command.add_argument(
        "--date",
        default=None,
        help="release date as YYYY-MM-DD (default: today, UTC)",
    )
    command.add_argument(
        "--fallback",
        default=str(DEFAULT_FALLBACK),
        help="reviewed notes used when no pull request was merged",
    )
    command.add_argument(
        "--output",
        default=None,
        help="write the notes here instead of standard output",
    )
    command.add_argument(
        "--strict",
        action="store_true",
        help="fail instead of using the fallback when GitHub is unreachable",
    )
    return command


def main(argv: list[str] | None = None) -> int:
    arguments = parser().parse_args(argv)
    date = (
        _datetime.date.fromisoformat(arguments.date)
        if arguments.date
        else _datetime.datetime.now(_datetime.UTC).date()
    )
    notes: dict[str, Any] | None
    try:
        notes = generate(
            arguments.repository, arguments.before, arguments.sha, date=date, api=gh_api,
        )
    except ReleaseNotesError as error:
        if arguments.strict:
            print(f"release-notes: {error}", file=sys.stderr)
            return 1
        print(f"release-notes: {error}; using the fallback notes", file=sys.stderr)
        notes = None
    source = "merged pull requests"
    if notes is None:
        source = f"fallback {arguments.fallback}"
        notes = json.loads(Path(arguments.fallback).read_text(encoding="utf-8"))
    print(
        f"release-notes: {source} ({len(notes['changes'])} changes)",
        file=sys.stderr,
    )
    if arguments.output:
        _write(Path(arguments.output), notes)
        print(arguments.output)
    else:
        json.dump(notes, sys.stdout, indent=2, sort_keys=True)
        sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
