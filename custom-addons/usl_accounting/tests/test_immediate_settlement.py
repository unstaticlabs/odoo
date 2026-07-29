from datetime import timedelta

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
        cls.document_date = fields.Date.today() - timedelta(days=10)
        currency_code = (
            "EUR" if cls.env.company.currency_id.name != "EUR" else "USD"
        )
        cls.foreign_currency = cls.setup_other_currency(
            currency_code,
            rates=[(cls.document_date, 1.1111111111)],
        )
        cls.company = cls.env.company
        cls.company.immediate_settlement_max_days = 3
        cls.company.immediate_settlement_max_rate_deviation = 3.0
        cls.company._ensure_immediate_settlement_journal()
        cls.analytic_plan = cls.env["account.analytic.plan"].create(
            {"name": "Immediate settlement test"},
        )
        cls.analytic_account = cls.env["account.analytic.account"].create(
            {
                "name": "Immediate settlement test",
                "plan_id": cls.analytic_plan.id,
            },
        )

    def _document(self, move_type, foreign_amount=5.0):
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
            invoice_line_ids=[
                self._prepare_invoice_line(
                    name="Immediate settlement test",
                    account_id=account,
                    quantity=1.0,
                    price_unit=foreign_amount,
                    tax_ids=self.env["account.tax"],
                    analytic_distribution={
                        str(self.analytic_account.id): 100.0,
                    },
                ),
            ],
            post=True,
        )

    def _payment_line(
        self,
        document,
        foreign_amount,
        company_amount,
        payment_date=None,
    ):
        term_line = document.line_ids.filtered(
            lambda line: line.display_type == "payment_term",
        )[:1]
        sign = -1.0 if term_line.balance > 0.0 else 1.0
        signed_company = sign * company_amount
        signed_foreign = sign * foreign_amount
        bank_account = self.company_data[
            "default_journal_bank"
        ].default_account_id
        move = self.env["account.move"].create(
            {
                "move_type": "entry",
                "journal_id": self.company_data["default_journal_bank"].id,
                "date": payment_date or self.document_date + timedelta(days=1),
                "ref": "Executed foreign payment",
                "line_ids": [
                    Command.create(
                        {
                            "name": "Payment counterpart",
                            "account_id": term_line.account_id.id,
                            "partner_id": document.commercial_partner_id.id,
                            "currency_id": self.foreign_currency.id,
                            "balance": signed_company,
                            "amount_currency": signed_foreign,
                        },
                    ),
                    Command.create(
                        {
                            "name": "Bank",
                            "account_id": bank_account.id,
                            "currency_id": self.company.currency_id.id,
                            "balance": -signed_company,
                            "amount_currency": -signed_company,
                        },
                    ),
                ],
            },
        )
        move.action_post()
        return move.line_ids.filtered(
            lambda line: line.account_id == term_line.account_id,
        )

    def test_immediate_vendor_payment_uses_executed_rate(self):
        bill = self._document("in_invoice")
        expense_line = bill.invoice_line_ids
        reference_balance = expense_line.balance
        self.assertAlmostEqual(reference_balance, 4.5, places=2)
        payment_line = self._payment_line(bill, 5.0, 4.4)
        bank_balance = (payment_line.move_id.line_ids - payment_line).balance

        result = bill.js_settle_outstanding_line(payment_line.id)
        settlement = self.env["account.immediate.settlement"].browse(
            result["settlement_id"],
        )

        self.assertEqual(settlement.state, "settled")
        self.assertEqual(settlement.foreign_amount, 5.0)
        self.assertEqual(settlement.company_amount, 4.4)
        self.assertAlmostEqual(settlement.executed_rate, 0.88, places=6)
        self.assertTrue(bill.currency_id.is_zero(bill.amount_residual))
        self.assertTrue(
            bill.company_currency_id.is_zero(
                bill.line_ids.filtered(
                    lambda line: line.display_type == "payment_term",
                ).amount_residual,
            ),
        )
        self.assertTrue(payment_line.reconciled)
        self.assertEqual(
            (payment_line.move_id.line_ids - payment_line).balance,
            bank_balance,
        )
        self.assertFalse(settlement.partial_reconcile_ids.exchange_move_id)
        economic_adjustment = settlement.adjustment_move_id.line_ids.filtered(
            lambda line: line.immediate_settlement_role == "economic",
        )
        self.assertAlmostEqual(
            reference_balance + economic_adjustment.balance,
            4.4,
            places=2,
        )
        self.assertFalse(economic_adjustment.tax_ids)
        self.assertFalse(economic_adjustment.tax_tag_ids)
        self.assertEqual(
            economic_adjustment.analytic_distribution,
            expense_line.analytic_distribution,
        )

    def test_native_add_path_remains_standard_reconciliation(self):
        bill = self._document("in_invoice")
        payment_line = self._payment_line(bill, 5.0, 4.4)

        bill.js_assign_outstanding_line(payment_line.id)

        self.assertFalse(
            self.env["account.immediate.settlement"].search(
                [
                    ("document_id", "=", bill.id),
                    ("payment_line_id", "=", payment_line.id),
                ],
            ),
        )
        self.assertTrue(bill.currency_id.is_zero(bill.amount_residual))
        partials = payment_line.matched_debit_ids + payment_line.matched_credit_ids
        self.assertTrue(partials.exchange_move_id)

    def test_immediate_customer_receipt(self):
        invoice = self._document("out_invoice")
        revenue_line = invoice.invoice_line_ids
        payment_line = self._payment_line(invoice, 5.0, 4.4)

        invoice.js_settle_outstanding_line(payment_line.id)

        settlement = invoice.immediate_settlement_ids
        economic_adjustment = settlement.adjustment_move_id.line_ids.filtered(
            lambda line: line.immediate_settlement_role == "economic",
        )
        self.assertTrue(payment_line.reconciled)
        self.assertTrue(invoice.currency_id.is_zero(invoice.amount_residual))
        self.assertAlmostEqual(
            revenue_line.balance + economic_adjustment.balance,
            -4.4,
            places=2,
        )

    def test_partial_settlement_keeps_reference_rate_residual(self):
        invoice = self._document("out_invoice", foreign_amount=10.0)
        term_line = invoice.line_ids.filtered(
            lambda line: line.display_type == "payment_term",
        )
        payment_line = self._payment_line(invoice, 5.0, 4.4)

        invoice.js_settle_outstanding_line(payment_line.id)

        self.assertTrue(payment_line.reconciled)
        self.assertEqual(term_line.amount_residual_currency, 5.0)
        self.assertAlmostEqual(term_line.amount_residual, 4.5, places=2)
        self.assertEqual(invoice.payment_state, "partial")

    def test_delay_and_rate_policy_block_settlement(self):
        bill = self._document("in_invoice")
        delayed_payment = self._payment_line(
            bill,
            5.0,
            4.4,
            payment_date=self.document_date + timedelta(days=4),
        )
        eligibility = bill._get_immediate_settlement_eligibility(
            delayed_payment,
        )
        self.assertFalse(eligibility["eligible"])
        self.assertIn("4 days", eligibility["reason"])

        other_bill = self._document("in_invoice")
        rate_payment = self._payment_line(other_bill, 5.0, 4.0)
        eligibility = other_bill._get_immediate_settlement_eligibility(
            rate_payment,
        )
        self.assertFalse(eligibility["eligible"])
        self.assertIn("above the", eligibility["reason"])

    def test_payment_journal_can_apply_a_stricter_or_wider_policy(self):
        bill = self._document("in_invoice")
        payment_line = self._payment_line(
            bill,
            5.0,
            4.4,
            payment_date=self.document_date + timedelta(days=4),
        )
        payment_line.journal_id.write(
            {
                "immediate_settlement_policy_override": True,
                "immediate_settlement_max_days": 4,
                "immediate_settlement_max_rate_deviation": 3.0,
            },
        )

        self.assertTrue(
            bill._get_immediate_settlement_eligibility(payment_line)["eligible"],
        )
        payment_line.journal_id.immediate_settlement_max_days = 3
        self.assertFalse(
            bill._get_immediate_settlement_eligibility(payment_line)["eligible"],
        )

    def test_reversal_restores_document_and_payment_residuals(self):
        bill = self._document("in_invoice")
        term_line = bill.line_ids.filtered(
            lambda line: line.display_type == "payment_term",
        )
        payment_line = self._payment_line(bill, 5.0, 4.4)
        bill.js_settle_outstanding_line(payment_line.id)
        settlement = bill.immediate_settlement_ids

        settlement.action_reverse()

        self.assertEqual(settlement.state, "reversed")
        self.assertTrue(settlement.reversal_move_id)
        self.assertEqual(term_line.amount_residual_currency, -5.0)
        self.assertAlmostEqual(term_line.amount_residual, -4.5, places=2)
        self.assertEqual(payment_line.amount_residual_currency, 5.0)
        self.assertAlmostEqual(payment_line.amount_residual, 4.4, places=2)
        self.assertEqual(bill.payment_state, "not_paid")

    def test_suspense_bridge_preserves_imported_bank_facts(self):
        bill = self._document("in_invoice")
        bank_journal = self.company_data["default_journal_bank"]
        bank_journal.suspense_account_id.reconcile = True
        statement_line = self.env["account.bank.statement.line"].create(
            {
                "journal_id": bank_journal.id,
                "date": self.document_date + timedelta(days=1),
                "payment_ref": "Immediate card purchase",
                "amount": -4.4,
                "foreign_currency_id": self.foreign_currency.id,
                "amount_currency": -5.0,
                "partner_id": bill.commercial_partner_id.id,
            },
        )
        _liquidity, suspense_lines, _other = statement_line._seek_for_lines()
        source_line = suspense_lines.filtered(
            lambda line: line.account_id == bank_journal.suspense_account_id,
        )
        source_account = source_line.account_id
        source_partner = source_line.partner_id

        bill.js_settle_outstanding_line(source_line.id)

        settlement = bill.immediate_settlement_ids
        self.assertTrue(source_line.reconciled)
        self.assertEqual(source_line.account_id, source_account)
        self.assertEqual(source_line.partner_id, source_partner)
        self.assertEqual(statement_line.amount, -4.4)
        self.assertEqual(statement_line.amount_currency, -5.0)
        self.assertEqual(
            set(
                settlement.adjustment_move_id.line_ids.mapped(
                    "immediate_settlement_role",
                ),
            ),
            {
                "suspense_clear",
                "payment_bridge",
                "valuation",
                "economic",
            },
        )
        self.assertTrue(bill.currency_id.is_zero(bill.amount_residual))
        self.assertFalse(settlement.partial_reconcile_ids.exchange_move_id)

    def test_explicit_bank_fee_stays_separate_and_requires_configuration(self):
        bill = self._document("in_invoice")
        bank_journal = self.company_data["default_journal_bank"]
        bank_journal.suspense_account_id.reconcile = True
        fee_account = self.env["account.account"].create(
            {
                "name": "Explicit bank fees",
                "code": "IMSTFEE",
                "account_type": "expense",
                "company_ids": [Command.set(self.company.ids)],
            },
        )
        statement_line = self.env["account.bank.statement.line"].create(
            {
                "journal_id": bank_journal.id,
                "date": self.document_date + timedelta(days=1),
                "payment_ref": "Card purchase with explicit fee",
                "amount": -4.5,
                "foreign_currency_id": self.foreign_currency.id,
                "amount_currency": -5.0,
                "partner_id": bill.commercial_partner_id.id,
            },
        )
        statement_move = statement_line.move_id
        statement_move.button_draft()
        _liquidity, suspense_lines, _other = statement_line._seek_for_lines()
        suspense_line = suspense_lines.filtered(
            lambda line: line.account_id == bank_journal.suspense_account_id,
        )
        statement_move.write(
            {
                "line_ids": [
                    Command.update(
                        suspense_line.id,
                        {"balance": 4.4},
                    ),
                    Command.create(
                        {
                            "name": "Explicit card fee",
                            "account_id": fee_account.id,
                            "currency_id": self.company.currency_id.id,
                            "balance": 0.1,
                            "amount_currency": 0.1,
                        },
                    ),
                ],
            },
        )
        statement_move.action_post()
        fee_line = statement_move.line_ids.filtered(
            lambda line: line.account_id == fee_account,
        )

        eligibility = bill._get_immediate_settlement_eligibility(
            suspense_line,
        )
        self.assertFalse(eligibility["eligible"])
        self.assertIn("unexplained amount or fee", eligibility["reason"])

        self.company.immediate_settlement_fee_account_ids = fee_account
        bill.js_settle_outstanding_line(suspense_line.id)

        settlement = bill.immediate_settlement_ids
        self.assertEqual(settlement.company_amount, 4.4)
        self.assertEqual(statement_line.amount, -4.5)
        self.assertEqual(statement_line.amount_currency, -5.0)
        self.assertEqual(fee_line.balance, 0.1)
        self.assertFalse(fee_line.immediate_settlement_id)
        self.assertFalse(fee_line.reconciled)

    def test_permission_and_lock_blockers_are_enforced(self):
        bill = self._document("in_invoice")
        payment_line = self._payment_line(bill, 5.0, 4.4)
        billing_user = self.env["res.users"].create(
            {
                "name": "Immediate settlement billing user",
                "login": "immediate_settlement_billing_user",
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
            payment_line.with_user(billing_user),
        )
        self.assertFalse(eligibility["eligible"])
        self.assertIn("Only accountants", eligibility["reason"])
        with self.assertRaises(AccessError):
            restricted_bill.js_settle_outstanding_line(payment_line.id)

        self.company.sudo().hard_lock_date = self.document_date
        eligibility = bill._get_immediate_settlement_eligibility(payment_line)
        self.assertFalse(eligibility["eligible"])
        self.assertIn("locked", eligibility["reason"])

    def test_stale_or_repeated_action_is_idempotent(self):
        bill = self._document("in_invoice")
        payment_line = self._payment_line(bill, 5.0, 4.4)
        first = bill.js_settle_outstanding_line(payment_line.id)
        second = bill.js_settle_outstanding_line(payment_line.id)
        self.assertEqual(first, second)
        self.assertEqual(len(bill.immediate_settlement_ids), 1)

    def test_ambiguous_multiple_terms_are_not_eligible(self):
        bill = self._create_invoice(
            move_type="in_invoice",
            partner_id=self.partner_a,
            company_id=self.company,
            currency_id=self.foreign_currency,
            journal_id=self.company_data["default_journal_purchase"],
            invoice_date=self.document_date,
            date=self.document_date,
            invoice_payment_term_id=self.pay_terms_b,
            invoice_line_ids=[
                self._prepare_invoice_line(
                    name="Ambiguous settlement",
                    account_id=self.company_data["default_account_expense"],
                    quantity=1.0,
                    price_unit=10.0,
                    tax_ids=self.env["account.tax"],
                ),
            ],
            post=True,
        )
        payment_line = self._payment_line(bill, 2.0, 1.76)

        eligibility = bill._get_immediate_settlement_eligibility(payment_line)

        self.assertFalse(eligibility["eligible"])
        self.assertIn("unambiguously", eligibility["reason"])

    def test_settlement_rechecks_stale_candidate(self):
        bill = self._document("in_invoice")
        payment_line = self._payment_line(bill, 5.0, 4.4)
        bill.js_assign_outstanding_line(payment_line.id)
        with self.assertRaisesRegex(UserError, "still open"):
            bill.js_settle_outstanding_line(payment_line.id)

    def test_multiline_allocation_preserves_accounts_and_analytics(self):
        second_expense = self.env["account.account"].create(
            {
                "name": "Immediate settlement second expense",
                "code": "IMSTEXP2",
                "account_type": "expense",
                "company_ids": [Command.set(self.company.ids)],
            },
        )
        second_analytic = self.env["account.analytic.account"].create(
            {
                "name": "Immediate settlement second analytic",
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
                    name="First allocation",
                    account_id=self.company_data["default_account_expense"],
                    quantity=1.0,
                    price_unit=3.0,
                    tax_ids=self.env["account.tax"],
                    analytic_distribution={
                        str(self.analytic_account.id): 100.0,
                    },
                ),
                self._prepare_invoice_line(
                    name="Second allocation",
                    account_id=second_expense,
                    quantity=1.0,
                    price_unit=2.0,
                    tax_ids=self.env["account.tax"],
                    analytic_distribution={
                        str(second_analytic.id): 100.0,
                    },
                ),
            ],
            post=True,
        )
        payment_line = self._payment_line(bill, 5.0, 4.4)

        bill.js_settle_outstanding_line(payment_line.id)

        allocations = bill.immediate_settlement_ids.allocation_ids
        self.assertEqual(len(allocations), 2)
        self.assertEqual(
            allocations.mapped("account_id"),
            bill.invoice_line_ids.account_id,
        )
        for allocation in allocations:
            self.assertEqual(
                allocation.adjustment_line_id.analytic_distribution,
                allocation.analytic_distribution_snapshot,
            )
            self.assertFalse(allocation.adjustment_line_id.tax_ids)
            self.assertFalse(allocation.adjustment_line_id.tax_tag_ids)

    def test_tax_lines_and_tax_reporting_metadata_are_unchanged(self):
        tax = self.env["account.tax"].create(
            {
                "name": "Immediate settlement purchase tax",
                "amount": 10.0,
                "amount_type": "percent",
                "type_tax_use": "purchase",
                "company_id": self.company.id,
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
                    name="Taxed immediate settlement",
                    account_id=self.company_data["default_account_expense"],
                    quantity=1.0,
                    price_unit=5.0,
                    tax_ids=tax,
                ),
            ],
            post=True,
        )
        tax_line = bill.line_ids.filtered("tax_line_id")
        tax_snapshot = {
            "balance": tax_line.balance,
            "amount_currency": tax_line.amount_currency,
            "tax_base_amount": tax_line.tax_base_amount,
            "tags": tax_line.tax_tag_ids.ids,
        }
        payment_line = self._payment_line(bill, 5.5, 4.84)

        bill.js_settle_outstanding_line(payment_line.id)

        self.assertEqual(tax_line.balance, tax_snapshot["balance"])
        self.assertEqual(
            tax_line.amount_currency,
            tax_snapshot["amount_currency"],
        )
        self.assertEqual(
            tax_line.tax_base_amount,
            tax_snapshot["tax_base_amount"],
        )
        self.assertEqual(tax_line.tax_tag_ids.ids, tax_snapshot["tags"])
        generated_lines = bill.immediate_settlement_ids.adjustment_move_id.line_ids
        self.assertFalse(generated_lines.tax_ids)
        self.assertFalse(generated_lines.tax_tag_ids)
        self.assertFalse(generated_lines.tax_line_id)

    def test_cash_basis_processing_stays_native_without_fx_move(self):
        cash_basis_journal = self.env["account.journal"].create(
            {
                "name": "Immediate settlement cash basis",
                "code": "IMCB",
                "type": "general",
                "company_id": self.company.id,
            },
        )
        cash_basis_base_account = self.env["account.account"].create(
            {
                "name": "Cash basis base",
                "code": "IMCBB",
                "account_type": "expense",
                "company_ids": [Command.set(self.company.ids)],
            },
        )
        cash_basis_transition_account = self.env["account.account"].create(
            {
                "name": "Cash basis transition",
                "code": "IMCBT",
                "account_type": "asset_current",
                "reconcile": True,
                "company_ids": [Command.set(self.company.ids)],
            },
        )
        tax_account = self.env["account.account"].create(
            {
                "name": "Cash basis tax",
                "code": "IMCBTAX",
                "account_type": "liability_current",
                "company_ids": [Command.set(self.company.ids)],
            },
        )
        self.company.tax_cash_basis_journal_id = cash_basis_journal
        self.company.tax_exigibility = True
        self.company.account_cash_basis_base_account_id = (
            cash_basis_base_account
        )
        tax = self.env["account.tax"].create(
            {
                "name": "Immediate settlement cash basis tax",
                "amount": 10.0,
                "amount_type": "percent",
                "type_tax_use": "purchase",
                "tax_exigibility": "on_payment",
                "cash_basis_transition_account_id": (
                    cash_basis_transition_account.id
                ),
                "company_id": self.company.id,
                "invoice_repartition_line_ids": [
                    Command.create({"repartition_type": "base"}),
                    Command.create(
                        {
                            "repartition_type": "tax",
                            "account_id": tax_account.id,
                        },
                    ),
                ],
                "refund_repartition_line_ids": [
                    Command.create({"repartition_type": "base"}),
                    Command.create(
                        {
                            "repartition_type": "tax",
                            "account_id": tax_account.id,
                        },
                    ),
                ],
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
                    name="Cash basis immediate settlement",
                    account_id=self.company_data["default_account_expense"],
                    quantity=1.0,
                    price_unit=5.0,
                    tax_ids=tax,
                ),
            ],
            post=True,
        )
        payment_line = self._payment_line(bill, 5.5, 4.84)

        bill.js_settle_outstanding_line(payment_line.id)

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
        self.assertEqual(set(cash_basis_moves.mapped("state")), {"posted"})
        self.assertFalse(settlement.partial_reconcile_ids.exchange_move_id)

    def test_refunds_and_receipts_follow_the_same_generic_engine(self):
        for move_type in ("in_refund", "out_refund", "in_receipt", "out_receipt"):
            document = self._document(move_type)
            payment_line = self._payment_line(document, 5.0, 4.4)

            document.js_settle_outstanding_line(payment_line.id)

            self.assertTrue(
                document.currency_id.is_zero(document.amount_residual),
                move_type,
            )
            self.assertTrue(payment_line.reconciled, move_type)
            self.assertFalse(
                document.immediate_settlement_ids.partial_reconcile_ids.exchange_move_id,
                move_type,
            )

    def test_unreconciliation_reverses_the_complete_settlement(self):
        bill = self._document("in_invoice")
        payment_line = self._payment_line(bill, 5.0, 4.4)
        bill.js_settle_outstanding_line(payment_line.id)
        settlement = bill.immediate_settlement_ids

        settlement.partial_reconcile_ids[:1].unlink()

        self.assertEqual(settlement.state, "reversed")
        self.assertTrue(settlement.reversal_move_id)
        self.assertFalse(settlement.partial_reconcile_ids)
        self.assertFalse(payment_line.reconciled)
        self.assertEqual(bill.payment_state, "not_paid")

    def test_adjustment_is_immutable_outside_settlement_reversal(self):
        bill = self._document("in_invoice")
        payment_line = self._payment_line(bill, 5.0, 4.4)
        bill.js_settle_outstanding_line(payment_line.id)
        adjustment = bill.immediate_settlement_ids.adjustment_move_id

        with self.assertRaisesRegex(UserError, "Reverse the linked"):
            adjustment.button_draft()
        with self.assertRaisesRegex(UserError, "cannot be edited"):
            bill.immediate_settlement_ids.name = "Edited"
