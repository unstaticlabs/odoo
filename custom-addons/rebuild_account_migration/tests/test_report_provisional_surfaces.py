import io
import json
import re
import zipfile
from unittest.mock import patch
from xml.etree import ElementTree

from odoo import Command
from odoo.exceptions import AccessError
from odoo.tests import TransactionCase, new_test_user, tagged

from .test_declaration_closing import COMPANY_PAYLOAD, RENDER_RESULT

_NS = {"x": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
_QUALIFIED = "{%s}" % _NS["x"]


@tagged("post_install", "-at_install", "report_provisional_surfaces")
class TestReportProvisionalSurfaces(TransactionCase):
    """The provisional status computed for the PDF reaches screen and XLSX."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.first_company = cls.env["res.company"].create({
            "name": "[QA] Surface Alpha",
            "currency_id": cls.env.ref("base.EUR").id,
        })
        cls.second_company = cls.env["res.company"].create({
            "name": "[QA] Surface Beta",
            "currency_id": cls.env.ref("base.EUR").id,
        })
        both = [cls.first_company.id, cls.second_company.id]
        cls.manager = new_test_user(
            cls.env, login="qa.surface.manager@example.invalid",
            company_id=cls.first_company.id,
            company_ids=[Command.set(both)],
            groups="account.group_account_manager",
        )
        cls.reader = new_test_user(
            cls.env, login="qa.surface.reader@example.invalid",
            company_id=cls.first_company.id,
            company_ids=[Command.set(both)],
            groups="account.group_account_readonly",
        )
        cls.alpha_only = new_test_user(
            cls.env, login="qa.surface.alpha@example.invalid",
            company_id=cls.first_company.id,
            company_ids=[Command.set(cls.first_company.ids)],
            groups="account.group_account_manager",
        )
        accounts = cls.env["account.account"].with_company(
            cls.first_company,
        ).create([
            {"name": "[QA] Cash", "code": "512001", "account_type": "asset_cash"},
            {"name": "[QA] Sales", "code": "706001", "account_type": "income"},
        ])
        journal = cls.env["account.journal"].create({
            "name": "[QA] Surfaces", "code": "QASF", "type": "general",
            "company_id": cls.first_company.id,
        })
        cls.env["account.move"].create({
            "company_id": cls.first_company.id,
            "journal_id": journal.id,
            "date": "2026-03-15",
            "move_type": "entry",
            "line_ids": [
                Command.create({
                    "name": "[QA] Evidence", "account_id": accounts[0].id,
                    "debit": 100,
                }),
                Command.create({
                    "name": "[QA] Sale", "account_id": accounts[1].id,
                    "credit": 100,
                }),
            ],
        }).action_post()

    def _wizard(self, user=None, companies=None, **values):
        companies = companies or (self.first_company | self.second_company)
        user = user or self.manager
        return self.env["rebuild.account.report.export.wizard"].with_user(
            user,
        ).with_context(allowed_company_ids=companies.ids).create({
            "report_type": "trial_balance",
            "company_id": self.first_company.id,
            "company_ids": [Command.set(companies.ids)],
            "date_from": "2026-01-01",
            "date_to": "2026-12-31",
            "target_move": "posted",
            "group_by": "none",
            "preview_limit": 500,
            **values,
        })

    def _lock(self, first=False, second=False):
        self.first_company.fiscalyear_lock_date = first
        self.second_company.fiscalyear_lock_date = second

    def _payload(self, wizard):
        wizard.action_preview_report()
        return wizard._report_client_payload()

    def _workbook(self, wizard):
        archive = zipfile.ZipFile(io.BytesIO(wizard._xlsx_payload(
            wizard._visible_preview_rows(wizard._report_rows()),
        )))
        strings = [
            "".join(node.text or "" for node in item.iter(_QUALIFIED + "t"))
            for item in ElementTree.fromstring(
                archive.read("xl/sharedStrings.xml"),
            ).iter(_QUALIFIED + "si")
        ]

        def cells(sheet):
            root = ElementTree.fromstring(archive.read(f"xl/worksheets/{sheet}.xml"))
            return {
                cell.get("r"): strings[int(cell.find("x:v", _NS).text)]
                for cell in root.iter(_QUALIFIED + "c")
                if cell.get("t") == "s"
            }

        return {
            "metadata": cells("sheet1"),
            "report": cells("sheet2"),
            "report_xml": archive.read("xl/worksheets/sheet2.xml").decode(),
            "strings": strings,
        }

    # Screen payload ---------------------------------------------------

    def test_payload_exposes_provisional_until_lock_covers_closing_date(self):
        wizard = self._wizard(companies=self.first_company)
        for lock, expected in (
            (False, True),
            ("2026-12-30", True),
            ("2026-12-31", False),
            ("2027-01-01", False),
        ):
            with self.subTest(lock=lock):
                self._lock(first=lock)
                status = self._payload(wizard)["provisional"]
                self.assertEqual(status["required"], expected)
                self.assertEqual(bool(status["reasons"]), expected)
                if expected:
                    self.assertIn("verrouillage fiscal", " ".join(status["reasons"]))

    def test_payload_status_is_identical_for_two_users(self):
        for lock in (False, "2026-12-31"):
            self._lock(first=lock, second=lock)
            statuses = [
                self._payload(self._wizard(user=user))["provisional"]
                for user in (self.manager, self.reader)
            ]
            with self.subTest(lock=lock):
                self.assertEqual(statuses[0], statuses[1])
                self.assertEqual(statuses[0]["required"], lock is False)

    def test_payload_status_matches_the_status_in_export_metadata(self):
        wizard = self._wizard()
        for lock in (False, "2026-12-31"):
            self._lock(first=lock, second=lock)
            payload = self._payload(wizard)
            self.assertEqual(
                payload["provisional"], wizard._export_metadata()["provisional"],
            )

    def test_old_or_invalid_preview_recomputes_instead_of_showing_final(self):
        wizard = self._wizard()
        self._lock()
        wizard.action_preview_report()
        for metadata in (
            "{}", "not valid json", "[]",
            json.dumps({"provisional": "yes"}),
            json.dumps({"provisional": {"required": False}}),
            json.dumps({"provisional": {"required": False, "reasons": [1]}}),
        ):
            with self.subTest(metadata=metadata):
                wizard.preview_metadata = metadata
                self.assertTrue(wizard._report_client_payload()["provisional"]["required"])
        self._lock("2026-12-31", "2026-12-31")
        wizard.preview_metadata = "{}"
        self.assertEqual(
            wizard._report_client_payload()["provisional"],
            {"required": False, "reasons": []},
        )

    def test_payload_one_unlocked_company_of_two_is_provisional(self):
        wizard = self._wizard()
        self._lock(first="2026-12-31", second=False)
        self.assertTrue(self._payload(wizard)["provisional"]["required"])
        self._lock(first="2026-12-31", second="2026-12-31")
        self.assertFalse(self._payload(wizard)["provisional"]["required"])

    def test_payload_locked_prior_year_with_open_current_period_is_provisional(self):
        wizard = self._wizard(
            companies=self.first_company,
            comparison_mode="custom",
            comparison_date_from="2025-01-01",
            comparison_date_to="2025-12-31",
        )
        self._lock(first="2025-12-31")
        self.assertTrue(self._payload(wizard)["provisional"]["required"])
        self._lock(first="2026-12-31")
        self.assertFalse(self._payload(wizard)["provisional"]["required"])
        # The comparison period is judged as well as the current one.
        wizard.write({
            "comparison_date_from": "2027-01-01", "comparison_date_to": "2027-12-31",
        })
        self.assertTrue(self._payload(wizard)["provisional"]["required"])

    def test_payload_drafts_included_is_provisional_even_when_locked(self):
        self._lock("2026-12-31", "2026-12-31")
        wizard = self._wizard()
        self.assertFalse(self._payload(wizard)["provisional"]["required"])
        wizard.target_move = "all"
        status = self._payload(wizard)["provisional"]
        self.assertTrue(status["required"])
        self.assertIn("brouillon", " ".join(status["reasons"]))

    def test_payload_ignores_tax_locks_and_lock_exceptions(self):
        wizard = self._wizard(companies=self.first_company)
        self._lock()
        self.first_company.tax_lock_date = "2026-12-31"
        self.first_company.sale_lock_date = "2026-12-31"
        self.first_company.purchase_lock_date = "2026-12-31"
        self.assertTrue(self._payload(wizard)["provisional"]["required"])
        self.first_company.fiscalyear_lock_date = "2026-12-31"
        self.assertFalse(self._payload(wizard)["provisional"]["required"])

    def test_tax_report_payload_keeps_provisional(self):
        company = self.env.company
        wizard = self.env["rebuild.account.report.export.wizard"].create({
            "report_type": "tax_report",
            "company_id": company.id,
            "company_ids": [Command.set(company.ids)],
            "date_from": "2026-01-01",
            "date_to": "2026-06-30",
            "target_move": "posted",
            "group_by": "section",
        })
        company.fiscalyear_lock_date = False
        payload = self._payload(wizard)
        self.assertEqual(payload["title"], "Déclaration de TVA")
        self.assertTrue(payload["provisional"]["required"])
        company.fiscalyear_lock_date = "2026-06-30"
        payload = self._payload(wizard)
        self.assertEqual(payload["title"], "Déclaration de TVA")
        self.assertEqual(payload["provisional"], {"required": False, "reasons": []})

    def test_payload_does_not_leak_a_company_outside_the_allowed_set(self):
        self._lock()
        wizard = self._wizard(
            user=self.alpha_only, companies=self.first_company,
        )
        payload = self._payload(wizard)
        self.assertTrue(payload["provisional"]["required"])
        self.assertNotIn(
            self.second_company.name, json.dumps(payload["provisional"]),
        )
        self.assertNotIn(self.second_company.name, json.dumps(payload, default=str))
        with self.assertRaises(AccessError):
            outside = self._wizard(
                user=self.alpha_only, companies=self.first_company,
            )
            outside.write({"company_ids": [Command.set(
                (self.first_company | self.second_company).ids,
            )]})
            outside._report_client_payload()

    # XLSX -------------------------------------------------------------

    def test_xlsx_provisional_report_shows_banner_header_footer_and_status(self):
        self._lock()
        sheets = self._workbook(self._wizard())
        report = sheets["report"]
        self.assertTrue(report["A2"].startswith("DOCUMENT PROVISOIRE — "))
        self.assertIn("verrouillage fiscal", report["A2"])
        # The subtitle moves down one row; the title stays first.
        self.assertIn("[QA] Surface Alpha", report["A3"])
        self.assertEqual(report["A1"], "Balance générale")
        self.assertIn("Document provisoire", sheets["report_xml"])
        self.assertEqual(sheets["report_xml"].count("Document provisoire"), 2)
        metadata = sheets["metadata"]
        status_row = next(
            ref for ref, value in metadata.items()
            if value == "Statut" and ref.startswith("A")
        )
        self.assertEqual(metadata["B" + status_row[1:]], "Provisoire")
        self.assertNotIn('"required"', " ".join(sheets["strings"]))

    def test_xlsx_final_report_has_no_provisional_label(self):
        self._lock("2026-12-31", "2026-12-31")
        sheets = self._workbook(self._wizard())
        everything = " ".join(sheets["strings"]) + sheets["report_xml"]
        self.assertNotIn("PROVISOIRE", everything)
        self.assertNotIn("rovisoire", everything)
        self.assertIn("[QA] Surface Alpha", sheets["report"]["A2"])
        status_row = next(
            ref for ref, value in sheets["metadata"].items() if value == "Statut"
        )
        self.assertEqual(sheets["metadata"]["B" + status_row[1:]], "Définitif")

    def test_xlsx_table_header_follows_the_banner(self):
        self._lock()
        provisional = self._workbook(self._wizard())["report"]
        self._lock("2026-12-31", "2026-12-31")
        final = self._workbook(self._wizard())["report"]
        # One extra row above the table, nothing else moves.
        self.assertEqual(provisional["A5"], final["A4"])
        self.assertEqual(provisional["A6"], final["A5"])

    def test_pdf_and_xlsx_agree_under_the_same_locks(self):
        renderer = self.env["usl.document.renderer"]
        for first, second, expected in (
            (False, False, True),
            ("2026-12-31", False, True),
            ("2026-12-31", "2026-12-31", False),
        ):
            self._lock(first, second)
            wizard = self._wizard(export_format="pdf")
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
                wizard._pdf_payload(wizard._report_rows(), return_result=True)
            pdf_document = render.call_args.args[2]
            in_pdf = "Document provisoire" in " ".join(pdf_document["context"])
            in_xlsx = "DOCUMENT PROVISOIRE" in self._workbook(wizard)["report"].get("A2", "")
            screen = self._payload(wizard)["provisional"]["required"]
            with self.subTest(first=first, second=second):
                self.assertEqual((in_pdf, in_xlsx, screen), (expected,) * 3)

    def test_csv_keeps_the_status_inside_its_metadata(self):
        self._lock()
        wizard = self._wizard(export_format="csv")
        content = wizard._csv_payload(wizard._report_rows()).decode()
        self.assertIn('provisional', content)
        self.assertNotIn("DOCUMENT PROVISOIRE", content)

    # FEC --------------------------------------------------------------

    def test_fec_text_file_never_carries_a_provisional_label(self):
        company = self.env.company
        files = []
        for lock in (False, "2026-01-31"):
            company.fiscalyear_lock_date = lock
            wizard = self.env["rebuild.account.report.export.wizard"].create({
                "report_type": "fec",
                "export_format": "txt",
                "company_id": company.id,
                "company_ids": [Command.set(company.ids)],
                "date_from": "2026-01-01",
                "date_to": "2026-01-31",
                "target_move": "posted",
                "fec_test_mode": True,
            })
            content, filename, _metadata = wizard._fec_export_payload()
            files.append((content, filename))
            self.assertFalse(re.search(rb"provisoire", content, re.IGNORECASE))
            self.assertFalse(re.search(rb"provisoire", filename.encode(), re.IGNORECASE))
        self.assertEqual(files[0], files[1])
