from datetime import timedelta

from odoo import Command, fields
from odoo.exceptions import AccessError
from odoo.tests import TransactionCase, new_test_user, tagged


@tagged(
    "post_install",
    "-at_install",
    "rebuild_account_migration_unit",
    "usl_accounting_multi_company_overview",
)
class TestMultiCompanyAccountingOverview(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company_a = cls.env.company
        cls.company_b = cls.env["res.company"].create(
            {
                "name": "Accounting Overview Company B",
                "currency_id": cls.company_a.currency_id.id,
            },
        )
        cls.manager = new_test_user(
            cls.env,
            login="multi-company-overview-manager@example.invalid",
            groups="account.group_account_manager",
            company_id=cls.company_a.id,
            company_ids=[Command.set((cls.company_a | cls.company_b).ids)],
        )

    def _overview_model(self, company_ids):
        return self.env["rebuild.account.overview"].with_user(
            self.manager,
        ).with_context(allowed_company_ids=company_ids)

    def _declaration(self, company, deadline, suffix, status="to_prepare"):
        rule = self.env.ref(
            "rebuild_account_migration.declaration_rule_3517_2026",
        )
        return self.env["rebuild.account.declaration"].with_company(
            company,
        ).create(
            {
                "name": f"[QA dashboard] {suffix}",
                "company_id": company.id,
                "rule_id": rule.id,
                "period_start": deadline - timedelta(days=90),
                "period_end": deadline - timedelta(days=1),
                "fiscalyear_start": deadline - timedelta(days=365),
                "fiscalyear_end": deadline - timedelta(days=1),
                "deadline_date": deadline,
                "deadline_basis": "Synthetic dashboard deadline.",
                "applicability_reason": "Synthetic dashboard fixture.",
                "status": status,
            },
        )

    def test_dashboard_splits_stateful_overviews_only_in_multi_company_mode(self):
        single_action = self._overview_model(
            [self.company_a.id],
        ).action_open_accounting_home()
        self.assertEqual(single_action["view_mode"], "form")
        self.assertEqual(single_action["res_id"], self.company_a.id)

        combined_model = self._overview_model(
            [self.company_a.id, self.company_b.id],
        )
        combined_action = combined_model.action_open_accounting_home()
        self.assertEqual(combined_action["view_mode"], "kanban,form")
        self.assertNotIn("res_id", combined_action)
        self.assertEqual(
            set(combined_action["domain"][0][2]),
            {self.company_a.id, self.company_b.id},
        )

    def test_pending_declarations_are_ordered_bounded_and_company_scoped(self):
        today = fields.Date.today()
        overdue = self._declaration(
            self.company_a,
            today - timedelta(days=10),
            "Overdue VAT",
        )
        next_due = self._declaration(
            self.company_a,
            today + timedelta(days=5),
            "Next VAT",
        )
        later = self._declaration(
            self.company_a,
            today + timedelta(days=60),
            "Later VAT",
        )
        self._declaration(
            self.company_a,
            today + timedelta(days=90),
            "Fourth VAT",
        )
        self._declaration(
            self.company_a,
            today - timedelta(days=20),
            "Filed VAT",
            status="filed",
        )
        self._declaration(
            self.company_b,
            today - timedelta(days=30),
            "Other company VAT",
        )

        overview = self._overview_model([self.company_a.id]).browse(
            self.company_a.id,
        )

        self.assertEqual(
            overview.pending_declaration_ids.ids,
            [overdue.id, next_due.id, later.id],
        )
        self.assertEqual(overview.next_declaration_id, overdue)

    def test_drilldowns_match_the_displayed_company_scope(self):
        overviews = self._overview_model(
            [self.company_a.id, self.company_b.id],
        )._selected_overviews()
        self.assertEqual(
            set(overviews.company_id.ids),
            {self.company_a.id, self.company_b.id},
        )

        combined_action = overviews.action_open_bank_review()
        self.assertIn(
            ("company_id", "in", overviews.company_id.ids),
            combined_action["domain"],
        )
        company_action = overviews.filtered(
            lambda overview: overview.company_id == self.company_b,
        ).action_open_bank_review()
        self.assertIn(
            ("company_id", "=", self.company_b.id),
            company_action["domain"],
        )
        self.assertEqual(
            company_action["context"]["default_company_id"],
            self.company_b.id,
        )
        journal_action = overviews.action_open_journal_dashboard()
        self.assertIn(
            ("company_id", "in", overviews.company_id.ids),
            journal_action["domain"],
        )
        settings_action = overviews.filtered(
            lambda overview: overview.company_id == self.company_b,
        ).action_open_accounting_settings()
        self.assertEqual(
            settings_action["context"]["allowed_company_ids"],
            [self.company_b.id],
        )

        bank_attention_action = overviews.action_open_bank_attention()
        self.assertEqual(
            bank_attention_action["domain"],
            overviews._bank_attention_domain(),
        )
        self.assertIn(
            ("company_id", "in", overviews.company_id.ids),
            bank_attention_action["domain"],
        )
        self.assertIn(
            ("is_reconciled", "=", False),
            bank_attention_action["domain"],
        )
        self.assertIn(
            ("move_id.review_state", "in", ("todo", "anomaly")),
            bank_attention_action["domain"],
        )

        expenses_action = overviews.action_open_expenses()
        self.assertIn(
            ("state", "in", ["draft", "submitted", "approved"]),
            expenses_action["domain"],
        )
        self.assertIn(
            ("company_id", "in", overviews.company_id.ids),
            expenses_action["domain"],
        )

    def test_record_rules_and_crafted_company_context_fail_closed(self):
        restricted = new_test_user(
            self.env,
            login="single-company-overview-reviewer@example.invalid",
            groups="rebuild_account_migration.group_rebuild_accountant_reviewer",
            company_id=self.company_a.id,
            company_ids=[Command.set(self.company_a.ids)],
        )
        Overview = self.env["rebuild.account.overview"].with_user(
            restricted,
        ).with_context(allowed_company_ids=[self.company_a.id])
        self.assertFalse(Overview.search([("company_id", "=", self.company_b.id)]))

        crafted = Overview.with_context(
            allowed_company_ids=[self.company_a.id, self.company_b.id],
        )
        with self.assertRaises(AccessError):
            crafted._selected_overviews()
