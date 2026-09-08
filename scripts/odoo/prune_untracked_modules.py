"""Uninstall modules the tracked action surface does not carry.

Initializing from the tracked ``root_modules`` is never enough on its own:
Odoo also installs every ``auto_install`` module whose dependencies are then
present, and the delivered closure does not carry those. A database one module
wide of the tracked set produces thousands of spurious action entries and hides
the change under review, so the review procedure requires pruning before a
discovery diff can be trusted.

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

    modules = env["ir.module.module"]
    installed = modules.search([("state", "=", "installed")])
    installed_names = set(installed.mapped("name"))

    missing = sorted(tracked - installed_names)
    if missing:
        sys.exit(
            "Refusing to prune: the tracked closure is not fully installed.\n"
            "Missing: " + ", ".join(missing),
        )

    extra = sorted(installed_names - tracked)
    if not extra:
        print(f"Module set already matches the tracked closure ({len(tracked)}).")
        return

    print("Uninstalling modules absent from the tracked closure:")
    for name in extra:
        print(f"  {name}")
    modules.search([("name", "in", extra)]).button_immediate_uninstall()
    env.cr.commit()

    remaining = set(
        modules.search([("state", "=", "installed")]).mapped("name"),
    )
    if remaining != tracked:
        sys.exit(
            "Prune did not reach the tracked closure.\n"
            f"Unexpected: {sorted(remaining - tracked)}\n"
            f"Missing:    {sorted(tracked - remaining)}",
        )
    print(f"Module set now matches the tracked closure ({len(tracked)}).")


main()
