from odoo import Command
from odoo.tests import TransactionCase, tagged


@tagged("usl_accounting", "post_install", "-at_install")
class TestAccountResequenceAccess(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.manager_group = cls.env.ref("account.group_account_manager")
        cls.accountant_group = cls.env.ref("account.group_account_user")
        cls.resequence_action = cls.env.ref("account.action_account_resequence")

    def _user(self, login, group):
        return self.env["res.users"].create({
            "name": login,
            "login": login,
            "group_ids": [Command.set([group.id])],
        })

    @staticmethod
    def _bound_action_ids(bindings):
        return {
            action["id"]
            for action in bindings.get("action", [])
        }

    def test_native_resequence_action_is_bound_to_accounting_managers(self):
        self.assertEqual(self.resequence_action.group_ids, self.manager_group)
        self.assertEqual(self.resequence_action.binding_view_types, "list,kanban")

        manager = self._user("resequence-manager", self.manager_group)
        accountant = self._user("resequence-accountant", self.accountant_group)

        manager_actions = self._bound_action_ids(
            self.env["ir.actions.actions"]
            .with_user(manager)
            .get_bindings("account.move"),
        )
        accountant_actions = self._bound_action_ids(
            self.env["ir.actions.actions"]
            .with_user(accountant)
            .get_bindings("account.move"),
        )

        self.assertIn(self.resequence_action.id, manager_actions)
        self.assertNotIn(self.resequence_action.id, accountant_actions)
