"""Supply-chain checks for the pinned external add-on from mattermost-odoo.

``deploy/mattermost-odoo/release.json`` pins one commit of the public
repository unstaticlabs/mattermost-odoo. GitHub also serves a commit that
exists only in a fork through the URL of the parent repository, so a checkout
by SHA accepts a fork-only commit. ``verify_reachable`` refuses a pin unless
the commit is in the history of ``main`` of the pinned repository.
``review`` writes the module change between two pins as Markdown, so the
reviewer of a pin change sees the code and not only a new SHA.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Any

SCHEMA = "usl-mattermost-odoo-release-v1"
PIN_PATH = "deploy/mattermost-odoo/release.json"
MAIN_BRANCH = "main"
COMMIT = re.compile(r"[0-9a-f]{40}\Z")
REPOSITORY = re.compile(r"https://github\.com/unstaticlabs/[A-Za-z0-9._-]+\.git\Z")
MODULE = re.compile(r"usl_[a-z0-9_]+\Z")
MODULE_PATH = re.compile(r"[A-Za-z0-9._/-]+\Z")
EXPECTED_KEYS = {"schema", "repository", "commit", "module", "path"}
# GitHub refuses a job summary above 1 MiB. Keep room for the other steps.
MAX_DIFF_BYTES = 600_000
# Git knows the empty tree without an object in the repository.
EMPTY_TREE = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"


class MattermostOdooReleaseError(ValueError):
    """Raised when the Mattermost Odoo pin is not a reviewed main commit."""


def parse_pin(text: str) -> dict[str, str]:
    try:
        value = json.loads(text)
    except json.JSONDecodeError as error:
        raise MattermostOdooReleaseError("the Mattermost Odoo pin is not valid JSON") from error
    if not isinstance(value, dict) or set(value) != EXPECTED_KEYS:
        raise MattermostOdooReleaseError("the Mattermost Odoo pin has unexpected fields")
    if not all(isinstance(item, str) for item in value.values()):
        raise MattermostOdooReleaseError("the Mattermost Odoo pin values must be strings")
    if value["schema"] != SCHEMA:
        raise MattermostOdooReleaseError("unsupported Mattermost Odoo pin schema")
    if not REPOSITORY.fullmatch(value["repository"]):
        raise MattermostOdooReleaseError(
            "the Mattermost Odoo repository must be a public unstaticlabs URL"
        )
    if not COMMIT.fullmatch(value["commit"]):
        raise MattermostOdooReleaseError("the Mattermost Odoo commit must be a full SHA-1")
    if not MODULE.fullmatch(value["module"]):
        raise MattermostOdooReleaseError("the Mattermost Odoo module name is invalid")
    if not MODULE_PATH.fullmatch(value["path"]) or ".." in value["path"].split("/"):
        raise MattermostOdooReleaseError("the Mattermost Odoo module path is invalid")
    return value


def load_pin(repository_root: Path) -> dict[str, str]:
    path = repository_root / PIN_PATH
    try:
        return parse_pin(path.read_text(encoding="utf-8"))
    except OSError as error:
        raise MattermostOdooReleaseError(f"cannot read the Mattermost Odoo pin: {path}") from error


def load_pin_at(repository_root: Path, revision: str) -> dict[str, str] | None:
    """Return the pin at ``revision`` of this repository, or None if it has none."""
    process = subprocess.run(
        ("git", "-C", str(repository_root), "show", f"{revision}:{PIN_PATH}"),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if process.returncode:
        exists = subprocess.run(
            ("git", "-C", str(repository_root), "cat-file", "-e", f"{revision}^{{commit}}"),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        if exists.returncode:
            raise MattermostOdooReleaseError(f"the base revision {revision} is not available")
        return None
    return parse_pin(process.stdout)


def _git(
    directory: Path,
    *arguments: str,
    check: bool = True,
    lazy_fetch: bool = True,
) -> subprocess.CompletedProcess[str]:
    environment = None
    if not lazy_fetch:
        environment = {**os.environ, "GIT_NO_LAZY_FETCH": "1"}
    process = subprocess.run(
        ("git", "-C", str(directory), *arguments),
        env=environment,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if check and process.returncode:
        raise MattermostOdooReleaseError(
            process.stderr.strip() or f"git {' '.join(arguments)} failed"
        )
    return process


def _has_commit(directory: Path, commit: str) -> bool:
    # A partial clone fetches a missing object by SHA on demand, and GitHub
    # serves a fork-only commit that way. Look only at the local objects.
    process = _git(directory, "cat-file", "-t", commit, check=False, lazy_fetch=False)
    return process.returncode == 0 and process.stdout.strip() == "commit"


def fetch_main(remote: str, directory: Path) -> None:
    """Fetch only ``main`` of ``remote`` into a new bare repository.

    The fetch asks for the branch, never for a SHA, so the result holds only
    commits that the branch history contains. Blobs come on demand.
    """
    _git(directory, "init", "--quiet", "--bare")
    _git(directory, "remote", "add", "origin", remote)
    _git(directory, "config", "remote.origin.promisor", "true")
    _git(directory, "config", "remote.origin.partialclonefilter", "blob:none")
    _git(
        directory,
        "fetch",
        "--quiet",
        "--no-tags",
        "--filter=blob:none",
        "origin",
        f"+refs/heads/{MAIN_BRANCH}:refs/remotes/origin/{MAIN_BRANCH}",
    )


def verify_reachable(remote: str, commit: str, directory: Path) -> str:
    """Refuse ``commit`` unless it is an ancestor of ``main`` of ``remote``.

    Return the commit of ``main`` that the check used.
    """
    if not COMMIT.fullmatch(commit):
        raise MattermostOdooReleaseError("the Mattermost Odoo commit must be a full SHA-1")
    fetch_main(remote, directory)
    main = _git(directory, "rev-parse", f"refs/remotes/origin/{MAIN_BRANCH}").stdout.strip()
    if not _has_commit(directory, commit):
        raise MattermostOdooReleaseError(
            f"the pinned commit {commit} is not in the history of {MAIN_BRANCH} "
            f"of {remote}; pin a commit that is merged to {MAIN_BRANCH}"
        )
    # This is the gate. It holds also on a Git that ignores GIT_NO_LAZY_FETCH.
    ancestor = _git(
        directory,
        "merge-base",
        "--is-ancestor",
        commit,
        f"refs/remotes/origin/{MAIN_BRANCH}",
        check=False,
        lazy_fetch=False,
    )
    if ancestor.returncode == 1:
        raise MattermostOdooReleaseError(
            f"the pinned commit {commit} is not an ancestor of {MAIN_BRANCH} of {remote}"
        )
    if ancestor.returncode:
        raise MattermostOdooReleaseError(ancestor.stderr.strip() or "git merge-base failed")
    return main


def _fence(text: str) -> str:
    longest = max((len(run) for run in re.findall(r"`+", text)), default=0)
    return "`" * max(3, longest + 1)


def _code_block(text: str, language: str = "") -> list[str]:
    fence = _fence(text)
    return [f"{fence}{language}", text.rstrip("\n"), fence]


def _web_url(repository: str) -> str:
    return repository.removesuffix(".git")


def review(
    old: dict[str, str] | None,
    new: dict[str, str],
    directory: Path,
    *,
    remote: str | None = None,
    max_diff_bytes: int = MAX_DIFF_BYTES,
) -> str:
    """Return Markdown that shows the module change from ``old`` to ``new``.

    Call it after ``verify_reachable`` filled ``directory``. A missing old
    commit is fetched by SHA: an old pin can predate this check.
    """
    module_path = new["path"]
    if old is not None and old["commit"] == new["commit"] and old["path"] == module_path:
        return ""
    lines = ["## Mattermost Odoo pin change", ""]
    url = _web_url(new["repository"])
    if old is None:
        lines.append(f"This change adds the pin `{new['commit']}` of {url}.")
        stat_arguments = (EMPTY_TREE, f"{new['commit']}:{module_path}")
        diff_arguments = stat_arguments
    else:
        if old["repository"] != new["repository"]:
            lines.append(
                f"**The repository changes** from {old['repository']} to {new['repository']}."
            )
        if old["path"] != module_path:
            lines.append(f"**The module path changes** from `{old['path']}` to `{module_path}`.")
        if not _has_commit(directory, old["commit"]):
            _git(
                directory,
                "fetch",
                "--quiet",
                "--no-tags",
                "--filter=blob:none",
                remote or old["repository"],
                old["commit"],
            )
        lines.append(
            f"The pin moves from `{old['commit']}` to `{new['commit']}`: "
            f"[compare on GitHub]({url}/compare/{old['commit']}...{new['commit']})."
        )
        count = _git(
            directory, "rev-list", "--count", f"{old['commit']}..{new['commit']}"
        ).stdout.strip()
        behind = _git(
            directory, "rev-list", "--count", f"{new['commit']}..{old['commit']}"
        ).stdout.strip()
        lines.append(f"Commits that the new pin adds: {count}. Commits that it drops: {behind}.")
        if old["path"] == module_path:
            stat_arguments = (old["commit"], new["commit"], "--", module_path)
            diff_arguments = stat_arguments
        else:
            stat_arguments = (f"{old['commit']}:{old['path']}", f"{new['commit']}:{module_path}")
            diff_arguments = stat_arguments
    stat = _git(directory, "diff", "--stat=120", *stat_arguments).stdout
    diff = _git(directory, "diff", "--no-color", *diff_arguments).stdout
    lines += ["", f"### `git diff --stat` of `{module_path}`", ""]
    lines += _code_block(stat or "no change\n")
    lines += ["", f"### Diff of `{module_path}`", ""]
    encoded = diff.encode("utf-8")
    if len(encoded) > max_diff_bytes:
        diff = encoded[:max_diff_bytes].decode("utf-8", errors="ignore")
        lines.append(
            f"The diff has {len(encoded)} bytes. This summary shows the first "
            f"{max_diff_bytes} bytes. Read the full diff on GitHub."
        )
        lines.append("")
    lines += _code_block(diff or "no change\n", "diff")
    return "\n".join(lines) + "\n"


def summary(pin: dict[str, Any], main: str) -> dict[str, str]:
    return {
        "schema": "usl-mattermost-odoo-pin-check-v1",
        "status": "passed",
        "repository": pin["repository"],
        "commit": pin["commit"],
        "branch": MAIN_BRANCH,
        "branch_commit": main,
    }


def temporary_directory() -> tempfile.TemporaryDirectory[str]:
    return tempfile.TemporaryDirectory(prefix="usl-mattermost-odoo-")
