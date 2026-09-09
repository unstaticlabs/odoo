"""Settling what the supplier consumed out of the wallet it is paid from.

Printful does not invoice an order.  It draws on a wallet the shop tops up,
so the ledger owes it one document per month for what it drew, credited to
the wallet the money came out of, and the wallet's balance is then a fact the
bank can be reconciled against rather than an opinion.

What a fulfilment cost is stated once, by the fulfilment event, and spread
across the lines it shipped.  This bills exactly that same number, so the
cost in the accounts and the cost in the margin can never disagree — which is
why the supplier's carriage is not split out here: it was already spread as
part of the cost of the goods it carried.

A fulfilment that answers to no sale is marketing or prototyping.  It
consumed the wallet just the same, so it is billed, but never as the cost of
something sold.
"""

from collections import defaultdict
from decimal import Decimal

from odoo import Command, fields, models
from odoo.exceptions import UserError

#: A cost that reached the ledger and one that has not differ by less than a
#: cent only when they are the same cost.
CENT = Decimal("0.01")

#: Findings the wallet run owns, cleared each time it runs.
WALLET_KINDS = ("wallet_overdrawn", "wallet_disagrees", "supply_period_closed")


class ResCompanySupply(models.Model):
    _inherit = "res.company"

    usl_b2c_supply_partner_id = fields.Many2one(
        "res.partner",
        string="Print-on-demand supplier",
        ondelete="restrict",
        help="Who fulfils print-on-demand orders and draws on the wallet for them.",
    )
    usl_b2c_wallet_journal_id = fields.Many2one(
        "account.journal",
        string="Supplier wallet",
        domain="[('type', 'in', ('bank', 'cash'))]",
        ondelete="restrict",
        help="The journal holding the prepaid balance the supplier draws on. Its "
             "account is what a top-up pays into and what a month's supply "
             "empties.",
    )
    usl_b2c_supply_product_id = fields.Many2one(
        "product.product",
        string="Supply of goods sold",
        ondelete="restrict",
        help="What the supplier's fulfilment of a sale is bought as, so it reaches "
             "the cost-of-sales account.",
    )
    usl_b2c_internal_supply_product_id = fields.Many2one(
        "product.product",
        string="Supply for no sale",
        ondelete="restrict",
        help="What a fulfilment answering to no sale is bought as. It is the cost "
             "of finding out what to sell, never the cost of something sold.",
    )

    def _usl_b2c_wallet_account(self):
        """Return the account the supplier's wallet is held in."""
        self.ensure_one()
        journal = self.usl_b2c_wallet_journal_id
        return journal.default_account_id


class B2cSupplySettlement(models.Model):
    _name = "b2c.supply.settlement"
    _description = "B2C Supply Settlement"
    _order = "id desc"

    company_id = fields.Many2one("res.company", required=True, index=True, ondelete="cascade")
    event_id = fields.Many2one(
        "b2c.fulfilment.event",
        required=True,
        index=True,
        ondelete="cascade",
        string="Fulfilment",
    )
    move_id = fields.Many2one(
        "account.move",
        required=True,
        index=True,
        ondelete="restrict",
        string="Document",
    )
    batch_id = fields.Many2one("b2c.import.batch", index=True, ondelete="set null")
    currency_id = fields.Many2one("res.currency", required=True, ondelete="restrict")
    cogs_amount = fields.Monetary(
        currency_field="currency_id",
        help="What of this fulfilment's cost this document settled.",
    )
    company_currency_id = fields.Many2one(
        related="company_id.currency_id",
        string="Company currency",
    )
    company_cogs_amount = fields.Monetary(currency_field="company_currency_id")


class B2cImportBatchWallet(models.Model):
    _inherit = "b2c.import.batch"

    wallet_move_ids = fields.Many2many(
        "account.move",
        "b2c_import_batch_wallet_move_rel",
        string="Wallet documents",
        copy=False,
    )
    wallet_balance = fields.Monetary(
        compute="_compute_wallet_balance",
        currency_field="supplier_currency_id",
        help="What is left of the prepaid balance the supplier draws on.",
    )

    def _compute_wallet_balance(self):
        for batch in self:
            batch.wallet_balance = batch._wallet_position()

    def action_settle_wallet(self):
        """Bill the supplier for what this drop shows it drew on the wallet."""
        for batch in self:
            if batch.state != "applied":
                raise UserError(batch.env._("Apply the drop before settling its supply."))
            batch.issue_ids.filtered(lambda issue: issue.kind in WALLET_KINDS).unlink()
            batch._assert_supply_configured()
            settled = batch.env["account.move"]
            for (period, currency), residuals in batch._unsettled_supply().items():
                settled |= batch._supply_document(period, currency, residuals)
            # A document already settled stays settled: a second run that
            # finds nothing new must not forget what the first one did.
            batch.write({"wallet_move_ids": [Command.link(move.id) for move in settled]})
            batch._check_wallet_position()
            batch._check_supply_against_statement()
            batch.write({"report": batch._build_report()})
        return True

    def action_open_wallet_documents(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": self.env._("Supply settled by this import"),
            "res_model": "account.move",
            "view_mode": "list,form",
            "domain": [("id", "in", self.wallet_move_ids.ids)],
        }

    # -- what is owed ------------------------------------------------------

    def _assert_supply_configured(self):
        """Refuse to settle supply the company has not said how to buy."""
        self.ensure_one()
        company = self.company_id
        missing = [
            name
            for name, value in (
                (self.env._("a supplier"), company.usl_b2c_supply_partner_id),
                (self.env._("a wallet journal"), company.usl_b2c_wallet_journal_id),
                (self.env._("a supply product"), company.usl_b2c_supply_product_id),
                (
                    self.env._("a product for supply answering to no sale"),
                    company.usl_b2c_internal_supply_product_id,
                ),
            )
            if not value
        ]
        if missing:
            raise UserError(
                self.env._(
                    "%(company)s names no %(missing)s, so what the supplier drew "
                    "cannot leave the wallet.",
                    company=company.display_name,
                    missing=", ".join(missing),
                ),
            )
        if not company._usl_b2c_wallet_account():
            raise UserError(
                self.env._(
                    "%(journal)s holds no account, so the wallet has no balance to "
                    "empty.",
                    journal=company.usl_b2c_wallet_journal_id.display_name,
                ),
            )

    def _unsettled_supply(self):
        """Return what each month still owes the supplier, per currency.

        A fulfilment already settled owes nothing.  One whose cost has since
        changed — a refund, a correction — owes the difference, which is what
        keeps a second drop a correction rather than a second charge.
        """
        self.ensure_one()
        settled = self._settled_amounts()
        owed = defaultdict(dict)
        for event in self.fulfilment_event_ids:
            if not event.event_date:
                continue
            residual = Decimal(str(event.cogs_amount)) - settled.get(event.id, Decimal("0"))
            if abs(residual) < CENT:
                continue
            period = event.event_date.date().replace(day=1)
            owed[period, event.currency_id][event] = residual
        return dict(sorted(owed.items(), key=lambda item: (item[0][0], item[0][1].name)))

    def _settled_amounts(self):
        """Return how much of each fulfilment's cost already reached a document."""
        self.ensure_one()
        found = defaultdict(Decimal)
        for settlement in self.env["b2c.supply.settlement"].search(
            [
                ("company_id", "=", self.company_id.id),
                ("event_id", "in", self.fulfilment_event_ids.ids),
                ("move_id.state", "!=", "cancel"),
            ],
        ):
            found[settlement.event_id.id] += Decimal(str(settlement.cogs_amount))
        return found

    # -- the document ------------------------------------------------------

    def _supply_document(self, period, currency, residuals):
        """Bill, or credit, one month of what the supplier drew in one currency."""
        self.ensure_one()
        date = self._period_end(period)
        by_purpose = defaultdict(Decimal)
        for event, residual in residuals.items():
            by_purpose[bool(event.order_id)] += residual
        total = sum(by_purpose.values())
        if all(abs(amount) < CENT for amount in by_purpose.values()):
            return self.env["account.move"]
        # A month whose cost of sales and prototyping cancel each other out
        # still owes both accounts their entry; only the wallet is untouched.
        if not self._period_open(date):
            self._report_closed_period(
                "supply_period_closed",
                self.env._("what the supplier drew"), period, currency, total,
            )
            return self.env["account.move"]
        credit = total < 0
        sign = Decimal("-1") if credit else Decimal("1")
        company = self.company_id
        move = self.env["account.move"].create(
            {
                "move_type": "in_refund" if credit else "in_invoice",
                "company_id": company.id,
                "partner_id": company.usl_b2c_supply_partner_id.id,
                "invoice_date": date,
                "date": date,
                "currency_id": currency.id,
                "ref": self._supply_reference(period, currency),
                "invoice_line_ids": [
                    Command.create(
                        {
                            "product_id": (
                                company.usl_b2c_supply_product_id.id
                                if sold
                                else company.usl_b2c_internal_supply_product_id.id
                            ),
                            "name": self._supply_label(period, sold),
                            "quantity": 1,
                            "price_unit": float(sign * amount),
                        },
                    )
                    for sold, amount in sorted(by_purpose.items(), reverse=True)
                    if abs(amount) >= CENT
                ],
            },
        )
        move.action_post()
        if move.currency_id.compare_amounts(move.amount_total, 0):
            self._pay_from_wallet(move, date)
        self._record_settlements(move, residuals)
        return move

    def _supply_reference(self, period, currency):
        """Return a reference no month can hold twice, and a late cost can."""
        self.ensure_one()
        base = f"printful:wallet:{period:%Y-%m}:{currency.name}"
        Move = self.env["account.move"]
        taken = Move.search_count(
            [
                ("company_id", "=", self.company_id.id),
                ("ref", "=like", f"{base}%"),
                ("state", "!=", "cancel"),
            ],
        )
        return base if not taken else f"{base}#{taken + 1}"

    def _supply_label(self, period, sold):
        if sold:
            return self.env._("Supply of goods sold — %(period)s", period=f"{period:%B %Y}")
        return self.env._(
            "Supply answering to no sale — %(period)s",
            period=f"{period:%B %Y}",
        )

    def _pay_from_wallet(self, move, date):
        """Empty the wallet by exactly what the supplier drew from it."""
        self.ensure_one()
        journal = self.company_id.usl_b2c_wallet_journal_id
        credit = move.move_type == "in_refund"
        methods = (
            journal.inbound_payment_method_line_ids
            if credit
            else journal.outbound_payment_method_line_ids
        )
        self.env["account.payment.register"].with_context(
            active_model="account.move",
            active_ids=move.ids,
        ).create(
            {
                "journal_id": journal.id,
                "payment_date": date,
                "payment_method_line_id": methods[:1].id,
            },
        ).action_create_payments()

    def _record_settlements(self, move, residuals):
        """Record which fulfilment each document settled, and by how much."""
        self.ensure_one()
        rate = {}
        self.env["b2c.supply.settlement"].create(
            [
                {
                    "company_id": self.company_id.id,
                    "event_id": event.id,
                    "move_id": move.id,
                    "batch_id": self.id,
                    "currency_id": event.currency_id.id,
                    "cogs_amount": float(residual),
                    "company_cogs_amount": float(
                        self._in_company_currency(event, residual, rate),
                    ),
                }
                for event, residual in residuals.items()
            ],
        )
        settled = self.env["b2c.fulfilment.event"].browse(
            [event.id for event in residuals],
        )
        settled.sudo().write(
            {
                "accounting_link_state": "verified",
                "accounting_link_note": self.env._(
                    "Settled by %(move)s.", move=move.name,
                ),
            },
        )

    def _in_company_currency(self, event, residual, rate):
        """Return a residual in the currency the books are kept in."""
        self.ensure_one()
        currency = event.currency_id
        company_currency = self.company_id.currency_id
        if currency == company_currency:
            return residual
        stated = Decimal(str(event.cogs_amount))
        if stated:
            # The event states both amounts for the same cost, so the rate it
            # was converted at is the rate its correction belongs at too.
            return residual * Decimal(str(event.company_cogs_amount)) / stated
        key = (currency.id, event.event_date.date())
        if key not in rate:
            rate[key] = Decimal(
                str(
                    currency._convert(
                        1.0, company_currency, self.company_id, event.event_date,
                    ),
                ),
            )
        return residual * rate[key]

    # -- what is left ------------------------------------------------------

    def _wallet_position(self):
        """Return what remains of the prepaid balance, as the ledger holds it."""
        self.ensure_one()
        account = self.company_id._usl_b2c_wallet_account()
        if not account:
            return 0.0
        found = self.env["account.move.line"]._read_group(
            [
                ("company_id", "=", self.company_id.id),
                ("account_id", "=", account.id),
                ("parent_state", "=", "posted"),
            ],
            aggregates=["balance:sum"],
        )
        return found[0][0] or 0.0

    def _check_supply_against_statement(self):
        """Compare what the supplier says it drew with what Odoo says it cost.

        The cost of a fulfilment is read from the order the supplier fulfilled;
        what it actually took out of the wallet is stated only in the account
        the supplier keeps of itself.  They should be the same number.  When
        they are not, either a fulfilment never reached Odoo or a cost changed
        after it did, and both are things a person has to look at — so this
        reports the difference and never quietly prefers one side.
        """
        self.ensure_one()
        drawn = defaultdict(Decimal)
        for row in self.row_ids.filtered(
            lambda item: item.grain == "charge"
            and item.provider == "printful"
            and item.occurred_at,
        ):
            values = row.values or {}
            if values.get("entry_kind") not in ("supply", "supply_refund"):
                continue
            drawn[row.occurred_at.date().replace(day=1)] += -Decimal(
                str(values.get("wallet_amount") or "0"),
            )
        held = defaultdict(Decimal)
        for event in self.fulfilment_event_ids.filtered("event_date"):
            held[event.event_date.date().replace(day=1)] += Decimal(str(event.cogs_amount))
        if not (drawn and held):
            # A drop carrying a statement and no supplier read has nothing to
            # disagree with. Saying every month differs would be true and
            # useless, and would bury the months that really do.
            return False
        found = False
        # Only the months the supplier read covered: a statement reaches back
        # years further than the fulfilments this drop asked about.
        for period in sorted(held):
            difference = drawn[period] - held[period]
            if abs(difference) < CENT:
                continue
            found = True
            self._raise_issue(
                "wallet_disagrees",
                self.env._(
                    "The supplier drew %(drawn)s in %(period)s; Odoo holds "
                    "%(held)s of fulfilment",
                    drawn=drawn[period],
                    period=f"{period:%B %Y}",
                    held=held[period],
                ),
                severity="advisory",
                note=self.env._(
                    "A difference of %(difference)s. Either a fulfilment has "
                    "not reached Odoo, or one cost something other than what "
                    "it was read as.",
                    difference=difference,
                ),
            )
        return found

    def _check_wallet_position(self):
        """Say so when the supplier has drawn more than was ever paid in."""
        self.ensure_one()
        account = self.company_id._usl_b2c_wallet_account()
        balance = self._wallet_position()
        if not account or balance >= 0:
            return False
        self._raise_issue(
            "wallet_overdrawn",
            self.env._(
                "The supplier has drawn %(amount)s more than the wallet holds",
                amount=-balance,
            ),
            severity="advisory",
            note=self.env._(
                "%(account)s is in credit, which a prepaid balance cannot be. A "
                "top-up paid from the bank is most likely still unreconciled.",
                account=account.display_name,
            ),
        )
        return True
