from datetime import timedelta
from unittest.mock import patch

from odoo import Command, fields
from odoo.exceptions import AccessError, UserError
from odoo.tests import tagged

from odoo.addons.account.tests.common import AccountTestInvoicingCommon


@tagged(
    "post_install",
    "-at_install",
    "usl_accounting_immediate_settlement",
    "usl_accounting_unit",
)
class TestImmediateSettlement(AccountTestInvoicingCommon):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env.user.group_ids = [
            Command.link(cls.env.ref("account.group_account_user").id),
        ]
        cls.company = cls.env.company
        cls.company.currency_id = cls.env.ref("base.EUR")
        cls.document_date = fields.Date.today() - timedelta(days=10)
        cls.statement_date = cls.document_date + timedelta(days=1)
        cls.foreign_currency = cls.setup_other_currency(
            "USD",
            rates=[
                (cls.document_date - timedelta(days=1), 5.0 / 4.38),
                (cls.document_date, 5.03 / 4.40),
            ],
        )
        cls.company.immediate_settlement_max_days = 3
        cls.company.immediate_settlement_max_rate_deviation = 3.0
        cls.bank_journal = cls.company_data["default_journal_bank"]
        cls.bank_journal.reconcile_mode = "edit"
        cls.bank_journal.suspense_account_id.reconcile = True
        cls.analytic_plan = cls.env["account.analytic.plan"].create(
            {"name": "Exact settlement test"},
        )
        cls.analytic_account = cls.env["account.analytic.account"].create(
            {
                "name": "Exact settlement test",
                "plan_id": cls.analytic_plan.id,
            },
        )

    def _document(
        self,
        move_type="in_invoice",
        foreign_amount=5.0,
        *,
        tax_ids=None,
        payment_term=None,
    ):
        is_purchase = move_type in ("in_invoice", "in_refund", "in_receipt")
        account = (
            self.company_data["default_account_expense"]
            if is_purchase
            else self.company_data["default_account_revenue"]
        )
        return self._create_invoice(
            move_type=move_type,
            partner_id=self.partner_a,
            company_id=self.company,
            currency_id=self.foreign_currency,
            journal_id=(
                self.company_data["default_journal_purchase"]
                if is_purchase
                else self.company_data["default_journal_sale"]
            ),
            invoice_date=self.document_date,
            date=self.document_date,
            invoice_payment_term_id=payment_term,
            invoice_line_ids=[
                self._prepare_invoice_line(
                    name="Cloudflare-style exact settlement",
                    account_id=account,
                    quantity=1.0,
                    price_unit=foreign_amount,
                    tax_ids=(
                        tax_ids
                        if tax_ids is not None
                        else self.env["account.tax"]
                    ),
                    analytic_distribution={
                        str(self.analytic_account.id): 100.0,
                    },
                ),
            ],
            post=True,
        )

    def _term_lines(self, document):
        return document.line_ids.filtered(
            lambda line: (
                line.account_id.account_type
                in ("asset_receivable", "liability_payable")
                and not line.reconciled
            ),
        )

    def _bank_candidate(
        self,
        document,
        company_amount=4.40,
        *,
        payment_date=None,
        foreign_amount=None,
    ):
        term_line = self._term_lines(document)[:1]
        statement_amount = (
            company_amount if term_line.balance > 0 else -company_amount
        )
        vals = {
            "journal_id": self.bank_journal.id,
            "date": payment_date or self.statement_date,
            "payment_ref": "CLOUDFLARE",
            "amount": statement_amount,
            "partner_id": document.commercial_partner_id.id,
        }
        if foreign_amount is not None:
            vals.update(
                {
                    "foreign_currency_id": self.foreign_currency.id,
                    "amount_currency": (
                        foreign_amount if statement_amount > 0 else -foreign_amount
                    ),
                },
            )
        statement_line = self.env["account.bank.statement.line"].create(vals)
        _liquidity, suspense_lines, _other = statement_line._seek_for_lines()
        return statement_line, suspense_lines

    def test_settle_replaces_synthetic_foreign_amount_and_keeps_native_fx(self):
        bill = self._document()
        term_line = self._term_lines(bill)
        self.assertAlmostEqual(abs(term_line.balance), 4.38, places=2)
        statement_line, source_line = self._bank_candidate(bill)
        liquidity_before = [
            (line.balance, line.amount_currency, line.currency_id)
            for line in statement_line._seek_for_lines()[0]
        ]
        synthetic_amount = bill._rebuild_payment_candidate_amount(source_line)
        self.assertAlmostEqual(synthetic_amount, 5.03, places=2)

        result = bill.js_settle_outstanding_line(source_line.id)
        settlement = self.env["account.immediate.settlement"].browse(
            result["settlement_id"],
        )

        self.assertEqual(settlement.mechanism, "bank_statement")
        self.assertEqual(settlement.foreign_amount_source, "document_residual")
        self.assertAlmostEqual(settlement.foreign_amount, 5.0, places=2)
        self.assertAlmostEqual(settlement.company_amount, 4.40, places=2)
        self.assertAlmostEqual(
            settlement.synthetic_foreign_amount,
            5.03,
            places=2,
        )
        self.assertAlmostEqual(
            settlement.reference_company_amount,
            4.38,
            places=2,
        )
        self.assertAlmostEqual(
            settlement.preview_settlement_difference,
            0.02,
            places=2,
        )
        self.assertAlmostEqual(settlement.settlement_difference, 0.02, places=2)
        self.assertEqual(settlement.settlement_difference_type, "loss")
        self.assertEqual(
            settlement.exchange_account_id,
            self.company.expense_currency_exchange_account_id,
        )
        self.assertAlmostEqual(
            sum(settlement.exchange_line_ids.mapped("balance")),
            0.02,
            places=2,
        )
        self.assertEqual(
            settlement.exchange_move_ids,
            settlement.partial_reconcile_ids.exchange_move_id,
        )
        self.assertTrue(settlement.exchange_move_names)
        self.assertEqual(statement_line.amount, -4.40)
        self.assertEqual(statement_line.foreign_currency_id, self.foreign_currency)
        self.assertEqual(statement_line.amount_currency, -5.0)
        self.assertEqual(
            statement_line.immediate_settlement_foreign_amount_source,
            "document_residual",
        )
        self.assertEqual(
            [
                (line.balance, line.amount_currency, line.currency_id)
                for line in statement_line._seek_for_lines()[0]
            ],
            liquidity_before,
        )
        self.assertFalse(statement_line._seek_for_lines()[1])
        self.assertTrue(statement_line.is_reconciled)
        self.assertTrue(bill.currency_id.is_zero(bill.amount_residual))
        self.assertTrue(
            all(
                line.currency_id.is_zero(line.amount_residual_currency)
                and line.company_currency_id.is_zero(line.amount_residual)
                for line in term_line
            ),
        )
        counterpart = statement_line.move_id.line_ids.filtered(
            lambda line: line.immediate_settlement_role == "bank_counterpart",
        )
        self.assertAlmostEqual(abs(counterpart.amount_currency), 5.0, places=2)
        self.assertAlmostEqual(abs(counterpart.balance), 4.40, places=2)
        self.assertFalse(settlement.adjustment_move_id)
        with self.assertRaisesRegex(UserError, "cannot be edited directly"):
            settlement.exchange_line_ids[:1].write(
                {"name": "Tampered native exchange line"},
            )
        with self.assertRaisesRegex(UserError, "cannot be reset to draft"):
            settlement.exchange_move_ids.button_draft()

    def test_add_remains_the_existing_standard_path(self):
        bill = self._document()
        statement_line, source_line = self._bank_candidate(bill)

        bill.js_assign_outstanding_line(source_line.id)

        self.assertFalse(
            self.env["account.immediate.settlement"].search(
                [("document_id", "=", bill.id)],
            ),
        )
        self.assertFalse(statement_line.foreign_currency_id)
        self.assertEqual(statement_line.amount_currency, 0.0)
        self.assertEqual(statement_line.amount, -4.40)

    def test_use_payment_rate_adjusts_expense_without_exchange_entry(self):
        bill = self._document()
        term_line = self._term_lines(bill)
        invoice_line = bill.invoice_line_ids
        original_expense_balance = invoice_line.balance
        statement_line, source_line = self._bank_candidate(bill)
        liquidity_before = [
            (line.id, line.balance, line.amount_currency, line.currency_id)
            for line in statement_line._seek_for_lines()[0]
        ]

        eligibility = bill._get_payment_rate_settlement_eligibility(source_line)
        self.assertTrue(eligibility["eligible"], eligibility["reason"])
        self.assertEqual(eligibility["confidence"], "recommended")
        result = bill.js_use_payment_rate_outstanding_line(source_line.id)
        settlement = self.env["account.immediate.settlement"].browse(
            result["settlement_id"],
        )

        self.assertEqual(settlement.mechanism, "payment_rate")
        self.assertEqual(settlement.foreign_amount_source, "document_residual")
        self.assertAlmostEqual(settlement.foreign_amount, 5.0, places=2)
        self.assertAlmostEqual(settlement.company_amount, 4.40, places=2)
        self.assertAlmostEqual(settlement.reference_company_amount, 4.38, places=2)
        self.assertAlmostEqual(
            settlement.preview_settlement_difference,
            0.02,
            places=2,
        )
        self.assertAlmostEqual(
            settlement.economic_adjustment_amount,
            0.02,
            places=2,
        )
        self.assertEqual(settlement.settlement_difference_type, "none")
        self.assertFalse(settlement.exchange_move_ids)
        self.assertFalse(settlement.exchange_line_ids)
        self.assertFalse(settlement.exchange_account_id)
        self.assertFalse(settlement.adjustment_move_id)
        self.assertEqual(len(settlement.allocation_ids), 1)
        allocation = settlement.allocation_ids
        self.assertEqual(allocation.original_line_id, invoice_line)
        self.assertEqual(
            allocation.account_id_snapshot,
            invoice_line.account_id,
        )
        self.assertEqual(
            allocation.analytic_distribution_snapshot,
            invoice_line.analytic_distribution,
        )
        adjustment_line = settlement.economic_adjustment_line_ids
        counterpart = settlement.generated_line_ids.filtered(
            lambda line: line.immediate_settlement_role == "bank_counterpart",
        )
        self.assertEqual(len(counterpart), 1)
        self.assertAlmostEqual(abs(counterpart.amount_currency), 5.0, places=2)
        self.assertAlmostEqual(abs(counterpart.balance), 4.38, places=2)
        self.assertEqual(
            settlement.generated_line_ids,
            settlement.bank_move_id.line_ids.filtered(
                lambda line: line.immediate_settlement_id == settlement,
            ),
        )
        self.assertEqual(adjustment_line.account_id, invoice_line.account_id)
        self.assertAlmostEqual(adjustment_line.balance, 0.02, places=2)
        self.assertEqual(
            adjustment_line.analytic_distribution,
            invoice_line.analytic_distribution,
        )
        self.assertFalse(adjustment_line.tax_line_id)
        self.assertFalse(adjustment_line.tax_ids)
        self.assertFalse(adjustment_line.tax_tag_ids)
        self.assertFalse(adjustment_line.tax_repartition_line_id)
        self.assertEqual(adjustment_line.tax_base_amount, 0.0)
        self.assertEqual(invoice_line.balance, original_expense_balance)
        self.assertAlmostEqual(
            original_expense_balance + adjustment_line.balance,
            4.40,
            places=2,
        )
        self.assertEqual(
            [
                (line.id, line.balance, line.amount_currency, line.currency_id)
                for line in statement_line._seek_for_lines()[0]
            ],
            liquidity_before,
        )
        self.assertEqual(statement_line.amount, -4.40)
        self.assertEqual(statement_line.amount_currency, -5.0)
        self.assertFalse(statement_line._seek_for_lines()[1])
        self.assertTrue(statement_line.is_reconciled)
        self.assertTrue(bill.currency_id.is_zero(bill.amount_residual))
        self.assertTrue(
            term_line.currency_id.is_zero(term_line.amount_residual_currency),
        )
        self.assertTrue(
            term_line.company_currency_id.is_zero(term_line.amount_residual),
        )
        with self.assertRaisesRegex(UserError, "cannot be edited directly"):
            adjustment_line.write({"name": "Tampered payment-rate line"})
        with self.assertRaisesRegex(UserError, "cannot be deleted directly"):
            adjustment_line.unlink()
        with self.assertRaisesRegex(UserError, "cannot be reset to draft"):
            settlement.bank_move_id.button_draft()

    def test_payment_rate_uses_same_engine_for_customer_and_refunds(self):
        for move_type in ("out_invoice", "in_refund", "out_refund"):
            document = self._document(move_type)
            statement_line, source_line = self._bank_candidate(document)

            document.js_use_payment_rate_outstanding_line(source_line.id)
            settlement = document.immediate_settlement_ids

            self.assertEqual(settlement.mechanism, "payment_rate", move_type)
            self.assertTrue(statement_line.is_reconciled, move_type)
            self.assertTrue(
                document.currency_id.is_zero(document.amount_residual),
                move_type,
            )
            self.assertFalse(settlement.exchange_move_ids, move_type)
            self.assertFalse(settlement.exchange_line_ids, move_type)
            self.assertAlmostEqual(
                abs(settlement.economic_adjustment_amount),
                0.02,
                places=2,
                msg=move_type,
            )

    def test_payment_rate_allocates_multiple_economic_lines_proportionally(self):
        second_account = self.env["account.account"].create(
            {
                "name": "Second safe expense",
                "code": "EXACT002",
                "account_type": "expense",
                "company_ids": [Command.set(self.company.ids)],
            },
        )
        second_analytic = self.env["account.analytic.account"].create(
            {
                "name": "Second exact settlement analytic",
                "plan_id": self.analytic_plan.id,
            },
        )
        bill = self._create_invoice(
            move_type="in_invoice",
            partner_id=self.partner_a,
            company_id=self.company,
            currency_id=self.foreign_currency,
            journal_id=self.company_data["default_journal_purchase"],
            invoice_date=self.document_date,
            date=self.document_date,
            invoice_line_ids=[
                self._prepare_invoice_line(
                    name="First safe allocation",
                    account_id=self.company_data["default_account_expense"],
                    quantity=1.0,
                    price_unit=2.0,
                    tax_ids=self.env["account.tax"],
                    analytic_distribution={
                        str(self.analytic_account.id): 100.0,
                    },
                ),
                self._prepare_invoice_line(
                    name="Second safe allocation",
                    account_id=second_account,
                    quantity=1.0,
                    price_unit=3.0,
                    tax_ids=self.env["account.tax"],
                    analytic_distribution={
                        str(second_analytic.id): 100.0,
                    },
                ),
            ],
            post=True,
        )
        statement_line, source_line = self._bank_candidate(bill)

        bill.js_use_payment_rate_outstanding_line(source_line.id)
        settlement = bill.immediate_settlement_ids

        self.assertEqual(len(settlement.allocation_ids), 2)
        self.assertEqual(
            settlement.allocation_ids.account_id_snapshot,
            bill.invoice_line_ids.account_id,
        )
        self.assertAlmostEqual(
            sum(settlement.allocation_ids.mapped("proportion")),
            1.0,
            places=8,
        )
        self.assertAlmostEqual(
            sum(settlement.allocation_ids.mapped("company_amount")),
            0.02,
            places=2,
        )
        for allocation in settlement.allocation_ids:
            self.assertEqual(
                allocation.adjustment_line_id.analytic_distribution,
                allocation.analytic_distribution_snapshot,
            )
            self.assertFalse(allocation.adjustment_line_id.tax_ids)
            self.assertFalse(allocation.adjustment_line_id.tax_tag_ids)
        self.assertTrue(statement_line.is_reconciled)
        self.assertFalse(settlement.exchange_move_ids)

    def test_zero_company_difference_creates_no_exchange_line(self):
        bill = self._document()
        statement_line, source_line = self._bank_candidate(
            bill,
            company_amount=4.38,
        )

        bill.js_settle_outstanding_line(source_line.id)

        settlement = bill.immediate_settlement_ids
        self.assertEqual(settlement.settlement_difference_type, "none")
        self.assertTrue(
            self.company.currency_id.is_zero(settlement.settlement_difference),
        )
        self.assertFalse(settlement.exchange_account_id)
        self.assertFalse(settlement.exchange_line_ids)
        self.assertTrue(statement_line.is_reconciled)

    def test_customer_receipt_records_native_settlement_gain(self):
        invoice = self._document("out_invoice")
        statement_line, source_line = self._bank_candidate(invoice)

        invoice.js_settle_outstanding_line(source_line.id)

        settlement = invoice.immediate_settlement_ids
        self.assertEqual(settlement.settlement_difference_type, "gain")
        self.assertAlmostEqual(settlement.settlement_difference, -0.02, places=2)
        self.assertEqual(
            settlement.exchange_account_id,
            self.company.income_currency_exchange_account_id,
        )
        self.assertAlmostEqual(
            sum(settlement.exchange_line_ids.mapped("balance")),
            -0.02,
            places=2,
        )
        self.assertTrue(statement_line.is_reconciled)
        self.assertTrue(invoice.currency_id.is_zero(invoice.amount_residual))

    def test_authoritative_bank_foreign_amount_is_not_eligible(self):
        bill = self._document()
        _statement_line, source_line = self._bank_candidate(
            bill,
            foreign_amount=5.0,
        )

        eligibility = bill._get_immediate_settlement_eligibility(source_line)

        self.assertFalse(eligibility["eligible"])
        self.assertIn("Add already uses the exact foreign amount", eligibility["reason"])
        payment_rate = bill._get_payment_rate_settlement_eligibility(source_line)
        self.assertTrue(payment_rate["eligible"], payment_rate["reason"])

    def test_integration_conflict_and_fee_facts_are_blocking(self):
        bill = self._document()
        _statement_line, source_line = self._bank_candidate(bill)
        move_model = self.env.registry["account.move"]
        original = move_model._get_immediate_settlement_source_facts

        for flag, expected in (
            ("conflicting_foreign", "conflicts with the document"),
            ("has_fee_or_withholding", "includes a fee or withholding"),
        ):
            def source_facts(records, line, current_flag=flag):
                facts = original(records, line)
                facts[current_flag] = True
                return facts

            with patch.object(
                move_model,
                "_get_immediate_settlement_source_facts",
                source_facts,
            ):
                eligibility = bill._get_immediate_settlement_eligibility(
                    source_line,
                )
            self.assertFalse(eligibility["eligible"])
            self.assertIn(expected, eligibility["reason"])

    def test_trusted_integration_date_allows_payment_rate_policy(self):
        bill = self._document()
        _statement_line, source_line = self._bank_candidate(
            bill,
            payment_date=self.document_date + timedelta(days=10),
        )
        move_model = self.env.registry["account.move"]
        original = move_model._get_immediate_settlement_source_facts

        def source_facts(records, line):
            facts = original(records, line)
            facts.update(
                {
                    "trusted_date": True,
                    "provenance": "trusted_card_transaction",
                },
            )
            return facts

        with patch.object(
            move_model,
            "_get_immediate_settlement_source_facts",
            source_facts,
        ):
            eligibility = bill._get_immediate_settlement_eligibility(source_line)
            payment_rate = bill._get_payment_rate_settlement_eligibility(
                source_line,
            )

        self.assertTrue(eligibility["eligible"])
        self.assertTrue(payment_rate["eligible"], payment_rate["reason"])
        self.assertEqual(
            eligibility["facts"]["provenance"],
            "trusted_card_transaction",
        )

    def test_delay_and_rate_warn_settle_but_block_payment_rate(self):
        delayed_bill = self._document()
        _statement, delayed_line = self._bank_candidate(
            delayed_bill,
            payment_date=self.document_date + timedelta(days=4),
        )
        delayed = delayed_bill._get_immediate_settlement_eligibility(
            delayed_line,
        )
        self.assertTrue(delayed["eligible"], delayed["reason"])
        self.assertIn("4 days", delayed["reason"])
        delayed_payment_rate = (
            delayed_bill._get_payment_rate_settlement_eligibility(delayed_line)
        )
        self.assertFalse(delayed_payment_rate["eligible"])
        self.assertIn("limited to 3 days", delayed_payment_rate["reason"])

        rate_bill = self._document()
        _statement, rate_line = self._bank_candidate(
            rate_bill,
            company_amount=4.0,
        )
        rate = rate_bill._get_immediate_settlement_eligibility(rate_line)
        self.assertTrue(rate["eligible"], rate["reason"])
        self.assertIn("from the reference rate", rate["reason"])
        rate_payment_rate = rate_bill._get_payment_rate_settlement_eligibility(
            rate_line,
        )
        self.assertFalse(rate_payment_rate["eligible"])
        self.assertIn("above the 3.00% policy", rate_payment_rate["reason"])

    def test_journal_policy_override_is_respected(self):
        bill = self._document()
        _statement, source_line = self._bank_candidate(
            bill,
            payment_date=self.document_date + timedelta(days=4),
        )
        self.bank_journal.write(
            {
                "immediate_settlement_policy_override": True,
                "immediate_settlement_max_days": 4,
                "immediate_settlement_max_rate_deviation": 3.0,
            },
        )
        self.assertTrue(
            bill._get_payment_rate_settlement_eligibility(source_line)[
                "eligible"
            ],
        )
        self.bank_journal.immediate_settlement_max_days = 3
        self.assertFalse(
            bill._get_payment_rate_settlement_eligibility(source_line)[
                "eligible"
            ],
        )
        self.assertTrue(
            bill._get_immediate_settlement_eligibility(source_line)["eligible"],
        )

    def test_one_unique_payment_term_can_be_settled(self):
        payment_term = self.env["account.payment.term"].create(
            {
                "name": "40 percent then 60 percent",
                "line_ids": [
                    Command.create(
                        {
                            "value": "percent",
                            "value_amount": 40.0,
                            "nb_days": 0,
                        },
                    ),
                    Command.create(
                        {
                            "value": "percent",
                            "value_amount": 60.0,
                            "nb_days": 30,
                        },
                    ),
                ],
            },
        )
        bill = self._document(
            foreign_amount=10.0,
            payment_term=payment_term,
        )
        statement_line, source_line = self._bank_candidate(
            bill,
            company_amount=3.52,
        )

        eligibility = bill._get_immediate_settlement_eligibility(source_line)
        self.assertTrue(eligibility["eligible"])
        self.assertEqual(len(eligibility["allocation"]["lines"]), 1)
        self.assertAlmostEqual(eligibility["foreign_amount"], 4.0, places=2)

        bill.js_settle_outstanding_line(source_line.id)

        self.assertTrue(statement_line.is_reconciled)
        self.assertEqual(bill.payment_state, "partial")
        self.assertAlmostEqual(abs(bill.amount_residual), 6.0, places=2)
        self.assertAlmostEqual(
            bill.immediate_settlement_ids.foreign_amount,
            4.0,
            places=2,
        )

    def test_ambiguous_equal_payment_terms_are_not_inferred(self):
        payment_term = self.env["account.payment.term"].create(
            {
                "name": "Two equal terms",
                "line_ids": [
                    Command.create(
                        {
                            "value": "percent",
                            "value_amount": 50.0,
                            "nb_days": 0,
                        },
                    ),
                    Command.create(
                        {
                            "value": "percent",
                            "value_amount": 50.0,
                            "nb_days": 30,
                        },
                    ),
                ],
            },
        )
        bill = self._document(
            foreign_amount=10.0,
            payment_term=payment_term,
        )
        _statement_line, source_line = self._bank_candidate(
            bill,
            company_amount=4.40,
        )

        eligibility = bill._get_immediate_settlement_eligibility(source_line)

        self.assertFalse(eligibility["eligible"])
        self.assertIn("more than one payment term", eligibility["reason"])

    def test_combined_fee_or_allocation_is_blocked(self):
        bill = self._document()
        statement_line, source_line = self._bank_candidate(
            bill,
            company_amount=4.50,
        )
        fee_account = self.env["account.account"].create(
            {
                "name": "Explicit bank fees",
                "code": "EXACTFEE",
                "account_type": "expense",
                "company_ids": [Command.set(self.company.ids)],
            },
        )
        move = statement_line.move_id
        move.button_draft()
        move.write(
            {
                "line_ids": [
                    Command.update(
                        source_line.id,
                        {"balance": 4.40, "amount_currency": 4.40},
                    ),
                    Command.create(
                        {
                            "name": "Explicit fee",
                            "account_id": fee_account.id,
                            "currency_id": self.company.currency_id.id,
                            "balance": 0.10,
                            "amount_currency": 0.10,
                        },
                    ),
                ],
            },
        )
        move.action_post()

        eligibility = bill._get_immediate_settlement_eligibility(source_line)

        self.assertFalse(eligibility["eligible"])
        self.assertIn("allocation, fee, or withholding", eligibility["reason"])

    def test_tax_and_analytic_values_are_unchanged(self):
        tax = self.env["account.tax"].create(
            {
                "name": "Exact settlement purchase tax",
                "amount": 10.0,
                "amount_type": "percent",
                "type_tax_use": "purchase",
                "company_id": self.company.id,
            },
        )
        bill = self._document(tax_ids=tax)
        tax_line = bill.line_ids.filtered("tax_line_id")
        invoice_line = bill.invoice_line_ids
        snapshot = {
            "tax_balance": tax_line.balance,
            "tax_amount_currency": tax_line.amount_currency,
            "tax_base_amount": tax_line.tax_base_amount,
            "tax_tags": tax_line.tax_tag_ids.ids,
            "invoice_balance": invoice_line.balance,
            "analytic_distribution": invoice_line.analytic_distribution,
        }
        company_amount = abs(self._term_lines(bill).amount_residual) + 0.02
        _statement, source_line = self._bank_candidate(
            bill,
            company_amount=company_amount,
        )

        bill.js_settle_outstanding_line(source_line.id)

        self.assertEqual(tax_line.balance, snapshot["tax_balance"])
        self.assertEqual(
            tax_line.amount_currency,
            snapshot["tax_amount_currency"],
        )
        self.assertEqual(tax_line.tax_base_amount, snapshot["tax_base_amount"])
        self.assertEqual(tax_line.tax_tag_ids.ids, snapshot["tax_tags"])
        self.assertEqual(invoice_line.balance, snapshot["invoice_balance"])
        self.assertEqual(
            invoice_line.analytic_distribution,
            snapshot["analytic_distribution"],
        )
        self.assertFalse(bill.immediate_settlement_ids.adjustment_move_id)

    def test_payment_rate_leaves_document_tax_metadata_unchanged(self):
        tax = self.env["account.tax"].create(
            {
                "name": "Payment-rate purchase tax",
                "amount": 10.0,
                "amount_type": "percent",
                "type_tax_use": "purchase",
                "company_id": self.company.id,
            },
        )
        bill = self._document(tax_ids=tax)
        tax_line = bill.line_ids.filtered("tax_line_id")
        invoice_line = bill.invoice_line_ids
        snapshot = bill._foreign_settlement_tax_snapshot()
        company_amount = abs(self._term_lines(bill).amount_residual) + 0.02
        _statement, source_line = self._bank_candidate(
            bill,
            company_amount=company_amount,
        )

        bill.js_use_payment_rate_outstanding_line(source_line.id)
        settlement = bill.immediate_settlement_ids

        self.assertEqual(
            bill._foreign_settlement_tax_snapshot(),
            snapshot,
        )
        self.assertEqual(invoice_line.tax_ids, tax)
        self.assertEqual(tax_line.tax_line_id, tax)
        self.assertTrue(settlement.economic_adjustment_line_ids)
        self.assertFalse(settlement.economic_adjustment_line_ids.tax_ids)
        self.assertFalse(settlement.economic_adjustment_line_ids.tax_tag_ids)
        self.assertFalse(
            settlement.economic_adjustment_line_ids.tax_repartition_line_id,
        )
        self.assertFalse(settlement.exchange_move_ids)

    def test_payment_rate_keeps_native_cash_basis_tax_processing(self):
        self.company.tax_exigibility = True
        self.company.account_cash_basis_base_account_id = self.company_data[
            "default_account_assets"
        ]
        transition_account = self.company_data[
            "default_account_tax_purchase"
        ].copy(
            {
                "name": "Payment-rate cash-basis transition",
                "reconcile": True,
            },
        )
        final_tax_account = self.company_data["default_account_tax_purchase"]
        tax = self.env["account.tax"].create(
            {
                "name": "Payment-rate cash-basis purchase tax",
                "amount": 10.0,
                "amount_type": "percent",
                "type_tax_use": "purchase",
                "company_id": self.company.id,
                "tax_exigibility": "on_payment",
                "cash_basis_transition_account_id": transition_account.id,
                "invoice_repartition_line_ids": [
                    Command.create({"repartition_type": "base"}),
                    Command.create(
                        {
                            "repartition_type": "tax",
                            "account_id": final_tax_account.id,
                        },
                    ),
                ],
                "refund_repartition_line_ids": [
                    Command.create({"repartition_type": "base"}),
                    Command.create(
                        {
                            "repartition_type": "tax",
                            "account_id": final_tax_account.id,
                        },
                    ),
                ],
            },
        )
        bill = self._document(tax_ids=tax)
        original_tax_snapshot = bill._foreign_settlement_tax_snapshot()
        company_amount = abs(self._term_lines(bill).amount_residual) + 0.02
        _statement, source_line = self._bank_candidate(
            bill,
            company_amount=company_amount,
        )

        bill.js_use_payment_rate_outstanding_line(source_line.id)
        settlement = bill.immediate_settlement_ids
        cash_basis_moves = self.env["account.move"].search(
            [
                (
                    "tax_cash_basis_rec_id",
                    "in",
                    settlement.partial_reconcile_ids.ids,
                ),
            ],
        )

        self.assertTrue(cash_basis_moves)
        self.assertTrue(all(move.state == "posted" for move in cash_basis_moves))
        self.assertEqual(
            bill._foreign_settlement_tax_snapshot(),
            original_tax_snapshot,
        )
        self.assertTrue(
            cash_basis_moves.line_ids.filtered(
                lambda line: line.tax_repartition_line_id,
            ),
        )
        self.assertFalse(settlement.economic_adjustment_line_ids.tax_ids)
        self.assertFalse(settlement.economic_adjustment_line_ids.tax_tag_ids)
        self.assertFalse(
            settlement.economic_adjustment_line_ids.tax_repartition_line_id,
        )
        self.assertFalse(settlement.exchange_move_ids)

    def test_unsafe_asset_account_keeps_add_and_settle_only(self):
        asset_account = self.env["account.account"].create(
            {
                "name": "Unsupported fixed asset",
                "code": "EXACTFA",
                "account_type": "asset_fixed",
                "company_ids": [Command.set(self.company.ids)],
            },
        )
        bill = self._create_invoice(
            move_type="in_invoice",
            partner_id=self.partner_a,
            company_id=self.company,
            currency_id=self.foreign_currency,
            journal_id=self.company_data["default_journal_purchase"],
            invoice_date=self.document_date,
            date=self.document_date,
            invoice_line_ids=[
                self._prepare_invoice_line(
                    name="Unsupported asset purchase",
                    account_id=asset_account,
                    quantity=1.0,
                    price_unit=5.0,
                    tax_ids=self.env["account.tax"],
                ),
            ],
            post=True,
        )
        _statement, source_line = self._bank_candidate(bill)

        settle = bill._get_immediate_settlement_eligibility(source_line)
        payment_rate = bill._get_payment_rate_settlement_eligibility(source_line)

        self.assertTrue(settle["eligible"], settle["reason"])
        self.assertFalse(payment_rate["eligible"])
        self.assertIn("cannot safely be adjusted", payment_rate["reason"])

    def test_mixed_safe_and_unsafe_lines_block_payment_rate(self):
        asset_account = self.env["account.account"].create(
            {
                "name": "Mixed unsupported fixed asset",
                "code": "EXACTFM",
                "account_type": "asset_fixed",
                "company_ids": [Command.set(self.company.ids)],
            },
        )
        bill = self._create_invoice(
            move_type="in_invoice",
            partner_id=self.partner_a,
            company_id=self.company,
            currency_id=self.foreign_currency,
            journal_id=self.company_data["default_journal_purchase"],
            invoice_date=self.document_date,
            date=self.document_date,
            invoice_line_ids=[
                self._prepare_invoice_line(
                    name="Safe expense portion",
                    account_id=self.company_data["default_account_expense"],
                    quantity=1.0,
                    price_unit=2.0,
                    tax_ids=self.env["account.tax"],
                ),
                self._prepare_invoice_line(
                    name="Unsupported asset portion",
                    account_id=asset_account,
                    quantity=1.0,
                    price_unit=3.0,
                    tax_ids=self.env["account.tax"],
                ),
            ],
            post=True,
        )
        _statement, source_line = self._bank_candidate(bill)

        settle = bill._get_immediate_settlement_eligibility(source_line)
        payment_rate = bill._get_payment_rate_settlement_eligibility(source_line)

        self.assertTrue(settle["eligible"], settle["reason"])
        self.assertFalse(payment_rate["eligible"])
        self.assertIn("cannot safely be adjusted", payment_rate["reason"])

    def test_reversal_restores_missing_foreign_amount_and_residuals(self):
        bill = self._document()
        term_line = self._term_lines(bill)
        statement_line, source_line = self._bank_candidate(bill)
        bill.js_settle_outstanding_line(source_line.id)
        settlement = bill.immediate_settlement_ids

        settlement.action_reverse()

        statement_line.invalidate_recordset()
        term_line.invalidate_recordset(
            ["amount_residual", "amount_residual_currency", "reconciled"],
        )
        self.assertEqual(settlement.state, "reversed")
        self.assertFalse(statement_line.foreign_currency_id)
        self.assertEqual(statement_line.amount_currency, 0.0)
        self.assertFalse(
            statement_line.immediate_settlement_foreign_amount_source,
        )
        self.assertFalse(statement_line.active_immediate_settlement_id)
        self.assertFalse(statement_line.is_reconciled)
        self.assertEqual(len(statement_line._seek_for_lines()[1]), 1)
        self.assertAlmostEqual(
            abs(statement_line._seek_for_lines()[1].amount_residual),
            4.40,
            places=2,
        )
        self.assertAlmostEqual(
            abs(term_line.amount_residual_currency),
            5.0,
            places=2,
        )
        self.assertAlmostEqual(abs(term_line.amount_residual), 4.38, places=2)
        self.assertEqual(bill.payment_state, "not_paid")

    def test_payment_rate_reversal_removes_generated_lines_and_keeps_snapshot(self):
        bill = self._document()
        term_line = self._term_lines(bill)
        statement_line, source_line = self._bank_candidate(bill)
        bill.js_use_payment_rate_outstanding_line(source_line.id)
        settlement = bill.immediate_settlement_ids
        generated_line_ids = settlement.economic_adjustment_line_ids.ids
        allocation = settlement.allocation_ids
        allocation_snapshot = {
            "line_id": allocation.adjustment_line_id_snapshot,
            "name": allocation.adjustment_line_name,
            "account_id": allocation.account_id_snapshot.id,
            "amount": allocation.company_amount,
            "analytic": allocation.analytic_distribution_snapshot,
        }

        settlement.action_reverse()

        statement_line.invalidate_recordset()
        term_line.invalidate_recordset(
            ["amount_residual", "amount_residual_currency", "reconciled"],
        )
        allocation.invalidate_recordset()
        self.assertEqual(settlement.state, "reversed")
        self.assertFalse(statement_line.foreign_currency_id)
        self.assertEqual(statement_line.amount_currency, 0.0)
        self.assertFalse(statement_line.is_reconciled)
        self.assertFalse(
            self.env["account.move.line"].browse(generated_line_ids).exists(),
        )
        self.assertFalse(allocation.adjustment_line_id)
        self.assertEqual(
            {
                "line_id": allocation.adjustment_line_id_snapshot,
                "name": allocation.adjustment_line_name,
                "account_id": allocation.account_id_snapshot.id,
                "amount": allocation.company_amount,
                "analytic": allocation.analytic_distribution_snapshot,
            },
            allocation_snapshot,
        )
        self.assertAlmostEqual(
            abs(term_line.amount_residual_currency),
            5.0,
            places=2,
        )
        self.assertAlmostEqual(abs(term_line.amount_residual), 4.38, places=2)
        self.assertEqual(bill.payment_state, "not_paid")

    def test_unlinking_linked_partial_reverses_the_bank_settlement(self):
        bill = self._document()
        statement_line, source_line = self._bank_candidate(bill)
        bill.js_settle_outstanding_line(source_line.id)
        settlement = bill.immediate_settlement_ids

        settlement.partial_reconcile_ids[:1].unlink()

        self.assertEqual(settlement.state, "reversed")
        self.assertFalse(statement_line.foreign_currency_id)
        self.assertFalse(statement_line.is_reconciled)
        self.assertEqual(bill.payment_state, "not_paid")

    def test_repeated_click_is_idempotent_after_source_line_replacement(self):
        bill = self._document()
        _statement, source_line = self._bank_candidate(bill)
        source_line_id = source_line.id

        first = bill.js_settle_outstanding_line(source_line_id)
        second = bill.js_settle_outstanding_line(source_line_id)

        self.assertEqual(first, second)
        self.assertEqual(len(bill.immediate_settlement_ids), 1)

    def test_payment_rate_repeated_click_is_idempotent_and_cross_action_is_blocked(
        self,
    ):
        bill = self._document()
        _statement, source_line = self._bank_candidate(bill)
        source_line_id = source_line.id

        first = bill.js_use_payment_rate_outstanding_line(source_line_id)
        second = bill.js_use_payment_rate_outstanding_line(source_line_id)

        self.assertEqual(first, second)
        self.assertEqual(len(bill.immediate_settlement_ids), 1)
        with self.assertRaisesRegex(UserError, "already settled using Use payment rate"):
            bill.js_settle_outstanding_line(source_line_id)

    def test_stale_click_revalidates_changed_bank_facts(self):
        bill = self._document()
        statement_line, source_line = self._bank_candidate(bill)
        statement_line.write(
            {
                "foreign_currency_id": self.foreign_currency.id,
                "amount_currency": -5.0,
            },
        )

        with self.assertRaisesRegex(
            UserError,
            "Add already uses the exact foreign amount",
        ):
            bill.js_settle_outstanding_line(source_line.id)

    def test_permissions_and_locks_are_enforced(self):
        bill = self._document()
        _statement_line, source_line = self._bank_candidate(bill)
        billing_user = self.env["res.users"].create(
            {
                "name": "Exact settlement billing user",
                "login": "exact_settlement_billing_user",
                "group_ids": [
                    Command.link(
                        self.env.ref("account.group_account_invoice").id,
                    ),
                ],
                "company_id": self.company.id,
                "company_ids": [Command.set(self.company.ids)],
            },
        )
        restricted_bill = bill.with_user(billing_user)
        eligibility = restricted_bill._get_immediate_settlement_eligibility(
            source_line.with_user(billing_user),
        )
        self.assertFalse(eligibility["eligible"])
        self.assertIn("Only accountants", eligibility["reason"])
        with self.assertRaises(AccessError):
            restricted_bill.js_settle_outstanding_line(source_line.id)

        self.company.sudo().hard_lock_date = self.document_date
        eligibility = bill._get_immediate_settlement_eligibility(source_line)
        self.assertFalse(eligibility["eligible"])
        self.assertIn("locked", eligibility["reason"])

    def test_cross_company_bank_candidates_are_rejected(self):
        bill = self._document()
        other_company_data = self.setup_other_company()
        other_company = other_company_data["company"]
        other_bank_journal = other_company_data["default_journal_bank"]
        other_bank_journal.reconcile_mode = "edit"
        other_bank_journal.suspense_account_id.reconcile = True
        statement_line = (
            self.env["account.bank.statement.line"]
            .with_company(other_company)
            .create(
                {
                    "journal_id": other_bank_journal.id,
                    "date": self.statement_date,
                    "payment_ref": "OTHER COMPANY CLOUDFLARE",
                    "amount": -4.40,
                    "partner_id": bill.commercial_partner_id.id,
                },
            )
        )
        _liquidity, source_line, _other = statement_line._seek_for_lines()
        allowed_company_ids = [self.company.id, other_company.id]
        bill = bill.with_context(allowed_company_ids=allowed_company_ids)
        source_line = source_line.with_context(
            allowed_company_ids=allowed_company_ids,
        )

        eligibility = bill._get_immediate_settlement_eligibility(source_line)

        self.assertFalse(eligibility["eligible"])
        self.assertIn("same company", eligibility["reason"])
        with self.assertRaisesRegex(UserError, "same company"):
            bill.js_settle_outstanding_line(source_line.id)

    def test_secure_entries_are_not_eligible(self):
        bill = self._document()
        statement_line, source_line = self._bank_candidate(bill)
        statement_line.move_id.inalterable_hash = "test-secure-hash"
        eligibility = bill._get_immediate_settlement_eligibility(source_line)
        self.assertFalse(eligibility["eligible"])
        self.assertIn("immutable accounting controls", eligibility["reason"])

    def test_locked_period_blocks_reversal(self):
        bill = self._document()
        _statement_line, source_line = self._bank_candidate(bill)
        bill.js_settle_outstanding_line(source_line.id)
        self.company.sudo().hard_lock_date = self.statement_date

        with self.assertRaisesRegex(UserError, "period is locked"):
            bill.immediate_settlement_ids.action_reverse()

    def test_audit_records_cannot_be_fabricated_through_rpc_access(self):
        bill = self._document()
        _statement_line, source_line = self._bank_candidate(bill)
        bill.js_settle_outstanding_line(source_line.id)
        settlement = bill.immediate_settlement_ids
        account_user = self.env["res.users"].create(
            {
                "name": "Exact settlement accountant",
                "login": "exact_settlement_accountant",
                "group_ids": [
                    Command.link(self.env.ref("account.group_account_user").id),
                ],
                "company_id": self.company.id,
                "company_ids": [Command.set(self.company.ids)],
            },
        )
        readonly_user = self.env["res.users"].create(
            {
                "name": "Exact settlement read-only accountant",
                "login": "exact_settlement_readonly_accountant",
                "group_ids": [
                    Command.link(
                        self.env.ref("account.group_account_readonly").id,
                    ),
                ],
                "company_id": self.company.id,
                "company_ids": [Command.set(self.company.ids)],
            },
        )
        other_company = self.setup_other_company()["company"]
        other_company_readonly_user = self.env["res.users"].create(
            {
                "name": "Other-company settlement read-only accountant",
                "login": "other_company_settlement_readonly_accountant",
                "group_ids": [
                    Command.link(
                        self.env.ref("account.group_account_readonly").id,
                    ),
                ],
                "company_id": other_company.id,
                "company_ids": [Command.set(other_company.ids)],
            },
        )

        self.assertEqual(
            settlement.with_user(readonly_user).read(["name"])[0]["name"],
            settlement.name,
        )
        self.assertFalse(
            self.env["account.immediate.settlement"]
            .with_user(other_company_readonly_user)
            .search([("id", "=", settlement.id)]),
        )

        with self.assertRaises(AccessError):
            self.env["account.immediate.settlement"].with_user(
                account_user,
            ).create(
                {
                    "name": "Fabricated",
                    "company_id": self.company.id,
                    "currency_id": self.foreign_currency.id,
                    "document_id": self._document().id,
                },
            )
        with self.assertRaises(AccessError):
            self.env["account.immediate.settlement"].sudo().create(
                {
                    "name": "Fabricated by superuser RPC",
                    "company_id": self.company.id,
                    "currency_id": self.foreign_currency.id,
                    "document_id": self._document().id,
                },
            )

    def test_refunds_and_receipts_use_the_same_bank_statement_engine(self):
        for move_type in ("in_refund", "out_refund", "in_receipt", "out_receipt"):
            document = self._document(move_type)
            statement_line, source_line = self._bank_candidate(document)

            document.js_settle_outstanding_line(source_line.id)

            self.assertTrue(
                document.currency_id.is_zero(document.amount_residual),
                move_type,
            )
            self.assertTrue(statement_line.is_reconciled, move_type)
            self.assertEqual(
                document.immediate_settlement_ids.mechanism,
                "bank_statement",
                move_type,
            )
