#!/usr/bin/env python3
"""Warn, at the moment of an edit, about invariants that live in another file.

Every rule here was earned by a real incident in which a change looked complete,
passed the gates a pull request runs, and failed hours later in a release — because
the constraint that made it wrong lived somewhere the edited file never mentions.

The hook only ever prints. It cannot block an edit and it must never fail a
session: any error exits quietly.
"""
from __future__ import annotations

import fnmatch
import json
import os
import re
import sys
import tempfile

#: ``(name, path globs, content pattern or None, message)``.
#:
#: A rule fires when the edited path matches a glob and, if a content pattern is
#: given, the file now contains it.  Keep this list short.  A warning that fires
#: on an ordinary edit teaches the reader to skip all of them.
RULES: list[tuple[str, tuple[str, ...], str | None, str]] = [
    (
        "preservation",
        ("custom-addons/*/static/description/icon.*", "operations/upgrade_preservation.py"),
        None,
        "A release refuses any change to a row that already existed in "
        "ir_attachment, mail_message, project_project or res_groups_users_rel "
        "(operations/upgrade_preservation.py). Application icons live in "
        "ir_attachment as ir.ui.menu.web_icon_data and are exempt via EXCLUDED_ROWS; "
        "nothing else is. A refused release rolls back, the rollback restores the old "
        "value, and the next attempt fails identically \u2014 it never self-heals. Note the "
        "fingerprint and the restore controls read the same tables with DIFFERENT "
        "semantics: the controls count rows, so narrowing their scope breaks them.",
    ),
    (
        "noupdate",
        ("custom-addons/*/security/*.xml",),
        r"implied_ids",
        "Security XML is noupdate=1: editing implied_ids reaches a fresh install and "
        "no existing database. Add custom-addons/<module>/migrations/"
        "saas~19.3.<version>/post-*.py writing res_groups_implied_rel directly, "
        "guarded by `if not version: return`. A group-implication change can also trip "
        "the res_groups_users_rel preservation fingerprint through "
        "usl.agent._reconcile_authority.",
    ),
    (
        "action-risk",
        ("custom-addons/*.py", "custom-addons/*.xml", "custom-addons/*.js"),
        None,
        "Any custom-addons source edit invalidates the sealed action surface. "
        "`make action-risk-inventory` fails until you refresh it from a RUNTIME "
        "discovery (static-only discover drops ~350 route and server actions). Changed "
        "digests need review values in action_policy.json reviewed_digests.",
    ),
    (
        "new-module",
        ("custom-addons/*/__manifest__.py",),
        None,  # additionally gated on the manifest being untracked
        "A new addon ships in the image immediately but NO pull-request gate sees it, "
        "so the branch merges green and the release branch then fails at 'Clean install "
        "and repeated upgrade'. Name the module in all four hard-coded product "
        "registries (operations/release_identity.py, scripts/odoo/release_identity.py "
        "and the two others) and classify its actions.",
    ),
    (
        "branch-contract",
        (".github/workflows/*.yml", ".github/workflows/*.yaml", "operations/*.py"),
        r"19-usl-staging|head_ref|promotion|deployment",
        "Production accepts a promotion from 19-usl-staging OR urgent/** "
        "(operations/source_policy.py:33, docs/operations/continuous-delivery.md). An "
        "urgent fix reaches staging AFTER it merges to production, so it can never "
        "carry staging evidence. Before adding a gate: enumerate every input class it "
        "will judge and prove one real historical example of each \u2014 a gate that "
        "silently welds the emergency path shut has no test that fails.",
    ),
]


#: A shell command only counts as an edit when it also says it writes.  The list
#: is deliberately generous: this hook prints, it never blocks, so a false
#: positive costs a line of text and a false negative costs an incident.
WRITE_INTENT = (
    ">", "sed -i", "tee ", "cp ", "mv ", "patch ", "truncate ", "install ",
    "open(", "write_text", "writelines", "dd ",
)
PATH_TOKEN = re.compile(r"[\w./~-]*[\w-]/[\w./-]+")


def _paths_written_by_shell(command: str) -> list[str]:
    """Paths a shell command plausibly wrote, for warning purposes only."""
    if not any(marker in command for marker in WRITE_INTENT):
        return []
    seen, out = set(), []
    for token in PATH_TOKEN.findall(command):
        token = token.strip("\"'`,;:()")
        if token and token not in seen:
            seen.add(token)
            out.append(token)
    return out[:40]


STATE_PREFIX = "usl-edit-tripwires-"


def _fired_before(session: str, names: frozenset[str]) -> frozenset[str]:
    """Return the subset of ``names`` not yet reported in this session.

    A rule repeats nothing: the second edit to the same area is the author acting
    on the warning, not a new reason to see it.
    """
    if not session:
        return names
    safe = re.sub(r"[^A-Za-z0-9_-]", "", session)[:64] or "anon"
    path = os.path.join(tempfile.gettempdir(), f"{STATE_PREFIX}{safe}.json")
    try:
        with open(path) as handle:
            seen = set(json.load(handle))
    except Exception:
        seen = set()
    fresh = names - seen
    if fresh:
        try:
            with open(path, "w") as handle:
                json.dump(sorted(seen | fresh), handle)
        except Exception:
            pass
    return fresh


def _matches(path: str, globs: tuple[str, ...]) -> bool:
    return any(fnmatch.fnmatch(path, glob) or fnmatch.fnmatch(path, f"*/{glob}") for glob in globs)


def _is_untracked(root: str, path: str) -> bool:
    """A manifest git has never seen is a module no gate has ever classified."""
    import subprocess

    try:
        done = subprocess.run(
            ["git", "ls-files", "--error-unmatch", path],
            cwd=root, capture_output=True, timeout=5,
        )
        return done.returncode != 0
    except Exception:
        return False


def main() -> int:
    payload = json.load(sys.stdin)
    tool = payload.get("tool_name")
    tool_input = payload.get("tool_input") or {}
    if tool == "Bash":
        candidates = _paths_written_by_shell(tool_input.get("command") or "")
    elif tool in {"Edit", "Write", "NotebookEdit", "MultiEdit"}:
        candidates = [tool_input.get("file_path") or ""]
    else:
        return 0
    candidates = [path for path in candidates if path]
    if not candidates:
        return 0

    root = payload.get("cwd") or os.getcwd()
    hits: dict[str, str] = {}
    touched: list[str] = []
    for absolute in candidates:
        relative = os.path.relpath(absolute, root) if os.path.isabs(absolute) else absolute
        if relative.startswith(".."):
            relative = absolute
        if not any(_matches(relative, globs) for _, globs, _, _ in RULES):
            continue

        body = ""
        try:
            with open(os.path.join(root, relative), encoding="utf-8", errors="ignore") as handle:
                body = handle.read(400_000)
        except Exception:
            pass

        matched = False
        for name, globs, pattern, message in RULES:
            if not _matches(relative, globs):
                continue
            if name == "new-module" and not _is_untracked(root, relative):
                continue
            if pattern and not re.search(pattern, body):
                continue
            hits[name] = message
            matched = True
        if matched:
            touched.append(relative)

    fresh = _fired_before(payload.get("session_id", ""), frozenset(hits))
    if not fresh:
        return 0

    lines = [f"Repository invariants touching {', '.join(touched)}:"]
    lines += [f"- {hits[name]}" for name in sorted(fresh)]
    lines.append(
        "These are warnings, not blocks. Each names a check that runs later and "
        "more expensively than this edit."
    )
    json.dump(
        {"hookSpecificOutput": {
            "hookEventName": "PostToolUse",
            "additionalContext": "\n".join(lines),
        }},
        sys.stdout,
    )
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        sys.exit(0)
