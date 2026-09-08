"""Refuse a discovery database whose module set is not the tracked closure.

`enforce_product_module_scope.py` removes the reviewed optional auto-installs
from a hardcoded list. This checks the outcome against the delivered
`action_surface.json` instead, so a module that starts auto-installing, or a
root gaining a dependency, is reported here rather than surfacing later as
thousands of spurious action entries that hide the change under review.

Run through ``odoo shell``; ``env`` is provided by that shell.
"""

import json
import pathlib
import sys

SURFACE = pathlib.Path(
    "/mnt/custom-addons/usl_access_control/policy/action_surface.json",
)


def main():
    if not SURFACE.is_file():
        sys.exit(f"Tracked action surface not found at {SURFACE}.")
    tracked = {
        module["name"]
        for module in json.loads(SURFACE.read_text(encoding="utf-8"))["modules"]
    }
    installed = set(
        env["ir.module.module"]  # noqa: F821
        .sudo()
        .search([("state", "=", "installed")])
        .mapped("name"),
    )

    missing = sorted(tracked - installed)
    unexpected = sorted(installed - tracked)
    if missing or unexpected:
        sys.exit(
            "Module set does not match the tracked closure; discovery here "
            "would produce a misleading diff.\n"
            f"Missing:    {missing}\n"
            f"Unexpected: {unexpected}",
        )
    print(f"Module set matches the tracked closure ({len(tracked)} modules).")


main()
