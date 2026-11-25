from odoo import Command, SUPERUSER_ID, fields
from odoo.exceptions import AccessError
from odoo.tests import tagged
from odoo.tests.common import new_test_user

from odoo.addons.hr_expense.tests.common import TestExpenseCommon


@tagged("post_install", "-at_install", "usl_overview_expense_states")
class TestOverviewExpenseStates(TestExpenseCommon):
    def _expense(self, state="draft"):
        self.product_c.rebuild_receipt_required = False
        self._scope_expense_number = getattr(self, "_scope_expense_number", 0) + 1
        expense = self.env["hr.expense"].with_user(self.expense_user_employee).create({
            "name": "[QA] Overview state " + state, "date": fields.Date.today(),
            "employee_id": self.expense_employee.id, "product_id": self.product_c.id,
            "company_id": self.env.company.id, "payment_mode": "own_account",
            "total_amount_currency": 17 + self._scope_expense_number, "tax_ids": [Command.clear()],
        })
        if state in ("submitted", "approved", "posted"):
            expense.action_submit()
        if state in ("approved", "posted"):
            expense.with_user(self.expense_user_manager).action_approve()
        if state == "posted":
            action = expense.with_user(self.env.user).action_post()
            if action:
                self.env[action["res_model"]].with_context(action["context"]).browse(action["res_id"]).action_post_entry()
        self.assertEqual(expense.state, state)
        return expense

    def _overview(self):
        self.env.flush_all()
        overview = self.env["rebuild.account.overview"].browse(self.env.company.id)
        overview.invalidate_recordset()
        return overview

    def test_separates_states_without_changing_legacy_work_count_or_source_records(self):
        expenses = self._expense() | self._expense() | self._expense("submitted") | self._expense("approved")
        posted = self._expense("posted")
        expenses |= posted
        before = expenses.read(["state", "approval_state", "total_amount_currency", "account_move_id", "write_date"])
        overview = self._overview()
        self.assertEqual(overview.draft_expense_count, 4)
        self.assertEqual(overview.draft_only_expense_count, 2)
        self.assertEqual(overview.submitted_expense_count, 1)
        self.assertEqual(overview.approved_expense_count, 1)
        self.assertEqual(before, expenses.read(["state", "approval_state", "total_amount_currency", "account_move_id", "write_date"]))
        self.assertEqual(posted.account_move_id.state, "posted")

    def test_approved_only_does_not_claim_any_drafts(self):
        self._expense("approved")
        overview = self._overview()
        self.assertEqual(overview.draft_expense_count, 1)
        self.assertEqual(overview.draft_only_expense_count, 0)
        self.assertEqual(overview.submitted_expense_count, 0)
        self.assertEqual(overview.approved_expense_count, 1)

    def test_readonly_overview_keeps_company_rules_and_aggregates_without_expense_access(self):
        self._expense()
        other = self.company_data_2["company"]
        employee = self.env["hr.employee"].with_user(SUPERUSER_ID).create({
            "name": "[QA] Other-company expense owner", "company_id": other.id,
        })
        self.env["hr.expense"].with_user(SUPERUSER_ID).with_company(other).create({
            "name": "[QA] Other-company draft", "date": fields.Date.today(),
            "company_id": other.id, "employee_id": employee.id,
            "product_id": self.product_c.id, "account_id": self.company_data_2["default_account_expense"].id,
            "total_amount_currency": 19, "tax_ids": [Command.clear()],
        })
        reader = new_test_user(self.env, login="qa.overview.scope.reader", groups="account.group_account_readonly",
            company_id=self.env.company.id, company_ids=[Command.set(self.env.company.ids)])
        overview = self._overview().with_user(reader).with_context(allowed_company_ids=self.env.company.ids)
        self.assertEqual(overview.draft_only_expense_count, 1)
        self.assertEqual(overview.approved_expense_count, 0)
        self.assertFalse(overview.has_access("write"))
        with self.assertRaises(AccessError):
            overview.browse(other.id).read(["draft_only_expense_count"])
