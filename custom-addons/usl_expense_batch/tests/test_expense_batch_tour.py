from time import sleep
from unittest.mock import patch

from odoo import Command, fields
from odoo.tests import HttpCase, tagged

from odoo.addons.hr_expense.tests.common import TestExpenseCommon
from odoo.addons.mail.tests.common import mail_new_test_user


@tagged("post_install", "-at_install", "usl_expense_batch_browser")
class TestExpenseBatchBrowser(TestExpenseCommon, HttpCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.batch = cls.env["usl.expense.batch"].create({
            "name": "SBFH — Browser Canada 2026",
            "purpose": "Browser-validated customer workshops",
            "context_type": "travel",
            "context_date_from": fields.Date.from_string("2026-07-01"),
            "context_date_to": fields.Date.from_string("2026-07-31"),
            "employee_id": cls.expense_employee.id,
            "company_id": cls.env.company.id,
            "analytic_distribution": {
                str(cls.analytic_account_1.id): 100.0,
            },
        })
        cls.expenses = cls.env["hr.expense"].with_user(
            cls.expense_user_employee,
        ).create([
            {
                "name": "Browser Canada hotel",
                "date": fields.Date.from_string("2026-07-10"),
                "employee_id": cls.expense_employee.id,
                "product_id": cls.product_c.id,
                "company_id": cls.env.company.id,
                "payment_mode": "own_account",
                "total_amount_currency": 210,
                "analytic_distribution": {
                    str(cls.analytic_account_1.id): 100.0,
                },
                "analytic_context_source": "product",
            },
            {
                "name": "Browser Canada taxi",
                "date": fields.Date.from_string("2026-07-11"),
                "employee_id": cls.expense_employee.id,
                "product_id": cls.product_c.id,
                "company_id": cls.env.company.id,
                "payment_mode": "company_account",
                "total_amount_currency": 45,
                "analytic_distribution": {
                    str(cls.analytic_account_1.id): 100.0,
                },
                "analytic_context_source": "product",
            },
        ])
        cls.receipt_expense = cls.env["hr.expense"].with_user(
            cls.expense_user_employee,
        ).create({
            "name": "Browser receipt evidence",
            "date": fields.Date.from_string("2026-07-09"),
            "employee_id": cls.expense_employee.id,
            "product_id": cls.product_c.id,
            "company_id": cls.env.company.id,
            "payment_mode": "own_account",
            "total_amount_currency": 18,
            "analytic_distribution": {
                str(cls.analytic_account_1.id): 100.0,
            },
            "analytic_context_source": "product",
        })
        cls.receipt_attachment = cls.env["ir.attachment"].sudo().create({
            "name": "browser-receipt.pdf",
            "type": "binary",
            "raw": b"browser receipt evidence",
            "res_model": "hr.expense",
            "res_id": cls.receipt_expense.id,
        })
        cls.receipt_expense.sudo().message_main_attachment_id = (
            cls.receipt_attachment
        )
        cls.exception_expense = cls.env["hr.expense"].with_user(
            cls.expense_user_employee,
        ).create({
            "name": "Browser Canada executive exception",
            "date": fields.Date.from_string("2026-07-12"),
            "employee_id": cls.expense_employee.id,
            "product_id": cls.product_c.id,
            "company_id": cls.env.company.id,
            "payment_mode": "own_account",
            "total_amount_currency": 30,
            "analytic_distribution": {
                str(cls.analytic_account_2.id): 100.0,
            },
            "analytic_context_source": "explicit",
            "expense_batch_id": cls.batch.id,
        })
        cls.lifecycle_expenses = cls.env["hr.expense"].with_user(
            cls.expense_user_employee,
        ).create([
            {
                "name": "Browser lifecycle hotel",
                "date": fields.Date.from_string("2026-08-10"),
                "employee_id": cls.expense_employee.id,
                "product_id": cls.product_c.id,
                "company_id": cls.env.company.id,
                "payment_mode": "own_account",
                "total_amount_currency": 310,
                "analytic_distribution": {
                    str(cls.analytic_account_1.id): 100.0,
                },
                "analytic_context_source": "product",
            },
            {
                "name": "Browser lifecycle company card",
                "date": fields.Date.from_string("2026-08-11"),
                "employee_id": cls.expense_employee.id,
                "product_id": cls.product_c.id,
                "company_id": cls.env.company.id,
                "payment_mode": "company_account",
                "payment_method_line_id": cls.company_data[
                    "default_journal_bank"
                ].outbound_payment_method_line_ids[0].id,
                "total_amount_currency": 65,
                "analytic_distribution": {
                    str(cls.analytic_account_1.id): 100.0,
                },
                "analytic_context_source": "product",
            },
        ])
        for expense in cls.lifecycle_expenses:
            attachment = cls.env["ir.attachment"].sudo().create({
                "name": f"{expense.name}.pdf",
                "type": "binary",
                "raw": b"browser lifecycle receipt",
                "res_model": "hr.expense",
                "res_id": expense.id,
            })
            expense.sudo().message_main_attachment_id = attachment
        cls.lifecycle_batch = cls.env["usl.expense.batch"].create({
            "name": "Browser role lifecycle",
            "purpose": "Validate submitter, manager and accountant handoffs",
            "context_type": "travel",
            "employee_id": cls.expense_employee.id,
            "company_id": cls.env.company.id,
            "expense_ids": [Command.set(cls.lifecycle_expenses.ids)],
        })
        cls.readonly_reviewer = mail_new_test_user(
            cls.env,
            name="Expense Batch Browser Reviewer",
            login="expense_batch_browser_reviewer",
            groups="base.group_user,account.group_account_readonly",
            company_id=cls.env.company.id,
            company_ids=[Command.set(cls.env.companies.ids)],
        )

    def test_create_or_select_batch_tour(self):
        action = self.env.ref("hr_expense.hr_expense_actions_my_all")
        self.start_tour(
            f"/odoo/action-{action.id}",
            "usl_expense_batch_create_or_select",
            login=self.expense_user_employee.login,
        )

        self.assertEqual(self.expenses.expense_batch_id, self.batch)
        self.assertEqual(
            self.expenses.mapped("analytic_context_source"),
            ["batch", "batch"],
        )
        self.assertEqual(
            self.expenses.mapped("payment_mode"),
            ["own_account", "company_account"],
        )

    def test_native_receipt_capture_tour(self):
        action = self.env.ref("hr_expense.hr_expense_actions_my_all")
        self.start_tour(
            f"/odoo/action-{action.id}",
            "usl_expense_batch_receipt_capture",
            login=self.expense_user_employee.login,
        )

        self.assertEqual(
            self.receipt_expense.sudo().message_main_attachment_id,
            self.receipt_attachment,
        )
        self.assertEqual(self.receipt_expense.payment_mode, "own_account")

    def test_focused_batch_review_tour(self):
        action = self.env.ref("usl_expense_batch.action_expense_batches")
        self.start_tour(
            f"/odoo/action-{action.id}/{self.batch.id}",
            "usl_expense_batch_focused_review",
            login=self.expense_user_employee.login,
        )

    def _check_saving_analytics_with_a_pending_lookup(self, lang):
        self.env["res.lang"]._activate_lang(lang)
        employee = self.expense_user_employee
        employee.lang = lang
        draft = self.expenses[0]
        approved = self.receipt_expense
        approved.with_user(employee).action_submit()
        approved.with_user(self.expense_user_manager).action_approve()
        batch = self.batch.with_user(employee)
        batch.add_expenses((draft | approved).ids)
        initial_revision = batch.context_revision
        original = batch.analytic_distribution
        exception_distribution = self.exception_expense.analytic_distribution
        revised_account = self.env["account.analytic.account"].create({
            "name": "QA revised allocation",
            "plan_id": self.analytic_plan.id,
        })
        expected = {str(revised_account.id): 100.0}
        analytic_model = type(revised_account)
        web_read = analytic_model.web_read

        def slow_account_lookup(record, specification):
            if record.ids == [revised_account.id] and "root_plan_id" in specification:
                sleep(0.65)
            return web_read(record, specification)

        action = self.env.ref("usl_expense_batch.action_expense_batches")
        url = f"/odoo/action-{action.id}/{batch.id}"
        with patch.object(analytic_model, "web_read", slow_account_lookup):
            self.start_tour(url, "usl_expense_batch_save_analytics", login=employee.login)

        batch.invalidate_recordset(["analytic_distribution", "context_revision"])
        draft.invalidate_recordset(["analytic_distribution", "batch_context_revision"])
        self.assertEqual(batch.analytic_distribution, expected)
        self.assertEqual(batch.context_revision, initial_revision + 1)
        self.assertEqual(draft.analytic_distribution, original)
        self.assertNotEqual(draft.batch_context_revision, batch.context_revision)

        self.start_tour(url, "usl_expense_batch_reopen_and_apply_analytics", login=employee.login)
        (draft | approved | self.exception_expense).invalidate_recordset()
        self.assertEqual(draft.analytic_distribution, expected)
        self.assertEqual(draft.batch_context_revision, batch.context_revision)
        self.assertEqual(approved.state, "approved")
        self.assertEqual(approved.analytic_distribution, original)
        self.assertEqual(self.exception_expense.analytic_distribution, exception_distribution)

    def test_saving_analytics_after_clicking_outside_the_editor(self):
        self._check_saving_analytics_with_a_pending_lookup("en_US")

    def test_saving_analytics_directly_from_the_batch_form(self):
        self._check_saving_analytics_with_a_pending_lookup("fr_FR")

    def test_submit_approve_and_post_role_handoff_tour(self):
        action = self.env.ref("usl_expense_batch.action_expense_batches")
        url = f"/odoo/action-{action.id}/{self.lifecycle_batch.id}"

        self.start_tour(
            url,
            "usl_expense_batch_submitter_handoff",
            login=self.expense_user_employee.login,
        )
        self.assertEqual(self.lifecycle_batch.expense_progress, "submitted")

        self.start_tour(
            url,
            "usl_expense_batch_manager_handoff",
            login=self.expense_user_manager.login,
        )
        self.assertEqual(self.lifecycle_batch.expense_progress, "approved")

        self.start_tour(
            url,
            "usl_expense_batch_accountant_handoff",
            login=self.env.user.login,
        )
        self.assertEqual(self.lifecycle_batch.expense_progress, "posted")
        self.assertEqual(
            self.lifecycle_expenses.sudo().mapped(
                "account_move_id.expense_batch_id",
            ),
            self.lifecycle_batch,
        )

    def test_readonly_accountant_audit_tour(self):
        action = self.env.ref("usl_expense_batch.action_expense_batches")
        self.start_tour(
            f"/odoo/action-{action.id}/{self.lifecycle_batch.id}",
            "usl_expense_batch_readonly_audit",
            login=self.readonly_reviewer.login,
        )
