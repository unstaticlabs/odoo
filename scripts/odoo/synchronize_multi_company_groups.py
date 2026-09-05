# ruff: noqa: F821, T201

"""Align Odoo's native multi-company UI group with allowed company scopes."""

import json

from odoo import Command


group = env.ref("base.group_multi_company")
users = env["res.users"].sudo().with_context(active_test=False).search([
    ("active", "=", True),
    ("share", "=", False),
])
eligible = users.filtered(lambda user: len(user.company_ids) > 1)
missing = eligible.filtered(lambda user: not user.has_group("base.group_multi_company"))
if missing:
    missing.write({"group_ids": [Command.link(group.id)]})

unresolved = eligible.filtered(lambda user: not user.has_group("base.group_multi_company"))
if unresolved:
    raise RuntimeError(
        "Multi-company UI group remains missing for: "
        + ", ".join(unresolved.mapped("login")),
    )

env.cr.commit()
print(json.dumps({
    "eligible": eligible.mapped("login"),
    "granted": missing.mapped("login"),
    "status": "passed",
}, indent=2, sort_keys=True))
