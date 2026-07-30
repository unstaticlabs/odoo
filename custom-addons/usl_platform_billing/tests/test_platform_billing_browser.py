from odoo import Command, fields
from odoo.tests import HttpCase, new_test_user, tagged

from odoo.addons.account.tests.common import AccountTestInvoicingCommon


@tagged(
    "post_install",
    "-at_install",
    "usl_platform_billing_browser",
)
class TestPlatformBillingBrowser(AccountTestInvoicingCommon, HttpCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.company_data["company"]
        cls.currency = cls.company.currency_id
        cls.product_a.write(
            {
                "taxes_id": [Command.clear()],
                "supplier_taxes_id": [Command.clear()],
            },
        )
        cls.product_b.write(
            {
                "taxes_id": [Command.clear()],
                "supplier_taxes_id": [Command.clear()],
            },
        )
        cls.platform = cls.env["usl.platform.billing.platform"].create(
            {
                "name": "Browser CreatorHub",
                "company_id": cls.company.id,
                "partner_id": cls.partner_a.id,
                "commission_rate": 20.0,
                "currency_id": cls.currency.id,
                "revenue_product_id": cls.product_a.id,
                "commission_product_id": cls.product_b.id,
                "sale_journal_id": cls.company_data["default_journal_sale"].id,
                "purchase_journal_id": cls.company_data[
                    "default_journal_purchase"
                ].id,
                "compensation_journal_id": cls.company_data[
                    "default_journal_misc"
                ].id,
                "bank_journal_id": cls.company_data["default_journal_bank"].id,
                "auto_create_compensation": True,
            },
        )
        cls.manager = new_test_user(
            cls.env,
            login="platform_billing_browser_manager",
            password="platform_billing_browser_manager",
            groups=(
                "account.group_account_user,"
                "account.group_account_manager,"
                "account.group_validate_bank_account"
            ),
            company_id=cls.company.id,
        )
        cls.reviewer = new_test_user(
            cls.env,
            login="platform_billing_browser_reviewer",
            password="platform_billing_browser_reviewer",
            groups="account.group_account_readonly",
            company_id=cls.company.id,
        )
        cls.billing_session = cls.env["usl.platform.billing.session"].create(
            {
                "name": "Browser journey — July 2026",
                "company_id": cls.company.id,
                "period_month": fields.Date.from_string("2026-07-01"),
                "invoice_date": fields.Date.from_string("2026-07-31"),
                "due_date": fields.Date.from_string("2026-07-31"),
                "bank_currency_id": cls.currency.id,
            },
        )
        cls.payout = cls.env["usl.platform.billing.payout"].create(
            {
                "session_id": cls.billing_session.id,
                "platform_id": cls.platform.id,
                "payout_date": fields.Date.from_string("2026-07-15"),
                "platform_reference": "BROWSER-2026-07-001",
                "net_platform_amount": 80.0,
            },
        )
        statement = cls.env["account.bank.statement"].create(
            {
                "name": "Browser CreatorHub July payout",
                "journal_id": cls.company_data["default_journal_bank"].id,
                "date": fields.Date.from_string("2026-07-20"),
            },
        )
        cls.bank_line = cls.env["account.bank.statement.line"].create(
            {
                "name": "Browser payout BROWSER-2026-07-001",
                "payment_ref": "Browser payout BROWSER-2026-07-001",
                "journal_id": cls.company_data["default_journal_bank"].id,
                "statement_id": statement.id,
                "amount": 80.0,
                "date": fields.Date.from_string("2026-07-20"),
            },
        )
        cls.payout.write(
            {
                "bank_statement_line_id": cls.bank_line.id,
                "bank_received_amount": 80.0,
                "bank_match_status": "selected",
            },
        )

    def test_manager_document_and_bank_journey(self):
        action = self.env.ref(
            "usl_platform_billing.action_platform_billing_sessions",
        )
        self.start_tour(
            f"/odoo/action-{action.id}/{self.billing_session.id}",
            "usl_platform_billing_manager_journey",
            login=self.manager.login,
        )
        self.assertEqual(self.billing_session.state, "paid")
        self.assertTrue(self.bank_line.is_reconciled)
        self.assertEqual(
            self.billing_session.customer_invoice_ids.payment_state,
            "paid",
        )
        self.assertEqual(
            self.billing_session.vendor_bill_ids.payment_state,
            "paid",
        )

    def test_reviewer_readonly_journey(self):
        self.billing_session.with_user(self.manager).action_check()
        self.billing_session.with_user(self.manager).action_generate_documents()
        self.billing_session.with_user(self.manager).action_post_documents()
        self.billing_session.with_user(self.manager).action_reconcile_bank()
        action = self.env.ref(
            "usl_platform_billing.action_platform_billing_sessions",
        )
        self.start_tour(
            f"/odoo/action-{action.id}/{self.billing_session.id}",
            "usl_platform_billing_reviewer_journey",
            login=self.reviewer.login,
        )
