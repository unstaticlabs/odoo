from datetime import date
from unittest.mock import patch

from odoo import Command
from odoo.exceptions import AccessError
from odoo.tests import TransactionCase, tagged

from .test_declaration_closing import COMPANY_PAYLOAD, RENDER_RESULT


@tagged("post_install", "-at_install", "report_provisional")
class TestReportProvisionalStatus(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.first_company = cls.env["res.company"].create({"name": "[QA] Report Alpha"})
        cls.second_company = cls.env["res.company"].create({"name": "[QA] Report Beta"})
        cls.allowed_env = cls.env(
            context={
                **cls.env.context,
                "allowed_company_ids": [cls.first_company.id, cls.second_company.id],
            },
        )

    def _wizard(self, **values):
        return (
            self.allowed_env["rebuild.account.report.export.wizard"]
            .with_company(self.first_company)
            .create(
                {
                    "report_type": "trial_balance",
                    "company_id": self.first_company.id,
                    "company_ids": [
                        Command.set([self.first_company.id, self.second_company.id]),
                    ],
                    "date_from": "2026-01-01",
                    "date_to": "2026-12-31",
                    **values,
                },
            )
        )

    def test_pdf_provisional_requires_full_fiscal_exercise_lock(self):
        wizard = self._wizard(company_ids=[Command.set([self.first_company.id])])
        company = self.first_company
        for lock, expected in (
            (False, True),
            ("2026-12-30", True),
            ("2026-12-31", False),
        ):
            with self.subTest(lock=lock):
                company.fiscalyear_lock_date = lock
                self.assertEqual(
                    wizard._export_metadata()["provisional"]["required"], expected,
                )
        wizard.write({"date_to": "2026-06-30"})
        company.fiscalyear_lock_date = "2026-06-30"
        self.assertTrue(wizard._export_metadata()["provisional"]["required"])
        company.write({"fiscalyear_last_month": "6", "fiscalyear_last_day": 30})
        self.assertFalse(wizard._export_metadata()["provisional"]["required"])

    def test_pdf_provisional_ignores_tax_sales_purchase_locks(self):
        wizard = self._wizard(company_ids=[Command.set([self.first_company.id])])
        self.first_company.write(
            {
                "tax_lock_date": "2026-12-31",
                "sale_lock_date": "2026-12-31",
                "purchase_lock_date": "2026-12-31",
            },
        )
        self.assertTrue(wizard._export_metadata()["provisional"]["required"])
        self.first_company.hard_lock_date = "2026-12-31"
        self.assertFalse(wizard._export_metadata()["provisional"]["required"])

    def test_pdf_provisional_covers_every_company_and_comparison(self):
        wizard = self._wizard()
        self.first_company.fiscalyear_lock_date = "2026-12-31"
        self.assertTrue(wizard._export_metadata()["provisional"]["required"])
        self.second_company.fiscalyear_lock_date = "2026-12-31"
        self.assertFalse(wizard._export_metadata()["provisional"]["required"])
        wizard.write(
            {
                "comparison_mode": "custom",
                "comparison_date_from": "2027-01-01",
                "comparison_date_to": "2027-12-31",
            },
        )
        self.assertTrue(wizard._export_metadata()["provisional"]["required"])
        wizard.write(
            {"comparison_date_from": "2025-01-01", "comparison_date_to": "2025-12-31"},
        )
        self.assertFalse(wizard._export_metadata()["provisional"]["required"])
        wizard.target_move = "all"
        self.assertTrue(wizard._export_metadata()["provisional"]["required"])
        self.assertIn(
            "brouillon", " ".join(wizard._export_metadata()["provisional"]["reasons"]),
        )

    def test_pdf_provisional_keeps_uncertain_periods_visible(self):
        Wizard = self.allowed_env["rebuild.account.report.export.wizard"]
        for missing in ("date_from", "date_to"):
            wizard = Wizard.new(
                {
                    "company_id": self.first_company.id,
                    "company_ids": [Command.set([self.first_company.id])],
                    "date_from": "2026-01-01",
                    "date_to": "2026-12-31",
                    missing: False,
                },
            )
            with patch.object(
                type(wizard), "_selected_companies", return_value=self.first_company,
            ):
                self.assertTrue(wizard._report_provisional_status()["required"])
        wizard = Wizard.new(
            {
                "company_id": self.first_company.id,
                "company_ids": [Command.set([self.first_company.id])],
                "date_from": "2026-01-01",
                "date_to": "2026-12-31",
                "comparison_mode": "custom",
                "comparison_date_from": False,
                "comparison_date_to": "2025-12-31",
            },
        )
        self.first_company.fiscalyear_lock_date = "2026-12-31"
        with patch.object(
            type(wizard), "_selected_companies", return_value=self.first_company,
        ):
            self.assertTrue(wizard._report_provisional_status()["required"])

    def test_pdf_provisional_is_independent_of_lock_exceptions(self):
        wizard = self._wizard(company_ids=[Command.set([self.first_company.id])])
        self.first_company.fiscalyear_lock_date = "2026-12-31"
        self.env["account.lock_exception"].create(
            {
                "company_id": self.first_company.id,
                "user_id": self.env.user.id,
                "lock_date_field": "fiscalyear_lock_date",
                "lock_date": False,
                "reason": "Synthetic personal exception",
            },
        )
        self.assertFalse(self.first_company.user_fiscalyear_lock_date > date.min)
        self.assertFalse(wizard._export_metadata()["provisional"]["required"])

    def test_pdf_provisional_preamble_footer_preserve_rendered_financial_rows(self):
        wizard = self._wizard(
            company_ids=[Command.set([self.first_company.id])], export_format="pdf",
        )
        rows = [
            {
                "account_code": "706991",
                "account_name": "Synthetic services",
                "debit": "1234.56",
                "credit": "0.00",
                "closing_balance": "1234.56",
            },
        ]
        renderer = self.allowed_env["usl.document.renderer"]
        captured = []
        for locked in (False, True):
            self.first_company.fiscalyear_lock_date = "2026-12-31" if locked else False
            with (
                patch.object(
                    type(self.first_company),
                    "_usl_document_renderer_company_payload",
                    return_value=(dict(COMPANY_PAYLOAD), []),
                ),
                patch.object(
                    type(renderer), "render", return_value=RENDER_RESULT,
                ) as render,
            ):
                wizard._pdf_payload(rows, return_result=True)
                captured.append(render.call_args)
        unlocked_company, unlocked_doc = captured[0].args[1:3]
        locked_company, locked_doc = captured[1].args[1:3]
        self.assertIn("Document provisoire", " ".join(unlocked_doc["context"]))
        self.assertIn("Document provisoire", unlocked_company["footer_label"])
        self.assertNotIn("Document provisoire", " ".join(locked_doc["context"]))
        self.assertNotIn("Document provisoire", locked_company["footer_label"])
        self.assertEqual(unlocked_doc["sections"], locked_doc["sections"])
        self.assertEqual(unlocked_doc["controls"], locked_doc["controls"])
        self.assertEqual(unlocked_doc["basis_note"], locked_doc["basis_note"])
        self.assertEqual(
            unlocked_company["legal_identity_lines"],
            locked_company["legal_identity_lines"],
        )
        self.assertEqual(
            unlocked_company["primary_color"], locked_company["primary_color"],
        )
        self.assertTrue(
            unlocked_company["footer_label"].startswith(locked_company["footer_label"]),
        )

    def test_provisional_metadata_rejects_unselected_company_scope(self):
        wizard = self._wizard().with_context(allowed_company_ids=self.first_company.ids)
        with self.assertRaises(AccessError):
            wizard._export_metadata()

    def test_pdf_provisional_honors_inherited_native_fiscal_lock(self):
        child = self.env["res.company"].create({
            "name": "[QA] Report child",
            "parent_id": self.first_company.id,
        })
        self.first_company.fiscalyear_lock_date = "2026-12-31"
        wizard = self._wizard(
            company_id=child.id,
            company_ids=[Command.set(child.ids)],
        ).with_context(allowed_company_ids=[self.first_company.id, child.id])
        self.assertFalse(wizard._export_metadata()["provisional"]["required"])

    def test_pdf_provisional_honors_exceptional_first_fiscal_year(self):
        self.first_company.write(
            {
                "fiscalyear_last_month": "9",
                "fiscalyear_last_day": 30,
                "rebuild_first_fiscalyear_start": "2024-01-10",
                "rebuild_first_fiscalyear_end": "2025-09-30",
                "fiscalyear_lock_date": "2024-09-30",
            },
        )
        wizard = self._wizard(
            company_ids=[Command.set(self.first_company.ids)],
            date_from="2024-01-10",
            date_to="2024-03-31",
        )
        self.assertTrue(wizard._export_metadata()["provisional"]["required"])
        self.first_company.fiscalyear_lock_date = "2025-09-30"
        self.assertFalse(wizard._export_metadata()["provisional"]["required"])
