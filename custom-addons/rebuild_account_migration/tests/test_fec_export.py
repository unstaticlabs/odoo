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
        self.assertIn("FEC20260131.txt", response.headers["Content-Disposition"])
        self.assertTrue(response.content.startswith(b"JournalCode|JournalLib|"))
