from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install", "rebuild_account_migration_unit")
class TestAccountingArchitectureCompatibility(TransactionCase):
    """Protect database ownership while the historical add-on is decomposed."""

    _STABLE_XML_IDS = (
        "group_rebuild_accountant_reviewer",
        "action_rebuild_accounting_home",
        "action_rebuild_account_overview",
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
        "rebuild.account.import.run": "rebuild_account_import_run",
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
