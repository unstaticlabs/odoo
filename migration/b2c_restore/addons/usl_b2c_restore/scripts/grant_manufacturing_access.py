"""Give the people who buy the components the Manufacturing app they work in.

The reconstruction records real manufacturing: bills of materials, production
orders and unbuilds that turn chain and padlocks into collars. Someone who can
raise a purchase order for those components needs to see what was built from
them, and today the Manufacturing menu is simply absent for them.

The module already declares that its Operator and Administrator groups imply the
Manufacturing ones, but that declaration sits in `noupdate="1"` security data, so
it reaches new databases only. It cannot be repaired by an upgrade either.
Widening what an existing group grants also widens what an Agent delegated that
group would inherit, so `usl.agent._reconcile_authority` withdraws the group from
the Agent instead, and the release then refuses to change existing memberships at
all. That is what failed both deployments on 2026-09-07.

So this grants Manufacturing to the people directly. No group changes meaning, so
no Agent is reconciled and nothing an Agent holds moves. A person who can approve
purchase orders gets the manager role, one who can only raise them gets the user
role, and anyone who already has it by any route is left alone.
"""
import json
import os

APPLY = os.environ.get("USL_B2C_MANUFACTURING_MODE", "dry_run") == "apply"

purchase_manager = env.ref("purchase.group_purchase_manager")
purchase_user = env.ref("purchase.group_purchase_user")
mrp_manager = env.ref("mrp.group_mrp_manager")
mrp_user = env.ref("mrp.group_mrp_user")

# Agents are excluded on purpose: an Agent's groups are derived from what its
# owner delegated and approved, and granting one directly here would be undone
# by the next reconciliation anyway.
people = env["res.users"].sudo().search([("share", "=", False)]).filtered(
    lambda user: not user.usl_is_ai_agent,
)

planned = []
for person in people:
    held = person.all_group_ids
    if purchase_manager in held:
        wanted = mrp_manager
    elif purchase_user in held:
        wanted = mrp_user
    else:
        continue
    if wanted in held:
        continue
    planned.append((person, wanted))

if APPLY and planned:
    for person, wanted in planned:
        person.write({"group_ids": [(4, wanted.id)]})
    env.cr.commit()
    env.invalidate_all()

granted = env["res.users"].sudo().search([("share", "=", False)]).filtered(
    lambda user: not user.usl_is_ai_agent and mrp_user in user.all_group_ids,
)
print("USL_B2C_MANUFACTURING_RESULT=" + json.dumps({
    "mode": "apply" if APPLY else "dry_run",
    "considered": len(people),
    "planned": sorted(
        f"{person.login} -> {wanted.full_name or wanted.name}"
        for person, wanted in planned
    ),
    "with_manufacturing_now": sorted(granted.mapped("login")),
    "agents_untouched": sorted(
        env["res.users"].sudo().search([("share", "=", False)])
        .filtered(lambda user: user.usl_is_ai_agent).mapped("login"),
    ),
}, sort_keys=True))
