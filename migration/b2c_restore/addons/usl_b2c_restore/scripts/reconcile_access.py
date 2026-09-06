"""Make the declared access model effective for the people who hold it.

A group's ``implied_ids`` are a promise: hold this group, hold those too. Odoo
keeps that promise when a user is added to the group, so a user who was added
before an implication was declared never receives it. The reconstruction adds
Manufacturing to the B2C groups, which is exactly such an implication, and the
same gap had already left the administrators without Sales, Purchase, Inventory
and Accounting.

This grants each internal user the transitive closure of what their own groups
already declare. It invents no access: every group added here is one the user
was already entitled to through a group they hold.
"""
import json
import os

from odoo.addons.usl_b2c_restore.models.native_history.comparison import (
    materialization_context,
)

# Same convention as the other stages: nothing is granted unless asked.
apply = os.environ.get("USL_B2C_ACCESS_MODE", "dry_run") == "apply"

users = env["res.users"].sudo().search([("share", "=", False), ("active", "=", True)])
granted = {}
for user in users:
    direct = user.group_ids
    closure = direct
    frontier = direct
    while frontier:
        frontier = frontier.implied_ids - closure
        closure |= frontier
    missing = closure - direct
    if not missing:
        continue
    external = missing.get_external_id()
    granted[user.login] = sorted(
        external.get(group.id) or f"id:{group.id}" for group in missing
    )
    if apply:
        user.sudo().with_context(**materialization_context()).write(
            {"group_ids": [(4, group.id) for group in missing]},
        )

if apply:
    env.cr.commit()
    env.invalidate_all()

# Prove it: nobody should still be short of what their groups declare.
remaining = 0
for user in env["res.users"].sudo().search([("share", "=", False), ("active", "=", True)]):
    direct = user.group_ids
    closure = direct
    frontier = direct
    while frontier:
        frontier = frontier.implied_ids - closure
        closure |= frontier
    remaining += len(closure - direct)

print("USL_B2C_ACCESS_RESULT=" + json.dumps({
    "mode": "apply" if apply else "dry_run",
    "users_considered": len(users),
    "users_changed": len(granted),
    "groups_granted": {login: len(names) for login, names in sorted(granted.items())},
    "unsatisfied_after": remaining,
}, sort_keys=True))
