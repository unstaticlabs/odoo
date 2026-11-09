from odoo import Command
from odoo.tests import tagged

from odoo.addons.mail.tests.common import mail_new_test_user
from odoo.addons.usl_docs.tests.journey import JourneyCase

from .test_expense_batch_tour import TestExpenseBatchBrowser

BATCHES = "/odoo/action-usl_expense_batch.action_expense_batches"


@tagged("post_install", "-at_install", "usl_docs_journey")
class TestExpenseBatchJourneys(TestExpenseBatchBrowser, JourneyCase):
    """The how-to guides for Expense Batches, generated from these journeys.

    The fixtures are the browser tour's own: synthetic expenses for a
    synthetic employee. The pages they produce are committed under
    ``docs/users/how-to`` by ``make docs``.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.accountant = mail_new_test_user(
            cls.env,
            name="Expense Batch Journey Accountant",
            login="expense_batch_journey_accountant",
            groups="base.group_user,account.group_account_manager,hr_expense.group_hr_expense_manager",
            company_id=cls.env.company.id,
            company_ids=[Command.set(cls.env.companies.ids)],
        )

    def test_group_expenses_into_a_batch(self):
        # The XML id keeps the page's "Open this screen" link stable across databases.
        self.run_journey(
            "usl_expense_batch_create_or_select",
            "/odoo/action-hr_expense.hr_expense_actions_my_all",
            login=self.expense_user_employee.login,
        )
        self.assertEqual(self.expenses.expense_batch_id, self.batch)

    def test_check_the_receipt_on_an_expense(self):
        self.run_journey(
            "usl_expense_batch_receipt_capture",
            "/odoo/action-hr_expense.hr_expense_actions_my_all",
            login=self.expense_user_employee.login,
        )
        self.assertEqual(self.receipt_expense.sudo().message_main_attachment_id, self.receipt_attachment)

    def test_review_a_batch_before_submitting(self):
        self.run_journey(
            "usl_expense_batch_focused_review",
            f"{BATCHES}/{self.batch.id}",
            login=self.expense_user_employee.login,
            screen=BATCHES,
        )

    def test_post_an_approved_batch(self):
        batch = self.lifecycle_batch
        batch.with_user(self.expense_user_employee).action_submit()
        batch.with_user(self.expense_user_manager).action_approve()
        self.assertEqual(batch.expense_progress, "approved")
        self.run_journey(
            "usl_expense_batch_accountant_handoff",
            f"{BATCHES}/{batch.id}",
            login=self.accountant.login,
            screen=BATCHES,
        )
        self.assertEqual(batch.expense_progress, "posted")
        self.assertEqual(
            self.lifecycle_expenses.sudo().mapped("account_move_id.expense_batch_id"),
            batch,
        )

    def test_audit_a_batch_read_only(self):
        self.run_journey(
            "usl_expense_batch_readonly_audit",
            f"{BATCHES}/{self.lifecycle_batch.id}",
            login=self.readonly_reviewer.login,
            screen=BATCHES,
        )
