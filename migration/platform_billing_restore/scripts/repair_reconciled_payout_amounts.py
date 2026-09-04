# ruff: noqa: F821, T201

import json
import os

from odoo.tools import float_compare, float_is_zero


apply_changes = os.getenv("USL_APPLY_RECONCILED_PAYOUT_REPAIR", "0") == "1"
expected_count = os.getenv("USL_EXPECTED_RECONCILED_PAYOUT_REPAIR_COUNT")
Payout = env["usl.platform.billing.payout"].sudo()
repairs = []


def ledger_control():
    env.cr.execute(
        """
            SELECT count(*),
                   COALESCE(sum(line.debit), 0),
                   COALESCE(sum(line.credit), 0)
              FROM account_move_line line
              JOIN account_move move ON move.id = line.move_id
             WHERE move.state = 'posted'
        """,
    )
    row_count, debit, credit = env.cr.fetchone()
    return {
        "posted_line_count": row_count,
        "posted_debit": str(debit),
        "posted_credit": str(credit),
    }


ledger_before = ledger_control()

for payout in Payout.search([("bank_match_status", "=", "partial")], order="id"):
    allocations = payout.bank_allocation_ids
    invoice = payout.customer_invoice_id
    bill = payout.vendor_bill_id
    currency = payout.platform_currency_id
    if (
        len(allocations) != 1
        or not invoice
        or not bill
        or currency != payout.company_currency_id
        or allocations.state != "reconciled"
        or invoice.payment_state not in {"paid", "reversed"}
        or bill.payment_state not in {"paid", "reversed"}
        or not float_is_zero(invoice.amount_residual, precision_rounding=currency.rounding)
        or not float_is_zero(bill.amount_residual, precision_rounding=currency.rounding)
    ):
        continue

    missing_amount = currency.round(
        payout.net_platform_amount - allocations.payout_amount,
    )
    if float_compare(missing_amount, 0.0, precision_rounding=currency.rounding) <= 0:
        continue

    receivable_lines = invoice.line_ids.filtered(
        lambda line: line.account_id.account_type == "asset_receivable",
    )
    if not receivable_lines or not all(receivable_lines.mapped("reconciled")):
        continue
    excluded_moves = payout.compensation_move_id | allocations.bank_statement_line_id.move_id
    other_settlement = 0.0
    for line in receivable_lines:
        for partial in line.matched_credit_ids:
            if partial.credit_move_id.move_id not in excluded_moves:
                other_settlement += partial.amount
        for partial in line.matched_debit_ids:
            if partial.debit_move_id.move_id not in excluded_moves:
                other_settlement += partial.amount
    other_settlement = currency.round(other_settlement)
    if currency.compare_amounts(other_settlement, missing_amount):
        continue

    repairs.append(
        {
            "payout_id": payout.id,
            "platform_reference": payout.platform_reference,
            "allocation_id": allocations.id,
            "bank_amount": allocations.bank_amount,
            "old_payout_amount": allocations.payout_amount,
            "new_payout_amount": payout.net_platform_amount,
            "native_adjustment_amount": other_settlement,
        },
    )
    if apply_changes:
        allocations.write({"payout_amount": payout.net_platform_amount})
        payout.session_id._refresh_state()

if expected_count is not None and len(repairs) != int(expected_count):
    raise RuntimeError(
        f"Expected {expected_count} repair candidate(s), found {len(repairs)}.",
    )

if apply_changes:
    repaired_payouts = Payout.browse([item["payout_id"] for item in repairs])
    invalid = repaired_payouts.filtered(
        lambda payout: (
            payout.bank_match_status != "reconciled"
            or payout.state != "paid"
            or not float_is_zero(
                payout.remaining_platform_amount,
                precision_rounding=payout.platform_currency_id.rounding,
            )
        ),
    )
    if invalid:
        raise RuntimeError(
            f"Repaired payouts did not reach Paid/Reconciled: {invalid.ids}.",
        )

ledger_after = ledger_control()
if ledger_after != ledger_before:
    raise RuntimeError("The platform status repair changed the posted ledger.")
if apply_changes:
    env.cr.commit()

print(
    json.dumps(
        {
            "applied": apply_changes,
            "ledger_after": ledger_after,
            "ledger_before": ledger_before,
            "repair_count": len(repairs),
            "repairs": repairs,
        },
        indent=2,
        sort_keys=True,
    ),
)
