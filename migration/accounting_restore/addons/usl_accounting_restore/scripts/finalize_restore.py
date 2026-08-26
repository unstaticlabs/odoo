# ruff: noqa: EM101, F821, T201

import json


def accounting_snapshot():
    posted_lines = env["account.move.line"].sudo().search([
        ("move_id.state", "=", "posted"),
    ])
    return {
        "companies": env["res.company"].with_context(active_test=False).sudo().search_count([]),
        "partners": env["res.partner"].with_context(active_test=False).sudo().search_count([]),
        "accounts": env["account.account"].with_context(active_test=False).sudo().search_count([]),
        "journals": env["account.journal"].with_context(active_test=False).sudo().search_count([]),
        "moves": env["account.move"].sudo().search_count([]),
        "move_lines": env["account.move.line"].sudo().search_count([]),
        "posted_debit": round(sum(posted_lines.mapped("debit")), 2),
        "posted_credit": round(sum(posted_lines.mapped("credit")), 2),
        "posted_balance": round(sum(posted_lines.mapped("balance")), 2),
        "payments": env["account.payment"].sudo().search_count([]),
        "bank_statement_lines": env["account.bank.statement.line"].sudo().search_count([]),
        "partial_reconciliations": env["account.partial.reconcile"].sudo().search_count([]),
        "full_reconciliations": env["account.full.reconcile"].sudo().search_count([]),
        "currency_rates": env["res.currency.rate"].sudo().search_count([]),
        "analytic_lines": env["account.analytic.line"].sudo().search_count([]),
        "assets": env["account.asset"].with_context(active_test=False).sudo().search_count([]),
        "asset_lines": env["account.asset.line"].sudo().search_count([]),
        "deferrals": env["rebuild.account.deferral"].sudo().search_count([]),
        "deferral_lines": env["rebuild.account.deferral.line"].sudo().search_count([]),
        "expenses": env["hr.expense"].sudo().search_count([]),
        "attachments": env["ir.attachment"].sudo().search_count([]),
    }


module = env["ir.module.module"].sudo().search(
    [("name", "=", "usl_accounting_restore")],
    limit=1,
)
if not module or module.state != "installed":
    raise RuntimeError(
        "usl_accounting_restore must be installed before finalization.",
    )

latest_run = env["rebuild.account.import.run"].sudo().search([], limit=1)
if latest_run and latest_run.status != "passed":
    raise RuntimeError(
        "The latest Accounting restoration must pass before finalization.",
    )
if latest_run:
    blocking = env["rebuild.account.discrepancy"].sudo().search_count([
        ("severity", "in", ["P0", "P1"]),
        ("status", "in", ["open", "investigating"]),
    ])
    if blocking:
        raise RuntimeError(
            f"{blocking} P0/P1 restoration discrepancy record(s) remain open.",
        )

before = accounting_snapshot()
run_evidence = (
    {
        "name": latest_run.name,
        "status": latest_run.status,
        "source_snapshot_id": latest_run.source_snapshot_id,
        "source_dump_sha256": latest_run.source_dump_sha256,
        "finished_at": str(latest_run.finished_at or ""),
        "statistics": latest_run.statistics_json or {},
    }
    if latest_run
    else {
        "status": "not_run",
        "reason": "empty temporary module reinstall cleanup",
    }
)
module.button_immediate_uninstall()
env.cr.commit()
after = accounting_snapshot()
if after != before:
    raise RuntimeError(
        f"Accounting facts changed during finalization: {before} -> {after}.",
    )

print(json.dumps({
    "migration_module": "uninstalled",
    "business_snapshot_before": before,
    "business_snapshot_after": after,
    "restore_evidence": run_evidence,
}, indent=2, sort_keys=True))
