"""Verify that SHA-pinned GitHub Actions state their real version.

A pinned action reads ``owner/repo@<40-character commit> # v1.2.3``. Git and
GitHub Actions ignore the trailing comment entirely, so nothing but this check
keeps it honest, and a reviewer auditing the supply chain reads the comment
rather than resolving the hash by hand. A comment that names the wrong release
is therefore worse than no comment at all.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

SHA = re.compile(r"\A[0-9a-f]{40}\Z")
USES = re.compile(
    r"^\s*(?:-\s+)?uses:\s*(?P<reference>\S+)(?:\s+#\s*(?P<comment>.+?))?\s*$"
)

TagResolver = Callable[[str], dict[str, str]]


class ActionPinError(ValueError):
    pass


@dataclass(frozen=True)
class Pin:
    """One ``uses:`` line pinned to a commit, with the version it claims."""

    workflow: str
    line: int
    action: str
    sha: str
    comment: str

    def __str__(self) -> str:
        return f"{self.workflow}:{self.line} {self.action}@{self.sha} # {self.comment}"


def _components(version: str) -> tuple[int, ...] | None:
    """The numeric parts of ``v1.2.3``, or None when it is not that shape."""
    if not version.startswith("v"):
        return None
    parts = version[1:].split(".")
    if not all(part.isdigit() for part in parts):
        return None
    return tuple(int(part) for part in parts)


def comment_matches(comment: str, tags: Iterable[str]) -> bool:
    """Whether ``comment`` names the release line the pinned commit belongs to.

    A comment may be less precise than the tag it refers to: ``# v4`` on the
    commit tagged ``v4.2.2`` is accurate and stays accurate after upstream moves
    the ``v4`` tag onward, which an exact-match rule would wrongly reject. A
    comment may never be *wrong*, so ``# v6.0.0`` on a ``v7.0.0`` commit fails.
    """
    tags = list(tags)
    if comment in tags:
        return True
    wanted = _components(comment)
    if wanted is None:
        return False
    return any(
        found is not None and found[: len(wanted)] == wanted
        for found in (_components(tag) for tag in tags)
    )


def scan(text: str, *, workflow: str) -> tuple[list[Pin], list[str]]:
    """Pins declared in one workflow, and the faults visible without a network."""
    pins: list[Pin] = []
    problems: list[str] = []
    for number, line in enumerate(text.splitlines(), start=1):
        match = USES.match(line)
        if not match:
            continue
        reference = match["reference"]
        if reference.startswith((".", "/")):
            continue
        action, separator, revision = reference.partition("@")
        where = f"{workflow}:{number} {reference}"
        if not separator or not SHA.fullmatch(revision):
            problems.append(f"{where}: pin to a full 40-character commit, not a mutable ref")
            continue
        if not match["comment"]:
            problems.append(f"{where}: state the pinned version in a trailing comment")
            continue
        pins.append(Pin(workflow, number, action, revision, match["comment"].strip()))
    return pins, problems


def verify(pins: Iterable[Pin], resolver: TagResolver) -> list[str]:
    """Faults that only the action's own tags can reveal."""
    pins = list(pins)
    tags_by_action = {pin.action: None for pin in pins}
    for action in tags_by_action:
        tags_by_action[action] = resolver(action)
    problems = []
    for pin in pins:
        tags = [name for name, sha in tags_by_action[pin.action].items() if sha == pin.sha]
        if not tags:
            problems.append(f"{pin}: no tag of {pin.action} resolves to this commit")
        elif not comment_matches(pin.comment, tags):
            problems.append(f"{pin}: the pinned commit is {', '.join(sorted(tags))}")
    return problems


def github_tags(action: str) -> dict[str, str]:
    """Every tag of an action repository, resolved to the commit it names."""
    process = subprocess.run(
        ["gh", "api", f"repos/{action}/tags", "--paginate",
         "--jq", ".[] | [.name, .commit.sha] | @tsv"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=False,
    )
    if process.returncode:
        raise ActionPinError(f"cannot read the tags of {action}: {process.stderr.strip()}")
    tags = dict(line.split("\t", 1) for line in process.stdout.splitlines() if "\t" in line)
    if not tags:
        raise ActionPinError(f"{action} reports no tags")
    return tags


def audit(directory: Path, resolver: TagResolver) -> tuple[list[Pin], list[str]]:
    """Every pin under ``directory``, with everything wrong about them."""
    pins: list[Pin] = []
    problems: list[str] = []
    workflows = sorted(set(directory.glob("*.yml")) | set(directory.glob("*.yaml")))
    if not workflows:
        raise ActionPinError(f"no workflow to audit in {directory}")
    for workflow in workflows:
        found, faults = scan(
            workflow.read_text(encoding="utf-8"),
            workflow=str(workflow.relative_to(directory.parents[1])),
        )
        pins.extend(found)
        problems.extend(faults)
    return pins, problems + verify(pins, resolver)
