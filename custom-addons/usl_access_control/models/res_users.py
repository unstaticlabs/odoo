from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class ResUsers(models.Model):
    _inherit = "res.users"

    usl_access_summary = fields.Char(
        string="Effective Distribution access",
        compute="_compute_usl_access_summary",
    )
    usl_is_ai_agent = fields.Boolean(
        string="AI Agent",
        compute="_compute_usl_access_summary",
    )
    usl_has_irreversible_actions = fields.Boolean(
        string="Irreversible Actions",
        compute="_compute_usl_access_summary",
    )

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

    @api.model
    def _usl_pocketid_profile_definitions(self):
        definitions = super()._usl_pocketid_profile_definitions()
        definitions["administrator"] = {
            **definitions["administrator"],
            "groups": (
                "usl_access_control.group_distribution_administrator",
                "usl_access_control.group_irreversible_actions",
                "base.group_system",
            ),
        }
        definitions["product_administrator"] = {
            "classification": "active",
            "active": True,
            "groups": (
                "usl_access_control.group_distribution_administrator",
                "base.group_system",
            ),
            "pocketid": True,
        }
        definitions["break_glass"] = {
            **definitions["break_glass"],
            "groups": (
                "usl_access_control.group_distribution_administrator",
                "usl_access_control.group_irreversible_actions",
                "base.group_system",
            ),
        }
        definitions["technical_operator"] = {
            "classification": "active",
            "active": True,
            "groups": ("usl_access_control.group_technical_administrator",),
            "pocketid": True,
        }
        definitions["accountant_reviewer"] = {
            **definitions["accountant_reviewer"],
            "groups": ("usl_access_control.group_accounting_reviewer",),
        }
        return definitions

    @api.model_create_multi
    def create(self, vals_list):
        self._usl_require_irreversible_action(
            "authorization.user.create",
            "create a user identity",
        )
        users = super().create(vals_list)
        users._check_usl_agent_irreversible_incompatibility()
        return users

    def write(self, values):
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
        return result

    def unlink(self):
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
