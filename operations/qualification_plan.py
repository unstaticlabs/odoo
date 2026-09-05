"""Select the exact owned Odoo module suites affected by a source change."""

from __future__ import annotations

from collections import defaultdict, deque
from pathlib import PurePosixPath
from typing import Any, Iterable

from operations.module_release import validate_inventory


FOUNDATION_PATHS = (
    "addons/",
    "odoo/",
    "oca-addons/",
    "oca-src/",
    "requirements.txt",
    "docker/constraints.txt",
)


def affected_modules(paths: Iterable[str], inventory: dict[str, Any]) -> list[str]:
    """Return changed owned modules and their owned reverse dependencies.

    Foundation changes conservatively select every shipped product module. Files
    outside the Odoo runtime select none. Inputs are repository-relative paths.
    """
    modules = validate_inventory(inventory)["modules"]
    changed: set[str] = set()
    foundation_changed = False

    for raw_path in paths:
        text = PurePosixPath(raw_path.strip()).as_posix()
        if not text or text == ".":
            continue
        if any(text == prefix or text.startswith(prefix) for prefix in FOUNDATION_PATHS):
            foundation_changed = True
            continue
        parts = PurePosixPath(text).parts
        if len(parts) >= 2 and parts[0] == "custom-addons" and parts[1] in modules:
            changed.add(parts[1])

    if foundation_changed:
        return sorted(modules)

    reverse: dict[str, set[str]] = defaultdict(set)
    for name, module in modules.items():
        for dependency in module["dependencies"]:
            if dependency in modules:
                reverse[dependency].add(name)

    queue = deque(sorted(changed))
    while queue:
        dependency = queue.popleft()
        for dependent in sorted(reverse.get(dependency, set())):
            if dependent not in changed:
                changed.add(dependent)
                queue.append(dependent)
    return sorted(changed)


def all_modules(inventory: dict[str, Any]) -> list[str]:
    """Return every shipped owned module for exhaustive qualification."""
    return sorted(validate_inventory(inventory)["modules"])
