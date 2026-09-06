from odoo import Command
from odoo.tests import HttpCase, new_test_user, tagged


@tagged("post_install", "-at_install", "usl_bank_period_browser")
class TestBankPeriodBrowser(HttpCase):
    def test_accountant_corrects_period_desktop_and_mobile(self):
        accountant = new_test_user(
            self.env,
            login="bank_period_browser",
            groups="account.group_account_user",
            company_id=self.env.company.id,
            company_ids=[Command.set(self.env.company.ids)],
        )
        journal = self.env["account.journal"].create(
            {"name": "Period browser", "code": "PBR", "type": "bank"}
        )
        config = self.env["account.bank.ingestion.config"].create(
            {
                "name": "Period browser",
                "journal_id": journal.id,
                "source_account_identifier": "FR7630001007941234567890185",
                "automatic_start_date": "2026-07-01",
            }
        )
        for size in ("1366x768", "390x844"):
            ingestion = self.env["account.bank.ingestion"].create(
                {
                    "name": "Period browser",
                    "subject": "Scheduled bank export",
                    "config_id": config.id,
                    "state": "attention",
                    "period_start": "2026-07-01",
                    "period_end": "2026-07-31",
                }
            )
            self.browser_size = size
            self.start_tour(
                f"/odoo/account.bank.ingestion/{ingestion.id}",
                "usl_bank_period_correction",
                login=accountant.login,
            )
            ingestion.invalidate_recordset()
            self.assertEqual(str(ingestion.period_start), "2026-08-01")
            self.assertEqual(str(ingestion.period_end), "2026-08-31")
            self.assertTrue(
                ingestion.message_ids.filtered(
                    lambda message: "Verified official bank statement"
                    in (message.body or "")
                    and message.author_id == accountant.partner_id
                )
            )
