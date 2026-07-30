# ruff: noqa: F821, T201

import json
import os

from odoo.tools import float_is_zero

expected_counts = {
    "platforms": 4,
    "sessions": 3,
    "payouts": 31,
    "moves": 51,
}
groups = {
    role: env.ref(f"usl_platform_billing.group_platform_billing_{role}")
    for role in ("reader", "operator", "manager")
}
app_groups = env["res.groups"].browse([group.id for group in groups.values()])
product_models = {
    "usl.platform.billing.platform",
    "usl.platform.billing.session",
    "usl.platform.billing.payout",
    "usl.platform.billing.bank.import.wizard",
    "usl.platform.billing.bank.import.wizard.line",
    "usl.platform.billing.post.confirm.wizard",
}
model_ids = env["ir.model"].sudo().search(
    [("model", "in", sorted(product_models))],
)
accesses = env["ir.model.access"].sudo().search(
    [("model_id", "in", model_ids.ids)],
)
unexpected_accesses = accesses.filtered(
    lambda access: access.group_id not in groups.values(),
)
account_groups = (
    env.ref("account.group_account_readonly")
    | env.ref("account.group_account_basic")
    | env.ref("account.group_account_user")
    | env.ref("account.group_account_manager")
)
reverse_role_implications = account_groups.filtered(
    lambda group: bool(group.all_implied_ids & app_groups),
)
counts = {
    "platforms": env["usl.platform.billing.platform"]
    .sudo()
    .with_context(active_test=False)
    .search_count([]),
    "sessions": env["usl.platform.billing.session"].sudo().search_count([]),
    "payouts": env["usl.platform.billing.payout"].sudo().search_count([]),
    "moves": env["account.move"]
    .sudo()
    .search_count([("platform_billing_session_id", "!=", False)]),
}
moves = env["account.move"].sudo().search(
    [("platform_billing_session_id", "!=", False)],
)
incomplete_moves = moves.filtered(
    lambda move: not (
        move.platform_billing_platform_id
        and move.platform_billing_payout_ids
    ),
)
unbalanced_moves = moves.filtered(
    lambda move: (
        move.state == "posted"
        and not float_is_zero(
            sum(move.line_ids.mapped("balance")),
            precision_rounding=move.company_currency_id.rounding,
        )
    ),
)
env.cr.execute(
    """
    SELECT COUNT(*)
      FROM pg_constraint
     WHERE conname =
           'usl_platform_billing_payout_bank_statement_line_unique'
    """,
)
obsolete_bank_constraints = env.cr.fetchone()[0]
module = env["ir.module.module"].sudo().search(
    [("name", "=", "usl_platform_billing")],
    limit=1,
)
admin = env.ref("base.user_admin")
live_flags = {
    "USL_EINVOICE_LIVE_ENABLED": os.getenv("USL_EINVOICE_LIVE_ENABLED", "0"),
    "USL_EREPORTING_LIVE_ENABLED": os.getenv(
        "USL_EREPORTING_LIVE_ENABLED",
        "0",
    ),
}
errors = []
if counts != expected_counts:
    errors.append(f"historical counts differ: {counts}")
if unexpected_accesses:
    errors.append(
        "non-app ACLs remain: "
        + ", ".join(unexpected_accesses.mapped("name")),
    )
if reverse_role_implications:
    errors.append(
        "Accounting groups imply app roles: "
        + ", ".join(reverse_role_implications.mapped("display_name")),
    )
if groups["manager"] not in admin.group_ids:
    errors.append("the local administrator lacks the app administrator role")
if incomplete_moves:
    errors.append(f"{len(incomplete_moves)} move links are incomplete")
if unbalanced_moves:
    errors.append(f"{len(unbalanced_moves)} linked posted moves are unbalanced")
if obsolete_bank_constraints:
    errors.append("the obsolete one-bank-line uniqueness constraint remains")
if module.state != "installed":
    errors.append(f"product module state is {module.state}")
if set(live_flags.values()) != {"0"}:
    errors.append(f"live electronic-invoice flags are unsafe: {live_flags}")

summary = {
    "access_groups": sorted(accesses.group_id.mapped("display_name")),
    "admin_role": groups["manager"].display_name,
    "counts": counts,
    "incomplete_move_links": len(incomplete_moves),
    "live_flags": live_flags,
    "module_state": module.state,
    "module_version": module.installed_version,
    "obsolete_bank_constraints": obsolete_bank_constraints,
    "unexpected_accesses": len(unexpected_accesses),
    "unbalanced_posted_moves": len(unbalanced_moves),
}
print(json.dumps(summary, indent=2, sort_keys=True))
if errors:
    raise RuntimeError("; ".join(errors))
