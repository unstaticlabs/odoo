import datetime

from odoo import SUPERUSER_ID, Command, fields
from odoo.exceptions import AccessError, ValidationError
from odoo.tests import TransactionCase, tagged

from ..exceptions import AgentPolicyAccessError
from ..models.agent import UslAgentCredential, UslAgentKeyWizard, UslAgentTransferWizard


@tagged("post_install", "-at_install", "usl_access_control")
class TestAutonomousAgents(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.other_company = cls.env["res.company"].create({"name": "Agent Other Company"})
        cls.group_user = cls.env.ref("base.group_user")
        cls.group_settings = cls.env.ref("base.group_system")
        cls.group_agent = cls.env.ref("usl_access_control.group_ai_agent")
        cls.group_irreversible = cls.env.ref("usl_access_control.group_irreversible_actions")
        cls.group_distribution_admin = cls.env.ref(
            "usl_access_control.group_distribution_administrator",
        )
        cls.owner = cls._create_user(
            "agent.owner",
            cls.group_settings,
            companies=cls.company | cls.other_company,
        )
        cls.other_user = cls._create_user("agent.other", cls.group_user)
        cls.portal = cls._create_user("agent.portal", cls.env.ref("base.group_portal"), share=True)

    @classmethod
    def _create_user(cls, login, *groups, companies=None, share=False):
        companies = companies or cls.company
        return cls.env["res.users"].with_user(SUPERUSER_ID).with_context(
            no_reset_password=True,
            usl_governed_identity_provisioning=True,
        ).create(
            {
                "name": login,
                "login": login,
                "email": f"{login}@example.test",
                "share": share,
                "company_id": companies[0].id,
                "company_ids": [Command.set(companies.ids)],
                "group_ids": [Command.set([group.id for group in groups])],
                "usl_identity_classification": "portal" if share else "active",
                "usl_pocketid_access": share,
                "usl_pocketid_email_link": share,
            },
        )

    def _create_agent(self):
        return self.env["usl.agent"].with_user(self.owner).create(
            {
                "name": "ChatGPT – Test Agent",
                "purpose": "Exercise the governed MCP identity contract.",
                "owner_id": self.owner.id,
                "company_id": self.company.id,
                "company_ids": [Command.set((self.company | self.other_company).ids)],
                "delegated_group_ids": [Command.set([self.group_settings.id])],
            },
        )

    def test_owner_creates_distinct_noninteractive_settings_agent(self):
        agent = self._create_agent()
        self.assertEqual(agent.owner_id, self.owner)
        self.assertNotEqual(agent.user_id, self.owner)
        self.assertTrue(agent.user_id.usl_is_ai_agent)
        self.assertTrue(agent.user_id.has_group("base.group_system"))
        self.assertFalse(agent.user_id.usl_has_irreversible_actions)
        self.assertFalse(agent.user_id.usl_pocketid_access)
        self.assertEqual(agent.user_id.usl_identity_classification, "agent")
        self.assertEqual(agent.user_id._get_auth_methods(), [])
        self.assertEqual(agent.user_id.company_ids, self.company | self.other_company)

    def test_only_internal_humans_create_owned_agents(self):
        owned = self.env["usl.agent"].with_user(self.other_user).create(
            {
                "name": "Owned basic Agent",
                "purpose": "Prove any active internal human can create an Agent.",
                "company_id": self.company.id,
                "company_ids": [Command.set([self.company.id])],
            },
        )
        self.assertEqual(owned.owner_id, self.other_user)
        with self.assertRaises(AccessError):
            self.env["usl.agent"].with_user(self.other_user).create(
                {
                    "name": "Not owned",
                    "purpose": "Must be rejected.",
                    "owner_id": self.owner.id,
                    "company_id": self.company.id,
                    "company_ids": [Command.set([self.company.id])],
                },
            )
        with self.assertRaises(AccessError):
            self.env["usl.agent"].with_user(self.portal).create(
                {
                    "name": "Portal Agent",
                    "purpose": "Must be rejected.",
                    "owner_id": self.portal.id,
                    "company_id": self.company.id,
                    "company_ids": [Command.set([self.company.id])],
                },
            )
        with self.assertRaises(AccessError):
            self.env["usl.agent"].with_user(owned.user_id).create(
                {
                    "name": "Nested Agent",
                    "purpose": "Agents cannot own Agents.",
                    "company_id": self.company.id,
                    "company_ids": [Command.set([self.company.id])],
                },
            )

    def test_authority_cannot_exceed_owner_or_platform_policy(self):
        with self.assertRaises(ValidationError):
            self.env["usl.agent"].with_user(self.owner).create(
                {
                    "name": "Unsafe Agent",
                    "purpose": "Must be rejected.",
                    "company_id": self.company.id,
                    "company_ids": [Command.set([self.company.id])],
                    "delegated_group_ids": [Command.set([self.group_irreversible.id])],
                },
            )
        with self.assertRaises(ValidationError):
            self.env["usl.agent"].with_user(self.other_user).create(
                {
                    "name": "Escalated Agent",
                    "purpose": "Must be rejected.",
                    "company_id": self.company.id,
                    "company_ids": [Command.set([self.company.id])],
                    "delegated_group_ids": [Command.set([self.group_settings.id])],
                },
            )

    def test_composite_role_cannot_imply_irreversible_access(self):
        owner = self._create_user("agent.distribution.owner", self.group_distribution_admin)
        with self.assertRaises(ValidationError):
            self.env["usl.agent"].with_user(owner).create(
                {
                    "name": "Indirectly unsafe Agent",
                    "purpose": "Must be rejected.",
                    "company_id": self.company.id,
                    "company_ids": [Command.set([self.company.id])],
                    "delegated_group_ids": [Command.set([self.group_distribution_admin.id])],
                },
            )

    def test_agent_cannot_administer_identities_or_irreversible_actions(self):
        agent = self._create_agent()
        with self.assertRaises(AgentPolicyAccessError) as denied:
            self.other_user.with_user(agent.user_id).write({"name": "Escalated"})
        self.assertEqual(denied.exception.context["usl_code"], "agent_irreversible_action_denied")
        with self.assertRaises(AgentPolicyAccessError) as denied:
            self.env["res.groups"].with_user(agent.user_id).create({"name": "Escalation"})
        self.assertEqual(denied.exception.context["usl_code"], "approval_required")
        with self.assertRaises(AgentPolicyAccessError) as denied:
            self.env["ir.model.access"].with_user(agent.user_id).create(
                {
                    "name": "Escalated model access",
                    "model_id": self.env["ir.model"]._get_id("res.users"),
                    "perm_read": True,
                },
            )
        self.assertEqual(denied.exception.context["usl_code"], "approval_required")

    def test_owner_loss_shrinks_and_never_reexpands_agent(self):
        agent = self._create_agent()
        self.owner.with_user(SUPERUSER_ID).with_context(usl_agent_provisioning=True).write(
            {"group_ids": [Command.set([self.group_user.id])]},
        )
        self.assertNotIn(self.group_settings, agent.delegated_group_ids)
        self.assertFalse(agent.user_id.has_group("base.group_system"))
        self.assertTrue(agent.authority_reduced_at)
        with self.assertRaises(AgentPolicyAccessError) as denied:
            self.env["usl.agent"].with_user(agent.user_id).current_identity()
        self.assertEqual(denied.exception.context["usl_code"], "agent_authority_reduced")
        self.owner.with_user(SUPERUSER_ID).with_context(usl_agent_provisioning=True).write(
            {"group_ids": [Command.set([self.group_settings.id])]},
        )
        self.assertNotIn(self.group_settings, agent.delegated_group_ids)
        agent.with_user(self.owner).action_acknowledge_authority_reduction()
        self.assertFalse(agent.authority_reduced_at)

    def test_owner_company_loss_shrinks_agent(self):
        agent = self._create_agent()
        self.owner.with_user(SUPERUSER_ID).with_context(usl_agent_provisioning=True).write(
            {
                "company_id": self.company.id,
                "company_ids": [Command.set([self.company.id])],
            },
        )
        self.assertEqual(agent.company_ids, self.company)
        self.assertEqual(agent.user_id.company_ids, self.company)

    def test_owner_deactivation_suspends_without_automatic_reactivation(self):
        agent = self._create_agent()
        self.owner.with_user(SUPERUSER_ID).with_context(usl_agent_provisioning=True).write({"active": False})
        self.assertEqual(agent.state, "suspended")
        self.assertFalse(agent.user_id.active)
        self.owner.with_user(SUPERUSER_ID).with_context(usl_agent_provisioning=True).write({"active": True})
        self.assertEqual(agent.state, "suspended")
        self.assertFalse(agent.user_id.active)

    def test_administrator_transfer_restricts_to_new_owner(self):
        agent = self._create_agent()
        wizard = self.env["usl.agent.transfer.wizard"].with_user(self.owner).create(
            {"agent_id": agent.id, "new_owner_id": self.other_user.id},
        )
        UslAgentTransferWizard.action_transfer.__wrapped__(wizard)
        self.assertEqual(agent.owner_id, self.other_user)
        self.assertEqual(agent.company_ids, self.company)
        self.assertNotIn(self.group_settings, agent.delegated_group_ids)
        self.assertTrue(agent.authority_reduced_at)

    def _generate_key(self, agent, *, name="MCP", duration="365"):
        wizard = self.env["usl.agent.key.wizard"].with_user(self.owner).create(
            {"agent_id": agent.id, "name": name, "duration": duration},
        )
        action = UslAgentKeyWizard.action_generate.__wrapped__(wizard)
        return action["context"]["default_key"]

    def test_agent_keys_default_to_one_year(self):
        agent = self._create_agent()
        before = fields.Datetime.now()
        self._generate_key(agent)
        credential = agent.credential_ids
        self.assertLessEqual(
            credential.expiration_date,
            before + datetime.timedelta(days=365, minutes=1),
        )
        self.assertGreaterEqual(
            credential.expiration_date,
            before + datetime.timedelta(days=365),
        )

    def test_agent_keys_allow_five_year_limit_and_overlapping_replacement(self):
        agent = self._create_agent()
        key = self._generate_key(agent, duration="1826")
        first = agent.credential_ids
        self.assertEqual(len(first), 1)
        self.assertEqual(first.status, "active")
        self.assertLessEqual(
            first.expiration_date,
            fields.Datetime.now() + datetime.timedelta(days=1826, minutes=1),
        )
        uid = self.env["res.users.apikeys"]._check_credentials(scope="rpc", key=key)
        self.assertEqual(uid, agent.user_id.id)
        identity = self.env["usl.agent"].with_user(agent.user_id).current_identity()
        self.assertEqual(identity["principal_kind"], "agent")
        self.assertEqual(identity["agent"]["id"], agent.id)

        replacement_action = first.with_user(self.owner).action_create_replacement()
        self.assertEqual(replacement_action["context"]["default_duration"], "365")
        replacement_key = self._generate_key(agent, name="Replacement for MCP")
        self.assertEqual(len(agent.credential_ids.filtered(lambda item: item.status == "active")), 2)
        self.assertEqual(
            self.env["res.users.apikeys"]._check_credentials(scope="rpc", key=replacement_key),
            agent.user_id.id,
        )

        UslAgentCredential.action_revoke.__wrapped__(first.with_user(self.owner))
        self.assertFalse(
            self.env["res.users.apikeys"]._check_credentials(scope="rpc", key=key),
        )
        self.assertEqual(
            self.env["res.users.apikeys"]._check_credentials(scope="rpc", key=replacement_key),
            agent.user_id.id,
        )

    def test_expired_agent_key_is_rejected(self):
        agent = self._create_agent()
        key = self._generate_key(agent)
        credential = agent.credential_ids
        expired_at = fields.Datetime.now() - datetime.timedelta(minutes=1)
        self.env.cr.execute(
            "UPDATE res_users_apikeys SET expiration_date = %s WHERE id = %s",
            [expired_at, credential.native_key_id],
        )
        credential.sudo().with_context(usl_agent_credential_internal=True).write(
            {"expiration_date": expired_at},
        )
        self.assertFalse(
            self.env["res.users.apikeys"]._check_credentials(scope="rpc", key=key),
        )

    def test_needs_attention_search_includes_authority_reduction(self):
        agent = self._create_agent()
        self._generate_key(agent)
        self.assertNotIn(agent, self.env["usl.agent"].search([("needs_attention", "=", True)]))
        agent.sudo().with_context(usl_agent_internal=True).write(
            {
                "authority_reduced_at": fields.Datetime.now(),
                "authority_reduction_reason": "Test reduction",
            },
        )
        self.assertIn(agent, self.env["usl.agent"].search([("needs_attention", "=", True)]))

    def test_human_key_is_rejected_by_agent_identity_contract(self):
        with self.assertRaises(AgentPolicyAccessError) as denied:
            self.env["usl.agent"].with_user(self.owner).current_identity()
        self.assertEqual(denied.exception.context["usl_code"], "agent_principal_required")

    def test_custom_key_expiry_cannot_exceed_five_years(self):
        agent = self._create_agent()
        with self.assertRaises(ValidationError):
            self.env["usl.agent.key.wizard"].with_user(self.owner).create(
                {
                    "agent_id": agent.id,
                    "name": "Too long",
                    "duration": "custom",
                    "expiration_date": fields.Datetime.now() + datetime.timedelta(days=1827),
                },
            )
