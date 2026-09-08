"""Invoicing a drop, and settling it against the channel that collected it.

An invoice is what makes a sale's VAT belong to the country it shipped to, and
French law requires one for a distance sale to another Member State whether or
not anyone asks.  The customer never pays the shop directly: the channel
collects and later remits, so the invoice is settled into the channel's own
clearing account and it is the payout that meets the bank.
"""

from odoo import Command, fields, models
from odoo.exceptions import UserError

from odoo.addons.usl_b2c_ingest.models.readiness import BLOCKS_INVOICING


class B2cChannelClearing(models.Model):
    _inherit = "b2c.channel"

    clearing_journal_ids = fields.Many2many(
        "account.journal",
        string="Clearing journals",
        domain="[('type', 'in', ('bank', 'cash')), ('company_id', '=', company_id)]",
        check_company=True,
        help="Where this channel's receipts are held until it pays them out, one "
             "per currency it collects in.",
    )

    def clearing_journal(self, currency):
        """Return where receipts in one currency are held for this channel."""
        self.ensure_one()
        exact = self.clearing_journal_ids.filtered(
            lambda journal, c=currency: journal.currency_id == c,
        )
        if exact:
            return exact[0]
        return self.clearing_journal_ids.filtered(
            lambda journal, c=currency: not journal.currency_id
            and journal.company_id.currency_id == c,
        )[:1]


class B2cImportBatchInvoicing(models.Model):
    _inherit = "b2c.import.batch"

    invoiced_move_ids = fields.Many2many("account.move", string="Invoices", copy=False)

    def action_invoice(self):
        """Invoice every sale this drop created, and settle it to the channel."""
        for batch in self:
            if batch.state != "applied":
                raise UserError(batch.env._("Apply the drop before invoicing it."))
            batch._assert_settled(BLOCKS_INVOICING, batch.env._("invoicing this drop"))
            posted = batch.env["account.move"]
            for sale in batch.applied_sale_ids:
                posted |= batch._invoice_sale(sale)
            batch.write({"invoiced_move_ids": [Command.set(posted.ids)]})
            batch.write({"report": batch._build_report()})
        return True

    def action_open_invoices(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": self.env._("Invoices from this import"),
            "res_model": "account.move",
            "view_mode": "list,form",
            "domain": [("id", "in", self.invoiced_move_ids.ids)],
        }

    def _invoice_sale(self, sale):
        """Return the posted invoice for one sale, settled to its channel."""
        self.ensure_one()
        existing = sale.invoice_ids.filtered(lambda move: move.state != "cancel")
        if existing:
            return existing
        invoice_date = fields.Date.to_date(sale.date_order)
        self._assert_period_open(invoice_date)
        invoice = sale._create_invoices()
        invoice.write({"invoice_date": invoice_date, "date": invoice_date})
        invoice.action_post()
        self._settle(sale, invoice, invoice_date)
        return invoice

    def _assert_period_open(self, date):
        """Refuse to write into a period that has been closed."""
        self.ensure_one()
        locked = max(
            filter(None, (
                self.company_id.fiscalyear_lock_date,
                self.company_id.tax_lock_date,
            )),
            default=None,
        )
        if locked and date <= locked:
            raise UserError(
                self.env._(
                    "%(date)s falls in a period closed on %(locked)s.",
                    date=date,
                    locked=locked,
                ),
            )

    def _settle(self, sale, invoice, date):
        """Record that the channel, not the customer, collected this invoice."""
        self.ensure_one()
        channel = sale.usl_b2c_order_id.channel_id
        journal = channel.clearing_journal(sale.currency_id)
        if not journal:
            raise UserError(
                self.env._(
                    "%(channel)s names no clearing journal for %(currency)s, so "
                    "what it collected has nowhere to be held until it pays out.",
                    channel=channel.display_name,
                    currency=sale.currency_id.name,
                ),
            )
        self.env["account.payment.register"].with_context(
            active_model="account.move",
            active_ids=invoice.ids,
        ).create(
            {
                "journal_id": journal.id,
                "payment_date": date,
                "payment_method_line_id": journal.inbound_payment_method_line_ids[:1].id,
            },
        ).action_create_payments()
