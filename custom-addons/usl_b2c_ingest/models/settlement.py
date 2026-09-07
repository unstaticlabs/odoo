"""What the channel kept, so the clearing account can come back to nothing.

A channel collects the whole price and remits it less its own commission, so
the clearing account only empties once that commission is a purchase like any
other.  It is billed as one document per channel per month, which is the grain
the channel itself invoices at, and paid out of the clearing account it was
deducted from.
"""

from calendar import monthrange
from collections import defaultdict
from decimal import Decimal

from odoo import Command, fields, models
from odoo.exceptions import UserError

from odoo.addons.usl_b2c_ingest.models.readiness import BLOCKS_SETTLEMENT


class B2cChannelFees(models.Model):
    _inherit = "b2c.channel"

    operator_partner_id = fields.Many2one(
        "res.partner",
        string="Channel operator",
        ondelete="restrict",
        help="Who runs this channel and bills its commission. Naming it is what "
             "makes the reverse charge on its services apply.",
    )
    fee_product_id = fields.Many2one(
        "product.product",
        string="Commission product",
        check_company=True,
        ondelete="restrict",
        help="The product the channel's commission is bought as, so it reaches "
             "the commissions account rather than a general expense.",
    )


class B2cImportBatchSettlement(models.Model):
    _inherit = "b2c.import.batch"

    fee_bill_ids = fields.Many2many(
        "account.move",
        "b2c_import_batch_fee_bill_rel",
        string="Commission bills",
        copy=False,
    )

    def action_bill_fees(self):
        """Bill each channel's commission for each month this drop touches."""
        for batch in self:
            if batch.state != "applied":
                raise UserError(batch.env._("Apply the drop before billing its fees."))
            batch._assert_settled(BLOCKS_SETTLEMENT, batch.env._("billing what it kept"))
            billed = batch.env["account.move"]
            for (channel, period), amount in batch._fees_by_period().items():
                billed |= batch._fee_bill(channel, period, amount)
            batch.write({"fee_bill_ids": [Command.set(billed.ids)]})
            batch.write({"report": batch._build_report()})
        return True

    def _fees_by_period(self):
        """Return what each channel kept on the sales this drop added.

        Only this drop's own sales count: a channel's commission on a sale Odoo
        already held has already been accounted for, and billing it again would
        charge it twice.
        """
        self.ensure_one()
        channels = self._channels()
        mine = self.applied_sale_ids.usl_b2c_order_id
        found = defaultdict(Decimal)
        for row in self.row_ids.filtered(
            lambda item, orders=mine: item.grain == "order"
            and item.order_id in orders
            and item.occurred_at,
        ):
            fee = self._decimal(row.values, "fee_amount")
            channel = channels.get(row.provider)
            if not fee or not channel:
                continue
            found[channel, row.occurred_at.date().replace(day=1)] += fee
        return dict(found)

    def _fee_bill(self, channel, period, amount):
        """Return the posted bill for one channel's commission in one month."""
        self.ensure_one()
        reference = f"{channel.code}:fees:{period:%Y-%m}"
        existing = self.env["account.move"].search(
            [
                ("company_id", "=", self.company_id.id),
                ("move_type", "=", "in_invoice"),
                ("ref", "=", reference),
                ("state", "!=", "cancel"),
            ],
            limit=1,
        )
        if existing:
            return existing
        if not (channel.operator_partner_id and channel.fee_product_id):
            raise UserError(
                self.env._(
                    "%(channel)s does not say who bills its commission or what it "
                    "is bought as, so what it kept cannot leave the clearing "
                    "account.",
                    channel=channel.display_name,
                ),
            )
        date = self._period_end(period)
        self._assert_period_open(date)
        bill = self.env["account.move"].create(
            {
                "move_type": "in_invoice",
                "company_id": self.company_id.id,
                "partner_id": channel.operator_partner_id.id,
                "invoice_date": date,
                "date": date,
                "ref": reference,
                "invoice_line_ids": [
                    Command.create(
                        {
                            "product_id": channel.fee_product_id.id,
                            "name": self.env._(
                                "%(channel)s commission — %(period)s",
                                channel=channel.name,
                                period=f"{period:%B %Y}",
                            ),
                            "quantity": 1,
                            "price_unit": float(amount),
                        },
                    ),
                ],
            },
        )
        bill.action_post()
        self._pay_from_clearing(channel, bill, date)
        return bill

    @staticmethod
    def _period_end(period):
        """Return the last day of the month a period starts."""
        return period.replace(day=monthrange(period.year, period.month)[1])

    def _pay_from_clearing(self, channel, bill, date):
        """Settle a bill out of the account the channel deducted it from."""
        self.ensure_one()
        journal = channel.clearing_journal(bill.currency_id)
        if not journal:
            raise UserError(
                self.env._(
                    "%(channel)s names no clearing journal for %(currency)s.",
                    channel=channel.display_name,
                    currency=bill.currency_id.name,
                ),
            )
        self.env["account.payment.register"].with_context(
            active_model="account.move",
            active_ids=bill.ids,
        ).create(
            {
                "journal_id": journal.id,
                "payment_date": date,
                "payment_method_line_id": journal.outbound_payment_method_line_ids[:1].id,
            },
        ).action_create_payments()
