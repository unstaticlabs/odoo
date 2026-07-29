from datetime import timedelta

from odoo import Command, fields
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
        term_line = cls.bill.line_ids.filtered(
            lambda line: line.display_type == "payment_term",
        )[:1]
        bank_journal = cls.company_data["default_journal_bank"]
        cls.payment_move = cls.env["account.move"].create(
            {
                "move_type": "entry",
                "journal_id": bank_journal.id,
                "date": cls.document_date + timedelta(days=1),
                "ref": "Immediate settlement browser tour",
                "line_ids": [
                    Command.create(
                        {
                            "name": "Payment counterpart",
                            "account_id": term_line.account_id.id,
                            "partner_id": cls.bill.commercial_partner_id.id,
                            "currency_id": cls.foreign_currency.id,
                            "balance": 4.4,
                            "amount_currency": 5.0,
                        },
                    ),
                    Command.create(
                        {
                            "name": "Bank executed debit",
                            "account_id": bank_journal.default_account_id.id,
                            "currency_id": cls.company.currency_id.id,
                            "balance": -4.4,
                            "amount_currency": -4.4,
                        },
                    ),
                ],
            },
        )
        cls.payment_move.action_post()

    def test_settle_trace_and_reversal_tour(self):
        self.start_tour(
            f"/odoo/account.move/{self.bill.id}",
            "usl_immediate_settlement",
            login=self.env.user.login,
        )

        settlement = self.bill.immediate_settlement_ids
        self.assertEqual(settlement.state, "reversed")
        self.assertEqual(settlement.reversal_move_id.state, "posted")
        self.assertFalse(settlement.partial_reconcile_ids)
        self.assertEqual(self.bill.payment_state, "not_paid")
        term_line = self.bill.line_ids.filtered(
            lambda line: line.display_type == "payment_term",
        )[:1]
        payment_line = self.payment_move.line_ids.filtered(
            lambda line: line.account_id == term_line.account_id,
        )
        self.assertEqual(term_line.amount_residual_currency, -5.0)
        self.assertAlmostEqual(term_line.amount_residual, -4.5, places=2)
        self.assertEqual(payment_line.amount_residual_currency, 5.0)
        self.assertAlmostEqual(payment_line.amount_residual, 4.4, places=2)
