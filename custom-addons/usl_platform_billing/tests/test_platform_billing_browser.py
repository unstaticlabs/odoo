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
        cls.env.user.group_ids += cls.env.ref(
            "usl_platform_billing.group_platform_billing_manager",
        )
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
                "bank_label_pattern": "Browser payout {ref}",
                "auto_create_compensation": True,
            },
        )
        cls.manager = new_test_user(
            cls.env,
            login="platform_billing_browser_manager",
            password="platform_billing_browser_manager",
            groups=(
                "usl_platform_billing.group_platform_billing_manager,"
                "account.group_validate_bank_account"
            ),
            company_id=cls.company.id,
            lang="en_US",
        )
        cls.operator = new_test_user(
            cls.env,
            login="platform_billing_browser_operator",
            password="platform_billing_browser_operator",
            groups="usl_platform_billing.group_platform_billing_operator",
            company_id=cls.company.id,
            lang="en_US",
        )
        cls.reviewer = new_test_user(
            cls.env,
            login="platform_billing_browser_reviewer",
            password="platform_billing_browser_reviewer",
            groups="usl_platform_billing.group_platform_billing_reader",
            company_id=cls.company.id,
            lang="en_US",
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
        cls.env[
            "usl.platform.billing.bank.allocation"
        ]._action_create(
            {
                "payout_id": cls.payout.id,
                "bank_statement_line_id": cls.bank_line.id,
                "bank_amount": 80.0,
                "payout_amount": 80.0,
                "score": 100,
                "detection_reason": "Prepared browser allocation",
            },
        )

    def test_operator_creates_and_posts_delayed_monthly_session(self):
        closed_session = self.env["usl.platform.billing.session"].create(
            {
                "name": "Browser closed session",
                "company_id": self.company.id,
                "period_month": fields.Date.from_string("2026-06-01"),
                "invoice_date": fields.Date.from_string("2026-06-30"),
                "bank_currency_id": self.currency.id,
            },
        )
        closed_session._workflow_write({"state": "cancelled"})
        self.platform.copy({"name": "Browser platform without August payout"})
        action = self.env.ref(
            "usl_platform_billing.action_platform_billing_sessions",
        )
        # Keep the parent-form calendar navigation deterministic across
        # execution dates.  The inline payout picker selects its year and month
        # explicitly in the browser journey.
        action.context = (
            "{'default_period_month': '2026-07-01', "
            "'default_invoice_date': '2026-07-31'}"
        )

        self.start_tour(
            f"/odoo/action-{action.id}",
            "usl_platform_billing_operator_creation_journey",
            login=self.operator.login,
        )

        session = self.env["usl.platform.billing.session"].search(
            [
                ("company_id", "=", self.company.id),
                ("name", "=", "Août 2026"),
            ],
        )
        self.assertEqual(len(session), 1)
        self.assertEqual(session.state, "posted")
        self.assertEqual(len(session.payout_ids), 1)
        self.assertFalse(session.payout_ids.bank_statement_line_id)
        self.assertEqual(session.customer_invoice_ids.state, "posted")
        self.assertEqual(session.customer_invoice_ids.amount_residual, 80.0)
        self.assertNotEqual(session.customer_invoice_ids.payment_state, "paid")
        self.assertEqual(session.vendor_bill_ids.payment_state, "paid")

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

    def test_manager_can_open_platform_configuration(self):
        action = self.env.ref(
            "usl_platform_billing.action_platform_billing_platforms",
        )
        self.start_tour(
            f"/odoo/action-{action.id}",
            "usl_platform_billing_manager_config_journey",
            login=self.manager.login,
        )
        self.assertIn("analytic_precision", self.platform._fields)
        self.assertIn(
            self.env.ref("analytic.group_analytic_accounting"),
            self.manager.all_group_ids,
        )

    def test_operator_imports_a_bank_transaction_as_a_new_payout(self):
        session = self.env["usl.platform.billing.session"].create(
            {
                "name": "Browser bank import creation",
                "company_id": self.company.id,
                "period_month": fields.Date.from_string("2026-09-01"),
                "invoice_date": fields.Date.from_string("2026-09-30"),
                "due_date": fields.Date.from_string("2026-09-30"),
                "bank_currency_id": self.currency.id,
            },
        )
        statement = self.env["account.bank.statement"].create(
            {
                "name": "Browser payout creation",
                "journal_id": self.company_data["default_journal_bank"].id,
                "date": fields.Date.from_string("2026-09-20"),
            },
        )
        bank_line = self.env["account.bank.statement.line"].create(
            {
                "name": "Unrecognised browser platform receipt",
                "payment_ref": "Unrecognised browser platform receipt",
                "journal_id": self.company_data["default_journal_bank"].id,
                "statement_id": statement.id,
                "amount": 80.0,
                "date": fields.Date.from_string("2026-09-20"),
            },
        )
        action = self.env.ref(
            "usl_platform_billing.action_platform_billing_sessions",
        )

        self.start_tour(
            f"/odoo/action-{action.id}/{session.id}",
            "usl_platform_billing_bank_create_journey",
            login=self.operator.login,
        )

        payout = session.payout_ids
        self.assertEqual(len(payout), 1)
        self.assertEqual(payout.platform_reference, "BROWSER-CREATE-001")
        self.assertEqual(payout.platform_id, self.platform)
        self.assertEqual(payout.platform_currency_id, self.platform.currency_id)
        self.assertEqual(payout.net_platform_amount, 80.0)
        self.assertEqual(payout.bank_statement_line_id, bank_line)
        self.assertEqual(payout.bank_allocation_ids.payout_amount, 80.0)
        self.assertEqual(payout.bank_match_status, "selected")
        self.assertEqual(payout.currency_valuation_method, "bank")
        self.assertAlmostEqual(payout.effective_bank_rate, 1.0)
        self.assertEqual(session.state, "ready")

    def test_operator_uses_bank_rate_for_foreign_payout_without_fx(self):
        usd = self.env["res.currency"].create(
            {
                "name": "BQF",
                "symbol": "$Q",
                "rounding": 0.01,
            },
        )
        self.env["res.currency.rate"].create(
            {
                "currency_id": usd.id,
                "company_id": self.company.id,
                "name": fields.Date.from_string("2026-09-01"),
                "rate": 2.0,
            },
        )
        platform = self.platform.copy(
            {
                "name": "Browser FX CreatorHub",
                "currency_id": usd.id,
                "bank_label_pattern": "Browser FX payout {ref}",
            },
        )
        session = self.env["usl.platform.billing.session"].create(
            {
                "name": "Browser effective bank rate",
                "company_id": self.company.id,
                "period_month": fields.Date.from_string("2026-09-01"),
                "invoice_date": fields.Date.from_string("2026-09-30"),
                "due_date": fields.Date.from_string("2026-09-30"),
                "bank_currency_id": self.currency.id,
            },
        )
        statement = self.env["account.bank.statement"].create(
            {
                "name": "Browser effective bank rate",
                "journal_id": self.company_data["default_journal_bank"].id,
                "date": fields.Date.from_string("2026-09-20"),
            },
        )
        bank_line = self.env["account.bank.statement.line"].create(
            {
                "name": "Browser FX payout BROWSER-FX-1000",
                "payment_ref": "Browser FX payout BROWSER-FX-1000",
                "journal_id": self.company_data["default_journal_bank"].id,
                "statement_id": statement.id,
                "amount": 700.0,
                "date": fields.Date.from_string("2026-09-20"),
            },
        )
        previous_exchange_moves = set(
            self.env["account.partial.reconcile"].search([]).exchange_move_id.ids,
        )
        action = self.env.ref(
            "usl_platform_billing.action_platform_billing_sessions",
        )

        self.start_tour(
            f"/odoo/action-{action.id}/{session.id}",
            "usl_platform_billing_bank_rate_journey",
            login=self.operator.login,
        )

        payout = session.payout_ids
        self.assertEqual(len(payout), 1)
        self.assertEqual(payout.platform_id, platform)
        self.assertEqual(payout.net_platform_amount, 1000.0)
        self.assertEqual(payout.currency_valuation_method, "bank")
        self.assertAlmostEqual(payout.effective_bank_rate, 0.7)
        self.assertEqual(abs(payout.customer_invoice_id.amount_total_signed), 875.0)
        self.assertEqual(abs(payout.vendor_bill_id.amount_total_signed), 175.0)
        self.assertEqual(payout.compensation_move_id.currency_id, usd)
        self.assertEqual(payout.compensation_move_id.amount_total, 250.0)
        self.assertEqual(payout.compensation_move_id.amount_total_signed, 175.0)
        self.assertEqual(bank_line.amount, 700.0)
        self.assertEqual(bank_line.amount_currency, 1000.0)
        self.assertTrue(bank_line.is_reconciled)
        self.assertEqual(session.state, "paid")
        current_exchange_moves = set(
            self.env["account.partial.reconcile"].search([]).exchange_move_id.ids,
        )
        self.assertEqual(current_exchange_moves, previous_exchange_moves)

    def test_operator_links_one_pooled_receipt_to_two_sessions(self):
        first = self.env["usl.platform.billing.session"].create(
            {
                "name": "Browser pooled receipt one",
                "company_id": self.company.id,
                "period_month": fields.Date.from_string("2026-10-01"),
                "invoice_date": fields.Date.from_string("2026-10-31"),
                "due_date": fields.Date.from_string("2026-10-31"),
                "bank_currency_id": self.currency.id,
            },
        )
        second = self.env["usl.platform.billing.session"].create(
            {
                "name": "Browser pooled receipt two",
                "company_id": self.company.id,
                "period_month": fields.Date.from_string("2026-11-01"),
                "invoice_date": fields.Date.from_string("2026-11-30"),
                "due_date": fields.Date.from_string("2026-11-30"),
                "bank_currency_id": self.currency.id,
            },
        )
        for session, reference, payout_date in (
            (first, "BROWSER-POOL-ONE", "2026-10-15"),
            (second, "BROWSER-POOL-TWO", "2026-11-15"),
        ):
            self.env["usl.platform.billing.payout"].create(
                {
                    "session_id": session.id,
                    "platform_id": self.platform.id,
                    "payout_date": fields.Date.from_string(payout_date),
                    "platform_reference": reference,
                    "net_platform_amount": 80.0,
                },
            )
            session.action_check()
            session.action_generate_documents()
            session.with_context(
                skip_platform_coverage_warning=True,
            ).action_post_documents()
        statement = self.env["account.bank.statement"].create(
            {
                "name": "Browser pooled receipt",
                "journal_id": self.company_data["default_journal_bank"].id,
                "date": fields.Date.from_string("2026-12-20"),
            },
        )
        bank_line = self.env["account.bank.statement.line"].create(
            {
                "name": "Browser pooled receipt 160",
                "payment_ref": "Browser pooled receipt 160",
                "journal_id": self.company_data["default_journal_bank"].id,
                "statement_id": statement.id,
                "amount": 160.0,
                "date": fields.Date.from_string("2026-12-20"),
            },
        )
        action = self.env.ref(
            "usl_platform_billing.action_platform_billing_sessions",
        )

        self.start_tour(
            f"/odoo/action-{action.id}/{first.id}",
            "usl_platform_billing_pooled_link_journey",
            login=self.operator.login,
        )

        self.assertTrue(bank_line.is_reconciled)
        self.assertEqual(first.state, "paid")
        self.assertEqual(second.state, "paid")
        allocations = self.env[
            "usl.platform.billing.bank.allocation"
        ].search([("bank_statement_line_id", "=", bank_line.id)])
        self.assertEqual(len(allocations), 2)
        first.action_reconcile_bank()
        self.assertEqual(len(allocations), 2)

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
