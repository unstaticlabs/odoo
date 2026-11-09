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

    def _provisional(self, wizard):
        return wizard._export_metadata()["provisional"]["required"]

    def test_pdf_provisional_requires_lock_to_cover_closing_date(self):
        wizard = self._wizard(company_ids=[Command.set([self.first_company.id])])
        company = self.first_company
        for lock, expected in (
            (False, True),
            ("2026-12-30", True),
            ("2026-12-31", False),
            ("2027-01-01", False),
        ):
            with self.subTest(lock=lock):
                company.fiscalyear_lock_date = lock
                self.assertEqual(self._provisional(wizard), expected)

    def test_pdf_provisional_lock_equal_to_closing_date_is_final(self):
        # The fiscal exercise does not matter, only the report's closing date.
        wizard = self._wizard(
            company_ids=[Command.set([self.first_company.id])],
            date_from="2026-01-01",
            date_to="2026-06-30",
        )
        self.first_company.fiscalyear_lock_date = "2026-06-30"
        self.assertFalse(self._provisional(wizard))
        self.assertEqual(wizard._export_metadata()["provisional"]["reasons"], [])
        # A calendar change does not move the closing date being judged.
        self.first_company.write(
            {"fiscalyear_last_month": "6", "fiscalyear_last_day": 30},
        )
        self.assertFalse(self._provisional(wizard))

    def test_pdf_provisional_lock_one_day_before_closing_date_is_provisional(self):
        wizard = self._wizard(
            company_ids=[Command.set([self.first_company.id])],
            date_from="2026-01-01",
            date_to="2026-06-30",
        )
        self.first_company.fiscalyear_lock_date = "2026-06-29"
        self.assertTrue(self._provisional(wizard))
        reasons = " ".join(wizard._export_metadata()["provisional"]["reasons"])
        self.assertIn("date de clôture", reasons)
        self.assertNotIn("entièrement", reasons)

    def test_pdf_provisional_without_any_lock_is_provisional(self):
        wizard = self._wizard()
        self.assertFalse(self.first_company.fiscalyear_lock_date)
        self.assertFalse(self.second_company.fiscalyear_lock_date)
        self.assertTrue(self._provisional(wizard))

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

    def test_pdf_provisional_hard_lock_alone_covers_closing_date(self):
        wizard = self._wizard(
            company_ids=[Command.set([self.first_company.id])],
            date_to="2026-06-30",
        )
        self.first_company.hard_lock_date = "2026-06-29"
        self.assertTrue(self._provisional(wizard))
        self.first_company.hard_lock_date = "2026-06-30"
        self.assertFalse(self._provisional(wizard))
        # The later of the two locks applies.
        self.first_company.fiscalyear_lock_date = "2026-05-31"
        self.assertFalse(self._provisional(wizard))

    def test_pdf_provisional_one_uncovered_company_makes_report_provisional(self):
        wizard = self._wizard(date_to="2026-06-30")
        self.first_company.fiscalyear_lock_date = "2026-06-30"
        self.second_company.fiscalyear_lock_date = "2026-06-29"
        self.assertTrue(self._provisional(wizard))
        self.second_company.fiscalyear_lock_date = "2026-06-30"
        self.assertFalse(self._provisional(wizard))

    def test_pdf_provisional_uncovered_comparison_period_is_provisional(self):
        wizard = self._wizard(
            company_ids=[Command.set([self.first_company.id])],
            date_to="2026-06-30",
            comparison_mode="custom",
            comparison_date_from="2025-07-01",
            comparison_date_to="2025-12-31",
        )
        self.first_company.fiscalyear_lock_date = "2026-06-30"
        self.assertFalse(self._provisional(wizard))
        wizard.write(
            {"comparison_date_from": "2026-07-01", "comparison_date_to": "2026-07-31"},
        )
        self.assertTrue(self._provisional(wizard))
        self.first_company.fiscalyear_lock_date = "2026-07-30"
        self.assertTrue(self._provisional(wizard))
        self.first_company.fiscalyear_lock_date = "2026-07-31"
        self.assertFalse(self._provisional(wizard))

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
        # An exception never makes an uncovered closing date final either.
        self.first_company.fiscalyear_lock_date = "2026-06-30"
        self.assertTrue(wizard._export_metadata()["provisional"]["required"])

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

    def _pdf_context(self, wizard):
        renderer = self.allowed_env["usl.document.renderer"]
        with (
            patch.object(
                type(self.first_company),
                "_usl_document_renderer_company_payload",
                return_value=(dict(COMPANY_PAYLOAD), []),
            ),
            patch.object(type(renderer), "render", return_value=RENDER_RESULT) as render,
        ):
            wizard._pdf_payload([], return_result=True)
        return render.call_args.args[2]["context"]

    def test_pdf_provisional_notice_shares_the_posted_entries_cell(self):
        wizard = self._wizard(
            company_ids=[Command.set([self.first_company.id])], export_format="pdf",
        )
        self.assertTrue(self._provisional(wizard))
        reasons = " ".join(wizard._export_metadata()["provisional"]["reasons"])
        context = self._pdf_context(wizard)
        self.assertFalse(
            [item for item in context if item.startswith("Document provisoire")],
            "the notice must not be a cell of its own",
        )
        merged = [
            item for item in context if item.startswith("Écritures comptabilisées au ")
        ]
        self.assertEqual(len(merged), 1)
        entries_line, notice = merged[0].split("\n")
        self.assertRegex(entries_line, r"^Écritures comptabilisées au \d\d/\d\d/\d{4}$")
        self.assertEqual(notice, f"Document provisoire — {reasons}")
        self.assertIn(
            "Le verrouillage fiscal ne couvre pas encore la date de clôture du rapport.",
            notice,
        )
        # The grid keeps its other cells and gains none: company, period,
        # entries and unit, in that order.
        self.assertEqual(len(context), 4)
        self.assertTrue(context[0].startswith("Société : "))
        self.assertTrue(context[1].startswith("Période : "))
        self.assertIs(context[2], merged[0])
        self.assertTrue(context[3].startswith("Unité : "))

    def test_pdf_provisional_notice_merges_into_the_draft_inclusive_cell(self):
        wizard = self._wizard(
            company_ids=[Command.set([self.first_company.id])],
            export_format="pdf",
            target_move="all",
        )
        context = self._pdf_context(wizard)
        self.assertFalse(
            [item for item in context if item.startswith("Document provisoire")],
        )
        merged = [
            item
            for item in context
            if item.startswith("Écritures comptabilisées et brouillons au ")
        ]
        self.assertEqual(len(merged), 1)
        reasons = " ".join(wizard._export_metadata()["provisional"]["reasons"])
        self.assertIn("Les écritures brouillon sont incluses.", reasons)
        entries_line, notice = merged[0].split("\n")
        self.assertRegex(
            entries_line, r"^Écritures comptabilisées et brouillons au \d\d/\d\d/\d{4}$",
        )
        # The whole notice, every reason, is in the entries cell.
        self.assertEqual(notice, f"Document provisoire — {reasons}")
        self.assertEqual(len(context), 4)
        self.assertIs(context[2], merged[0])

    def test_pdf_provisional_notice_stays_in_the_entries_cell_with_comparison(self):
        wizard = self._wizard(
            company_ids=[Command.set([self.first_company.id])],
            export_format="pdf",
            comparison_mode="custom",
            comparison_date_from="2025-01-01",
            comparison_date_to="2025-12-31",
        )
        self.assertTrue(self._provisional(wizard))
        reasons = " ".join(wizard._export_metadata()["provisional"]["reasons"])
        context = self._pdf_context(wizard)
        self.assertFalse(
            [item for item in context if item.startswith("Document provisoire")],
        )
        self.assertEqual(len(context), 5)
        self.assertTrue(context[0].startswith("Société : "))
        self.assertTrue(context[1].startswith("Période : "))
        self.assertEqual(
            context[2].split("\n")[1], f"Document provisoire — {reasons}",
        )
        self.assertTrue(context[3].startswith("Unité : "))
        self.assertTrue(context[4].startswith("Comparaison : "))
        self.assertEqual(
            [item for item in context if "provisoire" in item.lower()],
            [context[2]],
        )

    def test_pdf_final_report_keeps_a_plain_posted_entries_cell(self):
        self.first_company.fiscalyear_lock_date = "2026-12-31"
        wizard = self._wizard(
            company_ids=[Command.set([self.first_company.id])], export_format="pdf",
        )
        self.assertFalse(self._provisional(wizard))
        context = self._pdf_context(wizard)
        merged = [
            item for item in context if item.startswith("Écritures comptabilisées au ")
        ]
        self.assertEqual(len(merged), 1)
        self.assertNotIn("\n", merged[0])
        self.assertNotIn("provisoire", " ".join(context).lower())
        self.assertEqual(len(context), 4)

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
            },
        )
        wizard = self._wizard(
            company_ids=[Command.set(self.first_company.ids)],
            date_from="2024-01-10",
            date_to="2024-03-31",
        )
        # The report ends inside the long first exercise: the lock must reach
        # its closing date, not the end of that exercise.
        for lock, expected in (
            ("2024-03-30", True),
            ("2024-03-31", False),
            ("2025-09-30", False),
        ):
            with self.subTest(lock=lock):
                self.first_company.fiscalyear_lock_date = lock
                self.assertEqual(self._provisional(wizard), expected)
