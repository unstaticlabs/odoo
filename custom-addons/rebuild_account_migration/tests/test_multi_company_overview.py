from odoo import Command
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
