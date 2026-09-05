import unittest
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

PATH = Path(__file__).parents[1] / "addons/usl_collaboration_restore/routing.py"
RESTORE_PATH = Path(__file__).parents[1] / "addons/usl_collaboration_restore/models/restore.py"
ACCOUNTING_IMPORT_PATH = (
    Path(__file__).parents[2]
    / "accounting_restore/addons/usl_accounting_restore/models/import_run.py"
)
PROJECT_VALIDATION_PATH = (
    Path(__file__).parents[2]
    / "project_restore/addons/usl_project_restore/scripts/validate_restore.py"
)
ACCOUNTING_FINALIZE_PATH = (
    Path(__file__).parents[2]
    / "accounting_restore/addons/usl_accounting_restore/scripts/finalize_restore.py"
)
SPEC = spec_from_file_location("collaboration_routing", PATH)
routing = module_from_spec(SPEC)
SPEC.loader.exec_module(routing)


class RoutingTest(unittest.TestCase):
    def test_business_models_have_explicit_destinations(self):
        self.assertEqual(routing.route_model("account.move"), "native")
        self.assertEqual(routing.route_model("documents.document"), "translated")
        self.assertEqual(routing.route_model("sign.request"), "translated")
        self.assertEqual(routing.route_model("discuss.channel"), "discuss")
        self.assertEqual(
            routing.route_model("knowledge.article"),
            "deliberately_not_copied",
        )

    def test_technical_and_unknown_models_do_not_leak_to_native_records(self):
        self.assertEqual(routing.route_model("ir.cron"), "external_archive")
        self.assertEqual(routing.route_model(None), "external_archive")
        self.assertEqual(routing.route_model("unexpected.model"), "unclassified")

    def test_locked_source_counts_are_declared(self):
        self.assertEqual(routing.EXPECTED_COUNTS["messages"], 51491)
        self.assertEqual(routing.EXPECTED_COUNTS["tracking"], 37579)
        self.assertEqual(routing.EXPECTED_COUNTS["aliases"], 29)
        self.assertEqual(routing.EXPECTED_COUNTS["parent_links"], 24069)
        self.assertEqual(routing.EXPECTED_COUNTS["cross_accounting_parent_links"], 1643)
        self.assertEqual(
            routing.EXPECTED_MESSAGE_DISPOSITIONS,
            {"visible": 50588, "external": 0, "deliberately_not_copied": 903},
        )

    def test_every_populated_source_message_model_is_classified(self):
        populated = {
            None, "account.account", "account.analytic.account", "account.asset",
            "account.bank.statement.line", "account.depreciation.model", "account.journal",
            "account.move", "account.online.link", "account.payment", "account.reconcile.model",
            "account.return", "account.return.type", "account.tax", "base.automation",
            "crm.team", "crm.team.member", "discuss.channel", "documents.document",
            "hr.department", "hr.employee", "hr.expense", "hr.job", "hr.version",
            "iap.account", "ir.actions.server", "ir.cron", "knowledge.article",
            "product.category", "product.pricelist", "product.product", "product.template",
            "project.milestone", "project.project", "project.task", "project.update",
            "quality.alert.team", "res.company", "res.partner", "res.partner.bank",
            "sign.request",
        }
        self.assertNotIn("unclassified", {routing.route_model(model) for model in populated})

    def test_technical_mail_rows_have_explicit_recompute_discard_or_archive_routes(self):
        self.assertEqual(
            routing.route_technical_table("mail_template"),
            "xmlid_or_installed_module_recompute",
        )
        self.assertEqual(
            routing.route_technical_table("mail_push_device"),
            "discard_transient_state",
        )
        self.assertEqual(
            routing.route_technical_table("mail_blacklist"),
            "private_archive",
        )

    def test_legacy_declaration_workflow_is_translated_to_current_axes(self):
        self.assertEqual(
            routing.legacy_declaration_workflow_values(submitted_on=None),
            {"preparation_status": "not_required", "filing_status": "not_open"},
        )
        self.assertEqual(
            routing.legacy_declaration_workflow_values(submitted_on="2026-01-15"),
            {"preparation_status": "not_required", "filing_status": "filed"},
        )

    def test_late_expense_materialization_defers_synthetic_attachment_chatter(self):
        restore_source = RESTORE_PATH.read_text()
        accounting_source = ACCOUNTING_IMPORT_PATH.read_text()
        accounting_finalize_source = ACCOUNTING_FINALIZE_PATH.read_text()
        project_validation_source = PROJECT_VALIDATION_PATH.read_text()

        self.assertIn(
            '"defer_attachment_chatter_to_collaboration": True',
            restore_source,
        )
        self.assertIn(
            'options.get("defer_attachment_chatter_to_collaboration")',
            accounting_source,
        )
        self.assertIn(
            'values["login"] = row["login"]',
            accounting_source,
        )
        self.assertNotIn(
            'values = {\n                "login": row["login"],',
            accounting_source,
        )
        self.assertIn("side_effect_before = {", restore_source)
        self.assertIn(
            '"outbound_side_effect_counts_before": side_effect_before',
            restore_source,
        )
        self.assertIn(
            'statistics.get("source_expense_count") == 443',
            accounting_finalize_source,
        )
        self.assertIn("collaboration_fallback_note", project_validation_source)
        self.assertIn(
            'str(activity.note or "") == collaboration_fallback_note',
            project_validation_source,
        )
        self.assertIn("semantic_activity_type", project_validation_source)
        self.assertIn(
            'expected_activity_type = f"semantic:{row[\'activity_type_id\']}"',
            project_validation_source,
        )


if __name__ == "__main__":
    unittest.main()
