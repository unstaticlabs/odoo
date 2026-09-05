import csv
import io
import re
from unittest.mock import patch

from odoo import Command
from odoo.tests import HttpCase, new_test_user, tagged


@tagged("post_install", "-at_install", "rebuild_account_migration_fec")
class TestFecExport(HttpCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.password = "fec-download"
        cls.user = new_test_user(
            cls.env,
            login="fec-download-user",
            password=cls.password,
            company_id=cls.env.company.id,
            company_ids=[Command.set(cls.env.company.ids)],
            groups="account.group_account_manager",
        )
        cls.wizard = cls.env["l10n_fr.fec.export.wizard"].with_user(
            cls.user,
        ).create({
            "date_from": "2026-01-01",
            "date_to": "2026-01-31",
            "test_file": True,
            "export_type": "official",
        })

    def test_generate_action_downloads_streamed_fec(self):
        action = self.wizard.with_user(self.user).create_fec_report_action()
        self.assertEqual(action["type"], "ir.actions.act_url")
        self.assertEqual(action["target"], "self")

        self.authenticate(self.user.login, self.password)
        response = self.url_open(action["url"])

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["Cache-Control"], "private, no-store")
        self.assertEqual(response.headers["X-Content-Type-Options"], "nosniff")
        self.assertIn("attachment;", response.headers["Content-Disposition"])
        self.assertIn("FEC20260131", response.headers["Content-Disposition"])
        self.assertNotIn(".txt", response.headers["Content-Disposition"])
        self.assertTrue(response.content.startswith(b"JournalCode|JournalLib|"))

    def test_fec_repairs_cross_company_retained_earnings_fallback(self):
        company = self.env.company
        Account = self.env["account.account"]
        retained_earnings_accounts = Account.with_company(company).search([
            *Account._check_company_domain(company),
            ("account_type", "=", "equity_unaffected"),
        ], order="code desc")
        retained_earnings = next(
            (
                account
                for account in retained_earnings_accounts
                if re.match(r"^\d{3}", account.code or "")
            ),
            False,
        )
        if not retained_earnings:
            retained_earnings = Account.with_company(company).create({
                "name": "Affectation du résultat",
                "code": "129999",
                "account_type": "equity_unaffected",
                "company_ids": [Command.set(company.ids)],
            })
        foreign_company = self.env["res.company"].create({
            "name": "FEC foreign-company regression fixture",
        })
        Account.with_company(foreign_company).create({
            "name": "Foreign retained earnings",
            "code": "999999",
            "account_type": "equity_unaffected",
            "company_ids": [Command.set(foreign_company.ids)],
        })
        wizard = self.env["l10n_fr.fec.export.wizard"].sudo().with_company(
            company,
        ).with_context(
            allowed_company_ids=company.ids,
            fec_test_mode=True,
        ).create({
            "date_from": "2026-01-01",
            "date_to": "2026-01-31",
            "test_file": True,
            "export_type": "official",
        })
        opening_result = [
            "OUV", "Balance initiale", "OUVERTURE/2026", "20260101",
            "120/129", "Bénéfice (perte) reporté(e)", "", "", "-",
            "20260101", "Balance initiale", "0,00", "100,00", "", "",
            "20260101", "", "",
        ]

        with patch.object(
            type(wizard),
            "_do_query_unaffected_earnings",
            return_value=opening_result,
        ):
            result = wizard.generate_fec()
            content = b"".join(result["file_content"])

        rows = list(csv.reader(
            io.StringIO(content.decode("utf-8-sig")),
            delimiter="|",
        ))
        opening_rows = [
            row
            for row in rows[1:]
            if row[0] == "OUV"
            and row[2] == "OUVERTURE/2026"
            and row[10] == "Balance initiale"
            and row[12].strip() == "100,00"
        ]
        self.assertEqual(len(opening_rows), 1)
        self.assertEqual(opening_rows[0][4], retained_earnings.code)
        self.assertEqual(opening_rows[0][5], retained_earnings.name)
        self.assertTrue(all(re.match(r"^\d{3}", row[4]) for row in rows[1:]))
        self.assertFalse(result["file_name"].endswith(".txt"))
