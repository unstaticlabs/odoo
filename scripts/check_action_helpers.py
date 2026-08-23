"""Require concise helpers on consequential custom product actions."""

from __future__ import annotations

import re
import sys
from pathlib import Path
from xml.etree import ElementTree


ACTION_PREFIXES = (
    "accept",
    "apply",
    "approve",
    "begin",
    "cancel",
    "capture",
    "check",
    "close",
    "confirm",
    "create",
    "dismiss",
    "enable",
    "generate",
    "install",
    "link",
    "load",
    "mark",
    "permanently",
    "post",
    "prefill",
    "reconcile",
    "record",
    "refresh",
    "reject",
    "request",
    "reset",
    "restore",
    "return",
    "reverse",
    "save",
    "start",
    "submit",
    "supersede",
    "suspend",
    "sync",
    "test",
    "unlink",
    "update",
    "use",
)
QWEB_HANDLERS = {
    "confirm",
    "dismissOperation",
    "linkSelected",
    "markReviewed",
    "moveToTrash",
    "restoreFromTrash",
    "retryDocumentDetail",
    "save",
    "unlinkCurrent",
}
HELP_ATTRIBUTES = {"help", "title", "t-att-title", "t-attf-title"}


def _parse(path: Path) -> ElementTree.ElementTree | None:
    try:
        return ElementTree.parse(path)
    except ElementTree.ParseError:
        return None


def _central_accounting_guidance(addons: Path) -> set[str]:
    path = (
        addons
        / "rebuild_account_migration"
        / "views"
        / "accounting_action_guidance_views.xml"
    )
    tree = _parse(path)
    if tree is None:
        return set()
    actions: set[str] = set()
    for xpath in tree.iter("xpath"):
        if not any(
            attribute.get("name") in HELP_ATTRIBUTES
            for attribute in xpath.findall("attribute")
        ):
            continue
        actions.update(
            re.findall(r"@name=['\"]([^'\"]+)", xpath.get("expr", "")),
        )
    return actions


def _has_helper(button: ElementTree.Element) -> bool:
    return any(button.get(attribute) for attribute in HELP_ATTRIBUTES)


def _is_consequential_action(name: str) -> bool:
    return any(name.startswith(f"action_{prefix}") for prefix in ACTION_PREFIXES)


def main() -> int:
    addons = Path(sys.argv[1] if len(sys.argv) > 1 else "custom-addons")
    guided_accounting_actions = _central_accounting_guidance(addons)
    failures: list[str] = []

    for path in sorted(addons.glob("*/**/*.xml")):
        tree = _parse(path)
        if tree is None:
            continue
        module = path.relative_to(addons).parts[0]
        is_qweb = "static/src" in path.as_posix()
        for button in tree.iter("button"):
            if _has_helper(button):
                continue
            name = button.get("name", "")
            if (
                button.get("type") in {"object", "action"}
                and _is_consequential_action(name)
            ):
                if (
                    module == "rebuild_account_migration"
                    and name in guided_accounting_actions
                ):
                    continue
                failures.append(f"{path}: {name or button.get('string', 'button')}")
                continue
            handler = button.get("t-on-click", "").strip()
            if is_qweb and handler in QWEB_HANDLERS:
                failures.append(f"{path}: {handler}")

    if failures:
        print("Action helper validation failed:")
        for failure in failures:
            print(f"- {failure}")
        return 1
    print("Action helper validation passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
