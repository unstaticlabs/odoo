"""What left the money-handler and has to meet the bank.

A channel collects for weeks and pays out once.  Until that payout is an entry
of its own, the line on the bank statement has to be matched by hand against
every receipt it settles — forty of them for a good month — which is the work
this is here to remove.  Each payout becomes one entry against the bank's
suspense account, so one line meets one entry and the match is a click.

The wallet is the same movement in the opposite direction: a top-up leaves the
bank and arrives in the prepaid balance the supplier draws on.

Nothing here decides anything.  A transfer is posted only because a statement
said it happened, and it is recorded against that statement entry's own
identity, so a wider export dropped later adds what is new and repeats nothing.
"""

from collections import defaultdict
from datetime import timedelta
from decimal import Decimal

from odoo import Command, fields, models
from odoo.exceptions import UserError

from odoo.addons.usl_b2c.models.constants import SOURCE_PROVIDERS
from odoo.addons.usl_b2c_ingest.parsers import printful_transactions

#: An amount that reached the ledger and one that has not differ by less than a
#: cent only when they are the same amount.
CENT = Decimal("0.01")

#: How far a movement already in the ledger may sit from the day the statement
#: says it happened.  A payout leaves the channel on one day and reaches the
#: bank on another, and whatever posted it first may have used either.
MATCH_WINDOW = timedelta(days=10)

#: Findings the transfer run owns, cleared each time it runs.
TRANSFER_KINDS = (
    "payout_unattributed",
    "transfer_amount_missing",
    "transfer_period_closed",
    "transfer_already_posted",
)

TRANSFER_DIRECTIONS = [
    ("payout", "Paid out to the bank"),
    ("top_up", "Paid into the supplier's wallet"),
]


class B2cMoneyTransfer(models.Model):
    """One movement between a money-handler and the bank, as an entry."""

    _name = "b2c.money.transfer"
    _description = "B2C Money Transfer"
    _order = "occurred_on desc, id desc"

    company_id = fields.Many2one("res.company", required=True, index=True, ondelete="cascade")
    move_id = fields.Many2one(
        "account.move",
        required=True,
        index=True,
        ondelete="restrict",
        string="Entry",
    )
    batch_id = fields.Many2one("b2c.import.batch", index=True, ondelete="set null")
    channel_id = fields.Many2one("b2c.channel", index=True, ondelete="set null")
    provider = fields.Selection(SOURCE_PROVIDERS, required=True, index=True)
    direction = fields.Selection(TRANSFER_DIRECTIONS, required=True)
    entry_key = fields.Char(
        required=True,
        index=True,
        help="The statement entry this answers, named the way the statement "
             "names it. It is what makes a re-drop change nothing.",
    )
    occurred_on = fields.Date(required=True, index=True)
    currency_id = fields.Many2one("res.currency", required=True, ondelete="restrict")
    amount = fields.Monetary(currency_field="currency_id")

    _entry_key_unique = models.Constraint(
        "UNIQUE(company_id, provider, entry_key)",
        "A statement entry moves money once.",
    )


class ResCompanyTransfers(models.Model):
    _inherit = "res.company"

    usl_b2c_bank_journal_id = fields.Many2one(
        "account.journal",
        string="B2C bank",
        domain="[('type', '=', 'bank')]",
        ondelete="restrict",
        help="The bank the channels pay into and the wallet is topped up from. "
             "Its suspense account is where a payout waits for its statement "
             "line, which is the one match left to make by hand.",
    )
    usl_b2c_transfer_journal_id = fields.Many2one(
        "account.journal",
        string="B2C transfers",
        domain="[('type', '=', 'general')]",
        ondelete="restrict",
        help="Where the entries for payouts and top-ups are written, so they "
             "read as one movement rather than as bookkeeping inside a bank.",
    )


class B2cImportBatchTransfers(models.Model):
    _inherit = "b2c.import.batch"

    transfer_move_ids = fields.Many2many(
        "account.move",
        "b2c_import_batch_transfer_move_rel",
        string="Transfers",
        copy=False,
    )

    def action_post_transfers(self):
        """Post an entry for each payout and top-up the statements state."""
        for batch in self:
            if batch.state != "applied":
                raise UserError(
                    batch.env._("Apply the drop before posting what it moved."),
                )
            batch.issue_ids.filtered(lambda issue: issue.kind in TRANSFER_KINDS).unlink()
            posted = batch.env["account.move"]
            for movement in batch._stated_transfers():
                posted |= batch._transfer_entry(movement)
            # A transfer already posted stays posted: a second run that finds
            # nothing new must not forget what the first one did.
            batch.write({"transfer_move_ids": [Command.link(move.id) for move in posted]})
            batch.write({"report": batch._build_report()})
        return True

    def action_open_transfers(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": self.env._("What this import moved to the bank"),
            "res_model": "account.move",
            "view_mode": "list,form",
            "domain": [("id", "in", self.transfer_move_ids.ids)],
        }

    # -- what the statements say moved -------------------------------------

    def _stated_transfers(self):
        """Return each movement a statement states, once."""
        self.ensure_one()
        channels = self._fee_channels()
        known = self._transfers_already_posted()
        found = []
        for row in self.row_ids.filtered(
            lambda item: item.grain == "charge" and item.occurred_at,
        ):
            values = row.values or {}
            direction = self._direction(values)
            if not direction:
                continue
            key = row.external_order_id
            if (row.provider, key) in known:
                continue
            amount = self._transfer_amount(values, direction)
            if abs(amount) < CENT:
                self._raise_issue(
                    "transfer_amount_missing",
                    self.env._(
                        "%(provider)s states a movement without an amount",
                        provider=row.provider,
                    ),
                    row=row,
                    severity="advisory",
                    note=values.get("description") or values.get("stated_kind"),
                )
                continue
            found.append(
                {
                    "provider": row.provider,
                    "channel": channels.get(row.provider),
                    "direction": direction,
                    "entry_key": key,
                    "date": row.occurred_at.date(),
                    "currency": self._currency(values),
                    "amount": amount,
                    "description": values.get("description") or values.get("stated_kind"),
                },
            )
            known.add((row.provider, key))
        return found

    @staticmethod
    def _direction(values):
        """Return which way a statement entry moved money, or nothing."""
        kind = values.get("entry_kind")
        if kind == "payout":
            return "payout"
        if kind in printful_transactions.TRANSFER_KINDS and values.get("settled"):
            return "top_up"
        return None

    @staticmethod
    def _transfer_amount(values, direction):
        """Return what a movement was worth, as a positive amount.

        A statement states a payout as a deduction from its own balance and a
        top-up as an addition to the wallet, so the sign says which way it went
        and not how much it was.
        """
        source = "wallet_amount" if direction == "top_up" else "gross_amount"
        return abs(Decimal(str(values.get(source) or "0")))

    def _transfers_already_posted(self):
        """Return the statement entries the ledger already answers."""
        self.ensure_one()
        return {
            (transfer.provider, transfer.entry_key)
            for transfer in self.env["b2c.money.transfer"].search(
                [
                    ("company_id", "=", self.company_id.id),
                    ("move_id.state", "!=", "cancel"),
                ],
            )
        }

    # -- the entry ---------------------------------------------------------

    def _transfer_entry(self, movement):
        """Post one movement, and record which statement entry it answers."""
        self.ensure_one()
        self._assert_transfers_configured()
        date = movement["date"]
        currency = movement["currency"] or self.company_id.currency_id
        if not self._period_open(date):
            self._report_closed_period(
                "transfer_period_closed",
                self.env._("a %(direction)s", direction=movement["direction"]),
                date.replace(day=1), currency, movement["amount"],
            )
            return self.env["account.move"]
        held, met = self._transfer_accounts(movement)
        if not (held and met):
            self._raise_issue(
                "payout_unattributed",
                self.env._(
                    "%(provider)s paid out %(amount)s, and nothing says which "
                    "account held it",
                    provider=movement["provider"],
                    amount=movement["amount"],
                ),
                severity="advisory",
                note=self.env._(
                    "Name the clearing journal for %(currency)s on the channel, "
                    "or the wallet journal on the company.",
                    currency=currency.name,
                ),
            )
            return self.env["account.move"]
        standing = self._movement_already_in_the_ledger(movement, held, date)
        if standing:
            # Something posted this before the tool existed and under its own
            # reference — the reconstruction wrote a payout per bank line, not
            # per statement entry, so its identity cannot be recognised. What
            # can be recognised is the movement itself, already standing in the
            # account it would move.
            self._gather_issue(
                "transfer_already_posted",
                lambda count: self.env._(
                    "%(count)s movement(s) the ledger already shows",
                    count=count,
                ),
                self.env._(
                    "%(date)s, %(amount)s out of %(account)s — posted as "
                    "%(ref)s. Nothing was posted for %(key)s.",
                    date=date,
                    amount=movement["amount"],
                    account=held.code,
                    ref=standing.move_id.ref or standing.move_id.name,
                    key=movement["entry_key"],
                ),
            )
            return self.env["account.move"]
        entry = self.env["account.move"].create(
            {
                "move_type": "entry",
                "company_id": self.company_id.id,
                "journal_id": self.company_id.usl_b2c_transfer_journal_id.id,
                "date": date,
                "ref": self._transfer_reference(movement),
                "line_ids": [
                    Command.create(
                        self._transfer_line(movement, met, currency, date, debit=True),
                    ),
                    Command.create(
                        self._transfer_line(movement, held, currency, date, debit=False),
                    ),
                ],
            },
        )
        entry.action_post()
        self.env["b2c.money.transfer"].create(
            {
                "company_id": self.company_id.id,
                "move_id": entry.id,
                "batch_id": self.id,
                "channel_id": movement["channel"].id if movement["channel"] else False,
                "provider": movement["provider"],
                "direction": movement["direction"],
                "entry_key": movement["entry_key"],
                "occurred_on": date,
                "currency_id": currency.id,
                "amount": float(movement["amount"]),
            },
        )
        return entry

    def _movement_already_in_the_ledger(self, movement, held, date):
        """Return a line already standing for this movement, if there is one.

        A statement entry's identity is the tool's own way of not repeating
        itself, and it only recognises what the tool posted.  A movement posted
        by anything else — the reconstruction, or a person — carries no such
        identity, so what is looked for is the movement: the same amount
        leaving the same account on the same day.

        Two payouts of exactly the same amount within days of each other would
        look like one.  That is rarer than posting every payout twice.
        """
        self.ensure_one()
        ours = self.env["b2c.money.transfer"].search(
            [("company_id", "=", self.company_id.id)],
        ).move_id
        # Money always leaves the account it was held in, whichever direction
        # it went: a payout leaves the channel's clearing account and a top-up
        # leaves the bank. So what would stand on that account is a credit, and
        # a credit is what an earlier posting of the same movement left there.
        wanted = float(-movement["amount"])
        for line in self.env["account.move.line"].search(
            [
                ("account_id", "=", held.id),
                ("date", ">=", date - MATCH_WINDOW),
                ("date", "<=", date + MATCH_WINDOW),
                ("parent_state", "=", "posted"),
                ("move_id", "not in", ours.ids),
            ],
        ):
            if held.currency_id and line.amount_currency:
                found = line.amount_currency
            else:
                found = line.balance
            if abs(found - wanted) < float(CENT):
                return line
        return self.env["account.move.line"]

    def _transfer_accounts(self, movement):
        """Return the account the money sat in, and the one it moved to.

        A payout leaves the channel's clearing account and waits in the bank's
        suspense until its statement line arrives.  A top-up is the same
        journey run backwards into the supplier's wallet.
        """
        self.ensure_one()
        company = self.company_id
        suspense = company.usl_b2c_bank_journal_id.suspense_account_id
        if movement["direction"] == "top_up":
            return suspense, company._usl_b2c_wallet_account()
        channel = movement["channel"]
        currency = movement["currency"] or company.currency_id
        journal = channel.clearing_journal(currency) if channel else False
        return (journal.default_account_id if journal else False), suspense

    def _transfer_line(self, movement, account, currency, date, *, debit):
        """Return one side of a movement, stated in both currencies."""
        self.ensure_one()
        company = self.company_id
        amount = movement["amount"]
        in_company = (
            amount
            if currency == company.currency_id
            else Decimal(
                str(currency._convert(float(amount), company.currency_id, company, date)),
            )
        )
        signed = amount if debit else -amount
        return {
            "account_id": account.id,
            "name": movement["description"] or self.env._("Transfer"),
            "currency_id": currency.id,
            "amount_currency": float(signed),
            "debit": float(in_company) if debit else 0.0,
            "credit": 0.0 if debit else float(in_company),
        }

    def _transfer_reference(self, movement):
        """Return the reference stating which movement this answers."""
        return f"{movement['provider']}:{movement['direction']}:{movement['entry_key']}"

    def _assert_transfers_configured(self):
        """Refuse to move money the company has not said where to move it."""
        self.ensure_one()
        company = self.company_id
        missing = [
            name
            for name, value in (
                (self.env._("a bank"), company.usl_b2c_bank_journal_id),
                (self.env._("a journal to write transfers in"),
                 company.usl_b2c_transfer_journal_id),
            )
            if not value
        ]
        if missing:
            raise UserError(
                self.env._(
                    "%(company)s names no %(missing)s, so what the channels paid "
                    "out has nowhere to wait for the bank.",
                    company=company.display_name,
                    missing=", ".join(missing),
                ),
            )
        if not company.usl_b2c_bank_journal_id.suspense_account_id:
            raise UserError(
                self.env._(
                    "%(journal)s holds no suspense account, so a payout has "
                    "nowhere to wait for its statement line.",
                    journal=company.usl_b2c_bank_journal_id.display_name,
                ),
            )

    def _transfer_report(self):
        """Say what moved, and what is now waiting for the bank."""
        self.ensure_one()
        if not self.transfer_move_ids:
            return []
        by_direction = defaultdict(list)
        for transfer in self.env["b2c.money.transfer"].search(
            [("move_id", "in", self.transfer_move_ids.ids)],
        ):
            by_direction[transfer.direction].append(transfer)
        return [
            self.env._(
                "%(count)s %(direction)s: %(amount)s, waiting to meet the bank.",
                count=len(transfers),
                direction=dict(TRANSFER_DIRECTIONS)[direction].lower(),
                amount=sum(transfer.amount for transfer in transfers),
            )
            for direction, transfers in sorted(by_direction.items())
        ]
