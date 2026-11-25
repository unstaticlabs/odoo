from odoo import Command
from odoo.tests import HttpCase, new_test_user, tagged


@tagged("post_install", "-at_install")
class TestAgentInvitationLabels(HttpCase):
    def test_pending_user_keeps_resend_and_recipient(self):
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
        self.assertEqual(pending[agent.id], agent.login)
        self.assertEqual(pending[person.id], person.login)
        self.assertEqual(data["usl_pending_agent_names"][str(agent.id)], agent.name)
        self.assertNotIn(str(person.id), data["usl_pending_agent_names"])
