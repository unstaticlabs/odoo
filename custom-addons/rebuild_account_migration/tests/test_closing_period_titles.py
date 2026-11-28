from datetime import date

from freezegun import freeze_time
from lxml import etree

from odoo.tests import TransactionCase, new_test_user, tagged


@tagged("post_install", "-at_install", "usl_closing_period_titles")
class TestClosingPeriodTitles(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env["res.lang"]._activate_lang("fr_FR")
        cls.company = cls.env["res.company"].create({
            "name": "Period title fixture", "rebuild_declaration_profile_active": True,
        })
        cls.Closing = cls.env["rebuild.account.closing.period"].with_company(cls.company)
        cls.start, cls.end = date(2024, 1, 1), date(2024, 12, 31)

    def period(self, kind, start, end):
        return self.Closing._upsert_period(self.company, kind, start, end, self.start, self.end)

    def test_each_standard_title_follows_the_reader_and_preserves_the_saved_name(self):
        examples = [
            ("month", date(2024, 2, 1), date(2024, 2, 29), "February 2024 close",
             "Clôture mensuelle (février 2024)"),
            ("quarter", self.start, date(2024, 3, 31), "Quarter close 01 Jan 2024 - 31 Mar 2024",
             "Clôture trimestrielle du 01 janv. 2024 au 31 mars 2024"),
            ("annual", self.start, self.end, "Annual statutory close 01 Jan 2024 - 31 Dec 2024",
             "Clôture annuelle du 01 janv. 2024 au 31 déc. 2024"),
        ]
        for kind, start, end, stored, expected in examples:
            with self.subTest(kind=kind):
                period = self.period(kind, start, end)
                french = period.with_context(lang="fr_FR")
                self.assertEqual(french.display_name, expected)
                self.assertEqual(french.review_title, expected)
                self.assertEqual(period.with_context(lang="en_US").display_name, stored)
                self.assertEqual(french.display_name, expected)
                self.assertEqual(french.name, stored)

    @freeze_time("2024-03-20")
    def test_custom_title_survives_two_native_company_synchronizations(self):
        record = self.period("annual", self.start, self.end)
        own_title = "Annual statutory close 01 Jan 2024 - 31 Dec 2024 / Comité"
        record.name = own_title
        for language in ("fr_FR", "en_US"):
            refreshed = self.Closing.with_context(lang=language).sync_for_company(self.company)
            self.assertIn(record, refreshed)
            self.assertEqual(record.name, own_title)
            self.assertEqual(record.with_context(lang=language).display_name, own_title)

    def test_renaming_from_french_keeps_the_label_in_every_language(self):
        record = self.period("annual", self.start, self.end)
        french = record.with_context(lang="fr_FR")
        stored = record.name
        french.review_title = french.review_title
        self.assertEqual(record.name, stored)
        french.review_title = "Dossier du conseil de juin"
        self.assertEqual(record.name, "Dossier du conseil de juin")
        self.assertEqual(record.with_context(lang="en_US").display_name, "Dossier du conseil de juin")

    def test_a_standard_title_for_another_period_is_still_custom(self):
        period = self.period("annual", self.start, self.end)
        explicit = "Annual statutory close 01 Jan 2023 - 31 Dec 2023"
        period.name = explicit
        self.assertEqual(period.with_context(lang="fr_FR").display_name, explicit)

    def test_sorting_the_title_column_keeps_the_native_order_of_custom_names(self):
        alpha = self.period("annual", self.start, self.end)
        omega = self.period("quarter", self.start, date(2024, 3, 31))
        alpha.name, omega.name = "Alpha", "Oméga"
        scope = [("id", "in", (alpha | omega).ids)]
        periods = self.Closing.with_context(lang="fr_FR")
        self.assertEqual(periods.search(scope, order="display_name").ids, [alpha.id, omega.id])
        self.assertEqual(periods.search(scope, order="display_name desc").ids, [omega.id, alpha.id])

    def test_search_view_finds_displayed_english_and_saved_custom_titles(self):
        month = self.period("month", date(2024, 2, 1), date(2024, 2, 29))
        annual = self.period("annual", self.start, self.end)
        annual.name = "Dossier du conseil"
        scope = [("id", "in", (month | annual).ids)]
        view_id = self.env.ref(
            "rebuild_account_migration.view_rebuild_account_closing_search"
        ).id
        for lang, query, expected in [
            ("fr_FR", "février", month),
            ("fr_FR", "February", month),
            ("en_US", "February", month),
            ("fr_FR", "conseil", annual),
            ("en_US", "conseil", annual),
        ]:
            with self.subTest(lang=lang, query=query):
                periods = self.Closing.with_context(lang=lang)
                view = periods.get_view(view_id, "search")
                title_field = etree.fromstring(view["arch"]).xpath("/search/field")[0].get("name")
                self.assertEqual(periods.search(scope + [(title_field, "ilike", query)]).ids, expected.ids)
        self.assertEqual(month.name, "February 2024 close")
        self.assertEqual(annual.name, "Dossier du conseil")

    def test_localized_title_search_respects_the_readers_company(self):
        month = self.period("month", date(2024, 2, 1), date(2024, 2, 29))
        other_company = self.env["res.company"].create({"name": "Other period title fixture"})
        other_month = self.Closing._upsert_period(
            other_company, "month", month.date_from, month.date_to, self.start, self.end
        )
        reader = new_test_user(
            self.env, login="period_title_reader", groups="account.group_account_readonly",
            company_id=self.company.id,
        )
        periods = self.Closing.with_user(reader).with_context(
            lang="fr_FR", allowed_company_ids=[self.company.id]
        )
        scope = [("id", "in", (month | other_month).ids)]
        self.assertEqual(periods.search(scope + [("review_title", "ilike", "février")]).ids, month.ids)
        self.assertFalse(periods.search(scope + [("review_title", "not ilike", "February")]))
        self.assertFalse(periods.search(scope + [("review_title", "not ilike", "février")]))

    def test_new_package_uses_the_readers_title_without_changing_the_saved_period(self):
        period = self.period("annual", self.start, self.end)
        stored = period.name
        wizard = self.env["rebuild.account.report.export.wizard"].with_company(self.company).with_context(
            lang="fr_FR"
        ).create({
            "report_type": "closing_package", "company_id": self.company.id,
            "date_from": self.start, "date_to": self.end, "closing_period_id": period.id,
        })
        linked = wizard.web_read({"closing_period_id": {"fields": {"display_name": {}}}})[0]["closing_period_id"]
        self.assertEqual(linked["display_name"], "Clôture annuelle du 01 janv. 2024 au 31 déc. 2024")
        self.assertEqual(period.with_context(lang="fr_FR").action_open_closing_package()["name"], "Dossier de clôture")
        rows = wizard._closing_package_rows()
        status = next(row for row in rows if row["line_code"] == "CLOSE_STATUS")
        self.assertEqual(status["label"], "Clôture annuelle du 01 janv. 2024 au 31 déc. 2024")
        self.assertEqual((period.name, period.date_from, period.date_to), (stored, self.start, self.end))

    def test_export_in_a_second_language_keeps_the_first_package_attachment_intact(self):
        period = self.period("annual", self.start, self.end)
        wizard_model = self.env["rebuild.account.report.export.wizard"].with_company(self.company)
        values = {
            "report_type": "closing_package", "export_format": "csv",
            "company_id": self.company.id, "closing_period_id": period.id,
            "date_from": self.start, "date_to": self.end,
        }
        wizard_model.with_context(lang="en_US").create(values).action_generate_export()
        original = period.package_attachment_ids
        original.ensure_one()
        previous_bytes, previous_checksum = bytes(original.raw), original.checksum
        self.assertIn(b"Annual statutory close 01 Jan 2024 - 31 Dec 2024", previous_bytes)
        wizard_model.with_context(lang="fr_FR").create(values).action_generate_export()
        new_file = period.package_attachment_ids - original
        new_file.ensure_one()
        self.assertIn("Clôture annuelle du 01 janv. 2024 au 31 déc. 2024".encode(), bytes(new_file.raw))
        original.invalidate_recordset(["raw", "checksum"])
        self.assertIn(original, period.package_attachment_ids)
        self.assertEqual((bytes(original.raw), original.checksum), (previous_bytes, previous_checksum))
