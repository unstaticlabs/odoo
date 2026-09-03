from lxml import etree
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
        journal=None,
    ):
        journal = journal or self.company_data["default_journal_bank"]
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

    def _allocation(
        self,
        payout,
        bank_line,
        *,
        bank_amount=None,
        payout_amount=None,
        score=100,
    ):
        return self.env[
            "usl.platform.billing.bank.allocation"
        ]._action_create(
            {
                "payout_id": payout.id,
                "bank_statement_line_id": bank_line.id,
                "bank_amount": (
                    bank_line.amount if bank_amount is None else bank_amount
                ),
                "payout_amount": (
                    payout.net_platform_amount
                    if payout_amount is None
                    else payout_amount
                ),
                "score": score,
                "detection_reason": "Test allocation",
            },
        )

    def _bank_wizard(self, session, *, mode="link"):
        wizard = self.env[
            "usl.platform.billing.bank.import.wizard"
        ].create(
            {
                "session_id": session.id,
                "mode": mode,
                "candidate_scope": "all",
            },
        )
        wizard._populate_payout_candidates()
        wizard._populate_candidates()
        return wizard

    def test_documents_context_is_stable_and_relationship_complete(self):
        session = self._session()
        payout = self._payout(session)

        platform_context = self.platform._document_archive_context()
        self.assertEqual(
            platform_context["document_type"],
            "Platform agreement or statement",
        )
        self.assertEqual(platform_context["archive_mode"], "mandatory")
        self.assertEqual(platform_context["document_role"], "evidence")
        self.assertEqual(
            platform_context["entity_tags"][0],
            {
                "namespace": "platform",
                "model": "usl.platform.billing.platform",
                "id": self.platform.id,
                "name": "CreatorHub",
                "parent": "Platform billing",
            },
        )

        payout_context = payout._document_archive_context()
        self.assertEqual(payout_context["document_type"], "Platform payout evidence")
        self.assertEqual(payout_context["tags"], ["Accounting", "Platform billing"])
        self.assertEqual(payout_context["archive_mode"], "mandatory")
        self.assertEqual(payout_context["document_role"], "evidence")
        self.assertIn(
            {"model": "usl.platform.billing.session", "id": session.id},
            payout._document_related_records(),
        )

    def test_new_session_document_counters_are_zero(self):
        session = self.env["usl.platform.billing.session"].new({})

        self.assertEqual(session.archived_document_count, 0)
        self.assertEqual(session.document_archive_pending_count, 0)
        self.assertEqual(session.document_archive_failure_count, 0)

    def test_session_list_defaults_to_all_records(self):
        action = self.env.ref(
            "usl_platform_billing.action_platform_billing_sessions",
        )
        search_view = self.env.ref(
            "usl_platform_billing.view_platform_billing_session_search",
        )

        self.assertEqual(safe_eval(action.context or "{}"), {})
        self.assertIn('name="open"', search_view.arch_db)

    def test_bank_candidate_selection_is_local_and_keeps_required_relation(self):
        view = self.env.ref(
            "usl_platform_billing.view_platform_billing_bank_import_wizard_form",
        )
        arch = etree.fromstring(view.arch_db.encode())
        candidate_list = arch.xpath("//field[@name='candidate_ids']/list")[0]

        self.assertFalse(candidate_list.xpath(".//button[@name='action_select']"))
        self.assertFalse(candidate_list.xpath(".//button[@name='action_unselect']"))
        selected = candidate_list.xpath(".//field[@name='selected']")[0]
        self.assertEqual(selected.get("widget"), "boolean_icon")
        bank_line = candidate_list.xpath(
            ".//field[@name='bank_statement_line_id']",
        )[0]
        self.assertEqual(bank_line.get("column_invisible"), "True")

    def test_bank_transaction_preview_contains_the_linked_record(self):
        session = self._session(name="Bank preview — July 2026")
        payout = self._payout(session, reference="PREVIEW-001")
        bank_line = self._bank_line(
            80.0,
            label="CreatorHub preview PREVIEW-001",
        )
        self._allocation(payout, bank_line)

        preview = payout.bank_transaction_preview

        self.assertEqual(len(preview), 1)
        self.assertEqual(preview[0]["id"], bank_line.id)
        self.assertEqual(preview[0]["display_name"], bank_line.display_name)
        self.assertEqual(preview[0]["journal"], bank_line.journal_id.display_name)
        self.assertEqual(preview[0]["label"], bank_line.payment_ref)
        self.assertIn("80", preview[0]["amount"])
        self.assertFalse(preview[0]["reconciled"])

    def test_cancelled_session_deletes_children_before_parent_and_frees_bank_line(self):
        session = self._session(name="Cancelled deletion — July 2026")
        payout = self._payout(session, reference="DELETE-001")
        bank_line = self._bank_line(
            80.0,
            label="CreatorHub deletion DELETE-001",
        )
        allocation = self._allocation(payout, bank_line)
        session.action_cancel()

        session.with_user(self.manager).unlink()

        self.assertFalse(session.exists())
        self.assertFalse(payout.exists())
        self.assertFalse(allocation.exists())
        self.assertTrue(bank_line.exists())
        replacement = self._session(name="Replacement — July 2026")
        wizard = self._bank_wizard(replacement, mode="create")
        self.assertIn(bank_line, wizard.candidate_ids.bank_statement_line_id)

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
            },
        )
        payout.with_user(self.operator).write(
            {
                "state": "draft",
                "customer_invoice_id": False,
                "vendor_bill_id": False,
                "compensation_move_id": False,
            },
        )

        with self.assertRaises(AccessError):
            session.with_user(self.operator).write({"state": "ready"})
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
            payout.with_user(self.operator).write(
                {"currency_valuation_method": "bank"},
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
        bank_line = self._bank_line(
            80.0,
            label="Workflow protection WEB-2026-12-001",
            bank_date="2026-12-20",
        )
        with self.assertRaises(AccessError):
            self.env[
                "usl.platform.billing.bank.allocation"
            ].with_user(self.operator).create(
                {
                    "payout_id": payout.id,
                    "bank_statement_line_id": bank_line.id,
                    "bank_amount": 80.0,
                    "payout_amount": 80.0,
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

    def test_effective_accounts_are_visible_and_checked_before_generation(self):
        self.assertEqual(
            self.platform.revenue_account_id,
            self.product_a.product_tmpl_id.with_company(
                self.company,
            ).get_product_accounts()["income"],
        )
        self.assertEqual(
            self.platform.commission_account_id,
            self.product_b.product_tmpl_id.with_company(
                self.company,
            ).get_product_accounts()["expense"],
        )
        self.assertEqual(
            self.platform.customer_receivable_account_id.account_type,
            "asset_receivable",
        )
        self.assertEqual(
            self.platform.supplier_payable_account_id.account_type,
            "liability_payable",
        )
        self.assertEqual(self.platform.bank_account_id.account_type, "asset_cash")
        wrong_revenue_product = self._create_product(
            name="Wrong revenue account",
            property_account_income_id=self.company_data[
                "default_account_expense"
            ],
            taxes_id=False,
        )
        self.platform.revenue_product_id = wrong_revenue_product
        session = self._session(name="Invalid account mapping — July 2026")
        self._payout(session)

        with self.assertRaisesRegex(UserError, "Revenue product account"):
            session.action_check()

    def test_monthly_generation_posting_compensation_and_bank_reconciliation(self):
        session = self._session()
        payout = self._payout(session)
        attachment = self.env["ir.attachment"].create(
            {
                "name": "payout-proof.pdf",
                "raw": b"synthetic payout evidence",
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
            session.customer_invoice_ids.invoice_line_ids.filtered(
                lambda line: line.display_type == "product",
            ).account_id,
            self.platform.revenue_account_id,
        )
        self.assertEqual(
            session.vendor_bill_ids.invoice_line_ids.filtered(
                lambda line: line.display_type == "product",
            ).account_id,
            self.platform.commission_account_id,
        )
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
        self.assertEqual(
            compensation.platform_billing_payment_state,
            "not_applicable",
        )
        self.assertEqual(
            session.customer_invoice_ids.platform_billing_payment_state,
            session.customer_invoice_ids.payment_state,
        )
        self.assertEqual(session.vendor_bill_ids.payment_state, "paid")
        self.assertEqual(session.customer_invoice_ids.amount_residual, 80.0)

        bank_line = self._bank_line(80.0)
        self._allocation(payout, bank_line)
        original_bank_amount = bank_line.amount
        session.action_reconcile_bank()

        self.assertTrue(bank_line.is_reconciled)
        self.assertEqual(bank_line.amount, original_bank_amount)
        self.assertEqual(session.customer_invoice_ids.payment_state, "paid")
        self.assertEqual(
            session.customer_invoice_ids.platform_billing_payment_state,
            "paid",
        )
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
        platform, reference, score, reason, confidence, rule = wizard._detect_platform(
            bank_line,
            self.platform,
        )
        self.assertEqual(platform, self.platform)
        self.assertEqual(reference, payout.platform_reference)
        self.assertEqual(score, 100)
        self.assertEqual(confidence, "high")
        self.assertEqual(rule, "regex")
        self.assertIn("pattern", reason.lower())

        competing = self.platform.copy(
            {
                "name": "Competing platform",
                "bank_label_pattern": self.platform.bank_label_pattern,
            },
        )
        (
            platform,
            _reference,
            _score,
            reason,
            confidence,
            rule,
        ) = wizard._detect_platform(
            bank_line,
            self.platform | competing,
        )
        self.assertFalse(platform)
        self.assertEqual(confidence, "ambiguous")
        self.assertEqual(rule, "ambiguous")
        self.assertIn("CreatorHub", reason)

    def test_all_eligible_candidates_are_visible_and_create_mode_is_action_safe(self):
        session = self._session(name="All eligible — July 2026")
        unmatched = self._bank_line(
            321.45,
            label="Unrecognised incoming transfer",
            bank_date="2025-01-10",
        )
        wizard = self._bank_wizard(session, mode="create")

        candidate = wizard.candidate_ids.filtered(
            lambda line: line.bank_statement_line_id == unmatched,
        )
        self.assertEqual(len(candidate), 1)
        self.assertFalse(candidate.recommended)
        self.assertEqual(candidate.match_rule, "none")
        candidate.selected = True
        wizard.action_create_payouts()

        payout = session.payout_ids
        self.assertEqual(len(payout), 1)
        self.assertEqual(payout.platform_reference, f"BANK-{unmatched.id}")
        self.assertFalse(payout.platform_id)
        self.assertFalse(payout.platform_currency_id)
        self.assertEqual(payout.net_platform_amount, 0.0)
        self.assertEqual(payout.bank_statement_line_id, unmatched)
        self.assertEqual(payout.bank_received_amount, 321.45)
        self.assertEqual(payout.bank_match_status, "selected")
        self.assertEqual(payout.bank_allocation_ids.payout_amount, 0.0)
        self.assertEqual(payout.validation_status, "error")

        link_wizard = self._bank_wizard(session, mode="link")
        self.assertNotIn(
            payout,
            link_wizard.payout_candidate_ids.payout_id,
        )

        payout.write(
            {
                "platform_id": self.platform.id,
                "platform_reference": "ORIGINAL-PAYOUT-REFERENCE",
                "net_platform_amount": 321.45,
            },
        )

        self.assertEqual(payout.platform_currency_id, self.platform.currency_id)
        self.assertEqual(
            payout.commission_rate_snapshot,
            self.platform.commission_rate,
        )
        self.assertEqual(payout.bank_allocation_ids.payout_amount, 321.45)
        self.assertEqual(payout.validation_status, "ok")
        session.action_check()
        self.assertEqual(session.state, "ready")

    def test_recommended_scope_is_a_ranking_filter_not_an_eligibility_rule(self):
        session = self._session(name="Candidate scopes — July 2026")
        self._payout(session)
        unmatched = self._bank_line(
            19.99,
            label="Unrecognised incoming transfer",
            bank_date="2024-01-10",
        )
        wizard = self._bank_wizard(session)
        self.assertIn(unmatched, wizard.candidate_ids.bank_statement_line_id)

        wizard.candidate_scope = "recommended"
        wizard._populate_candidates()

        self.assertNotIn(unmatched, wizard.candidate_ids.bank_statement_line_id)

    def test_bank_candidates_use_the_session_bank_currency(self):
        foreign_currency = self.env["res.currency"].create(
            {
                "name": "BCX",
                "symbol": "BCX",
                "rounding": 0.01,
            },
        )
        foreign_journal = self.company_data["default_journal_bank"].copy(
            {
                "name": "Foreign currency bank",
                "code": "BCX",
                "currency_id": foreign_currency.id,
            },
        )
        foreign_line = self._bank_line(
            80.0,
            label="Foreign bank receipt",
            journal=foreign_journal,
        )
        session = self._session(name="EUR bank candidates — July 2026")
        wizard = self._bank_wizard(session, mode="create")

        self.assertNotIn(foreign_line, wizard.candidate_ids.bank_statement_line_id)

    def test_platform_roles_are_opt_in_and_server_side_enforced(self):
        session = self._session()
        payout = self._payout(session)
        analytic_group = self.env.ref("analytic.group_analytic_accounting")

        self.assertIn(
            analytic_group,
            self.env.ref(
                "usl_platform_billing.group_platform_billing_manager",
            ).implied_ids,
        )
        self.assertIn(analytic_group, self.manager.all_group_ids)

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
        with self.assertRaises(AccessError):
            self.platform.with_user(self.operator).copy(
                {"name": "Forbidden platform copy"},
            )
        self.platform.with_user(self.manager).write({"commission_rate": 21})
        manager_platform = self.platform.with_user(self.manager).copy(
            {"name": "Manager platform copy"},
        )
        self.assertTrue(manager_platform)
        with self.assertRaises(AccessError):
            session.with_user(self.operator).write({"state": "paid"})
        with self.assertRaises(AccessError):
            payout.with_user(self.operator).write({"state": "paid"})
        bank_line = self._bank_line(80.0)
        allocation = self._allocation(payout, bank_line)
        with self.assertRaises(AccessError):
            allocation.with_user(self.operator).write({"bank_amount": 70.0})
        with self.assertRaises(AccessError):
            allocation.with_user(self.operator).unlink()

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

    def test_one_payout_can_be_settled_by_two_partial_receipts(self):
        session = self._session(name="Partial receipts — July 2026")
        payout = self._payout(session)
        self._generate_and_post(session)
        first_line = self._bank_line(
            40.0,
            label="First partial CH-2026-07-001",
            bank_date="2026-08-20",
        )
        self._allocation(
            payout,
            first_line,
            bank_amount=40.0,
            payout_amount=40.0,
        )

        session.action_reconcile_bank()

        self.assertTrue(first_line.is_reconciled)
        self.assertEqual(payout.bank_match_status, "partial")
        self.assertEqual(payout.remaining_platform_amount, 40.0)
        self.assertEqual(session.customer_invoice_ids.amount_residual, 40.0)
        self.assertEqual(session.state, "posted")

        second_line = self._bank_line(
            40.0,
            label="Second partial CH-2026-07-001",
            bank_date="2026-09-20",
        )
        wizard = self._bank_wizard(session)
        candidate = wizard.candidate_ids.filtered(
            lambda line: line.bank_statement_line_id == second_line,
        )
        self.assertEqual(len(candidate), 1)
        candidate.selected = True
        wizard.action_link_payouts()
        session.action_reconcile_bank()

        self.assertTrue(second_line.is_reconciled)
        self.assertEqual(len(payout.bank_statement_line_ids), 2)
        self.assertFalse(payout.bank_statement_line_id)
        self.assertEqual(payout.bank_match_status, "reconciled")
        self.assertEqual(session.customer_invoice_ids.amount_residual, 0.0)
        self.assertEqual(session.state, "paid")

    def test_bank_allocations_reject_duplicates_and_overallocation(self):
        session = self._session(name="Allocation constraints — July 2026")
        payout = self._payout(session)
        bank_line = self._bank_line(80.0)
        self._allocation(
            payout,
            bank_line,
            bank_amount=40.0,
            payout_amount=40.0,
        )
        with (
            self.assertRaises(IntegrityError),
            self.cr.savepoint(),
            mute_logger("odoo.sql_db"),
        ):
            self._allocation(
                payout,
                bank_line,
                bank_amount=40.0,
                payout_amount=40.0,
            )
        second_line = self._bank_line(
            50.0,
            label="Over-allocation candidate",
        )
        with self.assertRaises(ValidationError), self.cr.savepoint():
            self._allocation(
                payout,
                second_line,
                bank_amount=50.0,
                payout_amount=50.0,
            )

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

        wizard = self._bank_wizard(july)
        august_line = wizard.payout_candidate_ids.filtered(
            lambda line: line.payout_id == august_payout,
        )
        self.assertEqual(len(august_line), 1)
        august_line.selected = True
        wizard._populate_candidates()
        candidate = wizard.candidate_ids.filtered(
            lambda candidate: candidate.bank_statement_line_id == bank_line,
        )
        self.assertEqual(len(candidate), 1)
        self.assertEqual(candidate.allocated_bank_amount, 120.0)
        candidate.selected = True
        wizard.action_link_payouts()
        wizard.action_link_payouts()

        self.assertEqual(july_payout.bank_statement_line_id, bank_line)
        self.assertEqual(august_payout.bank_statement_line_id, bank_line)
        self.assertEqual(
            self.env["usl.platform.billing.bank.allocation"].search_count(
                [("bank_statement_line_id", "=", bank_line.id)],
            ),
            2,
        )
        july.action_reconcile_bank()

        self.assertTrue(bank_line.is_reconciled)
        self.assertEqual(july.state, "paid")
        self.assertEqual(august.state, "paid")
        self.assertEqual(july.customer_invoice_ids.payment_state, "paid")
        self.assertEqual(august.customer_invoice_ids.payment_state, "paid")
        wizard.action_link_payouts()
        self.assertEqual(
            self.env["usl.platform.billing.bank.allocation"].search_count(
                [("bank_statement_line_id", "=", bank_line.id)],
            ),
            2,
        )

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
        self._allocation(
            payout,
            bank_line,
            bank_amount=79.0,
            payout_amount=79.0,
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
        self.assertEqual(payout.currency_valuation_method, "reference")
        self._generate_and_post(session)
        bank_line = self._bank_line(40.0, label="FX payout FX-001")
        self._allocation(
            payout,
            bank_line,
            bank_amount=40.0,
            payout_amount=80.0,
        )
        bank_line.statement_id.sudo().with_context(
            bank_review_internal=True,
        ).write({"certification_state": "certified"})
        certified_fingerprint = bank_line._certified_reconciliation_fingerprint()

        session.action_reconcile_bank()

        self.assertEqual(bank_line.amount, 40.0)
        self.assertEqual(bank_line.foreign_currency_id, foreign_currency)
        self.assertEqual(bank_line.amount_currency, 80.0)
        self.assertTrue(bank_line.is_reconciled)
        self.assertEqual(session.state, "paid")
        self.assertEqual(bank_line.statement_id.certification_state, "certified")
        self.assertEqual(
            bank_line._certified_reconciliation_fingerprint(),
            certified_fingerprint,
        )

        bank_line.unreconcile_bank_line()

        self.assertFalse(bank_line.is_reconciled)
        self.assertEqual(bank_line.statement_id.certification_state, "certified")
        self.assertEqual(
            bank_line._certified_reconciliation_fingerprint(),
            certified_fingerprint,
        )
        session.action_reconcile_bank()
        self.assertTrue(bank_line.is_reconciled)
        self.assertEqual(session.state, "paid")

    def test_bank_created_foreign_payout_forces_document_rate_without_fx(self):
        usd = self.env["res.currency"].create(
            {
                "name": "BFX",
                "symbol": "$B",
                "rounding": 0.01,
            },
        )
        self.env["res.currency.rate"].create(
            {
                "currency_id": usd.id,
                "company_id": self.company.id,
                "name": fields.Date.from_string("2026-07-01"),
                "rate": 2.0,
            },
        )
        platform = self.platform.copy(
            {
                "name": "Bank-rate CreatorHub",
                "currency_id": usd.id,
                "bank_label_pattern": "Bank-rate payout {ref}",
            },
        )
        session = self._session(name="Bank rate — July 2026")
        bank_line = self._bank_line(
            700.0,
            label="Bank-rate payout USD-1000",
        )
        wizard = self._bank_wizard(session, mode="create")
        candidate = wizard.candidate_ids.filtered(
            lambda line: line.bank_statement_line_id == bank_line,
        )
        self.assertEqual(len(candidate), 1)
        candidate.selected = True
        wizard.action_create_payouts()

        payout = session.payout_ids
        self.assertEqual(len(payout), 1)
        payout.write({"net_platform_amount": 1000.0})

        self.assertEqual(payout.platform_id, platform)
        self.assertEqual(payout.currency_valuation_method, "bank")
        self.assertEqual(payout.bank_rate_company_amount, 700.0)
        self.assertAlmostEqual(payout.effective_bank_rate, 0.7)
        self.assertEqual(payout.bank_allocation_ids.payout_amount, 1000.0)

        previous_exchange_moves = set(
            self.env["account.partial.reconcile"].search([]).exchange_move_id.ids,
        )
        session.action_check()
        session.action_generate_documents()
        invoice = payout.customer_invoice_id
        bill = payout.vendor_bill_id

        self.assertEqual(invoice.amount_total, 1250.0)
        self.assertEqual(bill.amount_total, 250.0)
        self.assertAlmostEqual(invoice.invoice_currency_rate, 1.0 / 0.7)
        self.assertAlmostEqual(bill.invoice_currency_rate, 1.0 / 0.7)
        self.assertEqual(abs(invoice.amount_total_signed), 875.0)
        self.assertEqual(abs(bill.amount_total_signed), 175.0)

        session.with_context(
            skip_platform_coverage_warning=True,
        ).action_post_documents()
        compensation = payout.compensation_move_id
        self.assertEqual(compensation.currency_id, usd)
        self.assertEqual(compensation.amount_total, 250.0)
        self.assertEqual(compensation.amount_total_signed, 175.0)
        self.assertEqual(
            sum(compensation.line_ids.filtered("debit").mapped("debit")),
            175.0,
        )
        self.assertEqual(invoice.amount_residual, 1000.0)
        self.assertEqual(abs(invoice.amount_residual_signed), 700.0)

        session.action_reconcile_bank()

        self.assertEqual(bank_line.amount, 700.0)
        self.assertEqual(bank_line.foreign_currency_id, usd)
        self.assertEqual(bank_line.amount_currency, 1000.0)
        self.assertTrue(bank_line.is_reconciled)
        self.assertEqual(invoice.payment_state, "paid")
        self.assertEqual(session.state, "paid")
        current_exchange_moves = set(
            self.env["account.partial.reconcile"].search([]).exchange_move_id.ids,
        )
        self.assertEqual(current_exchange_moves, previous_exchange_moves)

    def test_certified_foreign_receipts_keep_compensation_per_payout(self):
        usd = self.env["res.currency"].create(
            {
                "name": "PCP",
                "symbol": "$P",
                "rounding": 0.01,
            },
        )
        platform = self.platform.copy(
            {
                "name": "Pooled foreign platform",
                "currency_id": usd.id,
            },
        )
        session = self._session(name="Pooled certified receipts — July 2026")
        first = self._payout(
            session,
            platform=platform,
            reference="POOL-001",
            amount=1000.0,
        )
        second = self._payout(
            session,
            platform=platform,
            reference="POOL-002",
            amount=1000.0,
        )
        first_bank = self._bank_line(700.0, label="Pooled receipt POOL-001")
        second_bank = self._bank_line(800.0, label="Pooled receipt POOL-002")
        self._allocation(
            first,
            first_bank,
            bank_amount=700.0,
            payout_amount=1000.0,
        )
        self._allocation(
            second,
            second_bank,
            bank_amount=800.0,
            payout_amount=1000.0,
        )
        (first | second)._workflow_write({"currency_valuation_method": "bank"})

        previous_exchange_moves = set(
            self.env["account.partial.reconcile"].search([]).exchange_move_id.ids,
        )
        self._generate_and_post(session)

        self.assertEqual(len(session.customer_invoice_ids), 2)
        self.assertEqual(len(session.compensation_move_ids), 2)
        self.assertNotEqual(first.compensation_move_id, second.compensation_move_id)
        self.assertEqual(
            first.customer_invoice_id.amount_residual,
            first.net_platform_amount,
        )
        self.assertEqual(
            second.customer_invoice_id.amount_residual,
            second.net_platform_amount,
        )
        self.assertEqual(
            set(
                self.env["account.partial.reconcile"]
                .search([])
                .exchange_move_id.ids
            ),
            previous_exchange_moves,
        )
        bank_lines = first_bank | second_bank
        bank_lines.statement_id.sudo().with_context(
            bank_review_internal=True,
        ).write({"certification_state": "certified"})
        certified_fingerprints = {
            bank_line.id: bank_line._certified_reconciliation_fingerprint()
            for bank_line in bank_lines
        }

        session.action_reconcile_bank()

        self.assertTrue(all(bank_lines.mapped("is_reconciled")))
        self.assertTrue(
            all(
                invoice.currency_id.is_zero(invoice.amount_residual)
                for invoice in session.customer_invoice_ids
            ),
        )
        self.assertEqual(session.state, "paid")
        self.assertEqual(set(session.payout_ids.mapped("bank_match_status")), {"reconciled"})
        self.assertEqual(
            set(bank_lines.statement_id.mapped("certification_state")),
            {"certified"},
        )
        self.assertEqual(
            set(
                self.env["account.partial.reconcile"]
                .search([])
                .exchange_move_id.ids
            ),
            previous_exchange_moves,
        )
        for bank_line in bank_lines:
            self.assertEqual(
                bank_line._certified_reconciliation_fingerprint(),
                certified_fingerprints[bank_line.id],
            )

    def test_legacy_pooled_compensation_is_reversed_and_rebuilt(self):
        usd = self.env["res.currency"].create(
            {
                "name": "LCP",
                "symbol": "$L",
                "rounding": 0.01,
            },
        )
        platform = self.platform.copy(
            {
                "name": "Legacy pooled platform",
                "currency_id": usd.id,
            },
        )
        session = self._session(name="Legacy pooled compensation — July 2026")
        first = self._payout(
            session,
            platform=platform,
            reference="LEGACY-001",
            amount=1000.0,
        )
        second = self._payout(
            session,
            platform=platform,
            reference="LEGACY-002",
            amount=1000.0,
        )
        first_bank = self._bank_line(700.0, label="Legacy receipt LEGACY-001")
        second_bank = self._bank_line(800.0, label="Legacy receipt LEGACY-002")
        self._allocation(first, first_bank, bank_amount=700.0, payout_amount=1000.0)
        self._allocation(second, second_bank, bank_amount=800.0, payout_amount=1000.0)
        payouts = first | second
        payouts._workflow_write({"currency_valuation_method": "bank"})

        session.action_check()
        session.action_generate_documents()
        (session.customer_invoice_ids | session.vendor_bill_ids).action_post()
        company_currency = session.company_id.currency_id
        commission_amount = sum(payouts.mapped("commission_platform_amount"))
        company_amount = sum(
            company_currency.round(
                payout.commission_platform_amount * payout.effective_bank_rate,
            )
            for payout in payouts
        )
        legacy = self.env["account.move"].create(
            {
                "move_type": "entry",
                "company_id": session.company_id.id,
                "currency_id": usd.id,
                "journal_id": platform.compensation_journal_id.id,
                "date": session.invoice_date,
                "ref": "Legacy pooled compensation",
                "platform_billing_session_id": session.id,
                "platform_billing_platform_id": platform.id,
                "platform_billing_payout_ids": [Command.set(payouts.ids)],
                "line_ids": [
                    Command.create(
                        {
                            "name": "Legacy payable compensation",
                            "partner_id": platform.supplier_partner.id,
                            "account_id": platform.supplier_partner.property_account_payable_id.id,
                            "debit": company_amount,
                            "currency_id": usd.id,
                            "amount_currency": commission_amount,
                        },
                    ),
                    Command.create(
                        {
                            "name": "Legacy receivable compensation",
                            "partner_id": platform.customer_partner.id,
                            "account_id": (
                                platform.customer_partner
                                .property_account_receivable_id.id
                            ),
                            "credit": company_amount,
                            "currency_id": usd.id,
                            "amount_currency": -commission_amount,
                        },
                    ),
                ],
            },
        )
        payouts._workflow_write(
            {"compensation_move_id": legacy.id, "state": "posted"},
        )
        legacy.action_post()
        session._reconcile_compensation(platform, payouts, legacy)
        session._workflow_write({"state": "posted"})

        repaired = session._repair_legacy_grouped_compensations()

        self.assertEqual(len(repaired), 2)
        self.assertTrue(legacy.reversal_move_ids)
        self.assertEqual(len(payouts.compensation_move_id), 2)
        self.assertNotIn(legacy, payouts.compensation_move_id)
        self.assertEqual(first.customer_invoice_id.amount_residual, 1000.0)
        self.assertEqual(second.customer_invoice_id.amount_residual, 1000.0)
        self.assertFalse(first_bank.is_reconciled)
        self.assertFalse(second_bank.is_reconciled)
        self.assertFalse(session._repair_legacy_grouped_compensations())

        session.action_reconcile_bank()

        self.assertTrue(first_bank.is_reconciled)
        self.assertTrue(second_bank.is_reconciled)
        self.assertEqual(first.customer_invoice_id.payment_state, "paid")
        self.assertEqual(second.customer_invoice_id.payment_state, "paid")
