from odoo import SUPERUSER_ID, api


def migrate(cr, _version):
    cr.execute(
        """
        SELECT p.id,
               p.bank_statement_line_id,
               p.bank_received_amount,
               p.net_platform_amount,
               p.platform_currency_id,
               p.bank_currency_id,
               p.bank_match_score,
               p.bank_amount_difference,
               p.bank_date_difference,
               p.bank_detection_reason
          FROM usl_platform_billing_payout p
         WHERE p.bank_statement_line_id IS NOT NULL
           AND p.bank_received_amount > 0
        """,
    )
    rows = cr.dictfetchall()
    if not rows:
        return
    env = api.Environment(cr, SUPERUSER_ID, {})
    Allocation = env["usl.platform.billing.bank.allocation"].with_context(
        tracking_disable=True,
    )
    currencies = env["res.currency"].browse(
        {
            row["platform_currency_id"]
            for row in rows
        }
        | {
            row["bank_currency_id"]
            for row in rows
        },
    )
    currency_by_id = {currency.id: currency for currency in currencies}
    for row in rows:
        existing = Allocation.search(
            [
                ("payout_id", "=", row["id"]),
                (
                    "bank_statement_line_id",
                    "=",
                    row["bank_statement_line_id"],
                ),
            ],
            limit=1,
        )
        if existing:
            continue
        platform_currency = currency_by_id[row["platform_currency_id"]]
        if row["platform_currency_id"] == row["bank_currency_id"]:
            payout_amount = min(
                row["net_platform_amount"],
                row["bank_received_amount"],
            )
        else:
            payout_amount = row["net_platform_amount"]
        Allocation._action_create(
            {
                "payout_id": row["id"],
                "bank_statement_line_id": row["bank_statement_line_id"],
                "bank_amount": row["bank_received_amount"],
                "payout_amount": platform_currency.round(payout_amount),
                "score": row["bank_match_score"] or 0,
                "amount_difference": row["bank_amount_difference"] or 0.0,
                "date_difference": row["bank_date_difference"] or 0,
                "detection_reason": row["bank_detection_reason"],
            },
        )
