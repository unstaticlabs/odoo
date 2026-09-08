"""What a channel now says became of an order, and bringing Odoo to it.

An export states an order's present condition, not only that it once happened.
A sale cancelled before it shipped, one that went out after an earlier export
said it had not, and one refunded long afterwards all arrive wearing the
identity of a sale Odoo already holds.  Reading that word and acting on it is
what makes a second drop a reconciliation rather than a second opinion.

Nothing here invents a reversal.  A channel that gave money back says how
much, or says nothing and leaves a person to say it; what this refuses to do
is post revenue the channel has already taken back.
"""

from decimal import Decimal

from odoo import Command, fields, models
from odoo.exceptions import UserError

from odoo.addons.usl_b2c_ingest.parsers import ORDER_GRAIN

#: A sale that stands.
SOLD = "sold"
#: A sale the channel says never completed.
CANCELLED = "cancelled"
#: A sale the channel has given back in full.
REFUNDED = "refunded"
#: A sale the channel has given part of back, without saying which part.
PART_REFUNDED = "partly_refunded"

#: How far each outcome reverses the sale, so two exports that disagree settle
#: on the one that took the most back rather than on whichever was read last.
REVERSAL_RANK = {SOLD: 0, PART_REFUNDED: 1, REFUNDED: 2, CANCELLED: 3}

#: What each channel's own word for an order's condition means.  A channel
#: invents vocabulary far more often than it invents an outcome, so a word
#: that is not here is read as a sale that stands: taking an unknown word for
#: a cancellation would erase revenue that exists.
STATED_OUTCOMES = {
    "canceled": CANCELLED,
    "cancelled": CANCELLED,
    "canceled_by_buyer": CANCELLED,
    "canceled_by_seller": CANCELLED,
    "void": CANCELLED,
    "voided": CANCELLED,
    "refunded": REFUNDED,
    "fully_refunded": REFUNDED,
    "partially_refunded": PART_REFUNDED,
    "partly_refunded": PART_REFUNDED,
}

#: Findings this pass owns, cleared each time it runs so a resolved one does
#: not outlive the drop that raised it.
RECONCILIATION_KINDS = ("part_refund_unallocated", "cancelled_after_delivery")


def _stated(values, name):
    return Decimal(str((values or {}).get(name) or "0"))


def stated_outcome(values):
    """Return what a channel says became of an order, and what it gave back.

    Two channels state a reversal in two ways.  Medusa says the word.  Etsy
    says nothing at all and restates the order's money after adjustment, so a
    refund is the difference between what the buyer paid and what the order is
    now worth — and an order nothing happened to carries no adjustment, which
    is how an untouched order is told from one refunded to nothing.
    """
    values = values or {}
    outcome = SOLD
    for name in ("original_provider_state", "source_payment_state"):
        word = (values.get(name) or "").strip().casefold().replace(" ", "_")
        stated = STATED_OUTCOMES.get(word)
        if stated and REVERSAL_RANK[stated] > REVERSAL_RANK[outcome]:
            outcome = stated
    given_back = None
    adjusted = _stated(values, "adjusted_total_amount")
    touched = any(
        _stated(values, name)
        for name in ("adjusted_total_amount", "adjusted_fee_amount", "adjusted_net_amount")
    )
    if touched:
        paid = _stated(values, "buyer_paid_amount") or _stated(values, "total_amount")
        if not adjusted:
            outcome = CANCELLED if outcome == CANCELLED else REFUNDED
            given_back = paid
        elif adjusted < paid:
            given_back = paid - adjusted
            if REVERSAL_RANK[PART_REFUNDED] > REVERSAL_RANK[outcome]:
                outcome = PART_REFUNDED
    return outcome, given_back


class B2cImportBatchLifecycle(models.Model):
    _inherit = "b2c.import.batch"

    credit_note_ids = fields.Many2many(
        "account.move",
        "b2c_import_batch_credit_note_rel",
        string="Credit notes",
        copy=False,
    )
    cancelled_sale_ids = fields.Many2many(
        "sale.order",
        "b2c_import_batch_cancelled_sale_rel",
        string="Sales cancelled",
        copy=False,
    )

    def action_reconcile(self):
        """Bring Odoo to what these exports now say became of their orders."""
        for batch in self:
            if batch.state != "applied":
                raise UserError(batch.env._("Apply the drop before reconciling it."))
            batch.issue_ids.filtered(lambda issue: issue.kind in RECONCILIATION_KINDS).unlink()
            credited = batch.env["account.move"]
            cancelled = batch.env["sale.order"]
            for order, row in batch._stated_orders().items():
                batch._ship_if_shipped(order, row)
                credited |= batch._reverse_as_stated(order, row)
                cancelled |= batch._cancel_as_stated(order, row)
            batch.write(
                {
                    "credit_note_ids": [Command.link(move.id) for move in credited],
                    "cancelled_sale_ids": [Command.link(sale.id) for sale in cancelled],
                },
            )
            batch.write({"report": batch._build_report()})
        return True

    def action_open_credit_notes(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": self.env._("Credit notes from this import"),
            "res_model": "account.move",
            "view_mode": "list,form",
            "domain": [("id", "in", self.credit_note_ids.ids)],
        }

    # -- what this drop is about -------------------------------------------

    def _stated_orders(self):
        """Return each order this drop names, with the row that states it."""
        self.ensure_one()
        found = {}
        for row in self.row_ids.filtered(
            lambda item: item.grain == ORDER_GRAIN
            and item.resolution in ("new", "known")
            and item.order_id,
        ).sorted("id"):
            found[row.order_id] = row
        return found

    def _outcome_of(self, row):
        return stated_outcome(row.values)

    def _orders_stated_reversed(self):
        """Return the rows whose channel says the order did not stand."""
        self.ensure_one()
        return self.row_ids.filtered(
            lambda row: row.grain == ORDER_GRAIN
            and row.resolution in ("new", "known")
            and stated_outcome(row.values)[0] != SOLD,
        )

    # -- the goods ---------------------------------------------------------

    def _ship_if_shipped(self, order, row):
        """Send what an earlier drop could not, now that this one says it went.

        A channel exports an order the day it is paid and again once it ships.
        The first export leaves the delivery waiting on purpose; the second is
        what says the goods left, and on which day.
        """
        self.ensure_one()
        sale = order.sale_order_id
        if not sale or sale.state == "cancel":
            return False
        if not self._shipped_on(row):
            return False
        if not sale.picking_ids.filtered(lambda picking: picking.state not in ("done", "cancel")):
            return False
        self._deliver(sale, row)
        return True

    # -- the money ---------------------------------------------------------

    def _reverse_as_stated(self, order, row):
        """Credit what the channel says it gave back, and nothing else."""
        self.ensure_one()
        outcome, given_back = self._outcome_of(row)
        if outcome == SOLD:
            return self.env["account.move"]
        sale = order.sale_order_id
        invoice = sale.invoice_ids.filtered(
            lambda move: move.move_type == "out_invoice" and move.state == "posted",
        )
        if not invoice:
            return self.env["account.move"]
        existing = invoice.reversal_move_ids.filtered(lambda move: move.state != "cancel")
        if existing:
            return existing
        if outcome == PART_REFUNDED:
            return self._propose_credit_note(invoice, row, given_back)
        return self._credit_in_full(invoice, order, row)

    def _credit_in_full(self, invoice, order, row):
        """Reverse an invoice the channel has taken back in full."""
        self.ensure_one()
        date = fields.Date.context_today(self)
        self._assert_period_open(date)
        credit = invoice._reverse_moves(
            [
                {
                    "date": date,
                    "invoice_date": date,
                    "ref": self.env._(
                        "Reversal of %(invoice)s — %(reason)s",
                        invoice=invoice.name,
                        reason=self._outcome_of(row)[0],
                    ),
                },
            ],
        )
        credit.action_post()
        self._refund_to_clearing(order, credit, date)
        return credit

    def _propose_credit_note(self, invoice, row, given_back):
        """Draft the credit note a part refund needs, and say what it is for.

        A channel that gives part of an order back says how much, never what
        for.  Which line was refunded decides the revenue account and the
        rate, so the document is prepared and left in draft rather than posted
        against a guess.
        """
        self.ensure_one()
        credit = invoice._reverse_moves(
            [
                {
                    "date": fields.Date.context_today(self),
                    "invoice_date": fields.Date.context_today(self),
                    "ref": self.env._("Part refund of %(invoice)s", invoice=invoice.name),
                },
            ],
        )
        self._raise_issue(
            "part_refund_unallocated",
            self.env._(
                "%(order)s was refunded in part; %(credit)s is drafted for it",
                order=row.external_order_id,
                credit=credit.name,
            ),
            external_order_id=row.external_order_id,
            row=row,
            severity="advisory",
            note=self.env._(
                "The channel gave back %(amount)s of %(total)s. Trim %(credit)s to "
                "the lines it was given back for, then post it.",
                amount=given_back,
                total=invoice.amount_total,
                credit=credit.name,
            ),
        )
        return credit

    def _refund_to_clearing(self, order, credit, date):
        """Pay a credit note back out of the account the channel collected into."""
        self.ensure_one()
        channel = order.channel_id
        journal = channel.clearing_journal(credit.currency_id)
        if not journal:
            raise UserError(
                self.env._(
                    "%(channel)s names no clearing journal for %(currency)s, so what "
                    "it gave back has nowhere to come from.",
                    channel=channel.display_name,
                    currency=credit.currency_id.name,
                ),
            )
        self.env["account.payment.register"].with_context(
            active_model="account.move",
            active_ids=credit.ids,
        ).create(
            {
                "journal_id": journal.id,
                "payment_date": date,
                "payment_method_line_id": journal.outbound_payment_method_line_ids[:1].id,
            },
        ).action_create_payments()

    # -- the sale ----------------------------------------------------------

    def _cancel_as_stated(self, order, row):
        """Cancel a sale the channel says never completed.

        A sale taken back in full before it was ever invoiced is a sale that
        did not happen, whichever word the channel used for it: what is held
        against the customer goes back, and the order is closed.  One taken
        back after it was invoiced happened and was reversed, which the credit
        note has already said.
        """
        self.ensure_one()
        outcome, _given_back = self._outcome_of(row)
        sale = order.sale_order_id
        if not sale or sale.state == "cancel":
            return self.env["sale.order"]
        undone = outcome == CANCELLED or (
            outcome == REFUNDED
            and not sale.invoice_ids.filtered(lambda move: move.state == "posted")
        )
        if not undone:
            return self.env["sale.order"]
        delivered = sale.picking_ids.filtered(lambda picking: picking.state == "done")
        if delivered:
            self._raise_issue(
                "cancelled_after_delivery",
                self.env._(
                    "%(order)s was taken back but its goods went out",
                    order=row.external_order_id,
                ),
                external_order_id=row.external_order_id,
                row=row,
                note=self.env._(
                    "%(picking)s is done. Whether the goods came back is a stock "
                    "fact no export states, so the sale is left standing and the "
                    "return is yours to record.",
                    picking=", ".join(delivered.mapped("name")),
                ),
            )
            return self.env["sale.order"]
        self._return_advance(order, sale)
        sale.sudo()._action_cancel()
        return sale

    def _return_advance(self, order, sale):
        """Give back what was taken for a sale that never happened."""
        self.ensure_one()
        advance = self._advance_payment(sale)
        if not advance:
            return False
        journal = order.channel_id.clearing_journal(advance.currency_id)
        if not journal:
            return False
        returned = self.env["account.payment"].create(
            {
                "payment_type": "outbound",
                "partner_type": "customer",
                "partner_id": advance.partner_id.id,
                "amount": advance.amount,
                "currency_id": advance.currency_id.id,
                "journal_id": journal.id,
                "date": fields.Date.context_today(self),
                "memo": self.env._("Return of %(advance)s", advance=advance.memo or advance.name),
            },
        )
        returned.action_post()
        self._match_on_receivable(advance, returned)
        return returned
