from odoo import Command, tools
from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install", "usl_access_control")
class TestAgentKeepsSettingsAcrossQueueJobAcl(TransactionCase):
    """An ACL granted to Settings must not reduce an Agent delegated Settings.

    Granting through an implication on ``base.group_system`` re-ran Agent
    reconciliation and deleted ``res_groups_users_rel`` rows; an ACL line does
    neither.
    """

    def _group_rows(self, user):
        self.env.flush_all()
        self.env.cr.execute(
            "SELECT gid FROM res_groups_users_rel WHERE uid = %s ORDER BY gid",
            [user.id],
        )
        return [row[0] for row in self.env.cr.fetchall()]

    def test_agent_delegated_settings_survives_acl_load_and_reconciliation(self):
        system = self.env.ref("base.group_system")
        owner = self.env["res.users"].with_context(
            no_reset_password=True, usl_governed_identity_provisioning=True,
        ).create(
            {
                "name": "queue acl owner",
                "login": "queue.acl.owner",
                "email": "queue.acl.owner@example.test",
                "group_ids": [Command.set([system.id])],
            },
        )
        agent = self.env["usl.agent"].with_user(owner).create(
            {
                "name": "Queue ACL Agent",
                "purpose": "Prove Settings delegation survives an ACL load.",
                "owner_id": owner.id,
                "company_id": self.env.company.id,
                "company_ids": [Command.set(self.env.company.ids)],
                "delegated_group_ids": [Command.set([system.id])],
                "access_mode": "read_write",
            },
        )
        user = agent.user_id
        approved = sorted(agent.approved_effective_group_ids.ids)
        rows = self._group_rows(user)
        self.assertIn(system.id, rows)

        # Load the module's real ACL file again, then reconcile every Agent.
        tools.convert_file(
            self.env, "usl_accounting", "security/ir.model.access.csv",
            {}, "init", False, "data",
        )
        self.env["res.users"]._usl_validate_all_agent_capabilities()
        self.env["usl.agent"]._reconcile_all()

        self.assertIn(system, user.all_group_ids)
        self.assertEqual(sorted(agent.approved_effective_group_ids.ids), approved)
        self.assertEqual(agent.delegated_group_ids, system)
        self.assertEqual(self._group_rows(user), rows)
