from datetime import timedelta

from odoo import fields
from odoo.tests import HttpCase, tagged

from odoo.addons.account.tests.common import AccountTestInvoicingCommon


@tagged(
    "post_install",
    "-at_install",
    "usl_accounting_immediate_settlement_browser",
)
class TestImmediateSettlementBrowser(
    AccountTestInvoicingCommon,
    HttpCase,
):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
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
        cls.company.restrictive_audit_trail = False

        cls.bill = cls._create_invoice(
            move_type="in_invoice",
            partner_id=cls.partner_a,
            company_id=cls.company,
            currency_id=cls.foreign_currency,
            journal_id=cls.company_data["default_journal_purchase"],
            invoice_date=cls.document_date,
            date=cls.document_date,
            invoice_line_ids=[
                cls._prepare_invoice_line(
                    name="Immediate settlement browser tour",
                    account_id=cls.company_data["default_account_expense"],
                    quantity=1.0,
                    price_unit=5.0,
                    tax_ids=cls.env["account.tax"],
                ),
            ],
            post=True,
        )
        bank_journal = cls.company_data["default_journal_bank"]
        bank_journal.reconcile_mode = "edit"
        bank_journal.suspense_account_id.reconcile = True
        cls.statement_line = cls.env["account.bank.statement.line"].create(
            {
                "journal_id": bank_journal.id,
                "date": cls.statement_date,
                "payment_ref": "CLOUDFLARE EUR CARD",
                "amount": -4.40,
                "partner_id": cls.bill.commercial_partner_id.id,
            },
        )

    def test_settle_trace_and_reversal_tour(self):
        _liquidity, source_line, _other = self.statement_line._seek_for_lines()
        eligibility = self.bill._get_immediate_settlement_eligibility(
            source_line,
        )
        self.assertTrue(eligibility["eligible"], eligibility["reason"])
        widget_line = next(
            (
                line
                for line in self.bill.invoice_outstanding_credits_debits_widget[
                    "content"
                ]
                if line["id"] == source_line.id
            ),
            None,
        )
        self.assertIsNotNone(widget_line)
        self.assertTrue(
            widget_line["can_immediate_settle"],
            widget_line["immediate_settlement_reason"],
        )
        self.assertTrue(
            widget_line["can_use_payment_rate"],
            widget_line["payment_rate_settlement_reason"],
        )
        self.assertEqual(
            widget_line["recommended_settlement_action"],
            "payment_rate",
        )

        self.start_tour(
            f"/odoo/account.move/{self.bill.id}",
            "usl_immediate_settlement",
            login=self.env.user.login,
        )

        settlement = self.bill.immediate_settlement_ids
        self.assertEqual(settlement.state, "reversed")
        self.assertEqual(settlement.mechanism, "payment_rate")
        self.assertFalse(settlement.reversal_move_id)
        self.assertFalse(settlement.exchange_move_names)
        self.assertAlmostEqual(
            settlement.economic_adjustment_amount,
            0.02,
            places=2,
        )
        self.assertTrue(settlement.allocation_ids)
        self.assertFalse(settlement.allocation_ids.adjustment_line_id)
        self.assertFalse(settlement.partial_reconcile_ids)
        self.assertEqual(self.bill.payment_state, "not_paid")
        term_line = self.bill.line_ids.filtered(
            lambda line: line.display_type == "payment_term",
        )[:1]
        self.assertEqual(term_line.amount_residual_currency, -5.0)
        self.assertAlmostEqual(term_line.amount_residual, -4.38, places=2)
        self.assertFalse(self.statement_line.foreign_currency_id)
        self.assertEqual(self.statement_line.amount_currency, 0.0)
        self.assertFalse(
            self.statement_line.immediate_settlement_foreign_amount_source,
        )
        _liquidity, suspense_lines, _other = self.statement_line._seek_for_lines()
        self.assertEqual(len(suspense_lines), 1)
        self.assertAlmostEqual(suspense_lines.amount_residual, 4.40, places=2)
