# ruff: noqa: F821, T201

import json

from odoo.addons.usl_platform_billing_restore.models.restore import (
    canonical_digest,
)


def business_snapshot():
    platforms = (
        env["usl.platform.billing.platform"]
        .sudo()
        .with_context(active_test=False)
        .search([])
    )
    sessions = env["usl.platform.billing.session"].sudo().search([])
    payouts = env["usl.platform.billing.payout"].sudo().search([])
    moves = env["account.move"].sudo().search(
        [("platform_billing_session_id", "!=", False)],
    )
    return {
        "counts": {
            "platforms": len(platforms),
            "sessions": len(sessions),
            "payouts": len(payouts),
            "moves": len(moves),
        },
        "digest": canonical_digest(
            {
                "platforms": [
                    (
                        record.id,
                        record.name,
                        record.company_id.id,
                        record.commission_rate,
                    )
                    for record in platforms.sorted("id")
                ],
                "sessions": [
                    (
                        record.id,
                        record.name,
                        record.period_month,
                        record.state,
                    )
                    for record in sessions.sorted("id")
                ],
                "payouts": [
                    (
                        record.id,
                        record.platform_reference,
                        record.net_platform_amount,
                        record.state,
                        record.customer_invoice_id.id,
                        record.vendor_bill_id.id,
                        record.compensation_move_id.id,
                        record.bank_statement_line_id.id,
                    )
                    for record in payouts.sorted("id")
                ],
                "moves": [
                    (
                        record.id,
                        record.name,
                        record.date,
                        record.state,
                        record.platform_billing_session_id.id,
                        record.platform_billing_platform_id.id,
                        record.platform_billing_payout_ids.ids,
                    )
                    for record in moves.sorted("id")
                ],
            },
        ),
    }


module = env["ir.module.module"].sudo().search(
    [("name", "=", "usl_platform_billing_restore")],
    limit=1,
)
if not module or module.state != "installed":
    message = "usl_platform_billing_restore must be installed before finalization."
    raise RuntimeError(message)
run = env["usl.platform.billing.restore.run"].sudo().search(
    [],
    order="id desc",
    limit=1,
)
blocking = (
    run.issue_ids.filtered(
        lambda issue: issue.severity == "error" and not issue.resolved,
    )
    if run
    else True
)
if not run or run.status != "passed" or blocking:
    message = "The latest platform billing restoration has not passed cleanly."
    raise RuntimeError(message)
before = business_snapshot()
module.button_immediate_uninstall()
env.cr.commit()
after = business_snapshot()
if after != before:
    raise RuntimeError(
        f"Business history changed during finalization: {before} -> {after}.",
    )
print(
    json.dumps(
        {
            "migration_module": "uninstalled",
            "before": before,
            "after": after,
        },
        default=str,
        indent=2,
        sort_keys=True,
    ),
)
