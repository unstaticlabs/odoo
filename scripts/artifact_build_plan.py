#!/usr/bin/env python3
"""Select repository-owned runtime builds from an explicit prior release SHA."""

# ruff: noqa: EM101, T201 - release CLI reports concise fail-closed decisions.

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from continuous_operations_contracts import (  # noqa: E402
    ARTIFACT_BUILD_PLAN_SCHEMA,
    ARTIFACT_ROLES,
    validate_commit,
)

ALL_ROLES = set(ARTIFACT_ROLES)
FOUNDATION_PATHS = {
    ".dockerignore",
    ".github/workflows/product-image.yml",
    "operations/contracts/distribution-release-v3.schema.json",
    "scripts/artifact_build_plan.py",
    "scripts/continuous_operations_contracts.py",
    "scripts/distribution_release.py",
    "scripts/prior_release_input.py",
    "scripts/release_identity.py",
}
ROLE_PREFIXES = {
    "odoo_distribution": (
        "Dockerfile",
        "MANIFEST.in",
        "addons/",
        "agent/action-risk.json",
        "custom-addons/",
        "debian/",
        "docker/constraints.txt",
        "docker/entrypoint.sh",
        "docker/odoo.conf.template",
        "docs/users/",
        "oca-patches/",
        "odoo/",
        "odoo-bin",
        "requirements.txt",
        "scripts/sync-oca-addons",
        "setup.py",
        "setup/",
    ),
    "operations_tool": (
        "deploy/continuous-operations/",
        "deploy/odoo-backup/",
        "docker/operations.Dockerfile",
        "operations/contracts/",
        "scripts/continuous-operations",
        "scripts/continuous_operations_compose.py",
        "scripts/deployment_run.py",
        "scripts/odoo_backup.py",
        "scripts/production_cohort.py",
        "scripts/retention_policy.py",
        "scripts/upgrade_plan.py",
    ),
    "paperless_overlay": ("deploy/documents/paperless-ngx/",),
    "document_renderer": ("services/usl-document-renderer",),
    "native_sign_dss": (
        "services/usl-sign-dss/",
        "addons/web/static/fonts/sign/NotoSans-Reg.ttf",
    ),
}
NON_RUNTIME_PREFIXES = (
    ".agents/",
    ".claude/",
    ".cursor/",
    ".devcontainer/",
    ".github/",
    ".vscode/",
    "README.md",
    "agent-skills/",
    "agent/",
    "compose",
    "doc/",
    "docs/",
    "migration/",
    "scripts/agent/",
    "scripts/tests/",
)


class BuildPlanError(ValueError):
    """The build ownership decision cannot be proven."""


def _matches(path: str, prefix: str) -> bool:
    return path == prefix or path.startswith(prefix)


def classify(changed_paths: list[str], *, from_commit: str | None, to_commit: str) -> dict:
    paths = sorted(set(changed_paths))
    if from_commit is None:
        return _result(None, to_commit, paths, ALL_ROLES, "prior_release_unavailable")
    foundation = sorted(path for path in paths if path in FOUNDATION_PATHS)
    if foundation:
        return _result(from_commit, to_commit, paths, ALL_ROLES, "foundation_or_ownership_changed")
    build: set[str] = set()
    ambiguous: list[str] = []
    for path in paths:
        owners = {
            role
            for role, prefixes in ROLE_PREFIXES.items()
            if any(_matches(path, prefix) for prefix in prefixes)
        }
        if owners:
            build.update(owners)
        elif not any(_matches(path, prefix) for prefix in NON_RUNTIME_PREFIXES):
            ambiguous.append(path)
    if ambiguous:
        return _result(from_commit, to_commit, paths, ALL_ROLES, "ambiguous_paths")
    reason = "changed_runtime_inputs" if build else "no_runtime_inputs_changed"
    return _result(from_commit, to_commit, paths, build, reason)


def _result(
    from_commit: str | None,
    to_commit: str,
    paths: list[str],
    build: set[str],
    reason: str,
) -> dict:
    return {
        "schema": ARTIFACT_BUILD_PLAN_SCHEMA,
        "from_commit_sha": from_commit,
        "to_commit_sha": to_commit,
        "mode": "build_all" if build == ALL_ROLES else "selective",
        "reason": reason,
        "changed_paths": paths,
        "build_roles": sorted(build),
        "reuse_roles": sorted(ALL_ROLES - build),
    }


def _git(*arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *arguments],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )


def plan(from_commit: str | None, to_commit: str) -> dict:
    validate_commit(to_commit, "to_commit")
    if from_commit is None:
        return classify([], from_commit=None, to_commit=to_commit)
    validate_commit(from_commit, "from_commit")
    ancestry = _git("merge-base", "--is-ancestor", from_commit, to_commit)
    if ancestry.returncode:
        return _result(from_commit, to_commit, [], ALL_ROLES, "prior_commit_unreachable")
    changed = _git("diff", "--name-only", "--no-renames", f"{from_commit}..{to_commit}")
    if changed.returncode:
        return _result(from_commit, to_commit, [], ALL_ROLES, "git_diff_unavailable")
    paths = [line for line in changed.stdout.splitlines() if line]
    return classify(paths, from_commit=from_commit, to_commit=to_commit)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--from-commit")
    parser.add_argument("--to-commit", required=True)
    parser.add_argument("--output", required=True)
    arguments = parser.parse_args()
    try:
        value = plan(arguments.from_commit, arguments.to_commit)
    except (BuildPlanError, ValueError) as error:
        print(f"artifact build plan: {error}", file=sys.stderr)
        return 2
    output = Path(arguments.output)
    output.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
