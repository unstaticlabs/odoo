from odoo import SUPERUSER_ID, Command
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install", "usl_access_control")
class TestAgentReceiptDecisions(TransactionCase):
    """An owner may let their Agent record a receipt decision, under conditions."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.expense_manager = cls.env.ref("hr_expense.group_hr_expense_manager")
        cls.owner = cls._create_user("receipt.owner", cls.expense_manager)
        cls.employee = cls.env["hr.employee"].with_user(SUPERUSER_ID).create(
            {
                "name": "Receipt employee",
                "company_id": cls.company.id,
                "user_id": cls.owner.id,
            },
        )
        cls.category = cls.env["product.product"].with_user(SUPERUSER_ID).create(
            {
                "name": "Taxi",
                "type": "service",
                "can_be_expensed": True,
                "standard_price": 0.0,
                "list_price": 0.0,
                "rebuild_receipt_required": True,
            },
        )

    @classmethod
    def _create_user(cls, login, *groups):
        return cls.env["res.users"].with_user(SUPERUSER_ID).with_context(
            no_reset_password=True,
            usl_governed_identity_provisioning=True,
        ).create(
            {
                "name": login,
                "login": login,
                "email": f"{login}@example.test",
                "company_id": cls.company.id,
                "company_ids": [Command.set(cls.company.ids)],
                "group_ids": [Command.set([cls.env.ref("base.group_user").id, *[group.id for group in groups]])],
                "usl_identity_classification": "active",
            },
        )

    def _create_agent(self, delegated_groups=None, access_mode="read_write"):
        delegated_groups = self.expense_manager if delegated_groups is None else delegated_groups
        return self.env["usl.agent"].with_user(self.owner).create(
            {
                "name": "Expense assistant",
                "purpose": "Prepare expenses for the accounting review.",
                "owner_id": self.owner.id,
                "company_id": self.company.id,
                "company_ids": [Command.set(self.company.ids)],
                "delegated_group_ids": [Command.set(delegated_groups.ids)],
                "access_mode": access_mode,
            },
        )

    def _create_expense(self, reason="The taxi issued no ticket."):
        expense = self.env["hr.expense"].with_user(SUPERUSER_ID).create(
            {
                "name": "Taxi without a printed ticket",
                "employee_id": self.employee.id,
                "product_id": self.category.id,
                "company_id": self.company.id,
                "payment_mode": "own_account",
                "total_amount_currency": 42.0,
            },
        )
        expense.rebuild_receipt_waiver_reason = reason
        self.assertEqual(expense.rebuild_receipt_state, "missing")
        return expense

    def test_an_agent_without_the_authority_is_refused_and_told_who_can_grant_it(self):
        agent = self._create_agent()
        expense = self._create_expense()

        with self.assertRaises(AccessError) as denied:
            expense.with_user(agent.user_id).action_rebuild_waive_receipt()

        message = str(denied.exception)
        self.assertIn("a person must record", message)
        self.assertIn(self.owner.name, message)
        self.assertFalse(expense.rebuild_receipt_waived_at)

    def test_the_authority_needs_the_expense_access_it_stands_on(self):
        agent = self._create_agent(
            delegated_groups=self.env.ref("base.group_user"),
            access_mode="read_only",
        )

        with self.assertRaisesRegex(ValidationError, "write access to Expenses"):
            agent.with_user(self.owner).write({"expense_receipt_waiver_authority": True})

        self.assertFalse(agent.expense_receipt_waiver_authority)

    def test_an_agent_cannot_grant_itself_the_authority(self):
        agent = self._create_agent()
        expense = self._create_expense()

        with self.assertRaises(AccessError):
            agent.with_user(agent.user_id).write(
                {"expense_receipt_waiver_authority": True},
            )

        self.assertFalse(agent.expense_receipt_waiver_authority)
        with self.assertRaises(AccessError):
            expense.with_user(agent.user_id).action_rebuild_waive_receipt()

    def test_a_granted_agent_previews_the_decision_and_records_nothing(self):
        agent = self._create_agent()
        agent.with_user(self.owner).write({"expense_receipt_waiver_authority": True})
        expense = self._create_expense()

        preview = expense.with_user(agent.user_id).action_rebuild_waive_receipt()

        self.assertTrue(preview["confirmation_required"])
        self.assertEqual(preview["ids"], expense.ids)
        self.assertEqual(
            preview["confirm_with"]["kwargs"],
            {"agent_confirmed": True},
        )
        self.assertIn("Nothing has been recorded", preview["warning"])
        self.assertIn(self.owner.name, preview["ask_a_person"])
        self.assertEqual(preview["expenses"][0]["reason"], "The taxi issued no ticket.")
        self.assertFalse(expense.rebuild_receipt_waived_at)
        self.assertEqual(expense.rebuild_receipt_state, "missing")

    def test_the_preview_refuses_whatever_the_confirmation_would_refuse(self):
        agent = self._create_agent()
        agent.with_user(self.owner).write({"expense_receipt_waiver_authority": True})
        expense = self._create_expense(reason="   ")

        with self.assertRaisesRegex(UserError, "Explain why no receipt"):
            expense.with_user(agent.user_id).action_rebuild_waive_receipt()

        self.assertFalse(expense.rebuild_receipt_waived_at)

    def test_a_confirmed_decision_names_the_agent_and_the_human_authority(self):
        agent = self._create_agent()
        agent.with_user(self.owner).write({"expense_receipt_waiver_authority": True})
        expense = self._create_expense()

        expense.with_user(agent.user_id).action_rebuild_waive_receipt(
            agent_confirmed=True,
        )

        self.assertEqual(expense.rebuild_receipt_state, "waived")
        self.assertEqual(expense.rebuild_receipt_waived_by_id, agent.user_id)
        self.assertTrue(expense.rebuild_receipt_waived_at)
        note = expense.message_ids.filtered(
            lambda message: "continues without a receipt" in (message.body or ""),
        )
        self.assertEqual(len(note), 1)
        self.assertIn("The taxi issued no ticket.", note.body)
        self.assertIn(agent.name, note.body)
        self.assertIn(self.owner.name, note.body)

    def test_a_person_records_the_decision_in_one_call_without_agent_attribution(self):
        expense = self._create_expense()

        self.assertTrue(
            expense.with_user(self.owner).action_rebuild_waive_receipt(),
        )

        self.assertEqual(expense.rebuild_receipt_waived_by_id, self.owner)
        note = expense.message_ids.filtered(
            lambda message: "continues without a receipt" in (message.body or ""),
        )
        self.assertNotIn("on the receipt-decision authority", note.body)

    def test_withdrawing_the_expense_access_withdraws_the_receipt_authority(self):
        agent = self._create_agent()
        agent.with_user(self.owner).write({"expense_receipt_waiver_authority": True})
        expense = self._create_expense()

        self.owner.with_user(SUPERUSER_ID).write(
            {"group_ids": [Command.unlink(self.expense_manager.id)]},
        )
        self.env["usl.agent"]._reconcile_all()

        self.assertFalse(agent.expense_receipt_waiver_authority)
        self.assertTrue(agent.authority_reduced_at)
        with self.assertRaises(AccessError):
            expense.with_user(agent.user_id).action_rebuild_waive_receipt(
                agent_confirmed=True,
            )
        self.assertFalse(expense.rebuild_receipt_waived_at)
