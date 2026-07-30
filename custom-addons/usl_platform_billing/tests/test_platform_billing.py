import base64

from psycopg2 import IntegrityError

from odoo import Command, fields
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import new_test_user, tagged
from odoo.tools import mute_logger

from odoo.addons.account.tests.common import AccountTestInvoicingCommon


@tagged("post_install", "-at_install", "usl_platform_billing")
class TestPlatformBilling(AccountTestInvoicingCommon):
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
        cls.accountant = new_test_user(
            cls.env,
            login="platform_billing_accountant",
            groups="account.group_account_user",
        )
        cls.reviewer = new_test_user(
            cls.env,
            login="platform_billing_reviewer",
            groups="account.group_account_readonly",
        )

    def _session(self, *, name="Creator platforms — July 2026"):
        return self.env["usl.platform.billing.session"].create(
            {
                "name": name,
                "company_id": self.company.id,
                "period_month": fields.Date.from_string("2026-07-01"),
                "invoice_date": fields.Date.from_string("2026-07-31"),
                "due_date": fields.Date.from_string("2026-07-31"),
                "bank_currency_id": self.currency.id,
            },
        )

    def _payout(
        self,
        session,
        *,
        reference="CH-2026-07-001",
        amount=80.0,
        platform=None,
    ):
        platform = platform or self.platform
        return self.env["usl.platform.billing.payout"].create(
            {
                "session_id": session.id,
                "platform_id": platform.id,
                "payout_date": fields.Date.from_string("2026-07-15"),
                "platform_reference": reference,
                "net_platform_amount": amount,
            },
        )

    def _generate_and_post(self, session):
        session.action_check()
        session.action_generate_documents()
        session.action_post_documents()
        return session

    def _bank_line(self, amount, *, label="CH payout CH-2026-07-001"):
        journal = self.company_data["default_journal_bank"]
        statement = self.env["account.bank.statement"].create(
            {
                "name": "CreatorHub July payouts",
                "journal_id": journal.id,
                "date": fields.Date.from_string("2026-07-20"),
            },
        )
        return self.env["account.bank.statement.line"].create(
            {
                "name": label,
                "payment_ref": label,
                "journal_id": journal.id,
                "statement_id": statement.id,
                "amount": amount,
                "date": fields.Date.from_string("2026-07-20"),
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
                "bank_match_status": "selected",
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

    def test_accounting_user_operates_and_readonly_user_cannot_mutate(self):
        session = self._session()
        self._payout(session)

        session.with_user(self.accountant).action_check()
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
            self.platform.with_user(self.accountant).write({"commission_rate": 21})

    def test_blocked_bank_reconciliation_preserves_statement_amount(self):
        session = self._session()
        payout = self._payout(session)
        self._generate_and_post(session)
        bank_line = self._bank_line(79.0)
        payout.write(
            {
                "bank_statement_line_id": bank_line.id,
                "bank_received_amount": 80.0,
                "bank_match_status": "selected",
            },
        )

        session.action_reconcile_bank()

        self.assertFalse(bank_line.is_reconciled)
        self.assertEqual(bank_line.amount, 79.0)
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
                "bank_match_status": "selected",
            },
        )

        session.action_reconcile_bank()

        self.assertEqual(bank_line.amount, 40.0)
        self.assertEqual(bank_line.foreign_currency_id, foreign_currency)
        self.assertEqual(bank_line.amount_currency, 80.0)
        self.assertTrue(bank_line.is_reconciled)
        self.assertEqual(session.state, "paid")
