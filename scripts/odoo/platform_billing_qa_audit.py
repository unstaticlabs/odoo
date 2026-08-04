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
    "usl.platform.billing.bank.allocation",
    "usl.platform.billing.bank.import.wizard",
    "usl.platform.billing.bank.import.wizard.line",
    "usl.platform.billing.bank.import.wizard.payout.line",
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
total_counts = {
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
historical_sessions = env["usl.platform.billing.session"].sudo().search(
    [("name", "in", ("Avril 2026", "Mai 2026", "Juin 2026"))],
)
historical_payouts = historical_sessions.payout_ids
historical_platforms = historical_payouts.platform_id.with_context(
    active_test=False,
)
historical_moves = env["account.move"].sudo().search(
    [("platform_billing_session_id", "in", historical_sessions.ids)],
)
historical_allocations = historical_payouts.bank_allocation_ids
historical_bank_journal_counts = {
    journal.display_name: len(
        historical_allocations.filtered(
            lambda allocation, journal=journal: (
                allocation.bank_statement_line_id.journal_id == journal
            ),
        ),
    )
    for journal in historical_allocations.bank_statement_line_id.journal_id
}
historical_counts = {
    "platforms": len(historical_platforms),
    "sessions": len(historical_sessions),
    "payouts": len(historical_payouts),
    "moves": len(historical_moves),
    "bank_allocations": len(historical_allocations),
}
expected_counts["bank_allocations"] = 31
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
env.cr.execute(
    """
    SELECT COUNT(*)
      FROM (
            SELECT payout_id, bank_statement_line_id
              FROM usl_platform_billing_bank_allocation
          GROUP BY payout_id, bank_statement_line_id
            HAVING COUNT(*) > 1
           ) duplicate_pairs
    """,
)
duplicate_allocation_pairs = env.cr.fetchone()[0]
env.cr.execute(
    """
    SELECT COUNT(*)
      FROM (
            SELECT allocation.bank_statement_line_id
              FROM usl_platform_billing_bank_allocation allocation
              JOIN account_bank_statement_line bank_line
                ON bank_line.id = allocation.bank_statement_line_id
          GROUP BY allocation.bank_statement_line_id, bank_line.amount
            HAVING SUM(allocation.bank_amount) > bank_line.amount + 0.000001
           ) overallocated_bank_lines
    """,
)
overallocated_bank_lines = env.cr.fetchone()[0]
env.cr.execute(
    """
    SELECT COUNT(*)
      FROM (
            SELECT allocation.payout_id
              FROM usl_platform_billing_bank_allocation allocation
              JOIN usl_platform_billing_payout payout
                ON payout.id = allocation.payout_id
          GROUP BY allocation.payout_id, payout.net_platform_amount
            HAVING SUM(allocation.payout_amount)
                   > payout.net_platform_amount + 0.000001
           ) overallocated_payouts
    """,
)
overallocated_payouts = env.cr.fetchone()[0]
env.cr.execute(
    """
    SELECT COUNT(*)
      FROM (
            SELECT debit_move_id, credit_move_id
              FROM account_partial_reconcile
          GROUP BY debit_move_id, credit_move_id
            HAVING COUNT(*) > 1
           ) duplicate_reconciliations
    """,
)
duplicate_reconciliations = env.cr.fetchone()[0]
module = env["ir.module.module"].sudo().search(
    [("name", "=", "usl_platform_billing")],
    limit=1,
)
demo_platform = env["usl.platform.billing.platform"].sudo().search(
    [("name", "=", "QA DEMO Platform EUR")],
    limit=1,
)
open_import_sessions = env["usl.platform.billing.session"].sudo().search(
    [
        ("name", "ilike", "QA DEMO — Import a new payout%"),
        ("state", "in", ("draft", "ready")),
    ],
).filtered(lambda session: not session.payout_ids)
open_import_lines = env["account.bank.statement.line"].sudo().search(
    [
        ("payment_ref", "ilike", "QA DEMO QA-IMPORT-80%"),
        ("amount", ">", 0),
        ("is_reconciled", "=", False),
    ],
).filtered(
    lambda line: not env["usl.platform.billing.bank.allocation"].sudo().search_count(
        [("bank_statement_line_id", "=", line.id)],
    ),
)
demo_account_mapping = {
    "revenue": demo_platform.revenue_account_id.code or "",
    "commission": demo_platform.commission_account_id.code or "",
    "receivable": demo_platform.customer_receivable_account_id.code or "",
    "payable": demo_platform.supplier_payable_account_id.code or "",
    "bank": demo_platform.bank_account_id.code or "",
}
platform_account_mappings = [
    {
        "platform": platform.name,
        "revenue": platform.revenue_account_id.display_name,
        "commission": platform.commission_account_id.display_name,
        "receivable": platform.customer_receivable_account_id.display_name,
        "payable": platform.supplier_payable_account_id.display_name,
        "sale_journal": platform.sale_journal_id.display_name,
        "purchase_journal": platform.purchase_journal_id.display_name,
        "bank_journal": platform.bank_journal_id.display_name,
        "bank_account": platform.bank_account_id.display_name,
        "bank_account_type": platform.bank_account_id.account_type,
    }
    for platform in (historical_platforms | demo_platform)
]
bank_journal_mappings = [
    {
        "journal": journal.display_name,
        "code": journal.code,
        "account": journal.default_account_id.display_name,
        "account_type": journal.default_account_id.account_type,
        "active": journal.active,
    }
    for journal in env["account.journal"].sudo().with_context(active_test=False).search(
        [("company_id", "=", demo_platform.company_id.id), ("type", "=", "bank")],
    )
]
admin = env.ref("base.user_admin")
live_flags = {
    "USL_EINVOICE_LIVE_ENABLED": os.getenv("USL_EINVOICE_LIVE_ENABLED", "0"),
    "USL_EREPORTING_LIVE_ENABLED": os.getenv(
        "USL_EREPORTING_LIVE_ENABLED",
        "0",
    ),
}
errors = []
if historical_counts != expected_counts:
    errors.append(f"historical counts differ: {historical_counts}")
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
if duplicate_allocation_pairs:
    errors.append(f"{duplicate_allocation_pairs} duplicate allocation pairs exist")
if overallocated_bank_lines:
    errors.append(f"{overallocated_bank_lines} bank transactions are overallocated")
if overallocated_payouts:
    errors.append(f"{overallocated_payouts} payouts are overallocated")
if duplicate_reconciliations:
    errors.append(f"{duplicate_reconciliations} duplicate reconciliations exist")
if module.state != "installed":
    errors.append(f"product module state is {module.state}")
if not demo_platform:
    errors.append("the QA demo platform is missing")
if not open_import_sessions or not open_import_lines:
    errors.append("no clean bank-import QA demo remains")
elif not demo_account_mapping["revenue"].startswith("706"):
    errors.append(f"QA revenue account is not service revenue: {demo_account_mapping}")
if demo_platform and not demo_account_mapping["commission"].startswith("6222"):
    errors.append(f"QA commission account is not sales commission: {demo_account_mapping}")
if demo_platform and not demo_account_mapping["bank"].startswith("512"):
    errors.append(f"QA bank account is not a current bank account: {demo_account_mapping}")
if (
    demo_platform
    and historical_bank_journal_counts
    and demo_platform.bank_journal_id.display_name
    not in historical_bank_journal_counts
):
    errors.append(
        "QA bank journal does not match the restored payout history: "
        + demo_platform.bank_journal_id.display_name,
    )
if demo_platform and demo_platform._account_configuration_errors():
    errors.append(
        "QA platform account configuration is invalid: "
        + "; ".join(demo_platform._account_configuration_errors()),
    )
if set(live_flags.values()) != {"0"}:
    errors.append(f"live electronic-invoice flags are unsafe: {live_flags}")

summary = {
    "access_groups": sorted(accesses.group_id.mapped("display_name")),
    "admin_role": groups["manager"].display_name,
    "demo_account_mapping": demo_account_mapping,
    "open_import_demo": {
        "sessions": len(open_import_sessions),
        "bank_transactions": len(open_import_lines),
    },
    "bank_journal_mappings": bank_journal_mappings,
    "platform_account_mappings": platform_account_mappings,
    "allocation_invariants": {
        "duplicate_pairs": duplicate_allocation_pairs,
        "overallocated_bank_lines": overallocated_bank_lines,
        "overallocated_payouts": overallocated_payouts,
    },
    "duplicate_reconciliations": duplicate_reconciliations,
    "historical_counts": historical_counts,
    "historical_bank_journal_counts": historical_bank_journal_counts,
    "total_counts": total_counts,
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
