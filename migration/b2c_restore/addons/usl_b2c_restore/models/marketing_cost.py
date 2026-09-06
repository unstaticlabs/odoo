"""Move marketing and prototyping spend out of cost of goods sold.

An order raised by hand at the print supplier was never a sale, so what the
supplier billed for it is a marketing and product-development cost, not the cost
of goods sold. The monthly aggregates booked all print spend to purchases of
goods, so this posts the difference as an ordinary reclassification entry — one
per month, in the open financial year only. A closed year is left exactly as it
was declared.
"""

from __future__ import annotations

from collections import defaultdict
from decimal import Decimal

from odoo import Command, fields, models
from odoo.exceptions import UserError

from .native_history.comparison import materialization_context

COST_OF_GOODS_CODE = "607000"
MARKETING_COST_CODE = "623200"
REFERENCE = "B2C marketing and prototyping reclassification"


class UslB2cMarketingCostReclassification(models.AbstractModel):
    _name = "usl.b2c.marketing.cost.reclassification"
    _description = "USL B2C Marketing Cost Reclassification"

    def _company(self):
        companies = self.env["res.company"].sudo().search(
            [("partner_id.vat", "=", "FR48983982950")], limit=2,
        )
        if len(companies) != 1:
            raise UserError("USL company identity is not unique.")
        return companies

    def _account(self, company, code):
        account = self.env["account.account"].sudo().with_company(company).search(
            [("code", "=", code)], limit=2,
        )
        if len(account) != 1:
            raise UserError(
                f"Account {code!r} must identify exactly one account, found {len(account)}.",
            )
        return account

    def _journal(self, company):
        journal = self.env["account.journal"].sudo().search(
            [("company_id", "=", company.id), ("type", "=", "general")],
            order="sequence, id",
            limit=1,
        )
        if not journal:
            raise UserError("The company has no general journal for a correction.")
        return journal

    def _monthly_marketing_cost(self, company):
        """Return what the supplier billed for marketing, by month."""
        orders = self.env["b2c.order"].sudo().search(
            [
                ("company_id", "=", company.id),
                ("business_purpose", "=", "marketing_prototype"),
            ],
        )
        by_month = defaultdict(Decimal)
        for order in orders:
            if not order.supplier_cost_amount:
                continue
            month = fields.Date.to_date(order.order_date).replace(day=1)
            by_month[month] += Decimal(str(order.supplier_cost_amount))
        return dict(by_month)

    def reclassify(self, apply=True):
        """Post one correction per open month, once."""
        company = self._company()
        lock_date = company.fiscalyear_lock_date
        cost_of_goods = self._account(company, COST_OF_GOODS_CODE)
        marketing = self._account(company, MARKETING_COST_CODE)
        journal = self._journal(company)
        report = {
            "posted": 0, "already_posted": 0, "amount": "0",
            "declined_before_lock_date": "0",
            "lock_date": lock_date and str(lock_date),
        }
        posted = Decimal("0")
        declined = Decimal("0")
        for month, amount in sorted(self._monthly_marketing_cost(company).items()):
            date = month + relativedelta_end_of_month(month)
            if lock_date and date <= lock_date:
                # The year is closed and declared. Its figures stand.
                declined += amount
                continue
            existing = self.env["account.move"].sudo().search(
                [
                    ("company_id", "=", company.id),
                    ("ref", "=", f"{REFERENCE} {month:%Y-%m}"),
                ],
                limit=1,
            )
            if existing:
                report["already_posted"] += 1
                posted += amount
                continue
            if not apply:
                posted += amount
                continue
            move = self.env["account.move"].sudo().with_context(
                **materialization_context(),
            ).create(
                {
                    "company_id": company.id,
                    "journal_id": journal.id,
                    "date": date,
                    "ref": f"{REFERENCE} {month:%Y-%m}",
                    "move_type": "entry",
                    "line_ids": [
                        Command.create(
                            {
                                "account_id": marketing.id,
                                "name": "Marketing and prototyping print orders",
                                "debit": float(amount),
                                "credit": 0.0,
                            },
                        ),
                        Command.create(
                            {
                                "account_id": cost_of_goods.id,
                                "name": "Reclassified out of cost of goods sold",
                                "debit": 0.0,
                                "credit": float(amount),
                            },
                        ),
                    ],
                },
            )
            move.action_post()
            report["posted"] += 1
            posted += amount
        report["amount"] = str(posted)
        report["declined_before_lock_date"] = str(declined)
        return report


def relativedelta_end_of_month(month):
    """Return the offset from the first of a month to its last day."""
    from calendar import monthrange
    from datetime import timedelta

    return timedelta(days=monthrange(month.year, month.month)[1] - 1)


def run_reclassification(env, apply=True):
    return env["usl.b2c.marketing.cost.reclassification"].reclassify(apply=apply)
