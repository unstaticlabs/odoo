from decimal import Decimal

from odoo import Command
from odoo.exceptions import UserError
from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install", "rebuild_account_migration_unit")
class TestFrenchTaxReport(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.report = cls.env.ref("l10n_fr_account.tax_report")
        cls.wizard = cls.env[
            "rebuild.account.report.export.wizard"
        ].create({
            "report_type": "tax_report",
            "company_id": cls.env.company.id,
            "company_ids": [Command.set(cls.env.company.ids)],
            "date_from": "2025-01-01",
            "date_to": "2026-08-29",
            "target_move": "posted",
            "group_by": "section",
        })

    def test_online_reference_values_follow_localization_formulas(self):
        values = self.wizard._french_tax_expression_values(
            self.report,
            tag_balances={
                "A1": Decimal("-3752.46"),
                "A2": Decimal("35798.32"),
                "A3": Decimal("10075.25"),
                "B2": Decimal("1088.82"),
                "B4": Decimal("137.39"),
                "E2": Decimal("-195173.89"),
                "08_base": Decimal("-3752.46"),
                "08_base_rc": Decimal("47099.78"),
                "08_taxe": Decimal("-10170.50"),
                "17": Decimal("-217.75"),
                "20": Decimal("11089.53"),
                "24": Decimal("15.00"),
            },
            external_values={},
        )

        expected = {
            "box_A1": "3752",
            "box_A2": "35798",
            "box_A3": "10075",
            "box_B2": "1089",
            "box_B4": "137",
            "box_E2": "195174",
            "box_08_base": "50852",
            "box_08_taxe": "10170",
            "box_16": "10170",
            "box_17": "218",
            "box_20": "11090",
            "box_23": "11090",
            "box_24": "15",
            "box_25": "920",
            "box_27": "920",
        }
        self.assertEqual(
            {
                code: str(values[(code, "balance")])
                for code in expected
            },
            expected,
        )

    def test_report_keeps_complete_statutory_hierarchy(self):
        unit_label = self.wizard._display_unit_metadata()["short_label"]
        codes = set(self.report.line_ids.mapped("code"))
        self.assertGreaterEqual(len(self.report.line_ids), 130)
        self.assertTrue({
            "box_A1",
            "box_E2",
            "box_08_base",
            "box_16",
            "box_25",
            "box_TICFE",
            "box_32",
        } <= codes)
        self.assertEqual(
            self.wizard._report_client_columns(),
            [
                {
                    "key": "balance",
                    "label": f"Solde ({unit_label})",
                    "type": "currency",
                },
                {
                    "key": "adjustment",
                    "label": f"Ajustement ({unit_label})",
                    "type": "currency",
                },
            ],
        )

    def test_rounding_control_matches_online(self):
        values = {
            "box_08_base": Decimal("50852"),
            "box_A1": Decimal("3752"),
            "box_A2": Decimal("35798"),
            "box_A3": Decimal("10075"),
            "box_B2": Decimal("1089"),
            "box_B4": Decimal("137"),
        }
        warning = self.wizard._french_tax_control_warning_from_values(values)
        self.assertIn("Les contrôles suivants ont échoué", warning)
        values["box_08_base"] = Decimal("50851")
        self.assertFalse(
            self.wizard._french_tax_control_warning_from_values(values),
        )

    def test_formula_evaluator_rejects_executable_python(self):
        with self.assertRaises(UserError):
            self.wizard._french_tax_arithmetic_value(
                "__import__('os').system('true')",
                lambda _key: Decimal("0"),
            )

    def test_editable_adjustment_is_stored_as_native_external_value(self):
        payload = self.env[
            "rebuild.account.report.export.wizard"
        ].report_client_set_tax_adjustment(
            self.wizard.id,
            "box_A1",
            "12",
            self.env.company.id,
        )
        expression = self.report.line_ids.filtered(
            lambda line: line.code == "box_A1"
        ).expression_ids.filtered(lambda item: item.label == "adjustment")
        external_value = self.env["account.report.external.value"].search([
            ("target_report_expression_id", "=", expression.id),
            ("company_id", "=", self.env.company.id),
            ("date", "=", self.wizard.date_to),
        ])
        self.assertEqual(len(external_value), 1)
        self.assertEqual(external_value.value, 12)
        self.assertEqual(payload["title"], "Déclaration de TVA")
        a1 = next(
            line for line in payload["lines"]
            if line["line_code"] == "box_A1"
        )
        self.assertEqual(Decimal(a1["values"]["adjustment"]), Decimal("12.00"))
