from odoo import Command, tools
from odoo.tests import TransactionCase, tagged

ACL_XMLID = "usl_accounting.access_queue_job_settings_admin"


@tagged("post_install", "-at_install", "usl_access_control")
class TestAgentKeepsSettingsAcrossQueueJobAcl(TransactionCase):
    """Loading the queue.job ACL line must not reduce an Agent delegated Settings.

    The test follows the upgrade order: an Agent delegated Settings exists, the
    module's real ``ir.model.access.csv`` is then loaded and the Agent
    capabilities are reconciled. It proves that the ACL line leaves the Agent's
    group rows, approved groups and delegation unchanged and gives it read
    access to ``queue.job``. The contrast case proves the test can fail: linking
    the queue job manager group under Settings, the withdrawn design, removes
    Settings from the same Agent.
    """

    def _group_rows(self, user):
        self.env.flush_all()
        self.env.cr.execute(
            "SELECT gid FROM res_groups_users_rel WHERE uid = %s ORDER BY gid",
            [user.id],
        )
        return [row[0] for row in self.env.cr.fetchall()]

    def _snapshot(self, agent):
        return {
            "rows": self._group_rows(agent.user_id),
            "approved": sorted(agent.approved_effective_group_ids.ids),
            "delegated": sorted(agent.delegated_group_ids.ids),
        }

    def _settings_agent(self):
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
        self.assertIn(system.id, self._group_rows(agent.user_id))
        return agent

    def test_agent_delegated_settings_survives_acl_load_and_reconciliation(self):
        system = self.env.ref("base.group_system")
        # Start from a database that predates the grant.
        self.env.ref(ACL_XMLID).unlink()
        self.assertFalse(self.env.ref(ACL_XMLID, raise_if_not_found=False))

        agent = self._settings_agent()
        user = agent.user_id
        before = self._snapshot(agent)

        # The upgrade loads the module's real ACL file, then reconciles Agents.
        tools.convert_file(
            self.env, "usl_accounting", "security/ir.model.access.csv",
            {}, "init", False, "data",
        )
        acl = self.env.ref(ACL_XMLID, raise_if_not_found=False)
        self.assertTrue(acl, "the real CSV must recreate the ACL row")
        self.assertEqual(acl.group_id, system)
        self.env["res.users"]._usl_validate_all_agent_capabilities()

        self.assertEqual(self._snapshot(agent), before)
        self.assertIn(system, user.all_group_ids)
        self.env["queue.job"].with_user(user).check_access("read")

    def test_contrast_implication_on_settings_reconciles_the_agent(self):
        system = self.env.ref("base.group_system")
        manager = self.env.ref("queue_job.group_queue_job_manager")
        agent = self._settings_agent()
        user = agent.user_id
        before = self._snapshot(agent)
        self.assertNotIn(manager, agent.approved_effective_group_ids)

        # The withdrawn design: Settings implies the queue job manager group.
        system.write({"implied_ids": [Command.link(manager.id)]})
        self.env["res.users"]._usl_validate_all_agent_capabilities()

        self.assertNotEqual(self._snapshot(agent), before)
        self.assertNotIn(system, user.all_group_ids)
        self.assertNotIn(system.id, self._group_rows(user))
