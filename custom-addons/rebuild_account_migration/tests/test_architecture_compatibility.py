from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install", "rebuild_account_migration_unit")
class TestAccountingArchitectureCompatibility(TransactionCase):
    """Protect stable product ownership and the migration boundary."""

    _STABLE_XML_IDS = (
        "group_rebuild_accountant_reviewer",
        "action_rebuild_accounting_home",
        "action_rebuild_account_hygiene",
        "action_rebuild_account_closing_period",
        "action_rebuild_account_declaration",
        "action_rebuild_interactive_trial_balance",
        "action_rebuild_interactive_profit_loss",
        "menu_rebuild_accounting_overview",
        "menu_rebuild_analytic_reporting",
        "menu_rebuild_einvoice_readiness",
    )

    _STABLE_MODEL_TABLES = {
        "rebuild.account.overview": "rebuild_account_overview",
        "rebuild.account.hygiene.issue": "rebuild_account_hygiene_issue",
        "rebuild.account.hygiene.dismissal": "rebuild_account_hygiene_dismissal",
        "rebuild.account.closing.period": "rebuild_account_closing_period",
        "rebuild.account.declaration": "rebuild_account_declaration",
        "rebuild.account.report.definition": "rebuild_account_report_definition",
        "rebuild.einvoice.reception": "rebuild_einvoice_reception",
    }

    def test_stable_xml_ids_remain_owned_by_compatibility_module(self):
        model_data = self.env["ir.model.data"]
        for name in self._STABLE_XML_IDS:
            record = model_data.search([
                ("module", "=", "rebuild_account_migration"),
                ("name", "=", name),
            ])
            self.assertEqual(
                len(record),
                1,
                f"rebuild_account_migration.{name} must remain a unique XML ID",
            )
            self.assertTrue(record.exists())

    def test_stable_models_keep_their_database_tables(self):
        for model_name, table_name in self._STABLE_MODEL_TABLES.items():
            self.assertIn(model_name, self.env.registry)
            self.assertEqual(self.env[model_name]._table, table_name)

    def test_one_shot_restore_models_are_not_in_product_registry(self):
        for model_name in (
            "rebuild.account.import.run",
            "rebuild.account.discrepancy",
            "rebuild.account.source.report",
            "rebuild.account.deferred.schedule.line",
            "rebuild.account.analytic.override",
        ):
            self.assertNotIn(model_name, self.env.registry)

    def test_governed_definitions_do_not_duplicate_business_keys(self):
        for model_name in (
            "rebuild.account.closing.control.definition",
            "rebuild.account.report.definition",
            "rebuild.account.declaration.rule",
        ):
            model = self.env[model_name]
            keys = {}
            for record in model.with_context(active_test=False).search([]):
                key = (
                    record.company_id.id if "company_id" in model._fields else False,
                    record.code,
                    record.definition_version,
                )
                keys.setdefault(key, record)
                self.assertEqual(
                    keys[key],
                    record,
                    f"{model_name} contains duplicate governed definition {key}",
                )

    def test_distribution_owns_french_navigation_terms(self):
        expected_names = {
            "account.menu_finance": "Comptabilité",
            "account.menu_board_journal_1": "Journaux",
            "account.menu_finance_payables": "Fournisseurs",
            "account.menu_finance_reports": "Rapports",
            "account.account_audit_menu": "Révision",
            "account.account_reports_legal_statements_menu": "États financiers",
            "account.account_reports_partners_reports_menu": "Tiers et échéances",
            "account.account_reports_taxes_and_fiscal_menu": "Fiscalité",
            "account.account_reports_management_menu": "Pilotage",
            "account_reconcile_oca.menu_account_reconcile_model": (
                "Règles de rapprochement bancaire"
            ),
            "account.action_account_reconcile_model": (
                "Règles de rapprochement bancaire"
            ),
            "usl_platform_billing.selection__account_move__platform_billing_payment_state__in_payment": (
                "En cours de paiement"
            ),
            "usl_platform_billing.selection__account_move__platform_billing_payment_state__invoicing_legacy": (
                "Hérité de l’application Facturation"
            ),
            "usl_platform_billing.selection__account_move__platform_billing_payment_state__not_paid": (
                "Non payé"
            ),
        }
        for xmlid, expected_name in expected_names.items():
            record = self.env.ref(xmlid)
            self.assertEqual(
                record.with_context(lang="fr_FR").name,
                expected_name,
                f"{xmlid} must not retain an obsolete upstream French name",
            )

        platform_view = self.env.ref(
            "usl_platform_billing.view_platform_billing_platform_form",
        ).with_context(lang="fr_FR")
        self.assertIn(
            "Répartition analytique par défaut facultative",
            platform_view.arch_db,
        )
        self.assertNotIn(
            "Optional default analytic distribution",
            platform_view.arch_db,
        )
