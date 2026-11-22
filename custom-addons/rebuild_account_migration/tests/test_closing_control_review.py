from datetime import date

from odoo.exceptions import UserError
from odoo.tests import tagged

from odoo.addons.account.tests.common import AccountTestInvoicingCommon


@tagged("post_install", "-at_install", "usl_closing_control_review")
class TestClosingControlReview(AccountTestInvoicingCommon):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.company_data["company"]
        start, end = date(2023, 1, 1), date(2023, 12, 31)
        cls.period = cls.env["rebuild.account.closing.period"]._upsert_period(
            cls.company, "annual", start, end, start, end,
        )

    def result(self, code):
        values = getattr(self.period, f"_control_{code}")()
        return self.env["rebuild.account.closing.control"]._upsert(self.period, code, values)

    def test_export_review_opens_the_original_company_and_period_in_test_mode(self):
        for code, model in [("fec", "l10n_fr.fec.export.wizard"), ("reports", "rebuild.account.report.export.wizard")]:
            with self.subTest(code=code):
                control = self.result(code)
                action = control.action_open_records()
                self.assertEqual(action["res_model"], model)
                wizard_model = self.env[model].with_context(action["context"])
                self.assertEqual(wizard_model.env.company, self.company)
                wizard = wizard_model.new(wizard_model.default_get(list(wizard_model._fields)))
                self.assertEqual((wizard.date_from, wizard.date_to), (self.period.date_from, self.period.date_to))
                self.assertEqual(control.review_result, "Export available")
                if code == "fec":
                    self.assertTrue(wizard.test_file)
                else:
                    self.assertEqual(wizard.closing_period_id, self.period)
                    self.assertEqual(wizard.company_ids._origin, self.company)

    def test_report_review_names_the_unsaved_form_in_its_language(self):
        self.env["res.lang"]._activate_lang("fr_FR")
        action = self.result("reports").action_open_records()
        Wizard = self.env[action["res_model"]].with_context(action["context"], lang="fr_FR")
        report = Wizard.new(Wizard.default_get(list(Wizard._fields)))
        self.assertFalse(report._origin)
        self.assertEqual(report.display_name, "Balance générale")

        # The installed source labels are French in both languages. Give the
        # second selection a distinct translation to exercise language caching.
        option = self.env.ref(
            "rebuild_account_migration.selection__rebuild_account_report_export_wizard__report_type__general_ledger"
        )
        option.update_field_translations("name", {"fr_FR": "Grand livre de démonstration"})
        report.report_type = "general_ledger"
        self.assertEqual(report.display_name, "Grand livre de démonstration")
        self.assertEqual(report.with_context(lang="en_US").display_name, "Grand livre")
        self.assertEqual(report.display_name, "Grand livre de démonstration")

    def test_review_lists_only_drafts_and_posted_documents_missing_the_primary_file(self):
        def invoice():
            return self.init_invoice("out_invoice", partner=self.partner_a, invoice_date=self.period.date_to,
                                     amounts=[10], taxes=self.env["account.tax"])
        draft, missing, attached, cancelled = (invoice() for _ in range(4))
        (missing | attached).action_post()
        cancelled.button_cancel()
        file = self.env["ir.attachment"].create({
            "name": "Example.txt", "raw": b"disposable example",
            "res_model": "account.move", "res_id": attached.id,
        })
        attached.message_main_attachment_id = file
        control = self.result("document_completeness")
        domain = control.action_open_records()["domain"]
        found = self.env["account.move"].search(domain)
        self.assertTrue(draft in found and missing in found)
        self.assertFalse(attached in found or cancelled in found)
        self.assertEqual(len(found), control.record_count)

    def test_hygiene_overview_and_close_explain_different_date_and_severity_scopes(self):
        issues = self.env["rebuild.account.hygiene.issue"]
        overview = self.env["rebuild.account.overview"].browse(self.company.id)
        before = overview.hygiene_issue_count
        for key, severity, issue_date in [
            ("before", "4_information", self.period.date_from),
            ("undated", "2_warning", False),
            ("after", "1_blocking", date(2024, 1, 1)),
        ]:
            issues.create({
                "company_id": self.company.id, "issue_key": key, "issue_type": "draft",
                "severity": severity, "issue_date": issue_date, "title": "Synthetic finding",
                "description": "Example", "recommended_action": "Review the source",
                "why_it_matters": "Example", "accounting_consequence": "Example", "evidence": "Example",
            })
        control = self.result("issues")
        self.assertEqual(control.record_count, 1)
        self.assertEqual(control.status, "warning")
        self.assertEqual(set(issues.search(control.action_open_records()["domain"]).mapped("issue_key")), {"before", "undated"})
        overview.invalidate_recordset(["hygiene_issue_count"])
        self.assertEqual(overview.hygiene_issue_count, before + 3)

    def test_summary_counts_export_availability_within_the_successful_checks(self):
        for code in ("reports", "fec", "document_completeness"):
            self.result(code)
        self.assertEqual(self.period.passed_count, 3)
        self.assertEqual(self.period.available_export_count, 2)

    def test_review_explains_when_the_export_tool_is_unavailable(self):
        xml_id = self.env["ir.model.data"].search([
            ("module", "=", "rebuild_account_migration"), ("name", "=", "action_rebuild_account_report_export_fec"),
        ])
        self.assertTrue(xml_id)
        xml_id.unlink()
        control = self.result("fec")
        self.assertEqual(control.status, "block")
        with self.assertRaisesRegex(UserError, "This export is unavailable"):
            control.action_open_records()
