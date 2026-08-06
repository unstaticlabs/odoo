from odoo import fields
from odoo.tests import HttpCase, tagged

from odoo.addons.hr_expense.tests.common import TestExpenseCommon


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
