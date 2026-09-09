from odoo.tests import tagged

from odoo.addons.usl_docs.tests.journey import JourneyCase

from .test_expense_batch_tour import TestExpenseBatchBrowser


@tagged("post_install", "-at_install", "usl_docs_journey")
class TestExpenseBatchJourneys(TestExpenseBatchBrowser, JourneyCase):
    """The how-to guides for Expense Batches, generated from these journeys.

    The fixtures are the browser tour's own: synthetic expenses for a
    synthetic employee. The pages they produce are committed under
    ``docs/users/how-to`` by ``make docs``.
    """

    def test_group_expenses_into_a_batch(self):
        action = self.env.ref("hr_expense.hr_expense_actions_my_all")
        self.run_journey(
            "usl_expense_batch_create_or_select",
            f"/odoo/action-{action.id}",
            login=self.expense_user_employee.login,
        )
        self.assertEqual(self.expenses.expense_batch_id, self.batch)
