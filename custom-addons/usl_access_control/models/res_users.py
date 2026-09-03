from odoo import _, api, fields, models
from odoo.exceptions import AccessDenied, AccessError, ValidationError
from odoo.fields import Domain


class ResUsers(models.Model):
    _inherit = "res.users"

    usl_access_summary = fields.Char(
        string="Effective Distribution access",
        compute="_compute_usl_access_summary",
    )
    usl_is_ai_agent = fields.Boolean(
        string="AI Agent",
        compute="_compute_usl_access_summary",
        search="_search_usl_is_ai_agent",
    )
    usl_has_irreversible_actions = fields.Boolean(
        string="Irreversible Actions",
        compute="_compute_usl_access_summary",
    )
    usl_identity_classification = fields.Selection(
        selection_add=[("agent", "Autonomous Agent")],
        ondelete={"agent": "set null"},
    )
    usl_owned_agent_ids = fields.One2many(
        "usl.agent",
        "owner_id",
        string="Agents",
    )
    usl_owned_agent_count = fields.Integer(compute="_compute_usl_owned_agent_count")
    usl_managed_agent_id = fields.Many2one(
        "usl.agent",
        string="Managed Agent identity",
        compute="_compute_usl_managed_agent_id",
        compute_sudo=True,
    )

    @api.depends("usl_owned_agent_ids")
    def _compute_usl_owned_agent_count(self):
        for user in self:
            user.usl_owned_agent_count = len(user.usl_owned_agent_ids)

    def _compute_usl_managed_agent_id(self):
        by_user = {
            agent.user_id.id: agent
            for agent in self.env["usl.agent"].sudo().with_context(active_test=False).search(
                [("user_id", "in", self.ids)],
            )
        }
        for user in self:
            user.usl_managed_agent_id = by_user.get(user.id)

    @api.depends("all_group_ids")
    def _compute_usl_access_summary(self):
        role_xmlids = (
            ("usl_access_control.group_distribution_administrator", _("Full Product Administrator")),
            ("usl_access_control.group_technical_administrator", _("Technical Administrator")),
            ("usl_access_control.group_accounting_reviewer", _("Accounting Reviewer")),
        )
        agent_group = self.env.ref(
            "usl_access_control.group_ai_agent",
            raise_if_not_found=False,
        )
        irreversible_group = self.env.ref(
            "usl_access_control.group_irreversible_actions",
            raise_if_not_found=False,
        )
        resolved_roles = [
            (self.env.ref(xmlid, raise_if_not_found=False), label)
            for xmlid, label in role_xmlids
        ]
        for user in self:
            user.usl_is_ai_agent = bool(agent_group and agent_group in user.all_group_ids)
            user.usl_has_irreversible_actions = bool(
                irreversible_group and irreversible_group in user.all_group_ids,
            )
            labels = [label for group, label in resolved_roles if group and group in user.all_group_ids]
            if user.usl_is_ai_agent:
                labels.append(_("AI Agent"))
            if user.usl_has_irreversible_actions:
                labels.append(_("Irreversible Actions"))
            user.usl_access_summary = ", ".join(labels) or _("Application groups only")

    @api.model
    def _search_usl_is_ai_agent(self, operator, value):
        if operator not in ("=", "!=") or not isinstance(value, bool):
            raise NotImplementedError()
        group = self.env.ref("usl_access_control.group_ai_agent", raise_if_not_found=False)
        agent_user_ids = self.sudo().search(
            [("all_group_ids", "in", group.id)],
        ).ids if group else []
        return [("id", "in" if (operator == "=") == value else "not in", agent_user_ids)]

    @api.constrains("group_ids")
    def _check_usl_agent_irreversible_incompatibility(self):
        agent_group = self.env.ref(
            "usl_access_control.group_ai_agent",
            raise_if_not_found=False,
        )
        irreversible_group = self.env.ref(
            "usl_access_control.group_irreversible_actions",
            raise_if_not_found=False,
        )
        if not agent_group or not irreversible_group:
            return
        conflicts = self.filtered(
            lambda user: agent_group in user.all_group_ids
            and irreversible_group in user.all_group_ids,
        )
        if conflicts:
            raise ValidationError(
                _(
                    "AI Agent and Irreversible Actions are incompatible. "
                    "Remove the destructive capability or the Agent identity.",
                ),
            )

    @api.model
    def _usl_validate_all_agent_capabilities(self):
        users = self.sudo().with_context(active_test=False).search([])
        users._check_usl_agent_irreversible_incompatibility()
        self.env["usl.agent"]._reconcile_all()

    @api.model
    def _usl_pocketid_policy_exempt_users(self):
        users = super()._usl_pocketid_policy_exempt_users()
        agent_group = self.env.ref("usl_access_control.group_ai_agent", raise_if_not_found=False)
        if agent_group:
            users |= self.sudo().with_context(active_test=False).search(
                [("all_group_ids", "in", agent_group.id)],
            )
        return users

    def action_open_owned_agents(self):
        self.ensure_one()
        if self != self.env.user and not self.env.user.has_group("base.group_system"):
            raise AccessError(_("You can view only your own Agents."))
        action = self.env["ir.actions.actions"]._for_xml_id("usl_access_control.action_usl_agent")
        action["domain"] = [("owner_id", "=", self.id)]
        action["context"] = {"default_owner_id": self.id}
        return action

    def _check_credentials(self, credential, env):
        self.ensure_one()
        if self.usl_is_ai_agent and env.get("interactive", True):
            raise AccessDenied()
        return super()._check_credentials(credential, env)

    def _get_auth_methods(self):
        self.ensure_one()
        if self.usl_is_ai_agent:
            return []
        return super()._get_auth_methods()

    @api.depends("log_ids", "usl_identity_classification")
    def _compute_state(self):
        """Show governed Agents as active identities, never pending invitations."""
        super()._compute_state()
        for user in self:
            if user.usl_identity_classification == "agent":
                user.state = "active"

    def _search_state(self, operator, value):
        values = tuple(value)
        if operator != "in" or len(values) != 1:
            return super()._search_state(operator, value)
        if values[0] == "active":
            return Domain.OR(
                [
                    Domain("log_ids", "!=", False),
                    Domain("usl_identity_classification", "=", "agent"),
                ],
            )
        if values[0] == "new":
            return Domain.AND(
                [
                    Domain("log_ids", "=", False),
                    Domain("usl_identity_classification", "!=", "agent"),
                ],
            )
        return super()._search_state(operator, value)

    def _reject_agent_password_lifecycle(self):
        if self.filtered(lambda user: user.usl_identity_classification == "agent"):
            raise AccessError(
                _("Agent identities use governed API keys and cannot receive invitations or passwords."),
            )

    def _action_reset_password(self, signup_type="reset"):
        self._reject_agent_password_lifecycle()
        return super()._action_reset_password(signup_type=signup_type)

    def get_reset_password_link(self):
        self._reject_agent_password_lifecycle()
        return super().get_reset_password_link()

    def _generate_onboarding_todo(self):
        """Keep non-interactive Agent identities out of human onboarding."""
        human_users = self.filtered(
            lambda user: user.usl_identity_classification != "agent",
        )
        return super(ResUsers, human_users)._generate_onboarding_todo()

    @api.model
    def _usl_pocketid_profile_definitions(self):
        definitions = super()._usl_pocketid_profile_definitions()

        def extend_groups(profile, *groups):
            definition = definitions[profile]
            definitions[profile] = {
                **definition,
                "groups": tuple(
                    dict.fromkeys(tuple(definition.get("groups") or ()) + groups),
                ),
            }

        extend_groups(
            "administrator",
            "usl_access_control.group_distribution_administrator",
            "usl_access_control.group_irreversible_actions",
            "base.group_system",
        )
        definitions["product_administrator"] = {
            "classification": "active",
            "active": True,
            "groups": (
                "usl_access_control.group_distribution_administrator",
                "base.group_system",
            ),
            "pocketid": True,
        }
        extend_groups(
            "break_glass",
            "usl_access_control.group_distribution_administrator",
            "usl_access_control.group_irreversible_actions",
            "base.group_system",
        )
        definitions["technical_operator"] = {
            "classification": "active",
            "active": True,
            "groups": ("usl_access_control.group_technical_administrator",),
            "pocketid": True,
        }
        extend_groups(
            "accountant_reviewer",
            "usl_access_control.group_accounting_reviewer",
        )
        return definitions

    @api.model_create_multi
    def create(self, vals_list):
        creating_agent = any(
            values.get("usl_identity_classification") == "agent"
            for values in vals_list
        )
        if creating_agent and not self.env.context.get("usl_agent_provisioning"):
            raise AccessError(_("Agent identities must be created from My Agents."))
        self._usl_require_irreversible_action(
            "authorization.user.create",
            "create a user identity",
        )
        users = super().create(vals_list)
        users._check_usl_agent_irreversible_incompatibility()
        return users

    def write(self, values):
        if self.filtered("usl_managed_agent_id") and not self.env.context.get("usl_agent_provisioning"):
            raise AccessError(_("Manage Agent identities from the Agent record."))
        sensitive_fields = {
            "active",
            "company_id",
            "company_ids",
            "group_ids",
            "login",
            "share",
            "usl_identity_classification",
            "usl_local_break_glass",
            "usl_pocketid_access",
            "usl_pocketid_email_link",
        }
        changing_another_identity = self != self.env.user and {"email", "name"} & set(values)
        if sensitive_fields & set(values) or changing_another_identity:
            self._usl_require_irreversible_action(
                "authorization.user.change",
                "change user identity or authorization",
            )
        result = super().write(values)
        if "group_ids" in values:
            self._check_usl_agent_irreversible_incompatibility()
        if {"active", "company_id", "company_ids", "group_ids"} & set(values):
            self.env["usl.agent"]._reconcile_for_owners(self)
        return result

    def unlink(self):
        if self.filtered("usl_managed_agent_id"):
            raise AccessError(_("Suspend Agent identities instead of deleting them."))
        self._usl_require_irreversible_action(
            "authorization.user.delete",
            "delete a user identity",
        )
        return super().unlink()


class ResGroups(models.Model):
    _inherit = "res.groups"

    def write(self, values):
        result = super().write(values)
        if {"implied_ids", "user_ids"} & set(values):
            self.env["res.users"]._usl_validate_all_agent_capabilities()
        return result
