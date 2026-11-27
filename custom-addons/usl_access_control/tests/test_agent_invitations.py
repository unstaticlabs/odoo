from odoo import Command
from odoo.tests import HttpCase, new_test_user, tagged


@tagged("post_install", "-at_install")
class TestAgentInvitationLabels(HttpCase):
    def test_pending_invitations_list_people_not_agents(self):
        manager = new_test_user(self.env, login="qa.invitation.manager", groups="base.group_system")
        person = new_test_user(self.env, login="person@example.test")
        agent = self.env["usl.agent"].with_user(manager).create({
            "name": "Invitation assistant",
            "purpose": "Check pending invitations",
            "owner_id": manager.id,
            "access_mode": "read_write",
            "delegated_group_ids": [Command.set(self.env.ref("base.group_user").ids)],
        }).user_id
        self.authenticate(manager.login, manager.login)
        data = self.make_jsonrpc_request("/base_setup/data", {})
        self.assertTrue(data["resend_invitation"])
        pending = dict(data["pending_users"])
        self.assertEqual(pending[person.id], person.login)
        # The agent will never sign in: it is not an invitation somebody is waiting on.
        self.assertNotIn(agent.id, pending)
        people = self.env["res.users"].search([
            ("share", "=", False), ("log_ids", "=", False), ("usl_identity_classification", "!=", "agent"),
        ])
        self.assertEqual(data["pending_count"], len(people))
        opened = data["action_pending_users"]
        self.assertNotIn(agent.id, opened.get("domain", [[0, 0, [opened.get("res_id")]]])[0][2])
