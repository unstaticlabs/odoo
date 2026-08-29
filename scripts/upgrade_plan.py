#!/usr/bin/env python3
"""Derive a fail-closed Odoo product-module upgrade plan between Git SHAs."""

# ruff: noqa: EM101, T201 - release CLI reports concise literal failures.

from __future__ import annotations

import argparse
import ast
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from continuous_operations_contracts import (  # noqa: E402
    UPGRADE_SCHEMA,
    validate_commit,
)
from release_identity import PRODUCT_MODULES  # noqa: E402

FOUNDATION_PREFIXES = (
    "odoo/",
    "addons/",
    "requirements.txt",
    "docker/constraints.txt",
    "Dockerfile",
    "scripts/sync-oca-addons",
    "oca-addons/",
    "oca-src/",
    "custom-addons/usl_access_control/policy/",
)


class UpgradePlanError(RuntimeError):
    """The requested upgrade plan cannot be derived safely."""


def run_git(*arguments: str) -> str:
    result = subprocess.run(
        ["git", *arguments], cwd=ROOT, check=False, capture_output=True, text=True,
    )
    if result.returncode:
        raise UpgradePlanError((result.stderr or result.stdout).strip())
    return result.stdout


def manifest_at(commit: str, module: str) -> dict[str, Any]:
    raw = run_git("show", f"{commit}:custom-addons/{module}/__manifest__.py")
    value = ast.literal_eval(raw)
    if not isinstance(value, dict):
        raise UpgradePlanError(f"{module} manifest is not an object at {commit}")
    return value


def dependency_closure(commit: str, changed: set[str]) -> set[str]:
    dependencies: dict[str, set[str]] = {}
    for module in PRODUCT_MODULES:
        manifest = manifest_at(commit, module)
        raw = manifest.get("depends", [])
        if not isinstance(raw, list) or not all(isinstance(item, str) for item in raw):
            raise UpgradePlanError(f"{module} has an ambiguous dependency list")
        dependencies[module] = set(raw) & PRODUCT_MODULES
    closure = set(changed)
    while True:
        dependents = {
            module for module, depends in dependencies.items() if depends & closure
        }
        expanded = closure | dependents
        if expanded == closure:
            return closure
        closure = expanded


def full_plan(
    to_commit: str, *, from_commit: str | None, reason: str, paths: list[str],
) -> dict:
    return {
        "schema": UPGRADE_SCHEMA,
        "from_commit_sha": from_commit,
        "to_commit_sha": to_commit,
        "mode": "full_fallback",
        "reason": reason,
        "changed_modules": [],
        "upgrade_modules": sorted(PRODUCT_MODULES),
        "foundation_paths": sorted(set(paths)),
    }


def plan(from_commit: str | None, to_commit: str) -> dict:
    validate_commit(to_commit, "to_commit")
    if from_commit is None:
        return full_plan(
            to_commit,
            from_commit=None,
            reason="prior_release_unavailable",
            paths=[],
        )
    validate_commit(from_commit, "from_commit")
    try:
        run_git("cat-file", "-e", f"{from_commit}^{{commit}}")
        run_git("cat-file", "-e", f"{to_commit}^{{commit}}")
        run_git("merge-base", "--is-ancestor", from_commit, to_commit)
        changed_paths = sorted(
            path
            for path in run_git(
                "diff", "--name-only", from_commit, to_commit,
            ).splitlines()
            if path
        )
    except UpgradePlanError:
        return full_plan(
            to_commit,
            from_commit=from_commit,
            reason="prior_release_unavailable",
            paths=[],
        )
    foundations = [
        path
        for path in changed_paths
        if any(
            path == prefix or path.startswith(prefix) for prefix in FOUNDATION_PREFIXES
        )
    ]
    ownership = [
        path
        for path in changed_paths
        if path.startswith("custom-addons/")
        and (
            path.endswith(("/__manifest__.py", "/hooks.py"))
            or "/security/" in path
        )
    ]
    if foundations or ownership:
        return full_plan(
            to_commit,
            from_commit=from_commit,
            reason="foundation_or_ownership_changed",
            paths=foundations + ownership,
        )
    changed_modules: set[str] = set()
    ambiguous: list[str] = []
    for path in changed_paths:
        if not path.startswith("custom-addons/"):
            continue
        parts = path.split("/")
        if len(parts) < 3 or parts[1] not in PRODUCT_MODULES:
            ambiguous.append(path)
        else:
            changed_modules.add(parts[1])
    if ambiguous:
        return full_plan(
            to_commit,
            from_commit=from_commit,
            reason="ambiguous_product_change",
            paths=ambiguous,
        )
    if not changed_modules:
        return {
            "schema": UPGRADE_SCHEMA,
            "from_commit_sha": from_commit,
            "to_commit_sha": to_commit,
            "mode": "none",
            "reason": "no_product_module_change",
            "changed_modules": [],
            "upgrade_modules": [],
            "foundation_paths": [],
        }
    try:
        closure = dependency_closure(to_commit, changed_modules)
    except (UpgradePlanError, SyntaxError, ValueError):
        return full_plan(
            to_commit,
            from_commit=from_commit,
            reason="dependency_graph_ambiguous",
            paths=[],
        )
    return {
        "schema": UPGRADE_SCHEMA,
        "from_commit_sha": from_commit,
        "to_commit_sha": to_commit,
        "mode": "dependency_closure",
        "reason": "changed_product_module_dependency_closure",
        "changed_modules": sorted(changed_modules),
        "upgrade_modules": sorted(closure),
        "foundation_paths": [],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--from-commit")
    parser.add_argument("--to-commit", required=True)
    parser.add_argument("--output", required=True)
    arguments = parser.parse_args()
    try:
        value = plan(arguments.from_commit, arguments.to_commit)
    except (UpgradePlanError, ValueError) as error:
        print(f"upgrade plan: {error}", file=sys.stderr)
        return 2
    output = Path(arguments.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
