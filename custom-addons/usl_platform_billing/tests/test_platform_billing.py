import base64

from psycopg2 import IntegrityError

from odoo import Command, fields
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import Form, new_test_user, tagged
from odoo.tools import mute_logger
from odoo.tools.safe_eval import safe_eval

from odoo.addons.account.tests.common import AccountTestInvoicingCommon


@tagged("post_install", "-at_install", "usl_platform_billing")
class TestPlatformBilling(AccountTestInvoicingCommon):
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
        cls.platform_partner = cls.partner_a
        cls.platform = cls.env["usl.platform.billing.platform"].create(
            {
                "name": "CreatorHub",
                "company_id": cls.company.id,
                "partner_id": cls.platform_partner.id,
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
                "bank_label_pattern": "CH payout {ref}",
                "bank_label_keywords": "CREATORHUB,CREATOR HUB",
                "auto_create_compensation": True,
            },
        )
        cls.operator = new_test_user(
            cls.env,
            login="platform_billing_operator",
            groups="usl_platform_billing.group_platform_billing_operator",
        )
        cls.reviewer = new_test_user(
            cls.env,
            login="platform_billing_reviewer",
            groups="usl_platform_billing.group_platform_billing_reader",
        )
        cls.manager = new_test_user(
            cls.env,
            login="platform_billing_manager",
            groups="usl_platform_billing.group_platform_billing_manager",
        )
        cls.accountant = new_test_user(
            cls.env,
            login="accountant_without_platform_billing",
            groups="account.group_account_user",
        )

    def _session(
        self,
        *,
        name="Creator platforms — July 2026",
        period_month="2026-07-01",
        invoice_date="2026-07-31",
        due_date="2026-07-31",
    ):
        values = {
            "name": name,
            "company_id": self.company.id,
            "period_month": fields.Date.from_string(period_month),
            "invoice_date": fields.Date.from_string(invoice_date),
            "bank_currency_id": self.currency.id,
        }
        if due_date:
            values["due_date"] = fields.Date.from_string(due_date)
        return self.env["usl.platform.billing.session"].create(values)

    def _payout(
        self,
        session,
        *,
        reference="CH-2026-07-001",
        amount=80.0,
        platform=None,
        payout_date="2026-07-15",
    ):
        platform = platform or self.platform
        return self.env["usl.platform.billing.payout"].create(
            {
                "session_id": session.id,
                "platform_id": platform.id,
                "payout_date": fields.Date.from_string(payout_date),
                "platform_reference": reference,
                "net_platform_amount": amount,
            },
        )

    def _generate_and_post(self, session):
        session.action_check()
        session.action_generate_documents()
        session.with_context(
            skip_platform_coverage_warning=True,
        ).action_post_documents()
        return session

    def _bank_line(
        self,
        amount,
        *,
        label="CH payout CH-2026-07-001",
        bank_date="2026-07-20",
    ):
        journal = self.company_data["default_journal_bank"]
        statement = self.env["account.bank.statement"].create(
            {
                "name": "CreatorHub July payouts",
                "journal_id": journal.id,
                "date": fields.Date.from_string(bank_date),
            },
        )
        return self.env["account.bank.statement.line"].create(
            {
                "name": label,
                "payment_ref": label,
                "journal_id": journal.id,
                "statement_id": statement.id,
                "amount": amount,
                "date": fields.Date.from_string(bank_date),
            },
        )

    def test_session_list_defaults_to_all_records(self):
        action = self.env.ref(
            "usl_platform_billing.action_platform_billing_sessions",
        )
        search_view = self.env.ref(
            "usl_platform_billing.view_platform_billing_session_search",
        )

        self.assertEqual(safe_eval(action.context or "{}"), {})
        self.assertIn('name="open"', search_view.arch_db)

    def test_french_period_name_tracks_only_automatic_names(self):
        session_form = Form(
            self.env["usl.platform.billing.session"].with_user(self.operator),
        )
        session_form.period_month = fields.Date.from_string("2026-08-01")
        self.assertEqual(session_form.name, "Août 2026")
        session_form.name = "Monthly creator billing"
        session_form.period_month = fields.Date.from_string("2026-09-01")
        self.assertEqual(session_form.name, "Monthly creator billing")

        session = self.env["usl.platform.billing.session"].with_user(
            self.operator,
        ).create(
            {
                "company_id": self.company.id,
                "period_month": fields.Date.from_string("2026-08-01"),
                "invoice_date": fields.Date.from_string("2026-08-31"),
                "bank_currency_id": self.currency.id,
                "state": "draft",
                "generated_at": False,
                "generated_by_id": False,
            },
        )

        self.assertEqual(session.name, "Août 2026")
        session.write({"period_month": fields.Date.from_string("2026-09-01")})
        self.assertEqual(session.name, "Septembre 2026")

        session.write({"name": "Quarter-end creator billing"})
        session.write({"period_month": fields.Date.from_string("2026-10-01")})
        self.assertEqual(session.name, "Quarter-end creator billing")

        legacy = self.env["usl.platform.billing.session"].with_user(
            self.operator,
        ).create(
            {
                "name": "Platform billing — 2026-11",
                "company_id": self.company.id,
                "period_month": fields.Date.from_string("2026-11-01"),
                "invoice_date": fields.Date.from_string("2026-11-30"),
                "bank_currency_id": self.currency.id,
            },
        )
        self.assertEqual(legacy.name, "Novembre 2026")

    def test_web_workflow_defaults_are_harmless_but_transitions_are_blocked(self):
        session = self.env["usl.platform.billing.session"].with_user(
            self.operator,
        ).create(
            {
                "company_id": self.company.id,
                "period_month": fields.Date.from_string("2026-12-01"),
                "invoice_date": fields.Date.from_string("2026-12-31"),
                "bank_currency_id": self.currency.id,
                "state": "draft",
                "generated_at": False,
                "generated_by_id": False,
            },
        )
        session.with_user(self.operator).write(
            {
                "state": "draft",
                "generated_at": False,
                "generated_by_id": False,
            },
        )
        payout = self.env["usl.platform.billing.payout"].with_user(
            self.operator,
        ).create(
            {
                "session_id": session.id,
                "platform_id": self.platform.id,
                "payout_date": fields.Date.from_string("2026-12-15"),
                "platform_reference": "WEB-2026-12-001",
                "net_platform_amount": 80.0,
                "state": "draft",
                "customer_invoice_id": False,
                "vendor_bill_id": False,
                "compensation_move_id": False,
                "bank_match_status": "unmatched",
                "bank_match_score": 0,
                "bank_amount_difference": 0.0,
                "bank_date_difference": 0,
                "bank_detection_reason": False,
            },
        )
        payout.with_user(self.operator).write(
            {
                "state": "draft",
                "customer_invoice_id": False,
                "vendor_bill_id": False,
                "compensation_move_id": False,
                "bank_match_status": "unmatched",
                "bank_match_score": 0,
                "bank_amount_difference": 0.0,
                "bank_date_difference": 0,
                "bank_detection_reason": False,
            },
        )

        with self.assertRaises(AccessError):
            session.with_user(self.operator).write({"state": "ready"})
        with self.assertRaises(AccessError):
            payout.with_user(self.operator).write({"bank_match_status": "selected"})
        unrelated_invoice = self.init_invoice(
            "out_invoice",
            partner=self.platform_partner,
            invoice_date="2026-12-31",
            products=self.product_a,
        )
        with self.assertRaises(AccessError):
            payout.with_user(self.operator).write(
                {"customer_invoice_id": unrelated_invoice.id},
            )
        with self.assertRaises(AccessError):
            self.env["usl.platform.billing.payout"].with_user(
                self.operator,
            ).create(
                {
                    "session_id": session.id,
                    "platform_id": self.platform.id,
                    "payout_date": fields.Date.from_string("2026-12-16"),
                    "platform_reference": "WEB-2026-12-002",
                    "net_platform_amount": 80.0,
                    "state": "posted",
                },
            )

    def test_commission_formula_and_validation_constraints(self):
        session = self._session()
        payout = self._payout(session)

        self.assertEqual(payout.gross_platform_amount, 100.0)
        self.assertEqual(payout.commission_platform_amount, 20.0)
        self.assertEqual(payout.validation_status, "warning")
        self.assertIn("bank", payout.validation_message.lower())

        with self.assertRaises(ValidationError):
            self.platform.copy(
                {
                    "name": "Invalid commission",
                    "commission_rate": 100.0,
                },
            )
        with self.assertRaises(ValidationError):
            self._payout(session, reference="negative", amount=-1)
        with (
            self.assertRaises(IntegrityError),
            self.cr.savepoint(),
            mute_logger("odoo.sql_db"),
        ):
            self._payout(session, reference=payout.platform_reference)

    def test_monthly_generation_posting_compensation_and_bank_reconciliation(self):
        session = self._session()
        payout = self._payout(session)
        attachment = self.env["ir.attachment"].create(
            {
                "name": "payout-proof.pdf",
                "datas": base64.b64encode(b"synthetic payout evidence"),
                "res_model": payout._name,
                "res_id": payout.id,
            },
        )
        payout.attachment_ids = attachment

        session.action_check()
        self.assertEqual(session.state, "ready")
        session.action_generate_documents()
        self.assertEqual(session.state, "generated")
        self.assertEqual(len(session.customer_invoice_ids), 1)
        self.assertEqual(len(session.vendor_bill_ids), 1)
        self.assertEqual(session.customer_invoice_ids.amount_untaxed, 100.0)
        self.assertEqual(session.vendor_bill_ids.amount_untaxed, 20.0)
        self.assertEqual(
            session.customer_invoice_ids.platform_billing_payout_ids,
            payout,
        )
        copied_evidence = self.env["ir.attachment"].search(
            [
                ("res_model", "=", "account.move"),
                ("res_id", "=", session.customer_invoice_ids.id),
                ("checksum", "=", attachment.checksum),
            ],
        )
        self.assertTrue(copied_evidence)

        with self.assertRaises(UserError):
            session.action_generate_documents()
        session.action_post_documents()
        self.assertEqual(session.state, "posted")
        self.assertEqual(len(session.compensation_move_ids), 1)
        compensation = session.compensation_move_ids
        self.assertEqual(compensation.state, "posted")
        self.assertEqual(sum(compensation.line_ids.mapped("balance")), 0.0)
        self.assertEqual(session.vendor_bill_ids.payment_state, "paid")
        self.assertEqual(session.customer_invoice_ids.amount_residual, 80.0)

        bank_line = self._bank_line(80.0)
        payout.write(
            {
                "bank_statement_line_id": bank_line.id,
                "bank_received_amount": 80.0,
            },
        )
        original_bank_amount = bank_line.amount
        session.action_reconcile_bank()

        self.assertTrue(bank_line.is_reconciled)
        self.assertEqual(bank_line.amount, original_bank_amount)
        self.assertEqual(session.customer_invoice_ids.payment_state, "paid")
        self.assertEqual(session.state, "paid")
        self.assertEqual(payout.bank_match_status, "reconciled")

        session.action_reconcile_bank()
        self.assertEqual(session.state, "paid")

    def test_per_payout_bill_grouping_and_draft_reset(self):
        platform = self.platform.copy(
            {
                "name": "Per-payout CreatorHub",
                "vendor_bill_grouping_mode": "per_payout",
            },
        )
        session = self._session(name="Per payout — July 2026")
        first = self._payout(
            session,
            platform=platform,
            reference="PP-001",
        )
        second = self._payout(
            session,
            platform=platform,
            reference="PP-002",
            amount=40.0,
        )

        session.action_check()
        session.action_generate_documents()
        self.assertEqual(len(session.customer_invoice_ids), 1)
        self.assertEqual(len(session.vendor_bill_ids), 2)
        self.assertNotEqual(first.vendor_bill_id, second.vendor_bill_id)

        session.action_reset_drafts()
        self.assertEqual(session.state, "ready")
        self.assertFalse(session.generated_move_ids)
        self.assertFalse(first.customer_invoice_id)
        self.assertFalse(second.vendor_bill_id)

    def test_bank_candidate_priority_and_ambiguity(self):
        session = self._session()
        payout = self._payout(session)
        bank_line = self._bank_line(80.0)
        wizard = self.env["usl.platform.billing.bank.import.wizard"].create(
            {"session_id": session.id},
        )
        platform, reference, score, reason, confidence = wizard._detect_platform(
            bank_line,
            self.platform,
        )
        self.assertEqual(platform, self.platform)
        self.assertEqual(reference, payout.platform_reference)
        self.assertEqual(score, 100)
        self.assertEqual(confidence, "high")
        self.assertIn("pattern", reason.lower())

        competing = self.platform.copy(
            {
                "name": "Competing platform",
                "bank_label_pattern": self.platform.bank_label_pattern,
            },
        )
        platform, _reference, _score, reason, confidence = wizard._detect_platform(
            bank_line,
            self.platform | competing,
        )
        self.assertFalse(platform)
        self.assertEqual(confidence, "ambiguous")
        self.assertIn("CreatorHub", reason)

    def test_platform_roles_are_opt_in_and_server_side_enforced(self):
        session = self._session()
        payout = self._payout(session)

        session.with_user(self.operator).action_check()
        self.assertEqual(session.state, "ready")
        with self.assertRaises(AccessError):
            session.with_user(self.reviewer).action_generate_documents()
        with self.assertRaises(AccessError):
            self.env["usl.platform.billing.session"].with_user(
                self.reviewer,
            ).create(
                {
                    "name": "Forbidden",
                    "company_id": self.company.id,
                    "period_month": fields.Date.from_string("2026-08-01"),
                    "invoice_date": fields.Date.from_string("2026-08-31"),
                    "bank_currency_id": self.currency.id,
                },
            )
        with self.assertRaises(AccessError):
            self.env["usl.platform.billing.session"].with_user(
                self.accountant,
            ).search([])
        with self.assertRaises(AccessError):
            session.with_user(self.accountant).action_generate_documents()
        with self.assertRaises(AccessError):
            self.platform.with_user(self.operator).write({"commission_rate": 21})
        self.platform.with_user(self.manager).write({"commission_rate": 21})
        with self.assertRaises(AccessError):
            session.with_user(self.operator).write({"state": "paid"})
        with self.assertRaises(AccessError):
            payout.with_user(self.operator).write({"state": "paid"})
        with self.assertRaises(AccessError):
            payout.with_user(self.operator).write({"bank_match_status": "reconciled"})

    def test_posted_unpaid_payout_remains_open_receivable(self):
        session = self._session()
        payout = self._payout(session)

        self._generate_and_post(session)

        self.assertEqual(session.state, "posted")
        self.assertEqual(payout.state, "posted")
        self.assertFalse(payout.bank_statement_line_id)
        self.assertEqual(session.customer_invoice_ids.amount_residual, 80.0)
        self.assertNotEqual(session.customer_invoice_ids.payment_state, "paid")

        session.action_reconcile_bank()
        self.assertEqual(session.state, "posted")
        self.assertEqual(session.customer_invoice_ids.amount_residual, 80.0)

    def test_delayed_pooled_receipt_reconciles_multiple_sessions(self):
        july = self._session(name="Pooled receipt — July 2026")
        july_payout = self._payout(july)
        august = self._session(
            name="Pooled receipt — August 2026",
            period_month="2026-08-01",
            invoice_date="2026-08-31",
            due_date="2026-08-31",
        )
        august_payout = self._payout(
            august,
            reference="CH-2026-08-001",
            amount=40.0,
            payout_date="2026-08-15",
        )
        self._generate_and_post(july)
        self._generate_and_post(august)
        bank_line = self._bank_line(
            120.0,
            bank_date="2026-10-20",
        )

        july_wizard = self.env[
            "usl.platform.billing.bank.import.wizard"
        ].create({"session_id": july.id})
        july_wizard._populate_candidates()
        july_candidate = july_wizard.candidate_ids.filtered(
            lambda candidate: candidate.bank_statement_line_id == bank_line,
        )
        self.assertEqual(len(july_candidate), 1)
        self.assertEqual(july_candidate.allocated_bank_amount, 80.0)
        july_candidate.selected = True
        july_wizard.action_import()

        august_wizard = self.env[
            "usl.platform.billing.bank.import.wizard"
        ].create({"session_id": august.id})
        august_wizard._populate_candidates()
        august_candidate = august_wizard.candidate_ids.filtered(
            lambda candidate: candidate.bank_statement_line_id == bank_line,
        )
        self.assertEqual(len(august_candidate), 1)
        self.assertEqual(august_candidate.allocated_bank_amount, 40.0)
        august_candidate.selected = True
        august_wizard.action_import()

        self.assertEqual(july_payout.bank_statement_line_id, bank_line)
        self.assertEqual(august_payout.bank_statement_line_id, bank_line)
        july.action_reconcile_bank()

        self.assertTrue(bank_line.is_reconciled)
        self.assertEqual(july.state, "paid")
        self.assertEqual(august.state, "paid")
        self.assertEqual(july.customer_invoice_ids.payment_state, "paid")
        self.assertEqual(august.customer_invoice_ids.payment_state, "paid")

    def test_posting_warns_when_an_active_platform_is_missing(self):
        missing_platform = self.platform.copy({"name": "Missing CreatorHub"})
        session = self._session()
        self._payout(session)
        session.action_check()
        session.action_generate_documents()

        action = session.action_post_documents()

        self.assertEqual(session.state, "generated")
        self.assertEqual(
            action["res_model"],
            "usl.platform.billing.post.confirm.wizard",
        )
        wizard = self.env[action["res_model"]].browse(action["res_id"])
        self.assertEqual(wizard.missing_platform_ids, missing_platform)
        wizard.action_confirm()
        self.assertEqual(session.state, "posted")

    def test_partner_payment_term_is_used_without_session_override(self):
        payment_term = self.env.ref("account.account_payment_term_30days")
        self.platform.customer_partner.with_company(
            self.company,
        ).property_payment_term_id = payment_term
        session = self._session(
            name="Native payment terms — July 2026",
            due_date=False,
        )
        self._payout(session)

        session.action_check()
        session.action_generate_documents()

        invoice = session.customer_invoice_ids
        self.assertEqual(invoice.invoice_payment_term_id, payment_term)
        self.assertEqual(
            invoice.invoice_date_due,
            fields.Date.from_string("2026-08-30"),
        )

    def test_blocked_bank_reconciliation_preserves_statement_amount(self):
        session = self._session()
        payout = self._payout(session)
        self._generate_and_post(session)
        bank_line = self._bank_line(80.0)
        payout.write(
            {
                "bank_statement_line_id": bank_line.id,
                "bank_received_amount": 79.0,
            },
        )

        session.action_reconcile_bank()

        self.assertFalse(bank_line.is_reconciled)
        self.assertEqual(bank_line.amount, 80.0)
        self.assertEqual(payout.bank_match_status, "blocked")
        self.assertEqual(session.state, "posted")

    def test_foreign_currency_bank_actual_is_preserved(self):
        foreign_currency = self.env["res.currency"].create(
            {
                "name": "PFX",
                "symbol": "PF",
                "rounding": 0.01,
            },
        )
        self.env["res.currency.rate"].create(
            {
                "currency_id": foreign_currency.id,
                "company_id": self.company.id,
                "name": fields.Date.from_string("2026-07-01"),
                "rate": 2.0,
            },
        )
        platform = self.platform.copy(
            {
                "name": "Foreign CreatorHub",
                "currency_id": foreign_currency.id,
                "bank_label_pattern": "FX payout {ref}",
            },
        )
        session = self._session(name="Foreign platforms — July 2026")
        payout = self._payout(
            session,
            platform=platform,
            reference="FX-001",
        )
        self._generate_and_post(session)
        bank_line = self._bank_line(40.0, label="FX payout FX-001")
        payout.write(
            {
                "bank_statement_line_id": bank_line.id,
                "bank_received_amount": 40.0,
            },
        )

        session.action_reconcile_bank()

        self.assertEqual(bank_line.amount, 40.0)
        self.assertEqual(bank_line.foreign_currency_id, foreign_currency)
        self.assertEqual(bank_line.amount_currency, 80.0)
        self.assertTrue(bank_line.is_reconciled)
        self.assertEqual(session.state, "paid")
