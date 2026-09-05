"""Deterministic Odoo module inventory and release-to-release upgrade planning."""

from __future__ import annotations

import ast
import hashlib
import json
from collections import defaultdict, deque
from pathlib import Path
from typing import Any


INVENTORY_SCHEMA = "usl-module-inventory/v1"
PLAN_SCHEMA = "usl-module-upgrade-plan/v1"


class ModuleReleaseError(ValueError):
    """A module inventory or upgrade plan is incomplete or ambiguous."""


def _sha256_files(root: Path, paths: list[Path]) -> str:
    digest = hashlib.sha256()
    for path in sorted(paths):
        relative = path.relative_to(root).as_posix()
        digest.update(relative.encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def build_inventory(root: Path, *, require_dependencies: bool = False) -> dict[str, Any]:
    """Describe every installable custom add-on shipped in the product image."""
    addons = root / "custom-addons"
    modules: dict[str, Any] = {}
    for manifest_path in sorted(addons.glob("*/__manifest__.py")):
        name = manifest_path.parent.name
        if name == "usl_bootstrap":
            continue
        value = ast.literal_eval(manifest_path.read_text(encoding="utf-8"))
        if not value.get("installable", True):
            continue
        files = [
            path for path in manifest_path.parent.rglob("*")
            if path.is_file()
            and not {"__pycache__", ".pytest_cache"}.intersection(path.parts)
            and path.suffix not in {".pyc", ".pyo"}
        ]
        model_files = [
            path
            for path in files
            if "models" in path.relative_to(manifest_path.parent).parts
            and path.suffix == ".py"
        ]
        version = value.get("version")
        dependencies = value.get("depends", [])
        if not isinstance(version, str) or not version:
            raise ModuleReleaseError(f"module {name} has no version")
        if not isinstance(dependencies, list) or not all(
            isinstance(item, str) and item for item in dependencies
        ):
            raise ModuleReleaseError(f"module {name} has invalid dependencies")
        modules[name] = {
            "version": version,
            "dependencies": sorted(set(dependencies)),
            "source_sha256": _sha256_files(root, files),
            "stored_model_sha256": _sha256_files(root, model_files),
        }
    if not modules:
        raise ModuleReleaseError("module inventory is empty")
    if require_dependencies:
        available: set[str] = set()
        for directory in ("addons", "odoo/addons", "custom-addons", "oca-addons"):
            addons_root = root / directory
            if not addons_root.is_dir():
                continue
            for module in addons_root.iterdir():
                # OCA modules are symlinked to pinned source checkouts. A
                # direct manifest check follows those links; recursive pathlib
                # globbing does not.
                if (module / "__manifest__.py").is_file():
                    available.add(module.name)
        missing = sorted({
            dependency
            for module in modules.values()
            for dependency in module["dependencies"]
            if dependency not in available
        })
        if missing:
            raise ModuleReleaseError("module dependencies are missing: " + ", ".join(missing))
    canonical = json.dumps(modules, sort_keys=True, separators=(",", ":"))
    return {
        "schema": INVENTORY_SCHEMA,
        "modules": modules,
        "sha256": hashlib.sha256(canonical.encode()).hexdigest(),
    }


def validate_inventory(value: object) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != {"schema", "modules", "sha256"}:
        raise ModuleReleaseError("module inventory fields differ")
    if value["schema"] != INVENTORY_SCHEMA or not isinstance(value["modules"], dict):
        raise ModuleReleaseError("module inventory schema is invalid")
    for name, module in value["modules"].items():
        if not isinstance(name, str) or not name:
            raise ModuleReleaseError("module name is invalid")
        if not isinstance(module, dict) or set(module) != {
            "version",
            "dependencies",
            "source_sha256",
            "stored_model_sha256",
        }:
            raise ModuleReleaseError(f"module {name} fields differ")
        if not isinstance(module["version"], str) or not module["version"]:
            raise ModuleReleaseError(f"module {name} version is invalid")
        dependencies = module["dependencies"]
        if not isinstance(dependencies, list) or dependencies != sorted(set(dependencies)):
            raise ModuleReleaseError(f"module {name} dependencies are not canonical")
        for field in ("source_sha256", "stored_model_sha256"):
            if not isinstance(module[field], str) or len(module[field]) != 64:
                raise ModuleReleaseError(f"module {name} {field} is invalid")
    canonical = json.dumps(value["modules"], sort_keys=True, separators=(",", ":"))
    if hashlib.sha256(canonical.encode()).hexdigest() != value["sha256"]:
        raise ModuleReleaseError("module inventory digest differs")
    return value


def derive_upgrade_plan(
    active_release: dict[str, Any],
    candidate_release: dict[str, Any],
    installed_modules: set[str],
) -> dict[str, Any]:
    """Derive the exact closed module plan; never infer database state."""
    active = validate_inventory(active_release["modules"])["modules"]
    candidate_inventory = validate_inventory(candidate_release["modules"])
    candidate = candidate_inventory["modules"]
    unknown = sorted(installed_modules - set(candidate))
    if unknown:
        raise ModuleReleaseError("installed module ownership is ambiguous: " + ", ".join(unknown))

    foundation_changed = active_release["foundation"]["digest"] != candidate_release["foundation"]["digest"]
    changed: set[str] = set()
    reasons: dict[str, list[str]] = defaultdict(list)
    for name in sorted(installed_modules):
        current = active.get(name)
        wanted = candidate[name]
        if current is None:
            changed.add(name)
            reasons[name].append("newly-owned-installed-module")
            continue
        if current["source_sha256"] != wanted["source_sha256"]:
            if current["version"] == wanted["version"]:
                raise ModuleReleaseError(f"changed module {name} has no version bump")
            changed.add(name)
            reasons[name].append("source-changed")
        if current["stored_model_sha256"] != wanted["stored_model_sha256"]:
            if current["version"] == wanted["version"]:
                raise ModuleReleaseError(f"stored model changed without versioned upgrade path: {name}")
            reasons[name].append("stored-model-changed")
    # New product modules must be installed; existing optional modules that an
    # environment deliberately left uninstalled remain untouched.
    new_modules = set(candidate) - set(active) - installed_modules
    changed.update(new_modules)
    for name in new_modules:
        reasons[name].append("new-product-module")
    if foundation_changed:
        changed.update(installed_modules)
        for name in installed_modules:
            reasons[name].append("foundation-changed")

    reverse: dict[str, set[str]] = defaultdict(set)
    for name, module in candidate.items():
        for dependency in module["dependencies"]:
            reverse[dependency].add(name)
    queue = deque(changed)
    while queue:
        dependency = queue.popleft()
        for dependent in sorted(reverse.get(dependency, set()) & installed_modules):
            if dependent not in changed:
                changed.add(dependent)
                reasons[dependent].append(f"depends-on:{dependency}")
                queue.append(dependent)

    modules = sorted(changed)
    payload = {
        "schema": PLAN_SCHEMA,
        "active_release": active_release["identity"],
        "candidate_release": candidate_release["identity"],
        "candidate_module_inventory_sha256": candidate_inventory["sha256"],
        "installed_modules": sorted(installed_modules),
        "upgrade_modules": modules,
        "reasons": {name: sorted(set(reasons[name])) for name in modules},
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    payload["sha256"] = hashlib.sha256(canonical.encode()).hexdigest()
    return payload


def derive_legacy_upgrade_plan(
    candidate_release: dict[str, Any],
    installed_modules: set[str],
    *,
    active_identity: str,
) -> dict[str, Any]:
    """Create the one-time fail-safe plan from a v2 release without inventory."""
    candidate = validate_inventory(candidate_release["modules"])
    unknown = sorted(installed_modules - set(candidate["modules"]))
    if unknown:
        raise ModuleReleaseError("installed module ownership is ambiguous: " + ", ".join(unknown))
    modules = sorted(installed_modules)
    payload = {
        "schema": PLAN_SCHEMA,
        "active_release": active_identity,
        "candidate_release": candidate_release["identity"],
        "candidate_module_inventory_sha256": candidate["sha256"],
        "installed_modules": modules,
        "upgrade_modules": modules,
        "reasons": {name: ["legacy-v2-release-has-no-module-inventory"] for name in modules},
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    payload["sha256"] = hashlib.sha256(canonical.encode()).hexdigest()
    return payload


def validate_upgrade_plan(value: object) -> dict[str, Any]:
    expected = {
        "schema",
        "active_release",
        "candidate_release",
        "candidate_module_inventory_sha256",
        "installed_modules",
        "upgrade_modules",
        "reasons",
        "sha256",
    }
    if not isinstance(value, dict) or set(value) != expected or value.get("schema") != PLAN_SCHEMA:
        raise ModuleReleaseError("upgrade plan fields differ")
    for field in ("installed_modules", "upgrade_modules"):
        if value[field] != sorted(set(value[field])):
            raise ModuleReleaseError(f"upgrade plan {field} is not canonical")
    for name in set(value["upgrade_modules"]) - set(value["installed_modules"]):
        if value["reasons"].get(name) != ["new-product-module"]:
            raise ModuleReleaseError("upgrade plan contains an unapproved new module")
    body = {key: item for key, item in value.items() if key != "sha256"}
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":"))
    if hashlib.sha256(canonical.encode()).hexdigest() != value["sha256"]:
        raise ModuleReleaseError("upgrade plan digest differs")
    return value
