import datetime as dt
from unittest.mock import patch

from odoo import fields
from odoo.tests import TransactionCase, tagged


@tagged(
    "post_install",
    "-at_install",
    "rebuild_account_migration_unit",
    "usl_accounting_bank_hygiene",
)
class TestBankStatementHygiene(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        company = cls.env.company
        bank_account = cls.env["res.partner.bank"].create(
            {
                "account_number": "FR7630001007941234567890185",
                "partner_id": company.partner_id.id,
                "company_id": company.id,
            },
        )
        cls.journal = cls.env["account.journal"].create(
            {
                "name": "Monthly hygiene bank",
                "code": "MHB1",
                "type": "bank",
                "company_id": company.id,
                "bank_account_id": bank_account.id,
            },
        )
        cls.config = cls.env["account.bank.ingestion.config"].create(
            {
                "name": "Monthly hygiene export",
                "company_id": company.id,
                "journal_id": cls.journal.id,
                "source_account_identifier": bank_account.account_number,
                "responsible_user_id": cls.env.user.id,
                "automatic_start_date": dt.date(2026, 7, 1),
                "expected_delivery_day": 5,
                "alias_name": "monthly-hygiene-bank-test",
            },
        )

    def test_overdue_export_surfaces_in_hygiene_and_accounting_overview(self):
        today = dt.date(2026, 8, 25)
        with patch.object(fields.Date, "context_today", return_value=today):
            candidates = self.env[
                "rebuild.account.hygiene.issue"
            ]._evaluate_builtin_hygiene(self.env.company)
            bank_issue = [
                item
                for item in candidates
                if item["control_code"] == "hygiene_bank_statement"
            ]
            overview = self.env["rebuild.account.overview"].search(
                [("company_id", "=", self.env.company.id)],
                limit=1,
            )

        self.assertEqual(len(bank_issue), 1)
        self.assertEqual(bank_issue[0]["target_model"], "account.journal")
        self.assertEqual(bank_issue[0]["target_res_id"], self.journal.id)
        self.assertEqual(bank_issue[0]["severity"], "2_warning")
        self.assertEqual(self.config.review_status, "expected")
        self.assertEqual(overview.bank_checkpoint_config_id, self.config)
        self.assertEqual(overview.bank_checkpoint_status, "expected")
        action = overview.action_open_bank_checkpoint()
        self.assertEqual(action["res_model"], "account.bank.ingestion")
