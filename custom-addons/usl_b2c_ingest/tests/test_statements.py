"""What the parties holding the money say they kept, paid out and were paid.

An order export says what the customer paid.  It does not say what the channel
kept, and for a shop of one's own it cannot: the shop keeps nothing and the
processor keeps everything.  These are the tests for reading the accounts those
parties keep of themselves, and for making the ledger agree with them.
"""

from decimal import Decimal

from odoo.tests import tagged
from odoo.tools.binary import BinaryBytes

from . import fixtures
from .test_wallet import TestWallet

STATEMENTS = {
    "etsy-statement.csv": fixtures.etsy_statement(),
    "stripe-balance.csv": fixtures.stripe_balance_history(),
    "printful-transactions.csv": fixtures.printful_transactions(),
}


@tagged("post_install", "-at_install", "usl_b2c_ingest")
class TestStatementReading(TestWallet):
    """Reading a statement, whatever shape the party sends it in."""

    def _read(self, **files):
        batch = self._batch(**files)
        batch.action_parse()
        return batch

    def _entries(self, batch, provider):
        return batch.row_ids.filtered(
            lambda row: row.grain == "charge" and row.provider == provider,
        )

    def test_each_statement_is_recognised_by_the_columns_its_writer_writes(self):
        batch = self._read(**STATEMENTS)
        self.assertEqual(
            {source.format_id for source in batch.file_ids},
            {"etsy_statement", "stripe_balance_history", "printful_transactions"},
        )
        self.assertFalse(batch.file_ids.filtered(lambda item: item.state == "unreadable"))

    def test_a_stripe_export_is_the_same_export_with_or_without_metadata(self):
        """Stripe writes one column per metadata key an account happens to use."""
        bare = self._read(**{"stripe.csv": fixtures.stripe_balance_history(metadata=False)})
        self.assertEqual(bare.file_ids.format_id, "stripe_balance_history")
        self.assertEqual(len(self._entries(bare, "stripe")), 3)

    def test_a_statement_entry_answers_to_no_order(self):
        batch = self._read(**STATEMENTS)
        self.assertEqual(batch.order_count, 0)
        self.assertEqual(batch.line_count, 0)
        self.assertEqual(batch.statement_entry_count, 15)
        self.assertEqual(
            set(batch.row_ids.mapped("resolution")),
            {"statement"},
        )

    def test_a_statement_does_not_widen_the_period_it_reports_on(self):
        """A statement covers a year; the supplier read must not follow it there."""
        batch = self._read(
            **{
                "etsy-orders.csv": fixtures.etsy_orders(),
                "etsy-items.csv": fixtures.etsy_order_items(),
                "etsy-statement.csv": fixtures.etsy_statement(),
            },
        )
        self.assertEqual(str(batch.period_end), "2026-03-09")

    def test_identical_entries_are_each_themselves(self):
        """Etsy writes hundreds of identical listing fees; none may collapse."""
        batch = self._read(**{"etsy-statement.csv": fixtures.etsy_statement()})
        listings = self._entries(batch, "etsy").filtered(
            lambda row: "Listing fee" in (row.values or {}).get("description", ""),
        )
        self.assertEqual(len(listings), 2)
        self.assertEqual(len(set(listings.mapped("external_line_id"))), 2)

    def test_what_etsy_paid_out_is_read_from_the_sentence_stating_it(self):
        batch = self._read(**{"etsy-statement.csv": fixtures.etsy_statement()})
        deposit = self._entries(batch, "etsy").filtered(
            lambda row: (row.values or {}).get("entry_kind") == "payout",
        )
        self.assertEqual(len(deposit), 1)
        self.assertEqual(Decimal(deposit.values["gross_amount"]), Decimal("-104.00"))

    def test_a_statement_entry_without_a_date_does_not_end_the_drop(self):
        """Etsy writes a summary line carrying no date, and it settles nothing.

        Every run that reads a statement asks for a date before it looks at
        anything else, so such a line reaches none of them. Reading it must
        still leave the rest of the statement readable: the alternative is a
        month of real entries lost to a line that states nothing.
        """
        batch = self._read(
            **{
                "etsy-statement.csv": fixtures.etsy_statement(
                    (
                        {
                            "Date": "",
                            "Type": "Fee",
                            "Title": "Total fees for March 2026",
                            "Info": "",
                            "Currency": "EUR",
                            "Amount": "--",
                            "Fees & Taxes": "-€15.20",
                            "Net": "-€15.20",
                        },
                        *fixtures.ETSY_STATEMENT_ROWS,
                    ),
                ),
            },
        )
        self.assertFalse(batch.file_ids.filtered(lambda item: item.state == "unreadable"))
        undated = self._entries(batch, "etsy").filtered(lambda row: not row.occurred_at)
        self.assertEqual(len(undated), 1)
        self.assertEqual(
            len(self._entries(batch, "etsy")),
            len(fixtures.ETSY_STATEMENT_ROWS) + 1,
        )

    def test_a_marketplace_tax_is_not_a_fee(self):
        batch = self._read(**{"etsy-statement.csv": fixtures.etsy_statement()})
        kinds = {
            (row.values or {}).get("entry_kind") for row in self._entries(batch, "etsy")
        }
        self.assertIn("marketplace_tax", kinds)

    def test_stripes_own_charge_is_money_it_kept(self):
        """A billing charge is stated as a negative amount and no fee at all."""
        batch = self._read(**{"stripe.csv": fixtures.stripe_balance_history()})
        account_fee = self._entries(batch, "stripe").filtered(
            lambda row: (row.values or {}).get("entry_kind") == "account_fee",
        )
        self.assertEqual(Decimal(account_fee.values["fee_amount"]), Decimal("0.30"))

    def test_a_subscription_never_reaches_the_wallet(self):
        batch = self._read(**{"printful.csv": fixtures.printful_transactions()})
        subscription = self._entries(batch, "printful").filtered(
            lambda row: (row.values or {}).get("entry_kind") == "subscription",
        )
        self.assertEqual(Decimal(subscription.values["wallet_amount"]), 0)

    def test_a_top_up_that_did_not_go_through_moved_nothing(self):
        batch = self._read(**{"printful.csv": fixtures.printful_transactions()})
        failed = self._entries(batch, "printful").filtered(
            lambda row: not (row.values or {}).get("settled"),
        )
        self.assertEqual(len(failed), 1)
        self.assertEqual(Decimal(failed.values["wallet_amount"]), 0)

    def test_a_file_no_parser_knows_says_which_columns_it_lacks(self):
        batch = self._read(**{"nonsense.csv": b"alpha,beta\n1,2\n"})
        self.assertEqual(batch.file_ids.state, "unreadable")
        self.assertIn("missing", batch.file_ids.note)


@tagged("post_install", "-at_install", "usl_b2c_ingest")
class TestStatementSettlement(TestWallet):
    """A statement is the whole account of a month, and orders are not."""

    def _applied_with(self, **files):
        """Return an applied Etsy drop carrying whatever else is given."""
        batch, _jersey, _cap = self._etsy_drop()
        for name, content in files.items():
            self.env["b2c.import.file"].create(
                {"batch_id": batch.id, "name": name, "content": BinaryBytes(content)},
            )
        batch.action_parse()
        batch.action_resolve()
        batch.action_apply()
        batch.action_invoice()
        return batch

    def _bills(self, batch):
        return sum(
            bill.amount_total * (-1 if bill.move_type == "in_refund" else 1)
            for bill in batch.fee_bill_ids
        )

    def test_the_statement_outranks_the_orders_own_account_of_themselves(self):
        """Etsy prints the processing fee on an order and keeps four more."""
        batch = self._applied_with(**{"etsy-statement.csv": fixtures.etsy_statement()})
        batch.action_bill_fees()
        # 5.00 processing + 7.80 transaction + 0.20 + 0.20 listing + 2.00 ads.
        # Never 5.00 + what the orders printed on top of it.
        self.assertAlmostEqual(self._bills(batch), 15.20, places=2)

    def test_a_statement_dropped_later_bills_only_the_difference(self):
        orders_only = self._applied_with()
        orders_only.action_bill_fees()
        first = self._bills(orders_only)
        self.assertAlmostEqual(first, 5.00, places=2)

        later = self._batch(**{"etsy-statement.csv": fixtures.etsy_statement()})
        later.action_parse()
        later.action_resolve()
        later.action_apply()
        later.action_bill_fees()
        self.assertAlmostEqual(self._bills(later), 15.20 - first, places=2)

    def test_the_exports_can_arrive_in_either_order(self):
        statement_first = self._batch(**{"etsy-statement.csv": fixtures.etsy_statement()})
        statement_first.action_parse()
        statement_first.action_resolve()
        statement_first.action_apply()
        statement_first.action_bill_fees()

        orders_after = self._applied_with()
        orders_after.action_bill_fees()

        self.assertAlmostEqual(
            self._bills(statement_first) + self._bills(orders_after), 15.20, places=2,
        )

    def test_a_channel_with_only_its_own_orders_says_so(self):
        batch = self._applied_with()
        batch.action_bill_fees()
        finding = batch.issue_ids.filtered(
            lambda issue: issue.kind == "fee_source_missing",
        )
        self.assertTrue(finding)
        self.assertEqual(finding.severity, "advisory")

    def test_two_parties_stating_one_month_do_not_cancel_each_other(self):
        """A channel states its own commission or names a processor, not both.

        Counted per channel, a second document for the same month would
        subtract the first rather than add to it, so the fuller account is
        believed and the configuration is reported.
        """
        self.channels["etsy"].write({
            "processor_provider": "stripe",
            "processor_partner_id": self.operator.id,
            "processor_fee_product_id": self.commission_product.id,
        })
        batch = self._applied_with(
            **{
                "etsy-statement.csv": fixtures.etsy_statement(),
                "stripe-balance.csv": fixtures.stripe_balance_history(),
            },
        )
        batch.action_bill_fees()
        self.assertTrue(
            batch.issue_ids.filtered(lambda issue: issue.kind == "fee_source_conflict"),
        )
        # Etsy's 15.20 is the fuller account of March; Stripe's 1.34 is not
        # billed, and above all is not credited back out of Etsy's.
        self.assertGreater(self._bills(batch), 0)
        self.channels["etsy"].processor_provider = False

    def test_a_channel_with_a_statement_says_nothing(self):
        batch = self._applied_with(**{"etsy-statement.csv": fixtures.etsy_statement()})
        batch.action_bill_fees()
        self.assertFalse(
            batch.issue_ids.filtered(lambda issue: issue.kind == "fee_source_missing"),
        )

    def test_a_month_the_books_are_closed_to_is_reported_not_refused(self):
        """A statement covers years; only some of them are still open."""
        batch = self._applied_with(**{"etsy-statement.csv": fixtures.etsy_statement()})
        self.company.fiscalyear_lock_date = "2026-12-31"
        batch.action_bill_fees()
        self.assertFalse(batch.fee_bill_ids)
        finding = batch.issue_ids.filtered(
            lambda issue: issue.kind == "fee_period_closed",
        )
        self.assertTrue(finding)
        self.assertEqual(finding[0].severity, "advisory")
        self.company.fiscalyear_lock_date = False

    def test_billing_the_same_month_again_changes_nothing(self):
        batch = self._applied_with(**{"etsy-statement.csv": fixtures.etsy_statement()})
        batch.action_bill_fees()
        billed = self._bills(batch)
        settlements = self.env["b2c.channel.settlement"].search_count(
            [("company_id", "=", self.company.id)],
        )
        batch.action_bill_fees()
        self.assertAlmostEqual(self._bills(batch), billed, places=2)
        self.assertEqual(
            self.env["b2c.channel.settlement"].search_count(
                [("company_id", "=", self.company.id)],
            ),
            settlements,
        )


@tagged("post_install", "-at_install", "usl_b2c_ingest")
class TestTransfers(TestWallet):
    """A payout is one entry, so one bank line has one thing to meet."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.suspense = cls._account("Invented suspense", "asset_current")
        cls.bank_journal = cls.env["account.journal"].create(
            {
                "name": "Invented bank",
                "code": "INVB",
                "type": "bank",
                "company_id": cls.company.id,
                "default_account_id": cls._account("Invented bank account", "asset_cash").id,
                "suspense_account_id": cls.suspense.id,
            },
        )
        cls.transfer_journal = cls.env["account.journal"].create(
            {
                "name": "Invented transfers",
                "code": "INVT",
                "type": "general",
                "company_id": cls.company.id,
            },
        )
        cls.company.write(
            {
                "usl_b2c_bank_journal_id": cls.bank_journal.id,
                "usl_b2c_transfer_journal_id": cls.transfer_journal.id,
            },
        )
        cls.channels["medusa"].write({"processor_provider": "stripe"})

    def _moved(self, **files):
        batch = self._batch(**files)
        batch.action_parse()
        batch.action_resolve()
        batch.action_apply()
        batch.action_post_transfers()
        return batch

    def _balance(self, account):
        return round(
            sum(
                self.env["account.move.line"].search(
                    [
                        ("account_id", "=", account.id),
                        ("parent_state", "=", "posted"),
                    ],
                ).mapped("balance"),
            ),
            2,
        )

    def test_a_payout_becomes_one_entry_waiting_for_the_bank(self):
        batch = self._moved(**{"etsy-statement.csv": fixtures.etsy_statement()})
        self.assertEqual(len(batch.transfer_move_ids), 1)
        self.assertAlmostEqual(self._balance(self.suspense), 104.00, places=2)
        self.assertAlmostEqual(self._balance(self.clearing), -104.00, places=2)

    def test_a_top_up_leaves_the_bank_and_arrives_in_the_wallet(self):
        batch = self._moved(**{"printful.csv": fixtures.printful_transactions()})
        self.assertEqual(len(batch.transfer_move_ids), 1)
        self.assertAlmostEqual(self._balance(self.wallet_account), 250.00, places=2)
        self.assertAlmostEqual(self._balance(self.suspense), -250.00, places=2)

    def test_stripe_pays_out_of_the_channel_it_collected_for(self):
        batch = self._moved(**{"stripe.csv": fixtures.stripe_balance_history()})
        self.assertEqual(len(batch.transfer_move_ids), 1)
        self.assertAlmostEqual(self._balance(self.suspense), 58.66, places=2)

    def test_two_payouts_on_one_day_are_two_movements(self):
        """A day is not an identity, and Etsy states no identity of its own.

        A shop paid out in two currencies is paid out twice on the same day,
        and Etsy also makes an extra deposit on request. Keyed on the day, the
        second one is read as the first and never reaches the bank — money
        that moved, with nothing in the ledger to meet its statement line.
        """
        deposits = tuple(
            row for row in fixtures.ETSY_STATEMENT_ROWS if row["Type"] != "Deposit"
        ) + (
            {
                "Date": "March 31, 2026",
                "Type": "Deposit",
                "Title": "€104.00 sent to your bank account",
                "Info": "",
                "Currency": "EUR",
                "Amount": "--",
                "Fees & Taxes": "--",
                "Net": "--",
            },
            {
                "Date": "March 31, 2026",
                "Type": "Deposit",
                "Title": "£88.00 sent to your bank account",
                "Info": "",
                "Currency": "GBP",
                "Amount": "--",
                "Fees & Taxes": "--",
                "Net": "--",
            },
        )
        batch = self._moved(**{"etsy-statement.csv": fixtures.etsy_statement(deposits)})
        moved = self.env["b2c.money.transfer"].search(
            [("move_id", "in", batch.transfer_move_ids.ids)],
        )
        self.assertEqual(len(moved), 2)
        self.assertEqual(len(set(moved.mapped("entry_key"))), 2)
        # And running it again still recognises both, rather than posting a
        # third under a key that has since changed shape.
        batch.action_post_transfers()
        self.assertEqual(
            self.env["b2c.money.transfer"].search_count(
                [("move_id", "in", batch.transfer_move_ids.ids)],
            ),
            2,
        )

    def test_moving_the_same_statement_again_moves_nothing(self):
        batch = self._moved(**STATEMENTS)
        moved = batch.transfer_move_ids
        suspense = self._balance(self.suspense)
        batch.action_post_transfers()
        self.assertEqual(batch.transfer_move_ids, moved)
        self.assertAlmostEqual(self._balance(self.suspense), suspense, places=2)

    def test_a_wider_export_adds_only_what_is_new(self):
        first = self._moved(**{"printful.csv": fixtures.printful_transactions()})
        wider = self._moved(
            **{
                "printful.csv": fixtures.printful_transactions(
                    (
                        *fixtures.PRINTFUL_TRANSACTION_ROWS,
                        {
                            "Payment": "Deposit to Wallet 539044********1921",
                            "Status": "Completed",
                            "Amount": "€75.00",
                            "Date": "2026-04-02T09:00",
                            "ID": "900000005",
                        },
                    ),
                ),
            },
        )
        self.assertEqual(len(first.transfer_move_ids), 1)
        self.assertEqual(len(wider.transfer_move_ids), 1)
        self.assertAlmostEqual(self._balance(self.wallet_account), 325.00, places=2)

    def test_a_movement_the_ledger_already_shows_is_not_posted_again(self):
        """The reconstruction posted a payout per bank line, under its own name.

        Its reference says nothing a statement entry could be matched to, so
        what is recognised is the movement itself.
        """
        bank = self._account("Invented other bank", "asset_cash")
        standing = self.env["account.move"].create(
            {
                "company_id": self.company.id,
                "journal_id": self.transfer_journal.id,
                # Dated when it reached the bank, not when Etsy sent it.
                "date": "2026-04-03",
                "ref": "etsy:payout:BNK1/25-26/0326",
                "line_ids": [
                    (0, 0, {"account_id": bank.id, "balance": 104.00}),
                    (0, 0, {"account_id": self.clearing.id, "balance": -104.00}),
                ],
            },
        )
        standing.action_post()
        batch = self._moved(**{"etsy-statement.csv": fixtures.etsy_statement()})
        self.assertFalse(batch.transfer_move_ids)
        finding = batch.issue_ids.filtered(
            lambda issue: issue.kind == "transfer_already_posted",
        )
        self.assertTrue(finding)
        self.assertIn("etsy:payout:BNK1/25-26/0326", finding[0].note)

    def test_a_top_up_the_ledger_already_shows_is_not_posted_again(self):
        """Money leaves the account it was held in, whichever way it went.

        A top-up leaves the bank, so what an earlier posting left on the bank's
        suspense account is a credit — the same side a payout leaves on the
        clearing account.
        """
        standing = self.env["account.move"].create(
            {
                "company_id": self.company.id,
                "journal_id": self.transfer_journal.id,
                "date": "2026-03-02",
                "ref": "printful:topup:BNK1/25-26/0159",
                "line_ids": [
                    (0, 0, {"account_id": self.wallet_account.id, "balance": 250.00}),
                    (0, 0, {"account_id": self.suspense.id, "balance": -250.00}),
                ],
            },
        )
        standing.action_post()
        batch = self._moved(**{"printful.csv": fixtures.printful_transactions()})
        self.assertFalse(batch.transfer_move_ids)
        self.assertTrue(
            batch.issue_ids.filtered(
                lambda issue: issue.kind == "transfer_already_posted",
            ),
        )

    def test_settling_the_drop_does_every_run_in_order(self):
        """One button holds the order so nobody has to hold it in their head."""
        batch = self._moved(**{"etsy-statement.csv": fixtures.etsy_statement()})
        again = self._batch(**{"etsy-statement.csv": fixtures.etsy_statement()})
        again.action_parse()
        again.action_resolve()
        again.action_apply()
        again.action_settle()
        # The second drop states what the first already settled, so it adds
        # nothing — which is what makes the one button safe to press twice.
        self.assertFalse(again.transfer_move_ids)
        self.assertTrue(batch.transfer_move_ids)

    def test_transfers_cannot_be_posted_before_the_drop_is_applied(self):
        batch = self._batch(**{"printful.csv": fixtures.printful_transactions()})
        batch.action_parse()
        with self.assertRaises(Exception):
            batch.action_post_transfers()


@tagged("post_install", "-at_install", "usl_b2c_ingest")
class TestSupplyAgainstStatement(TestWallet):
    """The supplier's account of a month and Odoo's have to be the same."""

    def test_a_month_the_supplier_states_differently_is_reported(self):
        batch = self._drop()
        self.env["b2c.import.file"].create(
            {
                "batch_id": batch.id,
                "name": "printful.csv",
                # The supplier says it drew 20.00 in March, which is what the
                # fulfilment cost, plus 5.00 more for something Odoo never saw.
                "content": BinaryBytes(
                    fixtures.printful_transactions(
                        (
                            {
                                "Payment": "Order #9000000001 wallet",
                                "Status": "Completed",
                                "Amount": "€20.00",
                                "Date": "2026-03-05T12:00",
                                "ID": "900000010",
                            },
                            {
                                "Payment": "Order #9000000099 wallet",
                                "Status": "Completed",
                                "Amount": "€5.00",
                                "Date": "2026-03-06T12:00",
                                "ID": "900000011",
                            },
                        ),
                    ),
                ),
            },
        )
        batch.action_parse()
        batch.action_resolve()
        batch.action_apply()
        self._top_up(500)
        batch.action_settle_wallet()
        finding = batch.issue_ids.filtered(lambda issue: issue.kind == "wallet_disagrees")
        self.assertEqual(len(finding), 1)
        self.assertIn("5.00", finding.note)
        self.assertIn("5.00", finding.name)

    def test_a_statement_with_no_supplier_read_disagrees_with_nothing(self):
        """Without the supplier read there is nothing to compare against.

        Saying every month differs would be true and useless, and would bury
        the months that really do.
        """
        batch = self._batch(
            **{"printful.csv": fixtures.printful_transactions()},
        )
        batch.action_parse()
        batch.action_resolve()
        batch.action_apply()
        self._top_up(500)
        batch.action_settle_wallet()
        self.assertFalse(
            batch.issue_ids.filtered(lambda issue: issue.kind == "wallet_disagrees"),
        )

    def test_a_month_both_agree_on_is_not_reported(self):
        batch = self._drop()
        self.env["b2c.import.file"].create(
            {
                "batch_id": batch.id,
                "name": "printful.csv",
                "content": BinaryBytes(
                    fixtures.printful_transactions(
                        (
                            {
                                "Payment": "Order #9000000001 wallet",
                                "Status": "Completed",
                                "Amount": "€20.00",
                                "Date": "2026-03-05T12:00",
                                "ID": "900000010",
                            },
                        ),
                    ),
                ),
            },
        )
        batch.action_parse()
        batch.action_resolve()
        batch.action_apply()
        self._top_up(500)
        batch.action_settle_wallet()
        self.assertFalse(
            batch.issue_ids.filtered(lambda issue: issue.kind == "wallet_disagrees"),
        )


@tagged("post_install", "-at_install", "usl_b2c_ingest")
class TestAdoptingTheReconstruction(TestWallet):
    """A month settled before this tool existed is a month it leaves alone."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.migration = cls._migration()

    @staticmethod
    def _migration():
        """Return the adoption, which install and upgrade both run."""
        from odoo.addons.usl_b2c_ingest import hooks

        return hooks

    def _reconstruction_entry(self, ref, amount, account):
        """Post an entry in the shape the reconstruction wrote them.

        One document for a whole month: the sales, the receipts that settled
        them, and — the only part that was bought — what it charged to expense.
        """
        held = self._account(f"Invented held {ref}", "asset_current")
        sold = self._account(f"Invented sold {ref}", "income")
        move = self.env["account.move"].create(
            {
                "company_id": self.company.id,
                "journal_id": self.env["account.journal"].search(
                    [("company_id", "=", self.company.id), ("type", "=", "general")],
                    limit=1,
                ).id,
                "date": "2026-03-31",
                "ref": ref,
                "line_ids": [
                    (0, 0, {"account_id": held.id, "balance": 100.00}),
                    (0, 0, {"account_id": sold.id, "balance": -100.00}),
                    (0, 0, {"account_id": account.id, "balance": amount}),
                    (0, 0, {"account_id": held.id, "balance": -amount}),
                ],
            },
        )
        move.action_post()
        return move

    def test_a_month_of_supply_it_settled_is_not_billed_again(self):
        batch = self._drop()
        self._top_up(500)
        self._reconstruction_entry(
            "printful:wallet:consumption:2026-03", 20.00, self.cost_of_sales,
        )
        self.migration._adopt_supply_months(self.env)
        batch.action_settle_wallet()
        self.assertFalse(batch.wallet_move_ids)

    def test_only_what_a_month_charged_to_expense_counts_as_settled(self):
        """A reconstruction entry states a whole month, not only its fee.

        Its own total is the month's turnover; reading that as commission
        already bought would claim ten times what the channel kept.
        """
        move = self._reconstruction_entry("etsy:wallet:2026-03", 5.00, self.commissions)
        self.assertGreater(move.amount_total, 5.00)
        self.migration.adopt_reconstruction(self.env)
        settlement = self.env["b2c.channel.settlement"].search(
            [("move_id", "=", move.id)],
        )
        self.assertAlmostEqual(settlement.fee_amount, 5.00, places=2)

    def test_a_month_of_commission_it_settled_is_not_billed_again(self):
        batch, _jersey, _cap = self._etsy_drop()
        batch.action_apply()
        batch.action_invoice()
        self._reconstruction_entry("etsy:wallet:2026-03", 5.00, self.commissions)
        self.migration._adopt_channel_months(self.env)
        batch.action_bill_fees()
        self.assertFalse(batch.fee_bill_ids)

    def test_a_month_it_settled_for_less_is_billed_the_difference(self):
        """Adopting is not forgiving: a real disagreement is still owed."""
        batch, _jersey, _cap = self._etsy_drop()
        batch.action_apply()
        batch.action_invoice()
        self._reconstruction_entry("etsy:wallet:2026-03", 2.00, self.commissions)
        self.migration._adopt_channel_months(self.env)
        batch.action_bill_fees()
        self.assertAlmostEqual(batch.fee_bill_ids.amount_total, 3.00, places=2)

    def test_installing_and_upgrading_adopt_the_same_months(self):
        """Either can be the first time the tool meets a reconstructed ledger."""
        self._reconstruction_entry("etsy:wallet:2026-03", 5.00, self.commissions)
        self.migration.adopt_reconstruction(self.env)
        once = self.env["b2c.channel.settlement"].search_count(
            [("company_id", "=", self.company.id)],
        )
        self.assertEqual(once, 1)
        self.migration.post_init_hook(self.env)
        self.assertEqual(
            self.env["b2c.channel.settlement"].search_count(
                [("company_id", "=", self.company.id)],
            ),
            once,
        )

    def test_adopting_twice_records_one_settlement(self):
        self._reconstruction_entry("etsy:wallet:2026-03", 5.00, self.commissions)
        self.migration._adopt_channel_months(self.env)
        first = self.env["b2c.channel.settlement"].search_count(
            [("company_id", "=", self.company.id)],
        )
        self.migration._adopt_channel_months(self.env)
        self.assertEqual(
            self.env["b2c.channel.settlement"].search_count(
                [("company_id", "=", self.company.id)],
            ),
            first,
        )
