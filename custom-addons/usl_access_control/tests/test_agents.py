import datetime

from lxml import etree

from odoo import SUPERUSER_ID, Command, api, fields
from odoo.exceptions import AccessError, ValidationError
from odoo.tests import TransactionCase, tagged

from ..exceptions import AgentAuthenticationError, AgentPolicyAccessError
from ..controllers.json2 import UslAgentJson2Controller
from ..models.agent import (
    UslAgentCredential,
    UslAgentKeyWizard,
    UslAgentTransferWizard,
    _agent_key_path_allowed,
)
from ..models.agent_secrets import is_agent_secret_field, sanitize_agent_payload


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
            cls.env.ref("account.group_account_manager"),
            cls.env.ref("base.group_partner_manager"),
            cls.env.ref("hr.group_hr_manager"),
            cls.env.ref("hr_expense.group_hr_expense_manager"),
            cls.env.ref("project.group_project_manager"),
            cls.env.ref("purchase.group_purchase_manager"),
            cls.env.ref("sales_team.group_sale_manager"),
            cls.env.ref("stock.group_stock_manager"),
            cls.env.ref("usl_b2c.group_b2c_manager"),
            cls.env.ref("usl_document_templates.group_document_letter_manager"),
            cls.env.ref("usl_documents.group_documents_manager"),
            cls.env.ref("usl_platform_billing.group_platform_billing_manager"),
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
                "access_mode": "read_write",
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

    def test_agent_creation_does_not_create_human_onboarding_task(self):
        agent = self._create_agent()
        onboarding_tasks = self.env["project.task"].with_user(SUPERUSER_ID).search(
            [("user_ids", "in", agent.user_id.ids), ("name", "ilike", "Welcome")],
        )
        self.assertFalse(onboarding_tasks)

    def test_agent_identity_is_confirmed_without_invitation_or_fake_login(self):
        agent = self._create_agent()
        user = agent.user_id.with_context(active_test=False)

        self.assertEqual(user.state, "active")
        self.assertFalse(user.log_ids)
        self.assertFalse(user.partner_id.signup_type)
        self.assertNotIn(user, self.env["res.users"].search([("state", "in", ["new"])]))
        self.assertIn(user, self.env["res.users"].search([("state", "in", ["active"])]))
        with self.assertRaises(AccessError):
            user._action_reset_password(signup_type="signup")
        with self.assertRaises(AccessError):
            user.get_reset_password_link()

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

    def test_bulk_read_access_uses_every_safe_owner_group_with_runtime_readonly_mode(self):
        agent = self._create_agent()
        expected = agent._owner_delegable_groups(self.owner.all_group_ids)

        result = agent.with_user(self.owner).action_grant_all_read()

        self.assertEqual(agent.delegated_group_ids, expected)
        self.assertEqual(agent.access_mode, "read_only")
        self.assertEqual(result["tag"], "display_notification")
        self.assertEqual(result["params"]["type"], "success")
        self.assertIn(str(len(expected)), result["params"]["message"])
        self.assertEqual(result["params"]["next"]["tag"], "soft_reload")
        self.assertIn(self.group_settings, agent.delegated_group_ids)
        self.assertNotIn(self.group_irreversible, agent.user_id.all_group_ids)
        self.assertEqual(agent.company_ids, self.company | self.other_company)

        accounting_privilege = self.env.ref("account.res_groups_privilege_accounting")
        hierarchy = agent.view_group_hierarchy
        privilege = hierarchy["privileges"][str(accounting_privilege.id)]
        selected_group_id = privilege["group_ids"][0]
        self.assertEqual(
            hierarchy["groups"][str(selected_group_id)]["name"],
            "Read-only",
        )

    def test_new_agent_defaults_to_universal_readonly_profile(self):
        agent = self.env["usl.agent"].with_user(self.owner).create(
            {
                "name": "Default read-only Agent",
                "purpose": "Verify safe universal visibility by default.",
                "company_id": self.company.id,
                "company_ids": [Command.set([self.company.id])],
            },
        )
        self.assertEqual(agent.access_mode, "read_only")
        self.assertEqual(
            agent.delegated_group_ids,
            agent._owner_delegable_groups(self.owner.all_group_ids),
        )

    def test_bulk_access_profile_actions_are_visible_on_new_agent_form(self):
        architecture = etree.fromstring(
            self.env.ref("usl_access_control.view_usl_agent_form").arch_db.encode(),
        )
        for action_name in (
            "action_grant_all_read",
            "action_grant_all_read_write",
        ):
            with self.subTest(action_name=action_name):
                buttons = architecture.xpath(
                    f"//button[@name='{action_name}' and @type='object']",
                )
                self.assertEqual(len(buttons), 1)
                self.assertFalse(
                    buttons[0].xpath("ancestor-or-self::*[@invisible='not id']"),
                )

    def test_bulk_read_write_selects_highest_safe_owner_levels_and_settings(self):
        agent = self._create_agent()

        result = agent.with_user(self.owner).action_grant_all_read_write()

        hierarchy = self.env["res.groups"]._get_view_group_hierarchy()
        forbidden = agent._forbidden_delegated_groups()
        for privilege in hierarchy["privileges"].values():
            candidates = self.env["res.groups"].browse(privilege["group_ids"]).filtered(
                lambda group: group in self.owner.all_group_ids
                and not group.all_implied_ids & forbidden,
            )
            if candidates:
                self.assertIn(candidates[-1], agent.delegated_group_ids)
        self.assertIn(self.group_settings, agent.delegated_group_ids)
        self.assertEqual(agent.access_mode, "read_write")
        self.assertNotIn(self.group_irreversible, agent.user_id.all_group_ids)
        self.assertEqual(agent.company_ids, self.company | self.other_company)
        self.assertEqual(result["tag"], "display_notification")
        self.assertEqual(result["params"]["next"]["tag"], "soft_reload")

    def test_bulk_access_is_idempotent_and_owner_only(self):
        agent = self._create_agent()
        agent.with_user(self.owner).action_grant_all_read()
        first_group_ids = agent.delegated_group_ids.ids
        agent.with_user(self.owner).action_grant_all_read()
        self.assertEqual(set(agent.delegated_group_ids.ids), set(first_group_ids))
        with self.assertRaises(AccessError):
            agent.with_user(self.other_user).action_grant_all_read_write()
        with self.assertRaises(AccessError):
            agent.with_user(self.portal).action_grant_all_read_write()
        with self.assertRaises(AccessError):
            agent.with_user(agent.user_id).action_grant_all_read_write()
        with self.assertRaises(ValidationError):
            agent.with_user(self.owner).write({"access_mode": "read_only"})

    def test_new_owner_access_requires_explicit_read_profile_reapplication(self):
        project_manager = self.env.ref("project.group_project_manager")
        if project_manager in self.owner.all_group_ids:
            self.skipTest("Owner already has the project manager group")
        agent = self._create_agent()
        agent.with_user(self.owner).action_grant_all_read()

        self.owner.with_user(SUPERUSER_ID).with_context(
            usl_agent_provisioning=True,
        ).write({"group_ids": [Command.link(project_manager.id)]})

        self.assertNotIn(project_manager, agent.delegated_group_ids)
        self.assertTrue(agent.read_profile_update_available)
        agent.with_user(self.owner).action_grant_all_read()
        self.assertIn(project_manager, agent.delegated_group_ids)
        self.assertFalse(agent.read_profile_update_available)

    def test_read_profile_preserves_chatter_capability_on_readable_records(self):
        agent = self._create_agent()
        agent.with_user(self.owner).action_grant_all_read()
        move = self.env["account.move"].with_user(self.owner).create(
            {"move_type": "entry", "date": fields.Date.today()},
        )
        recipient = self.env["res.partner"].create(
            {"name": "Agent chatter recipient", "email": "agent-chatter@example.test"},
        )

        # Native read-only Chatter access permits users to follow themselves;
        # adding unrelated followers remains a business-record write operation.
        move.with_user(agent.user_id).message_subscribe(
            partner_ids=agent.user_id.partner_id.ids,
        )
        activity = move.with_user(agent.user_id).activity_schedule(
            "mail.mail_activity_data_todo",
            user_id=agent.user_id.id,
            summary="Review Agent finding",
        )
        mail_count = self.env["mail.mail"].sudo().search_count([])
        message = move.with_user(agent.user_id).with_context(
            mail_notify_force_send=False,
        ).message_post(
            body="Agent review note",
            message_type="comment",
            subtype_xmlid="mail.mt_comment",
            partner_ids=recipient.ids,
        )

        self.assertEqual(message.author_id, agent.user_id.partner_id)
        self.assertIn("Agent review note", message.body)
        self.assertEqual(activity.create_uid, agent.user_id)
        self.assertTrue(
            self.env["mail.followers"].sudo().search_count(
                [
                    ("res_model", "=", "account.move"),
                    ("res_id", "=", move.id),
                    ("partner_id", "=", agent.user_id.partner_id.id),
                ],
            ),
        )
        self.assertGreater(self.env["mail.mail"].sudo().search_count([]), mail_count)

    def test_readonly_agent_denies_crud_and_sudo_retaining_agent_actor(self):
        agent = self._create_agent()
        agent.with_user(self.owner).action_grant_all_read()
        partner = self.env["res.partner"].create({"name": "Read-only boundary"})

        self.assertEqual(
            partner.with_user(agent.user_id).read(["name"])[0]["name"],
            "Read-only boundary",
        )
        for operation in (
            lambda: self.env["res.partner"].with_user(agent.user_id).create(
                {"name": "Denied"},
            ),
            lambda: partner.with_user(agent.user_id).write({"name": "Denied"}),
            lambda: partner.with_user(agent.user_id).unlink(),
            lambda: partner.with_user(agent.user_id).sudo().write({"name": "Denied sudo"}),
        ):
            with self.assertRaises(AgentPolicyAccessError) as denied:
                operation()
            self.assertEqual(
                denied.exception.context["usl_code"],
                "agent_read_only_action_denied",
            )

    def test_readonly_profile_exposes_every_owner_application(self):
        agent = self._create_agent()
        agent.with_user(self.owner).action_grant_all_read()

        for model_name in (
            "account.bank.statement",
            "account.move",
            "hr.employee",
            "hr.expense",
            "project.project",
            "purchase.order",
            "res.config.settings",
            "res.partner",
            "sale.order",
            "stock.picking",
            "usl.document",
            "usl.document.letter",
        ):
            with self.subTest(model_name=model_name):
                model = self.env[model_name].with_user(agent.user_id)
                model.check_access("read")
                model.search([], limit=1)

    def test_readonly_scope_uses_owner_private_project_and_assignment_rules(self):
        project_user = self.env.ref("project.group_project_user")
        scoped_owner = self._create_user("agent.scoped.owner", project_user)
        agent = self.env["usl.agent"].with_user(scoped_owner).create(
            {
                "name": "Scoped project Agent",
                "purpose": "Exercise owner-specific project visibility.",
                "company_id": self.company.id,
                "company_ids": [Command.set([self.company.id])],
            },
        )
        followed_project = self.env["project.project"].create(
            {
                "name": "Owner-followed private project",
                "privacy_visibility": "followers",
                "company_id": self.company.id,
            },
        )
        followed_project.message_subscribe(partner_ids=scoped_owner.partner_id.ids)
        hidden_project = self.env["project.project"].create(
            {
                "name": "Unrelated private project",
                "privacy_visibility": "followers",
                "company_id": self.company.id,
            },
        )
        assigned_task = self.env["project.task"].create(
            {
                "name": "Owner-assigned standalone task",
                "user_ids": [Command.set(scoped_owner.ids)],
            },
        )

        visible_projects = self.env["project.project"].with_user(agent.user_id).search(
            [("id", "in", (followed_project | hidden_project).ids)],
        )
        visible_tasks = self.env["project.task"].with_user(agent.user_id).search(
            [("id", "=", assigned_task.id)],
        )

        self.assertEqual(visible_projects, followed_project)
        self.assertEqual(visible_tasks, assigned_task)

    def test_readonly_agent_scope_intersects_selected_companies(self):
        agent = self._create_agent()
        agent.with_user(self.owner).write(
            {
                "company_id": self.company.id,
                "company_ids": [Command.set([self.company.id])],
            },
        )
        agent.with_user(self.owner).action_grant_all_read()
        own_move = self.env["account.move"].with_company(self.company).create(
            {"move_type": "entry", "date": fields.Date.today()},
        )
        other_journal = self.env["account.journal"].with_company(
            self.other_company,
        ).create(
            {
                "name": "Agent other-company operations",
                "code": "AOTH",
                "type": "general",
                "company_id": self.other_company.id,
            },
        )
        other_move = self.env["account.move"].with_company(self.other_company).create(
            {
                "move_type": "entry",
                "date": fields.Date.today(),
                "journal_id": other_journal.id,
            },
        )
        visible = self.env["account.move"].with_user(agent.user_id).search(
            [("id", "in", [own_move.id, other_move.id])],
        )
        self.assertEqual(visible, own_move)
        foreign_context = self.env["account.move"].with_user(agent.user_id).with_context(
            allowed_company_ids=[self.other_company.id],
        )
        with self.assertRaisesRegex(AccessError, "unauthorized or invalid companies"):
            foreign_context.search([("id", "in", [own_move.id, other_move.id])])

    def test_agent_secret_fields_and_nested_payloads_are_redacted(self):
        self.assertTrue(is_agent_secret_field("smtp_password"))
        self.assertTrue(is_agent_secret_field("access_token"))
        self.assertTrue(is_agent_secret_field("invitation_token_sha256"))
        self.assertTrue(is_agent_secret_field("server_id/smtp_password"))
        self.assertTrue(is_agent_secret_field("provider.client_secret"))
        self.assertTrue(
            is_agent_secret_field(
                "private_key_id",
                model_name="account_edi_proxy_client.user",
            ),
        )
        self.assertTrue(
            is_agent_secret_field("content", model_name="certificate.certificate"),
        )
        self.assertFalse(is_agent_secret_field("content", model_name="usl.document"))
        self.assertFalse(is_agent_secret_field("token_expiration_date"))
        self.assertEqual(
            sanitize_agent_payload(
                {
                    "configured": True,
                    "smtp_password": "never-return",
                    "invitation_token_sha256": "never-return",
                    "nested": {"access_token": "never-return", "status": "ready"},
                },
            ),
            {"configured": True, "nested": {"status": "ready"}},
        )
        self.assertEqual(
            sanitize_agent_payload(
                {
                    "usl_document_renderer_private_key_path": "/run/secret/key.pem",
                    "usl_document_renderer_status": "healthy",
                },
                model_name="res.config.settings",
            ),
            {"usl_document_renderer_status": "healthy"},
        )
        agent = self._create_agent()
        agent.with_user(self.owner).action_grant_all_read()
        mail_server_fields = self.env["ir.mail_server"].with_user(
            agent.user_id,
        ).fields_get()
        self.assertNotIn("smtp_pass", mail_server_fields)
        self.assertIn("smtp_host", mail_server_fields)

    def test_agent_key_transport_is_json2_and_api_documentation_only(self):
        for path in (
            "/json/2/res.partner/search_read",
            "/doc-bearer/index.json",
            "/doc-bearer/res.partner.json",
        ):
            self.assertTrue(_agent_key_path_allowed(path))
        for path in (
            "/xmlrpc/2/object",
            "/jsonrpc",
            "/web/session/authenticate",
            "/mail/plugin/authenticate",
        ):
            self.assertFalse(_agent_key_path_allowed(path))

    def test_readonly_json2_policy_denies_unknown_mutations_and_secrets(self):
        UslAgentJson2Controller._check_readonly_agent_call(
            model_name="res.partner",
            method_name="search_read",
            kwargs={"fields": ["name", "email"]},
        )
        for model_name, method_name, kwargs in (
            ("res.partner", "write", {}),
            ("ir.config_parameter", "search_read", {}),
            ("res.config.settings", "read", {"fields": ["smtp_password"]}),
            (
                "res.config.settings",
                "read",
                {"fields": ["usl_document_renderer_private_key_path"]},
            ),
        ):
            with self.assertRaises(AgentPolicyAccessError) as denied:
                UslAgentJson2Controller._check_readonly_agent_call(
                    model_name=model_name,
                    method_name=method_name,
                    kwargs=kwargs,
                )
            self.assertEqual(
                denied.exception.context["usl_code"],
                "agent_read_only_action_denied",
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

    def test_new_owner_access_requires_read_profile_reapplication(self):
        owner = self._create_user("agent.profile.reapply.owner", self.group_user)
        agent = self.env["usl.agent"].with_user(owner).create(
            {
                "name": "Profile reapplication Agent",
                "purpose": "Prove new owner access does not expand automatically.",
                "company_id": self.company.id,
                "company_ids": [Command.set([self.company.id])],
            },
        )
        settings = self.env["res.config.settings"].with_user(agent.user_id)
        self.assertFalse(settings.has_access("read"))

        owner.with_user(SUPERUSER_ID).with_context(usl_agent_provisioning=True).write(
            {"group_ids": [Command.set([self.group_settings.id])]},
        )

        self.assertNotIn(self.group_settings, agent.delegated_group_ids)
        self.assertTrue(agent.read_profile_update_available)
        self.assertFalse(settings.has_access("read"))

        agent.with_user(owner).action_grant_all_read()
        self.assertIn(self.group_settings, agent.delegated_group_ids)
        self.assertTrue(settings.has_access("read"))

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

    def test_agent_key_authenticates_before_request_user_environment_exists(self):
        agent = self._create_agent()
        key = self._generate_key(agent)
        transaction = self.env.cr.transaction
        previous_default_env = transaction.default_env
        try:
            transaction.default_env = None
            authentication_env = api.Environment(self.env.cr, None, {})
            uid = authentication_env["res.users.apikeys"]._check_credentials(
                scope="rpc",
                key=key,
            )
        finally:
            transaction.default_env = previous_default_env
        self.assertEqual(uid, agent.user_id.id)

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

    def test_suspended_agent_key_returns_stable_authentication_error(self):
        agent = self._create_agent()
        key = self._generate_key(agent)
        agent.sudo().with_context(usl_agent_internal=True).write({"state": "suspended"})
        agent._sync_backing_user()
        with self.assertRaises(AgentAuthenticationError) as denied:
            self.env["res.users.apikeys"]._check_credentials(scope="rpc", key=key)
        self.assertEqual(denied.exception.context["usl_code"], "agent_suspended")

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
