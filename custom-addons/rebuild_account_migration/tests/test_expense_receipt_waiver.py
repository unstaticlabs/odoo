from odoo import fields
from odoo.exceptions import AccessError, UserError
from odoo.tests import tagged

from odoo.addons.hr_expense.tests.common import TestExpenseCommon


@tagged("post_install", "-at_install", "rebuild_account_migration_unit")
class TestExpenseReceiptWaiver(TestExpenseCommon):
    """A required receipt blocks the workflow unless a documented decision replaces it."""

    def _expense(self, name="Taxi without a printed ticket"):
        self.product_c.rebuild_receipt_required = True
        expense = self.env["hr.expense"].with_user(self.expense_user_employee).create(
            {
                "name": name,
                "date": fields.Date.from_string("2026-08-20"),
                "employee_id": self.expense_employee.id,
                "product_id": self.product_c.id,
                "company_id": self.env.company.id,
                "payment_mode": "own_account",
                "total_amount_currency": 42.0,
            },
        )
        self.assertEqual(expense.rebuild_receipt_state, "missing")
        self.assertEqual(expense.rebuild_next_step, "receipt")
        return expense

    def _attach_receipt(self, expense):
        attachment = self.env["ir.attachment"].sudo().create(
            {
                "name": f"{expense.name}.pdf",
                "type": "binary",
                "raw": b"receipt",
                "res_model": "hr.expense",
                "res_id": expense.id,
            },
        )
        expense.sudo().message_main_attachment_id = attachment
        expense.invalidate_recordset(["message_main_attachment_id"])

    def test_missing_receipt_blocks_the_workflow_and_points_to_the_decision(self):
        expense = self._expense()

        with self.assertRaisesRegex(UserError, "confirm continuing without one"):
            expense.action_submit()
        self.assertEqual(expense.state, "draft")

    def test_confirming_requires_a_written_reason_and_a_missing_receipt(self):
        expense = self._expense()

        with self.assertRaisesRegex(UserError, "Explain why no receipt"):
            expense.action_rebuild_waive_receipt()
        expense.rebuild_receipt_waiver_reason = "   "
        with self.assertRaisesRegex(UserError, "Explain why no receipt"):
            expense.action_rebuild_waive_receipt()
        self.assertFalse(expense.rebuild_receipt_waived_at)

        self._attach_receipt(expense)
        expense.rebuild_receipt_waiver_reason = "Lost the paper ticket."
        with self.assertRaisesRegex(UserError, "already has a receipt"):
            expense.action_rebuild_waive_receipt()

    def test_confirming_without_a_category_policy_is_refused(self):
        expense = self._expense()
        self.product_c.rebuild_receipt_required = False
        expense.rebuild_receipt_waiver_reason = "Lost the paper ticket."

        with self.assertRaisesRegex(UserError, "does not require a receipt"):
            expense.action_rebuild_waive_receipt()

    def test_documented_decision_records_who_when_and_why_and_unblocks_posting(self):
        expense = self._expense()
        expense.rebuild_receipt_waiver_reason = "  The taxi issued no ticket and the driver refused to write one.  "
        before = fields.Datetime.now()

        expense.with_user(self.expense_user_employee).action_rebuild_waive_receipt()

        self.assertEqual(expense.rebuild_receipt_state, "waived")
        self.assertEqual(expense.rebuild_next_step, "submit")
        self.assertEqual(expense.rebuild_receipt_waived_by_id, self.expense_user_employee)
        self.assertGreaterEqual(expense.rebuild_receipt_waived_at, before)
        self.assertEqual(
            expense.rebuild_receipt_waiver_reason,
            "The taxi issued no ticket and the driver refused to write one.",
        )
        notes = expense.message_ids.filtered(
            lambda message: "continues without a receipt" in (message.body or ""),
        )
        self.assertEqual(len(notes), 1)
        self.assertIn("driver refused", notes.body)
        self.assertEqual(notes.author_id, self.expense_user_employee.partner_id)

        expense.with_user(self.expense_user_employee).action_submit()
        self.assertEqual(expense.state, "submitted")
        expense.with_user(self.expense_user_manager).action_approve()
        self.assertEqual(expense.state, "approved")
        post_action = expense.with_user(self.env.user).action_post()
        if post_action:
            self.env[post_action["res_model"]].with_context(
                post_action["context"],
            ).browse(post_action["res_id"]).action_post_entry()
        self.assertEqual(expense.state, "posted")
        self.assertTrue(expense.account_move_id)
        self.assertEqual(expense.rebuild_receipt_state, "waived")

    def test_decision_is_searchable_and_no_longer_counts_as_missing_evidence(self):
        waived = self._expense("Waived parking")
        waived.rebuild_receipt_waiver_reason = "Parking meter without a printer."
        waived.action_rebuild_waive_receipt()
        missing = self._expense("Still missing")
        Expense = self.env["hr.expense"]

        self.assertIn(waived, Expense.search([("rebuild_receipt_state", "=", "waived")]))
        self.assertNotIn(missing, Expense.search([("rebuild_receipt_state", "=", "waived")]))
        self.assertIn(missing, Expense.search([("rebuild_receipt_state", "=", "missing")]))
        self.assertNotIn(waived, Expense.search([("rebuild_receipt_state", "=", "missing")]))
        self.assertIn(waived, Expense.search([("rebuild_receipt_state", "!=", "missing")]))

        overview = self.env["rebuild.account.overview"].search(
            [("company_id", "=", self.env.company.id)],
        )[:1]
        self.assertTrue(overview)
        domain = overview._missing_expense_attachments_domain()
        counted = Expense.search([("company_id", "=", self.env.company.id), *domain])
        self.assertIn(missing, counted)
        self.assertNotIn(waived, counted)
        overview.invalidate_recordset(["missing_expense_attachment_count"])
        self.assertEqual(overview.missing_expense_attachment_count, len(counted))

        self.assertEqual(waived.batch_attachment_status, "not_required")
        self.assertNotIn("receipt", waived.batch_incomplete_reason or "")
        self.assertEqual(missing.batch_attachment_status, "missing")

    def test_an_ai_agent_cannot_record_the_decision(self):
        if "usl.agent" not in self.env:
            self.skipTest("Agent governance is not installed")
        expense = self._expense()
        expense.rebuild_receipt_waiver_reason = "Provider issues no receipt."
        agent = self.env["usl.agent"].create(
            {
                "name": "Expense assistant",
                "purpose": "Exercise the human-only receipt decision.",
                "owner_id": self.expense_user_manager.id,
                "company_id": self.env.company.id,
                "company_ids": [(6, 0, self.env.company.ids)],
                "delegated_group_ids": [(6, 0, self.env.ref("hr_expense.group_hr_expense_manager").ids)],
                "access_mode": "read_write",
            },
        )

        with self.assertRaisesRegex(AccessError, "a person must record"):
            expense.with_user(agent.user_id).action_rebuild_waive_receipt()
        self.assertFalse(expense.rebuild_receipt_waived_at)

    def test_clearing_the_reason_withdraws_the_decision(self):
        expense = self._expense()
        expense.rebuild_receipt_waiver_reason = "Provider issues no receipt."
        expense.action_rebuild_waive_receipt()
        self.assertEqual(expense.rebuild_receipt_state, "waived")

        expense.rebuild_receipt_waiver_reason = ""

        self.assertFalse(expense.rebuild_receipt_waived_by_id)
        self.assertFalse(expense.rebuild_receipt_waived_at)
        self.assertEqual(expense.rebuild_receipt_state, "missing")
        with self.assertRaisesRegex(UserError, "Attach a receipt"):
            expense.action_submit()

    def test_attaching_a_receipt_supersedes_the_decision_in_presentation(self):
        expense = self._expense()
        expense.rebuild_receipt_waiver_reason = "Provider issues no receipt."
        expense.action_rebuild_waive_receipt()

        self._attach_receipt(expense)

        self.assertEqual(expense.rebuild_receipt_state, "received")
        self.assertTrue(expense.rebuild_receipt_waived_at)
        self.assertIn(
            expense,
            self.env["hr.expense"].search([("rebuild_receipt_state", "=", "received")]),
        )

    def test_copy_does_not_carry_the_decision(self):
        expense = self._expense()
        expense.rebuild_receipt_waiver_reason = "Provider issues no receipt."
        expense.action_rebuild_waive_receipt()

        duplicate = expense.with_user(self.env.user).copy()

        self.assertFalse(duplicate.rebuild_receipt_waiver_reason)
        self.assertFalse(duplicate.rebuild_receipt_waived_at)
        self.assertEqual(duplicate.rebuild_receipt_state, "missing")
