import base64

from odoo import Command, fields
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import tagged

from odoo.addons.hr_expense.tests.common import TestExpenseCommon
from odoo.addons.mail.tests.common import mail_new_test_user


@tagged("post_install", "-at_install", "usl_expense_batch")
class TestExpenseBatch(TestExpenseCommon):
    def _expense(
        self,
        name,
        *,
        amount=42.0,
        employee=None,
        payment_mode="own_account",
        with_receipt=True,
    ):
        employee = employee or self.expense_employee
        expense = self.env["hr.expense"].with_user(
            employee.user_id or self.env.user,
        ).create({
            "name": name,
            "date": fields.Date.from_string("2026-07-10"),
            "employee_id": employee.id,
            "product_id": self.product_c.id,
            "company_id": self.env.company.id,
            "payment_mode": payment_mode,
            "total_amount_currency": amount,
            "analytic_distribution": {str(self.analytic_account_1.id): 100},
        })
        if with_receipt:
            attachment = self.env["ir.attachment"].sudo().create({
                "name": f"{name}.pdf",
                "type": "binary",
                "datas": base64.b64encode(b"expense batch test receipt"),
                "res_model": "hr.expense",
                "res_id": expense.id,
            })
            expense.sudo().message_main_attachment_id = attachment
            expense.invalidate_recordset([
                "message_main_attachment_id",
                "batch_readiness",
                "batch_incomplete_reason",
            ])
        return expense

    def _batch(self, expenses, name="Toronto trip — July 2026"):
        return self.env["usl.expense.batch"].with_user(
            self.expense_user_employee,
        ).create({
            "name": name,
            "purpose": "Customer workshops in Toronto",
            "employee_id": self.expense_employee.id,
            "company_id": self.env.company.id,
            "expense_ids": [Command.set(expenses.ids)],
        })

    def test_readiness_and_preview_wizard_are_deterministic(self):
        complete = self._expense("Toronto hotel")
        incomplete = self._expense("Toronto taxi", with_receipt=False)
        self.assertEqual(complete.batch_readiness, "ready")
        self.assertEqual(incomplete.batch_readiness, "incomplete")
        self.assertIn("receipt", incomplete.batch_incomplete_reason)

        with self.assertRaisesRegex(UserError, "Select at least one draft expense"):
            (
                self.env["hr.expense"]
                .with_user(self.expense_user_employee)
                .action_open_expense_batch_wizard()
            )

        action = (
            self.env["hr.expense"]
            .with_user(self.expense_user_employee)
            .action_open_expense_batch_wizard(complete.ids)
        )
        wizard = self.env[action["res_model"]].browse(action["res_id"])
        self.assertEqual(action["views"], [(False, "form")])
        self.assertEqual(wizard.expense_ids, complete)
        self.assertEqual(wizard.expense_count, 1)
        self.assertEqual(wizard.total_amount, complete.total_amount)
        self.assertEqual(wizard.employee_paid_total, complete.total_amount)
        self.assertEqual(wizard.company_paid_total, 0)
        self.assertEqual(
            wizard.main_analytic_activity,
            self.analytic_account_1.display_name,
        )
        self.assertIn(self.expense_employee.name, wizard.name)
        self.assertTrue(wizard.purpose)

        complete.sudo().approval_state = "approved"
        self.assertFalse(complete.batch_readiness)
        complete.sudo().approval_state = False

        preview = self.env["usl.expense.batch.create.wizard"].create({
            "expense_ids": [Command.set((complete + incomplete).ids)],
        })
        self.assertEqual(preview.incomplete_expense_ids, incomplete)
        self.assertEqual(preview.incomplete_count, 1)

    def test_submit_approve_and_return_one_expense(self):
        first = self._expense("Toronto flight")
        second = self._expense("Toronto meals", amount=43)
        batch = self._batch(first + second)

        batch.with_user(self.expense_user_employee).action_submit()
        self.assertEqual(first.state, "submitted")
        self.assertEqual(second.state, "submitted")
        self.assertEqual(batch.state, "submitted")
        self.assertEqual(batch.submitted_by_id, self.expense_user_employee)

        first.with_user(self.expense_user_manager).action_return_from_batch()
        self.assertFalse(first.expense_batch_id)
        self.assertEqual(first.state, "draft")
        self.assertEqual(second.expense_batch_id, batch)
        self.assertEqual(batch.state, "submitted")

        batch.with_user(self.expense_user_manager).action_approve()
        self.assertEqual(second.state, "approved")
        self.assertEqual(batch.state, "approved")
        self.assertEqual(batch.approved_by_id, self.expense_user_manager)

    def test_submission_blocks_incomplete_lines_without_partial_transition(self):
        complete = self._expense("Toronto conference")
        incomplete = self._expense("Toronto subway", with_receipt=False)
        batch = self._batch(complete + incomplete)

        with self.assertRaisesRegex(UserError, "Complete the following expenses"):
            batch.with_user(self.expense_user_employee).action_submit()
        self.assertEqual(complete.state, "draft")
        self.assertEqual(incomplete.state, "draft")
        self.assertEqual(batch.incomplete_expense_ids, incomplete)

    def test_employee_and_company_are_hard_compatibility_boundaries(self):
        first = self._expense("Toronto outbound")
        other_user = mail_new_test_user(
            self.env,
            name="Other expense employee",
            login="other.expense.employee@example.invalid",
            groups="base.group_user",
            company_id=self.env.company.id,
            company_ids=[Command.set(self.env.companies.ids)],
        )
        other_employee = self.env["hr.employee"].sudo().create({
            "name": other_user.name,
            "user_id": other_user.id,
            "expense_manager_id": self.expense_user_manager.id,
            "company_id": self.env.company.id,
        })
        second = self._expense(
            "Toronto return",
            employee=other_employee,
        )

        with self.assertRaisesRegex(
            ValidationError,
            "same employee",
        ), self.cr.savepoint():
            self.env["usl.expense.batch"].create(
                {
                    "name": "Invalid mixed claim",
                    "purpose": "Must remain separate",
                    "employee_id": self.expense_employee.id,
                    "company_id": self.env.company.id,
                    "expense_ids": [Command.set((first + second).ids)],
                },
            )

    def test_accounting_values_keep_batch_reference_and_expense_lines(self):
        first = self._expense("Toronto lodging", amount=900)
        second = self._expense("Toronto ground transport", amount=120)
        batch = self._batch(first + second)

        move_values = (first + second)._prepare_receipts_vals()
        self.assertEqual(len(move_values), 1)
        self.assertEqual(move_values[0]["expense_batch_id"], batch.id)
        self.assertEqual(move_values[0]["ref"], batch.name)
        self.assertEqual(len(move_values[0]["line_ids"]), 2)
        self.assertEqual(
            set((first + second).ids),
            set((first + second)._expense_ids_from_move_vals(move_values[0])),
        )

    def test_company_paid_values_keep_batch_reference_and_expense_line(self):
        expense = self._expense(
            "Toronto company card",
            amount=215,
            payment_mode="company_account",
        )
        batch = self._batch(expense)

        move_values, payment_values = expense._prepare_payments_vals()
        self.assertEqual(move_values["expense_batch_id"], batch.id)
        self.assertEqual(move_values["ref"], batch.name)
        self.assertEqual(payment_values["memo"], batch.name)
        self.assertTrue(
            any(
                line_values.get("expense_id") == expense.id
                for command, _record_id, line_values in move_values["line_ids"]
                if command == Command.CREATE
            ),
        )

    def test_readonly_accountant_can_review_but_cannot_mutate(self):
        expense = self._expense("Toronto review")
        batch = self._batch(expense)
        reviewer = mail_new_test_user(
            self.env,
            name="Expense batch reviewer",
            login="expense.batch.reviewer@example.invalid",
            groups="base.group_user,account.group_account_readonly",
            company_id=self.env.company.id,
            company_ids=[Command.set(self.env.companies.ids)],
        )
        self.assertEqual(
            self.env["usl.expense.batch"].with_user(reviewer).browse(batch.id).name,
            batch.name,
        )
        with self.assertRaisesRegex(AccessError, "Read-only accountants"):
            batch.with_user(reviewer).write({"purpose": "Forbidden edit"})
        with self.assertRaisesRegex(AccessError, "Read-only accountants"):
            batch.with_user(reviewer).action_submit()

        form = self.env["usl.expense.batch"].with_user(reviewer).get_view(
            self.env.ref("usl_expense_batch.view_expense_batch_form").id,
            "form",
        )
        self.assertIn('edit="false"', form["arch"])
        self.assertNotIn('name="action_submit"', form["arch"])

    def test_views_keep_readiness_out_of_list_and_expose_drill_down(self):
        expense_list = self.env.ref(
            "hr_expense.hr_expense_view_expenses_analysis_tree",
        )._get_combined_arch()
        self.assertFalse(expense_list.xpath("//field[@name='batch_readiness']"))
        self.assertTrue(expense_list.xpath("//field[@name='expense_batch_id']"))

        expense_search = self.env.ref(
            "hr_expense.hr_expense_view_search",
        )._get_combined_arch()
        for filter_name in ("batch_ready", "batch_incomplete", "already_batched"):
            self.assertTrue(
                expense_search.xpath(f"//filter[@name='{filter_name}']"),
            )

        move_form = self.env.ref("account.view_move_form")._get_combined_arch()
        self.assertTrue(
            move_form.xpath("//button[@name='action_open_expense_batch']"),
        )

        batch = self._batch(self._expense("Toronto action contract"))
        self.assertEqual(
            batch.expense_ids.action_open_expense_batch()["views"],
            [(False, "form")],
        )
        self.assertEqual(
            self.env["account.move"].new({
                "expense_batch_id": batch.id,
            }).action_open_expense_batch()["views"],
            [(False, "form")],
        )
