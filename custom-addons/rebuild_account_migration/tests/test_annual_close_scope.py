from datetime import date

from odoo import Command
from odoo.tests import tagged

from odoo.addons.account.tests.common import AccountTestInvoicingCommon


@tagged("post_install", "-at_install", "usl_annual_close_scope")
class TestAnnualCloseScope(AccountTestInvoicingCommon):
    def test_annual_coverage_counts_posted_entries_even_after_archiving_journal(self):
        company = self.company_data["company"]
        Journal = self.env["account.journal"].with_company(company)
        journal = Journal.create({"name": "Historical journal", "type": "general", "code": "HIST"})
        expense = self.company_data["default_account_expense"]
        revenue = self.company_data["default_account_revenue"]
        for posting_date in [date(2022, 9, 30), date(2023, 9, 30)]:
            move = self.env["account.move"].with_company(company).create({
                "journal_id": journal.id,
                "date": posting_date,
                "line_ids": [
                    Command.create({"account_id": expense.id, "debit": 10}),
                    Command.create({"account_id": revenue.id, "credit": 10}),
                ],
            })
            move.action_post()
        journal.active = False
        period = self.env["rebuild.account.closing.period"]
        start, end = date(2022, 10, 1), date(2023, 9, 30)
        annual = period._upsert_period(company, "annual", start, end, start, end)
        period._upsert_period(company, "quarter", date(2023, 7, 1), end, start, end)
        overview = self.env["rebuild.account.overview"].browse(company.id)
        self.env.flush_all()
        self.assertEqual(overview.open_annual_closing_id, annual)
        self.assertEqual(overview.annual_posted_move_count, 1)
        self.assertEqual(overview.annual_journal_count, 1)
        self.assertEqual(overview.annual_archived_journal_count, 1)
        self.assertGreaterEqual(overview.posted_move_count, 2)
        self.assertEqual(overview.journal_count, Journal.search_count([("company_id", "=", company.id)]))

        annual.state = "closed"
        self.env.flush_all()
        overview.invalidate_recordset()
        self.assertFalse(overview.open_annual_closing_id)
        self.assertEqual(overview.annual_posted_move_count, 0)
