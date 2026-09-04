from odoo import Command, fields
from odoo.tests import tagged

from odoo.addons.hr_expense.tests.common import TestExpenseCommon


@tagged("post_install", "-at_install", "usl_accounting")
class TestExpenseBatchReporting(TestExpenseCommon):
    def test_journal_and_analytic_lines_keep_batch_and_payer_dimensions(self):
        expense = self.env["hr.expense"].create({
            "name": "Canada transport",
            "date": fields.Date.from_string("2026-07-10"),
            "employee_id": self.expense_employee.id,
            "product_id": self.product_c.id,
            "company_id": self.env.company.id,
            "payment_mode": "own_account",
            "total_amount_currency": 125,
            "analytic_distribution": {str(self.analytic_account_1.id): 100},
        })
        receipt = self.env["ir.attachment"].sudo().create({
            "name": "canada-transport-receipt.pdf",
            "raw": b"expense reporting test receipt",
            "mimetype": "application/pdf",
            "res_model": expense._name,
            "res_id": expense.id,
        })
        expense.sudo().message_main_attachment_id = receipt
        expense.invalidate_recordset(["message_main_attachment_id"])
        batch = self.env["usl.expense.batch"].create({
            "name": "SBFH — Canada 2026",
            "purpose": "Canada travel",
            "context_type": "travel",
            "employee_id": self.expense_employee.id,
            "company_id": self.env.company.id,
            "expense_ids": [Command.set(expense.ids)],
        })
        expense.sudo().approval_state = "approved"
        self.post_expenses_with_wizard(expense)

        expense_lines = expense.account_move_id.line_ids.filtered("expense_id")
        self.assertTrue(expense_lines)
        self.assertEqual(expense_lines.expense_batch_id, batch)
        self.assertEqual(
            set(expense_lines.mapped("expense_payment_mode")),
            {"own_account"},
        )
        analytic_lines = expense_lines.analytic_line_ids
        self.assertTrue(analytic_lines)
        self.assertEqual(analytic_lines.expense_batch_id, batch)
        self.assertEqual(
            set(analytic_lines.mapped("expense_payment_mode")),
            {"own_account"},
        )

        move_search = self.env.ref(
            "account.view_account_move_line_filter",
        )._get_combined_arch()
        self.assertTrue(move_search.xpath("//field[@name='expense_batch_id']"))
        self.assertTrue(
            move_search.xpath("//filter[@name='group_by_expense_batch']"),
        )
        analytic_pivot = self.env.ref(
            "analytic.view_account_analytic_line_pivot",
        )._get_combined_arch()
        self.assertTrue(
            analytic_pivot.xpath("//field[@name='expense_batch_id']"),
        )
