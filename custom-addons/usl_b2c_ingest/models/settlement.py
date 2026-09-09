"""What the channel kept, so the clearing account can come back to nothing.

A channel collects the whole price and remits it less what it kept, so the
clearing account only empties once that is a purchase like any other.  It is
billed as one document per channel per month per currency, which is the grain
the party that kept it accounts at.

**A channel's order export is not a reliable account of what it kept.**  Etsy's
sold-orders file states the card processing fee and nothing else — not the
transaction fee, not the listing fee, not the regulatory fee, not the
advertising — and Medusa's states nothing at all, because it is Stripe and not
Medusa that keeps anything.  An account built from order exports alone is
therefore short by everything the channel did not print on the order, and the
clearing account keeps that difference for ever.

So a statement outranks an order's own account of itself wherever one is
dropped, and where none is, that is said out loud rather than left to be
discovered as a balance that will not clear.
"""

from calendar import monthrange
from collections import defaultdict
from decimal import Decimal

from odoo import Command, fields, models
from odoo.exceptions import UserError

from odoo.addons.usl_b2c.models.constants import SOURCE_PROVIDERS

from odoo.addons.usl_b2c_ingest.models.readiness import BLOCKS_SETTLEMENT
from odoo.addons.usl_b2c_ingest.parsers import etsy, stripe

#: An amount that reached the ledger and one that has not differ by less than a
#: cent only when they are the same amount.
CENT = Decimal("0.01")

#: The statement entries whose money the channel kept, named by the parsers
#: that read them so the vocabulary has one home.
FEE_ENTRY_KINDS = frozenset(etsy.FEE_KINDS) | frozenset(stripe.FEE_KINDS)

#: Findings the commission run owns, cleared each time it runs.
FEE_KINDS = ("fee_source_missing", "fee_source_conflict", "fee_period_closed")


class B2cChannelSettlement(models.Model):
    """What one document bought of what one channel kept in one month.

    The ledger is asked what has already been bought rather than the documents
    being recognised by their reference, because a reference is a convention and
    conventions differ: the reconstruction that rebuilt the history wrote its own
    and knew nothing of this one.  A record here is what makes a month's cost
    answerable no matter which run, or which script, stated it.
    """

    _name = "b2c.channel.settlement"
    _description = "B2C Channel Settlement"
    _order = "period desc, id desc"

    company_id = fields.Many2one("res.company", required=True, index=True, ondelete="cascade")
    channel_id = fields.Many2one("b2c.channel", required=True, index=True, ondelete="cascade")
    move_id = fields.Many2one(
        "account.move",
        required=True,
        index=True,
        ondelete="restrict",
        string="Document",
    )
    batch_id = fields.Many2one("b2c.import.batch", index=True, ondelete="set null")
    provider = fields.Selection(
        SOURCE_PROVIDERS,
        required=True,
        help="Whose account of what was kept this document answers.",
    )
    period = fields.Date(required=True, index=True, help="The first day of the month settled.")
    currency_id = fields.Many2one("res.currency", required=True, ondelete="restrict")
    fee_amount = fields.Monetary(
        currency_field="currency_id",
        string="Settled",
        help="What of that month's cost this document bought.",
    )

    _channel_period_move_unique = models.Constraint(
        "UNIQUE(move_id, channel_id, period, currency_id)",
        "A document settles each channel's month once per currency.",
    )


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
    processor_provider = fields.Selection(
        selection=lambda self: self.env["b2c.order"]._fields["source_provider"].selection,
        string="Payment processor",
        help="Whose statement says what this channel's payments cost, when that "
             "is not the channel itself. A shop of one's own keeps nothing: it "
             "is the processor that does.",
    )
    processor_partner_id = fields.Many2one(
        "res.partner",
        string="Processor",
        ondelete="restrict",
        help="Who bills what the payment processor kept.",
    )
    processor_fee_product_id = fields.Many2one(
        "product.product",
        string="Processing product",
        check_company=True,
        ondelete="restrict",
        help="What the processor's charge is bought as. It is a banking cost "
             "rather than a selling commission, and rarely the same account.",
    )

    _company_processor_unique = models.Constraint(
        "UNIQUE(company_id, processor_provider)",
        "One channel answers for each payment processor per company.",
    )

    def _fee_vendor(self, provider):
        """Return who bills what was kept, and what it is bought as.

        Which of the two it is follows from who stated it: a channel accounts
        for its own commission, and a processor for its own charge.  Where a
        channel names no processor of its own, both are the channel's.
        """
        self.ensure_one()
        if provider != self.code and self.processor_provider == provider:
            return (
                self.processor_partner_id or self.operator_partner_id,
                self.processor_fee_product_id or self.fee_product_id,
            )
        return self.operator_partner_id, self.fee_product_id


class B2cImportBatchSettlement(models.Model):
    _inherit = "b2c.import.batch"

    fee_bill_ids = fields.Many2many(
        "account.move",
        "b2c_import_batch_fee_bill_rel",
        string="Commission bills",
        copy=False,
    )

    def action_bill_fees(self):
        """Bill what each channel kept, for each month and currency this drop shows."""
        for batch in self:
            if batch.state != "applied":
                raise UserError(batch.env._("Apply the drop before billing its fees."))
            batch._assert_settled(BLOCKS_SETTLEMENT, batch.env._("billing what it kept"))
            batch.issue_ids.filtered(lambda issue: issue.kind in FEE_KINDS).unlink()
            billed = batch.env["account.move"]
            for key, (kept, stated) in batch._fees_by_period().items():
                billed |= batch._fee_bill(*key, kept, stated=stated)
            batch._report_unstated_fees()
            # A month already billed stays billed: a second run that finds
            # nothing new must not forget what the first one did.
            batch.write({"fee_bill_ids": [Command.link(move.id) for move in billed]})
            batch.write({"report": batch._build_report()})
        return True

    # -- what was kept -----------------------------------------------------

    def _fees_by_period(self):
        """Return what was kept in each month and currency, and who stated it.

        Where a statement covers a month, it is the whole account of that month
        and the orders' own account of it is dropped rather than added to it:
        the card processing fee an order prints is part of what the statement
        already states, and counting both would buy it twice.
        """
        self.ensure_one()
        stated = self._sole_account_per_month(self._stated_fees())
        covered = {(channel, period, currency) for channel, _p, period, currency in stated}
        kept = {
            key: (amount, False)
            for key, amount in self._order_fees().items()
            if (key[0], key[2], key[3]) not in covered
        }
        kept.update({key: (amount, True) for key, amount in stated.items()})
        return dict(
            sorted(kept.items(), key=lambda item: (item[0][2], item[0][0].code, item[0][3].name)),
        )

    def _sole_account_per_month(self, stated):
        """Return one account of each month, and report where there were two.

        What a channel kept in a month is one quantity, and what has already
        been bought of it is counted per channel — so two parties each claiming
        to state the same month would have the second document subtract the
        first rather than add to it.  That is a configuration nobody meant, so
        it is reported and the fuller account is the one believed.
        """
        self.ensure_one()
        by_month = defaultdict(list)
        for (channel, provider, period, currency), amount in stated.items():
            by_month[channel, period, currency].append((provider, amount))
        found = {}
        for (channel, period, currency), accounts in by_month.items():
            provider, amount = max(accounts, key=lambda item: abs(item[1]))
            if len(accounts) > 1:
                # A misconfiguration is true of every month a statement covers,
                # so it is said once for all of them.
                self._gather_issue(
                    "fee_source_conflict",
                    lambda count: self.env._(
                        "%(count)s month(s) two parties each state what one "
                        "channel kept",
                        count=count,
                    ),
                    self.env._(
                        "%(channel)s, %(period)s: %(accounts)s. Only "
                        "%(provider)s's account is billed, being the fuller "
                        "one. A channel states its own commission or names a "
                        "processor that states it, never both.",
                        channel=channel.display_name,
                        period=f"{period:%B %Y}",
                        accounts="; ".join(
                            f"{name}: {value}" for name, value in sorted(accounts)
                        ),
                        provider=provider,
                    ),
                )
            found[channel, provider, period, currency] = amount
        return found

    def _stated_fees(self):
        """Return what the statements say was kept, per channel, month and currency."""
        self.ensure_one()
        channels = self._fee_channels()
        found = defaultdict(Decimal)
        for row in self.row_ids.filtered(
            lambda item: item.grain == "charge" and item.occurred_at,
        ):
            values = row.values or {}
            if values.get("entry_kind") not in FEE_ENTRY_KINDS:
                continue
            channel = channels.get(row.provider)
            currency = self._currency(values)
            if not (channel and currency):
                continue
            period = row.occurred_at.date().replace(day=1)
            found[channel, row.provider, period, currency] += self._decimal(values, "fee_amount")
        return dict(found)

    def _order_fees(self):
        """Return what the orders in this drop say the channel kept.

        Every order counts, not only the ones this drop added.  What stops a
        sale Odoo already held from being charged twice is the settlement
        already recorded for its month, which is also what lets a wider export
        dropped later correct a narrower one.
        """
        self.ensure_one()
        channels = self._channels()
        found = defaultdict(Decimal)
        for row in self.row_ids.filtered(
            lambda item: item.grain == "order" and item.occurred_at,
        ):
            fee = self._decimal(row.values, "fee_amount")
            channel = channels.get(row.provider)
            currency = self._currency(row.values or {})
            if not (fee and channel and currency):
                continue
            period = row.occurred_at.date().replace(day=1)
            found[channel, row.provider, period, currency] += fee
        return dict(found)

    def _fee_channels(self):
        """Return the channel each statement's provider states the cost of.

        A channel states its own commission.  A processor states the cost of
        the channel that named it, which is how Stripe's charge reaches the
        shop it collected for rather than a channel of its own.
        """
        self.ensure_one()
        found = dict(self._channels())
        for channel in self.env["b2c.channel"].search(
            [
                ("company_id", "=", self.company_id.id),
                ("processor_provider", "!=", False),
            ],
        ):
            found.setdefault(channel.processor_provider, channel)
        return found

    def _report_unstated_fees(self):
        """Say which channels only have their own orders' account of themselves.

        An order export states what the channel chose to print on an order.
        Where nothing else was dropped, the difference between that and what the
        channel actually kept stays in the clearing account, and saying so here
        is the only warning anybody gets before the balance refuses to clear.
        """
        self.ensure_one()
        stated = {channel for channel, _p, _period, _currency in self._stated_fees()}
        for channel in {channel for channel, _p, _period, _currency in self._order_fees()}:
            if channel in stated:
                continue
            self._raise_issue(
                "fee_source_missing",
                self.env._(
                    "Only %(channel)s's own orders say what it kept",
                    channel=channel.display_name,
                ),
                severity="advisory",
                note=self.env._(
                    "An order export states the charge the channel printed on the "
                    "order and not the rest of what it kept. Drop its statement "
                    "to bill the whole of it; until then %(account)s keeps the "
                    "difference.",
                    account=channel.clearing_journal(
                        self.company_id.currency_id,
                    ).default_account_id.display_name or self.env._("the clearing account"),
                ),
            )

    # -- the document ------------------------------------------------------

    def _fee_bill(self, channel, provider, period, currency, kept, *, stated):
        """Return the document that buys one month of what one channel kept."""
        self.ensure_one()
        date = self._period_end(period)
        # A month whose cost has since been stated more fully is billed the
        # difference, which is what keeps a wider statement a correction rather
        # than a second charge, and what lets the exports arrive in any order.
        residual = kept - self._fees_already_settled(channel, period, currency)
        if not stated:
            # The orders' account of a month is part of what a statement states,
            # never the whole of it. It may complete a month nothing else has
            # spoken for; it may never take back what a fuller account stated.
            residual = max(residual, Decimal("0"))
        if abs(residual) < CENT:
            return self.env["account.move"]
        if not self._period_open(date):
            # A statement covers years and only some of them are still open.
            # The closed ones are said out loud and left alone, because the
            # alternative is refusing the whole drop over a month nobody can
            # post to anyway.
            self._report_closed_period(
                "fee_period_closed",
                self.env._("what %(channel)s kept", channel=channel.name),
                period, currency, residual,
            )
            return self.env["account.move"]
        partner, product = channel._fee_vendor(provider)
        if not (partner and product):
            raise UserError(
                self.env._(
                    "%(channel)s does not say who bills what %(provider)s kept or "
                    "what it is bought as, so it cannot leave the clearing account.",
                    channel=channel.display_name,
                    provider=provider,
                ),
            )
        credit = residual < 0
        bill = self.env["account.move"].create(
            {
                "move_type": "in_refund" if credit else "in_invoice",
                "company_id": self.company_id.id,
                "partner_id": partner.id,
                "invoice_date": date,
                "date": date,
                "currency_id": currency.id,
                "ref": self._fee_reference(provider, period, currency),
                "invoice_line_ids": [
                    Command.create(
                        {
                            "product_id": product.id,
                            "name": self.env._(
                                "%(channel)s — %(period)s",
                                channel=channel.name,
                                period=f"{period:%B %Y}",
                            ),
                            "quantity": 1,
                            "price_unit": float(abs(residual)),
                        },
                    ),
                ],
            },
        )
        bill.action_post()
        self._pay_from_clearing(channel, bill, date)
        self.env["b2c.channel.settlement"].create(
            {
                "company_id": self.company_id.id,
                "channel_id": channel.id,
                "move_id": bill.id,
                "batch_id": self.id,
                "provider": provider,
                "period": period,
                "currency_id": currency.id,
                "fee_amount": float(residual),
            },
        )
        return bill

    def _report_closed_period(self, kind, what, period, currency, amount, *,
                              noun=None):
        """Say what a closed month would have cost, having posted nothing.

        The kind is the caller's own, because three runs report closed months
        and each clears its findings before it starts: sharing one kind would
        mean the last run erased what the others had to say.

        A caller counting months names what was not posted.  One counting
        things of its own passes ``noun`` and names none, because a headline
        that named the last one would be a headline about all of them.
        """
        self.ensure_one()
        return self._gather_issue(
            kind,
            (
                lambda count: self.env._(
                    "%(count)s %(noun)s fall in months the books are closed to",
                    count=count,
                    noun=noun,
                )
            )
            if noun
            else (
                lambda count: self.env._(
                    "%(count)s month(s) are closed, so %(what)s was not posted",
                    count=count,
                    what=what,
                )
            ),
            self.env._(
                "%(period)s: %(amount)s %(currency)s, against books closed on "
                "%(closed)s.",
                period=f"{period:%B %Y}",
                amount=amount,
                currency=currency.name,
                closed=self._closed_on(),
            ),
        )

    def _fee_reference(self, provider, period, currency):
        """Return a reference no month and currency can hold twice."""
        self.ensure_one()
        base = f"{provider}:fees:{period:%Y-%m}:{currency.name}"
        taken = self.env["account.move"].search_count(
            [
                ("company_id", "=", self.company_id.id),
                ("ref", "=like", f"{base}%"),
                ("state", "!=", "cancel"),
            ],
        )
        return base if not taken else f"{base}#{taken + 1}"

    def _fees_already_settled(self, channel, period, currency):
        """Return how much of one month's cost already reached a document.

        Whichever run or script posted it, and under whichever reference: what
        counts is that a document stands for it and has not been cancelled.
        """
        self.ensure_one()
        found = Decimal("0")
        for settlement in self.env["b2c.channel.settlement"].search(
            [
                ("company_id", "=", self.company_id.id),
                ("channel_id", "=", channel.id),
                ("period", "=", period),
                ("currency_id", "=", currency.id),
                ("move_id.state", "!=", "cancel"),
            ],
        ):
            found += Decimal(str(settlement.fee_amount))
        return found

    @staticmethod
    def _period_end(period):
        """Return the last day of the month a period starts."""
        return period.replace(day=monthrange(period.year, period.month)[1])

    def _pay_from_clearing(self, channel, bill, date):
        """Settle a document out of the account the channel deducted it from."""
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
        credit = bill.move_type == "in_refund"
        methods = (
            journal.inbound_payment_method_line_ids
            if credit
            else journal.outbound_payment_method_line_ids
        )
        self.env["account.payment.register"].with_context(
            active_model="account.move",
            active_ids=bill.ids,
        ).create(
            {
                "journal_id": journal.id,
                "payment_date": date,
                "payment_method_line_id": methods[:1].id,
            },
        ).action_create_payments()
