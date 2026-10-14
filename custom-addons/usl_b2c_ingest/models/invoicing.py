"""Invoicing a drop, and settling it against the channel that collected it.

An invoice is what makes a sale's VAT belong to the country it shipped to, and
French law requires one for a distance sale to another Member State whether or
not anyone asks.  The customer never pays the shop directly: the channel
collects and later remits, so the invoice is settled into the channel's own
clearing account and it is the payout that meets the bank.

Money taken for goods that have not left is not a sale yet.  VAT on a supply of
goods falls due on delivery, so an order paid and not yet shipped is held as
what it is — an advance on the customer's account — and becomes an invoice in
the drop that says the goods went out.
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
    advance_payment_ids = fields.Many2many(
        "account.payment",
        "b2c_import_batch_advance_payment_rel",
        string="Advances held",
        copy=False,
        help="What customers paid for orders whose goods have not left yet.",
    )

    def action_invoice(self):
        """Invoice every sale whose goods went out, and hold the rest.

        Every order this drop names is considered, not only the ones it added:
        an order paid in an earlier drop and shipped in this one becomes an
        invoice here, which is the whole point of dropping the later export.
        """
        for batch in self:
            if batch.state != "applied":
                raise UserError(batch.env._("Apply the drop before invoicing it."))
            batch._assert_settled(BLOCKS_INVOICING, batch.env._("invoicing this drop"))
            posted = batch.env["account.move"]
            held = batch.env["account.payment"]
            for order, row in batch._stated_orders().items():
                sale = order.sale_order_id
                if not sale or sale.state == "cancel":
                    continue
                if batch._shipped_on(row):
                    posted |= batch._invoice_sale(sale)
                else:
                    held |= batch._hold_as_advance(order, sale)
            batch.write(
                {
                    "invoiced_move_ids": [Command.set(posted.ids)],
                    "advance_payment_ids": [Command.set(held.ids)],
                },
            )
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

    def action_open_advances(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": self.env._("Advances held by this import"),
            "res_model": "account.payment",
            "view_mode": "list,form",
            "domain": [("id", "in", self.advance_payment_ids.ids)],
        }

    def _invoice_sale(self, sale):
        """Return the posted invoice for one sale, settled to its channel."""
        self.ensure_one()
        existing = sale.invoice_ids.filtered(
            lambda move: move.move_type == "out_invoice" and move.state != "cancel",
        )
        if existing:
            self._match_on_receivable(self._advance_payment(sale), existing)
            return existing
        invoice_date = fields.Date.to_date(sale.date_order)
        self._assert_period_open(invoice_date)
        invoice = sale._create_invoices()
        invoice.write({"invoice_date": invoice_date, "date": invoice_date})
        invoice.action_post()
        held = self._advance_payment(sale)
        if held:
            # The customer already paid; the invoice only names what for.
            self._match_on_receivable(held, invoice)
        else:
            self._settle(sale, invoice, invoice_date)
        return invoice

    def _period_open(self, date):
        """Return whether the books are still open on a date."""
        self.ensure_one()
        locked = max(
            filter(None, (
                self.company_id.fiscalyear_lock_date,
                self.company_id.tax_lock_date,
            )),
            default=None,
        )
        return not (locked and date <= locked)

    def _closed_on(self):
        """Return the day the books were closed to, or nothing."""
        self.ensure_one()
        return max(
            filter(None, (
                self.company_id.fiscalyear_lock_date,
                self.company_id.tax_lock_date,
            )),
            default=None,
        )

    def _assert_period_open(self, date):
        """Refuse to write into a period that has been closed."""
        self.ensure_one()
        locked = self._closed_on()
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
        journal = self._clearing_journal(channel, sale.currency_id)
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

    def _clearing_journal(self, channel, currency):
        """Return where this channel holds one currency, or say it names none."""
        self.ensure_one()
        journal = channel.clearing_journal(currency)
        if not journal:
            raise UserError(
                self.env._(
                    "%(channel)s names no clearing journal for %(currency)s, so "
                    "what it collected has nowhere to be held until it pays out.",
                    channel=channel.display_name,
                    currency=currency.name,
                ),
            )
        return journal

    # -- goods that have not left yet --------------------------------------

    def _advance_reference(self, order):
        return f"{order.channel_id.code}:advance:{order.external_order_id}"

    def _advance_payment(self, sale):
        """Return what was already taken for a sale, before it was invoiced."""
        self.ensure_one()
        order = sale.usl_b2c_order_id
        if not order:
            return self.env["account.payment"]
        return self.env["account.payment"].search(
            [
                ("company_id", "=", self.company_id.id),
                ("memo", "=", self._advance_reference(order)),
                ("state", "in", ("paid", "reconciled")),
            ],
            limit=1,
        )

    def _hold_as_advance(self, order, sale):
        """Hold what the customer paid for goods that have not gone out.

        The money is real and the sale is not yet one: recording it against the
        customer leaves the receivable in credit, which is what an advance is,
        and keeps it out of revenue and out of VAT until the goods leave.
        """
        self.ensure_one()
        held = self._advance_payment(sale)
        if held or sale.invoice_ids.filtered(lambda move: move.state == "posted"):
            return held
        date = fields.Date.to_date(sale.date_order)
        self._assert_period_open(date)
        journal = self._clearing_journal(order.channel_id, sale.currency_id)
        payment = self.env["account.payment"].create(
            {
                "payment_type": "inbound",
                "partner_type": "customer",
                "partner_id": sale.partner_invoice_id.id,
                "amount": sale.amount_total,
                "currency_id": sale.currency_id.id,
                "journal_id": journal.id,
                "date": date,
                "memo": self._advance_reference(order),
            },
        )
        payment.action_post()
        return payment

    @staticmethod
    def _open_receivable_lines(record):
        """Return the customer lines of a payment or a move that still stand."""
        move = record.move_id if record._name == "account.payment" else record
        return move.line_ids.filtered(
            lambda line: line.account_id.account_type == "asset_receivable"
            and not line.reconciled,
        )

    def _match_on_receivable(self, held, other):
        """Settle an advance against the invoice it turned out to be for."""
        self.ensure_one()
        if not held or not other:
            return False
        lines = self._open_receivable_lines(held) | self._open_receivable_lines(other)
        if len(lines) < 2 or len(lines.account_id) != 1:
            return False
        lines.reconcile()
        return True
