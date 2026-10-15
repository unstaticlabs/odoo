"""What a payment became once the processor converted it.

A shop of one's own sells in several currencies and its processor settles every
one of them in the company's.  The ledger holds each receipt at what the
customer paid, converted at Odoo's rate for the day; the processor converted it
at its own.  Fees and payouts arrive already converted, so the clearing account
can only come back to nothing once the difference between the two rates has
somewhere to go.

That difference is a gain or a loss on exchange, stated once per payment
against the processor's own figure for it.  The statement names what the
customer was charged and what reached the balance, which is everything needed
to find the receipt it answers and to say what it was worth.
"""

from decimal import Decimal

from odoo import Command, fields, models
from odoo.exceptions import UserError

from odoo.addons.usl_b2c.models.constants import SOURCE_PROVIDERS
from odoo.addons.usl_b2c_ingest.models.transfers import MATCH_WINDOW
from odoo.addons.usl_b2c_ingest.parsers import CHARGE_GRAIN

#: An amount that reached the ledger and one that has not differ by less than a
#: cent only when they are the same amount.
CENT = Decimal("0.01")

#: The statement entries a customer paid or was paid back, which are the only
#: ones whose currency the processor converts on the customer's behalf.
CONVERTED_KINDS = {"charge": 1, "refund": -1}

#: Where a conversion's difference goes, named by code because a code is what
#: the chart states and an id is only what one database happened to assign.
FX_LOSS_CODE = "656000"
FX_GAIN_CODE = "756000"

#: Findings the conversion run owns, cleared each time it runs.
CONVERSION_KINDS = ("processor_charge_unmatched", "conversion_period_closed")

#: The finding a payment with no receipt raises.  It is also withdrawn from
#: whichever earlier drop raised it once a later one converts that payment.
UNMATCHED = "processor_charge_unmatched"


class B2cProcessorConversion(models.Model):
    """One payment the processor converted, and the receipt it answers."""

    _name = "b2c.processor.conversion"
    _description = "B2C Processor Conversion"
    _order = "occurred_on desc, id desc"

    company_id = fields.Many2one("res.company", required=True, index=True, ondelete="cascade")
    channel_id = fields.Many2one("b2c.channel", index=True, ondelete="set null")
    batch_id = fields.Many2one("b2c.import.batch", index=True, ondelete="set null")
    provider = fields.Selection(SOURCE_PROVIDERS, required=True, index=True)
    entry_key = fields.Char(
        required=True,
        index=True,
        help="The statement entry this answers, named the way the statement "
             "names it. It is what makes a re-drop change nothing.",
    )
    occurred_on = fields.Date(required=True, index=True)
    move_id = fields.Many2one(
        "account.move",
        index=True,
        ondelete="restrict",
        string="Entry",
        help="What the difference between the two rates was posted as. Empty "
             "when the processor converted at Odoo's own rate.",
    )
    line_id = fields.Many2one(
        "account.move.line",
        index=True,
        ondelete="set null",
        string="Receipt",
        help="The clearing line of the receipt this payment was matched to.",
    )
    currency_id = fields.Many2one("res.currency", required=True, ondelete="restrict")
    stated_amount = fields.Monetary(
        currency_field="currency_id",
        string="Settled",
        help="What the processor says reached its balance.",
    )
    recorded_amount = fields.Monetary(
        currency_field="currency_id",
        string="Recorded",
        help="What the ledger held the receipt at, at Odoo's rate.",
    )
    facing_currency_id = fields.Many2one(
        "res.currency",
        required=True,
        ondelete="restrict",
        string="Charged in",
    )
    facing_amount = fields.Monetary(
        currency_field="facing_currency_id",
        string="Charged",
        help="What the customer was charged, in the currency they paid in.",
    )

    _entry_key_unique = models.Constraint(
        "UNIQUE(company_id, provider, entry_key)",
        "A statement entry is converted once.",
    )
    _line_unique = models.Constraint(
        "UNIQUE(line_id)",
        "A receipt answers one converted payment.",
    )


class B2cImportBatchConversion(models.Model):
    _inherit = "b2c.import.batch"

    def action_convert_receipts(self):
        """State what each converted payment was worth when it reached the balance.

        Every statement the company has applied is read, not only this one:
        the orders and the statement arrive in separate drops, in either
        order, and a payment converted before its receipt was recorded is
        converted by whichever drop brings the second of the two.
        """
        for batch in self:
            if batch.state != "applied":
                raise UserError(
                    batch.env._("Apply the drop before converting what it received."),
                )
            batch.issue_ids.filtered(lambda issue: issue.kind in CONVERSION_KINDS).unlink()
            used = batch._receipts_already_converted()
            for charge in batch._converted_charges():
                line = batch._receipt_for(charge, used)
                if not line:
                    batch._report_unmatched_charge(charge)
                    continue
                # A receipt answers one payment, even one left for a closed
                # month: pairing it again would answer two with one.
                used.add(line.id)
                batch._convert_receipt(charge, line)
            batch._withdraw_answered_findings()
            batch.write({"report": batch._build_report()})
        return True

    # -- what the statements say was converted -----------------------------

    def _converted_charges(self):
        """Return each converted payment no conversion answers yet, oldest first."""
        self.ensure_one()
        channels = {
            provider: channel
            for provider, channel in self._fee_channels().items()
            if channel.settlement_currency_id
        }
        if not channels:
            return []
        done = {
            (conversion.provider, conversion.entry_key)
            for conversion in self.env["b2c.processor.conversion"].search(
                [("company_id", "=", self.company_id.id)],
            )
        }
        # The entry is the conversion as far as the ledger is concerned. A
        # record lost without its entry must not let the payment be converted
        # a second time.
        for move in self.env["account.move"].search(
            [
                ("company_id", "=", self.company_id.id),
                ("ref", "=like", "%:conversion:%"),
                ("state", "!=", "cancel"),
            ],
        ):
            provider, _kind, key = move.ref.split(":", 2)
            done.add((provider, key))
        Currency = self.env["res.currency"].with_context(active_test=False)
        found = []
        for row in self.env["b2c.import.row"].search(
            [
                ("batch_id.company_id", "=", self.company_id.id),
                ("batch_id.state", "=", "applied"),
                ("grain", "=", CHARGE_GRAIN),
                ("provider", "in", list(channels)),
                ("occurred_at", "!=", False),
            ],
            order="occurred_at, id",
        ):
            values = row.values or {}
            sign = CONVERTED_KINDS.get(values.get("entry_kind"))
            channel = channels[row.provider]
            settles = channel.settlement_currency_id
            facing_code = (values.get("facing_currency") or "").strip().upper()
            facing_amount = abs(self._decimal(values, "facing_amount"))
            if not (sign and facing_code and facing_amount):
                continue
            if (values.get("currency") or "").strip().upper() != settles.name:
                continue
            if facing_code == settles.name:
                continue
            key = (row.provider, self._entry_key(row))
            if key in done:
                continue
            done.add(key)
            facing = Currency.search([("name", "=", facing_code)], limit=1)
            if not facing:
                continue
            found.append(
                {
                    "provider": row.provider,
                    "channel": channel,
                    "entry_key": key[1],
                    "date": row.occurred_at.date(),
                    "currency": settles,
                    "gross": self._decimal(values, "gross_amount"),
                    "facing_currency": facing,
                    "facing_amount": facing_amount * sign,
                    "refund": sign < 0,
                    "row": row,
                },
            )
        return found

    def _receipts_already_converted(self):
        """Return the receipt lines a conversion already answers."""
        self.ensure_one()
        return set(
            self.env["b2c.processor.conversion"].search(
                [("company_id", "=", self.company_id.id), ("line_id", "!=", False)],
            ).line_id.ids,
        )

    def _receipt_for(self, charge, used):
        """Return the receipt one converted payment answers, or nothing.

        A receipt is the clearing line of a payment registered in the journal
        the channel settles in, stated in the currency the customer paid and
        at exactly what they paid.  Two payments of the same amount are paired
        in the order they happened.

        A charge is recorded the day of the sale, so its receipt sits within
        days of it.  A refund is recorded the day somebody reads that it
        happened — the drop that states it, or a person registering a part
        refund — which is any day after Stripe gave the money back.  So a
        refund's receipt is looked for from shortly before it onwards, never
        only around it.
        """
        self.ensure_one()
        channel = charge["channel"]
        journal = channel.clearing_journal(charge["currency"])
        if not journal:
            return self.env["account.move.line"]
        date = charge["date"]
        wanted = float(charge["facing_amount"])
        when = [("date", ">=", date - MATCH_WINDOW)]
        if not charge["refund"]:
            when.append(("date", "<=", date + MATCH_WINDOW))
        for line in self.env["account.move.line"].search(
            [
                ("company_id", "=", self.company_id.id),
                ("journal_id", "=", journal.id),
                ("parent_state", "=", "posted"),
                ("move_id.origin_payment_id", "!=", False),
                ("currency_id", "=", charge["facing_currency"].id),
                *when,
            ],
            order="date, id",
        ):
            if line.id in used:
                continue
            if line.account_id != line.move_id.origin_payment_id.outstanding_account_id:
                continue
            if abs(line.amount_currency - wanted) < float(CENT) / 2:
                return line
        return self.env["account.move.line"]

    # -- the entry ---------------------------------------------------------

    def _convert_receipt(self, charge, line):
        """Post what one payment gained or lost in conversion, and record it.

        Return whether the payment is now answered for.  A month the books are
        closed to is reported and left unanswered, so the next drop after the
        books reopen, or a person, can still settle it.
        """
        self.ensure_one()
        company = self.company_id
        # The difference exists once both halves do: a refund registered after
        # Stripe made it is converted on the day it reached the ledger.
        date = max(charge["date"], line.date)
        stated = charge["gross"]
        recorded = Decimal(str(line.balance)).quantize(CENT)
        difference = stated - recorded
        entry = self.env["account.move"]
        if abs(difference) >= CENT:
            if not self._period_open(date):
                self._report_closed_period(
                    "conversion_period_closed",
                    self.env._("a conversion"),
                    date.replace(day=1), charge["currency"], difference,
                    noun=self.env._("conversion(s)"),
                )
                return False
            entry = self._conversion_entry(charge, line, difference, date)
        self.env["b2c.processor.conversion"].create(
            {
                "company_id": company.id,
                "channel_id": charge["channel"].id,
                "batch_id": self.id,
                "provider": charge["provider"],
                "entry_key": charge["entry_key"],
                "occurred_on": charge["date"],
                "move_id": entry.id or False,
                "line_id": line.id,
                "currency_id": charge["currency"].id,
                "stated_amount": float(stated),
                "recorded_amount": float(recorded),
                "facing_currency_id": charge["facing_currency"].id,
                "facing_amount": float(charge["facing_amount"]),
            },
        )
        return True

    def _conversion_entry(self, charge, line, difference, date):
        """Return the posted entry moving one conversion's difference to exchange."""
        self.ensure_one()
        company = self.company_id
        journal = company.currency_exchange_journal_id or company.usl_b2c_transfer_journal_id
        if not journal:
            raise UserError(
                self.env._(
                    "%(company)s names no exchange difference journal and no B2C "
                    "transfers journal, so what a conversion gained or lost has "
                    "nowhere to be written.",
                    company=company.display_name,
                ),
            )
        gain = difference > 0
        exchange = self._exchange_account(FX_GAIN_CODE if gain else FX_LOSS_CODE)
        currency = company.currency_id
        name = self.env._(
            "%(provider)s converted %(facing)s %(facing_currency)s into %(stated)s "
            "%(currency)s",
            provider=charge["provider"],
            facing=charge["facing_amount"],
            facing_currency=charge["facing_currency"].name,
            stated=charge["gross"],
            currency=charge["currency"].name,
        )
        entry = self.env["account.move"].create(
            {
                "move_type": "entry",
                "company_id": company.id,
                "journal_id": journal.id,
                "date": date,
                "ref": f"{charge['provider']}:conversion:{charge['entry_key']}",
                "line_ids": [
                    Command.create(
                        {
                            "account_id": line.account_id.id,
                            "partner_id": line.partner_id.id or False,
                            "name": name,
                            "currency_id": currency.id,
                            "amount_currency": float(difference),
                            "balance": float(difference),
                        },
                    ),
                    Command.create(
                        {
                            "account_id": exchange.id,
                            "name": name,
                            "currency_id": currency.id,
                            "amount_currency": float(-difference),
                            "balance": float(-difference),
                        },
                    ),
                ],
            },
        )
        entry.action_post()
        return entry

    def _exchange_account(self, code):
        """Return the company's account stating one side of an exchange difference."""
        self.ensure_one()
        company = self.company_id
        # The code of an account is what the company reading it says it is,
        # so the company has to be the one asking.
        account = self.env["account.account"].with_company(company).search(
            [("company_ids", "in", company.id), ("code", "=", code), ("active", "=", True)],
            limit=1,
        )
        if not account:
            raise UserError(
                self.env._(
                    "%(company)s's chart holds no account %(code)s, so what a "
                    "conversion gained or lost has nowhere to go.",
                    company=company.display_name,
                    code=code,
                ),
            )
        return account

    def _unmatched_headline(self, count):
        return self.env._("%(count)s converted payment(s) match no receipt", count=count)

    def _report_unmatched_charge(self, charge):
        """Say a converted payment has no receipt to answer, having posted nothing.

        Each line names the entry it is about, so a later drop that converts
        the payment can withdraw the line from whichever drop raised it.
        """
        self.ensure_one()
        values = {
            "date": charge["date"],
            "key": charge["entry_key"],
            "facing": abs(charge["facing_amount"]),
            "facing_currency": charge["facing_currency"].name,
            "stated": charge["gross"],
            "currency": charge["currency"].name,
        }
        if charge["refund"]:
            line = self.env._(
                "%(date)s, %(key)s: a refund of %(facing)s %(facing_currency)s "
                "cost %(stated)s %(currency)s. No payment in %(currency)s's "
                "clearing journal gives back %(facing)s %(facing_currency)s on "
                "or after %(earliest)s. Drop the order export that states the "
                "refund, or post the credit note and register its refund in "
                "that journal in %(facing_currency)s, dated the day it was "
                "refunded; until then the difference stays in the clearing "
                "account.",
                earliest=charge["date"] - MATCH_WINDOW,
                **values,
            )
        else:
            line = self.env._(
                "%(date)s, %(key)s: %(facing)s %(facing_currency)s became "
                "%(stated)s %(currency)s. No payment in %(currency)s's clearing "
                "journal states %(facing)s %(facing_currency)s within %(days)s "
                "days of it; drop the order export that states the sale, or the "
                "difference stays in the clearing account.",
                days=MATCH_WINDOW.days,
                **values,
            )
        issue = self._gather_issue(UNMATCHED, self._unmatched_headline, line)
        keys = [*((issue.proposal or {}).get("entries") or []), [charge["provider"], charge["entry_key"]]]
        issue.proposal = {"entries": keys}
        return issue

    def _withdraw_answered_findings(self):
        """Take converted payments out of the findings that said they had no receipt.

        A statement dropped before its orders raises the finding in its own
        drop; the drop that converts the payment is another one.  Left alone,
        the first drop would go on saying a payment has no receipt long after
        it was converted.
        """
        self.ensure_one()
        converted = {
            (conversion.provider, conversion.entry_key)
            for conversion in self.env["b2c.processor.conversion"].search(
                [("company_id", "=", self.company_id.id)],
            )
        }
        for issue in self.env["b2c.import.issue"].search(
            [
                ("kind", "=", UNMATCHED),
                ("batch_id.company_id", "=", self.company_id.id),
            ],
        ):
            entries = (issue.proposal or {}).get("entries") or []
            lines = (issue.note or "").splitlines()
            if len(entries) != len(lines):
                continue
            kept = [
                (entry, line)
                for entry, line in zip(entries, lines, strict=True)
                if tuple(entry) not in converted
            ]
            if len(kept) == len(entries):
                continue
            if not kept:
                issue.unlink()
                continue
            issue.write(
                {
                    "name": issue.batch_id._unmatched_headline(len(kept)),
                    "note": "\n".join(line for _entry, line in kept),
                    "proposal": {"entries": [entry for entry, _line in kept]},
                },
            )
