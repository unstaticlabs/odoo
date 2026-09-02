import datetime
import uuid
from copy import deepcopy

from odoo import SUPERUSER_ID, Command, _, api, fields, models
from odoo.addons.base.models.res_users import KEY_CRYPT_CONTEXT, check_identity
from odoo.exceptions import AccessDenied, AccessError, UserError, ValidationError
from odoo.http import request

from ..exceptions import AgentAuthenticationError, AgentPolicyAccessError


_AGENT_KEY_MAX_DAYS = 5 * 365 + 1

_AGENT_READ_ONLY_GROUP_XMLIDS = (
    "account.group_account_readonly",
    "usl_access_control.group_audit_reader",
    "usl_b2c.group_b2c_reader",
    "usl_b2c.group_b2c_sensitive_evidence",
    "usl_documents.group_documents_accountant",
    "usl_platform_billing.group_platform_billing_reader",
)

_AGENT_FULL_ACCESS_EXTRA_GROUP_XMLIDS = (
    "base.group_system",
)


class UslAgent(models.Model):
    _name = "usl.agent"
    _description = "Autonomous Agent"
    _order = "state, name, id"
    _rec_name = "name"

    name = fields.Char(required=True, index="trigram")
    purpose = fields.Text(required=True)
    owner_id = fields.Many2one(
        "res.users",
        required=True,
        default=lambda self: self.env.user,
        ondelete="restrict",
        index=True,
    )
    user_id = fields.Many2one(
        "res.users",
        string="Agent identity",
        required=True,
        readonly=True,
        copy=False,
        ondelete="restrict",
        index=True,
    )
    state = fields.Selection(
        [("active", "Active"), ("suspended", "Suspended")],
        required=True,
        default="active",
        index=True,
    )
    suspension_reason = fields.Char(readonly=True, copy=False)
    authority_reduced_at = fields.Datetime(readonly=True, copy=False)
    authority_reduction_reason = fields.Char(readonly=True, copy=False)
    company_ids = fields.Many2many(
        "res.company",
        "usl_agent_company_rel",
        "agent_id",
        "company_id",
        string="Companies",
        required=True,
        default=lambda self: self.env.companies,
    )
    company_id = fields.Many2one(
        "res.company",
        string="Default company",
        required=True,
        default=lambda self: self.env.company,
    )
    delegated_group_ids = fields.Many2many(
        "res.groups",
        "usl_agent_delegated_group_rel",
        "agent_id",
        "group_id",
        string="Application access",
    )
    approved_effective_group_ids = fields.Many2many(
        "res.groups",
        "usl_agent_approved_group_rel",
        "agent_id",
        "group_id",
        string="Approved effective access",
        readonly=True,
        copy=False,
    )
    view_group_hierarchy = fields.Json(
        string="Available application access",
        compute="_compute_view_group_hierarchy",
        store=False,
        copy=False,
    )
    owner_company_ids = fields.Many2many(
        "res.company",
        compute="_compute_owner_authority",
        string="Owner companies",
    )
    can_admin_agents = fields.Boolean(compute="_compute_owner_authority")
    credential_ids = fields.One2many(
        "usl.agent.credential",
        "agent_id",
        string="API keys",
        readonly=True,
    )
    audit_event_ids = fields.One2many(
        "usl.audit.event",
        "agent_id",
        string="Activity",
        readonly=True,
    )
    credential_count = fields.Integer(compute="_compute_credential_summary")
    active_credential_count = fields.Integer(compute="_compute_credential_summary")
    last_used_at = fields.Datetime(compute="_compute_credential_summary", store=False)
    needs_attention = fields.Boolean(compute="_compute_credential_summary", search="_search_needs_attention")
    access_summary = fields.Char(compute="_compute_access_summary")

    _user_unique = models.Constraint(
        "UNIQUE(user_id)",
        "An Odoo user can back only one Agent.",
    )

    @api.depends("owner_id")
    def _compute_owner_authority(self):
        can_admin = self.env.user.has_group("base.group_system")
        for agent in self:
            agent.owner_company_ids = agent.owner_id.company_ids
            agent.can_admin_agents = can_admin

    @api.depends(
        "credential_ids.status",
        "credential_ids.last_used_at",
        "state",
        "authority_reduced_at",
    )
    def _compute_credential_summary(self):
        for agent in self:
            agent.credential_count = len(agent.credential_ids)
            active = agent.credential_ids.filtered(lambda credential: credential.status == "active")
            agent.active_credential_count = len(active)
            used_dates = [value for value in agent.credential_ids.mapped("last_used_at") if value]
            agent.last_used_at = max(used_dates, default=False)
            agent.needs_attention = bool(
                agent.state == "active" and (not active or agent.authority_reduced_at),
            )

    def _search_needs_attention(self, operator, value):
        if operator not in ("=", "!=") or not isinstance(value, bool):
            raise NotImplementedError()
        active_agent_ids = self.env["usl.agent.credential"].sudo().search(
            [("status", "=", "active")],
        ).mapped("agent_id").ids
        candidates = self.sudo().search([("state", "=", "active")])
        attention_ids = [
            agent.id
            for agent in candidates
            if agent.id not in active_agent_ids or agent.authority_reduced_at
        ]
        return [("id", "in" if (operator == "=") == value else "not in", attention_ids)]

    @api.depends("delegated_group_ids", "company_ids")
    def _compute_access_summary(self):
        for agent in self:
            agent.access_summary = _(
                "%(apps)s access groups · %(companies)s companies",
                apps=len(agent.delegated_group_ids),
                companies=len(agent.company_ids),
            )

    @api.depends("owner_id", "owner_id.all_group_ids")
    def _compute_view_group_hierarchy(self):
        hierarchy = self.env["res.groups"]._get_view_group_hierarchy()
        forbidden = self._forbidden_delegated_groups()
        for agent in self:
            allowed = agent.owner_id.all_group_ids.filtered(
                lambda group: not group.all_implied_ids & forbidden,
            )
            allowed_ids = set(allowed.ids)
            value = deepcopy(hierarchy)
            value["groups"] = {
                group_id: {
                    **definition,
                    "disjoint_ids": [item for item in definition["disjoint_ids"] if item in allowed_ids],
                    "implied_ids": [item for item in definition["implied_ids"] if item in allowed_ids],
                    "all_implied_ids": [item for item in definition["all_implied_ids"] if item in allowed_ids],
                    "all_implied_by_ids": [item for item in definition["all_implied_by_ids"] if item in allowed_ids],
                }
                for group_id, definition in value["groups"].items()
                if group_id in allowed_ids
            }
            value["privileges"] = {
                privilege_id: {
                    **definition,
                    "group_ids": [item for item in definition["group_ids"] if item in allowed_ids],
                }
                for privilege_id, definition in value["privileges"].items()
                if any(item in allowed_ids for item in definition["group_ids"])
            }
            privilege_ids = set(value["privileges"])
            value["categories"] = [
                {
                    **category,
                    "privilege_ids": [item for item in category["privilege_ids"] if item in privilege_ids],
                }
                for category in value["categories"]
                if any(item in privilege_ids for item in category["privilege_ids"])
            ]
            agent.view_group_hierarchy = value

    @api.model
    def _forbidden_delegated_groups(self):
        groups = self.env["res.groups"]
        for xmlid in (
            "usl_access_control.group_ai_agent",
            "usl_access_control.group_irreversible_actions",
        ):
            group = self.env.ref(xmlid, raise_if_not_found=False)
            if group:
                groups |= group
        return groups

    @api.model
    def _caller_may_manage_owner(self, owner):
        return bool(
            self.env.user._is_internal()
            and not self.env.user.usl_is_ai_agent
            and self.env.user.active
            and (owner == self.env.user or self.env.user.has_group("base.group_system"))
        )

    def _check_caller_can_manage(self):
        for agent in self:
            if not self._caller_may_manage_owner(agent.owner_id):
                raise AccessError(_("You can manage only Agents you own."))

    @api.model
    def _validate_authority_values(self, owner, companies, groups, default_company):
        if not owner or not owner.active or owner.share or owner.usl_is_ai_agent:
            raise ValidationError(_("An Agent requires one active internal human owner."))
        if not companies or not companies <= owner.company_ids:
            raise ValidationError(_("Agent companies must remain within the owner's companies."))
        if default_company not in companies:
            raise ValidationError(_("The default company must be one of the Agent's companies."))
        forbidden = self._forbidden_delegated_groups()
        if groups.filtered(lambda group: group.all_implied_ids & forbidden):
            raise ValidationError(_("Agent and Irreversible Actions access cannot be delegated."))
        if not groups <= owner.all_group_ids:
            raise ValidationError(_("Agent access cannot exceed the owner's effective access."))

    @api.model
    def _effective_groups(self, delegated_groups):
        agent_group = self.env.ref("usl_access_control.group_ai_agent")
        return (delegated_groups | agent_group).all_implied_ids

    @api.model_create_multi
    def create(self, vals_list):
        records = self.browse()
        for values in vals_list:
            owner = self.env["res.users"].browse(values.get("owner_id") or self.env.uid).exists()
            if not self._caller_may_manage_owner(owner):
                raise AccessError(_("You cannot create an Agent for this owner."))
            companies = self.env["res.company"].browse(
                self._ids_from_commands(values.get("company_ids"), owner.company_ids.ids),
            ).exists()
            groups = self.env["res.groups"].browse(
                self._ids_from_commands(values.get("delegated_group_ids"), []),
            ).exists()
            default_company = self.env["res.company"].browse(
                values.get("company_id") or owner.company_id.id,
            ).exists()
            self._validate_authority_values(owner, companies, groups, default_company)
            technical_login = f"agent+{uuid.uuid4().hex}@usl.invalid"
            agent_group = self.env.ref("usl_access_control.group_ai_agent")
            backing_user = self.env["res.users"].with_user(SUPERUSER_ID).with_context(
                no_reset_password=True,
                usl_governed_identity_provisioning=True,
                usl_agent_provisioning=True,
            ).create(
                {
                    "name": values["name"],
                    "login": technical_login,
                    "email": False,
                    "active": values.get("state", "active") == "active",
                    "share": False,
                    "company_id": default_company.id,
                    "company_ids": [Command.set(companies.ids)],
                    "group_ids": [Command.set((groups | agent_group).ids)],
                    "usl_identity_classification": "agent",
                    "usl_pocketid_access": False,
                    "usl_pocketid_email_link": False,
                },
            )
            clean = dict(values)
            clean.update(
                owner_id=owner.id,
                user_id=backing_user.id,
                company_id=default_company.id,
                company_ids=[Command.set(companies.ids)],
                delegated_group_ids=[Command.set(groups.ids)],
                approved_effective_group_ids=[Command.set(self._effective_groups(groups).ids)],
            )
            records |= super().create(clean)
        return records

    @api.model
    def _ids_from_commands(self, commands, default):
        if not commands:
            return list(default)
        ids = set(default)
        for command in commands:
            operation = command[0]
            if operation == Command.SET:
                ids = set(command[2])
            elif operation == Command.LINK:
                ids.add(command[1])
            elif operation in (Command.UNLINK, Command.DELETE):
                ids.discard(command[1])
            elif operation == Command.CLEAR:
                ids.clear()
        return list(ids)

    def write(self, values):
        if self.env.context.get("usl_agent_internal"):
            return super().write(values)
        self._check_caller_can_manage()
        if "owner_id" in values:
            raise ValidationError(_("Use Transfer ownership to change an Agent owner."))
        for agent in self:
            companies = agent.company_ids
            groups = agent.delegated_group_ids
            default_company = agent.company_id
            if "company_ids" in values:
                companies = self.env["res.company"].browse(
                    self._ids_from_commands(values["company_ids"], companies.ids),
                ).exists()
            if "delegated_group_ids" in values:
                groups = self.env["res.groups"].browse(
                    self._ids_from_commands(values["delegated_group_ids"], groups.ids),
                ).exists()
            if "company_id" in values:
                default_company = self.env["res.company"].browse(values["company_id"]).exists()
            self._validate_authority_values(agent.owner_id, companies, groups, default_company)
        result = super().write(values)
        for agent in self:
            if "delegated_group_ids" in values:
                effective_group_ids = agent._effective_groups(agent.delegated_group_ids).ids
                agent.with_user(SUPERUSER_ID).with_context(usl_agent_internal=True).write(
                    {"approved_effective_group_ids": [Command.set(effective_group_ids)]},
                )
            agent._sync_backing_user()
        return result

    def unlink(self):
        raise UserError(_("Suspend an Agent instead of deleting its identity and audit history."))

    def _sync_backing_user(self):
        agent_group = self.env.ref("usl_access_control.group_ai_agent")
        for agent in self.sudo():
            values = {
                "name": agent.name,
                "active": agent.state == "active" and agent.owner_id.active,
                "company_id": agent.company_id.id,
                "company_ids": [Command.set(agent.company_ids.ids)],
                "group_ids": [Command.set((agent.delegated_group_ids | agent_group).ids)],
                "usl_identity_classification": "agent",
                "usl_pocketid_access": False,
                "usl_pocketid_email_link": False,
            }
            user = agent.user_id.with_user(SUPERUSER_ID).with_context(active_test=False)
            current_group_ids = set(user.group_ids.ids)
            expected_group_ids = set((agent.delegated_group_ids | agent_group).ids)
            multi_company_group = self.env.ref("base.group_multi_company")
            if multi_company_group.id not in expected_group_ids:
                current_group_ids.discard(multi_company_group.id)
            if (
                user.name != values["name"]
                or user.active != values["active"]
                or user.company_id.id != values["company_id"]
                or set(user.company_ids.ids) != set(agent.company_ids.ids)
                or current_group_ids != expected_group_ids
                or user.usl_identity_classification != "agent"
                or user.usl_pocketid_access
                or user.usl_pocketid_email_link
            ):
                user.with_context(
                    usl_agent_provisioning=True,
                    usl_governed_identity_provisioning=True,
                    no_reset_password=True,
                ).write(values)

    def _reconcile_authority(self):
        for agent in self.sudo().with_context(active_test=False):
            owner_groups = agent.owner_id.all_group_ids
            delegated = agent.delegated_group_ids & owner_groups
            forbidden = agent._forbidden_delegated_groups()
            delegated = delegated.filtered(lambda group: not group.all_implied_ids & forbidden)
            approved = agent.approved_effective_group_ids
            delegated = delegated.filtered(
                lambda group: group.all_implied_ids <= approved and group.all_implied_ids <= owner_groups,
            )
            companies = agent.company_ids & agent.owner_id.company_ids
            updates = {}
            if delegated != agent.delegated_group_ids:
                updates["delegated_group_ids"] = [Command.set(delegated.ids)]
                updates.update(
                    authority_reduced_at=fields.Datetime.now(),
                    authority_reduction_reason=_("Owner authority was reduced; review Agent access."),
                )
            if companies != agent.company_ids:
                updates["company_ids"] = [Command.set(companies.ids)]
                updates.update(
                    authority_reduced_at=fields.Datetime.now(),
                    authority_reduction_reason=_("Owner company access was reduced; review Agent access."),
                )
            if not agent.owner_id.active or not companies:
                updates.update(
                    state="suspended",
                    suspension_reason=_("The owner is inactive or no permitted company remains."),
                )
            if agent.company_id not in companies and companies:
                updates["company_id"] = companies[0].id
            if updates:
                agent.with_user(SUPERUSER_ID).with_context(usl_agent_internal=True).write(updates)
            agent._sync_backing_user()
        return True

    @api.model
    def _reconcile_all(self):
        return self.sudo().with_context(active_test=False).search([])._reconcile_authority()

    @api.model
    def _reconcile_for_owners(self, owners):
        return self.sudo().with_context(active_test=False).search(
            [("owner_id", "in", owners.ids)],
        )._reconcile_authority()

    def action_suspend(self):
        self._check_caller_can_manage()
        self.write({"state": "suspended", "suspension_reason": _("Suspended by %(name)s", name=self.env.user.name)})
        return True

    def action_reactivate(self):
        self._check_caller_can_manage()
        self.write({"state": "active", "suspension_reason": False})
        self._reconcile_authority()
        return True

    def action_acknowledge_authority_reduction(self):
        self._check_caller_can_manage()
        self.with_user(SUPERUSER_ID).with_context(usl_agent_internal=True).write(
            {"authority_reduced_at": False, "authority_reduction_reason": False},
        )
        return True

    @api.model
    def _groups_from_xmlids(self, xmlids):
        groups = self.env["res.groups"]
        for xmlid in xmlids:
            group = self.env.ref(xmlid, raise_if_not_found=False)
            if group:
                groups |= group
        return groups

    def _owner_delegable_groups(self, groups):
        self.ensure_one()
        forbidden = self._forbidden_delegated_groups()
        return (groups & self.owner_id.all_group_ids).filtered(
            lambda group: not group.all_implied_ids & forbidden,
        )

    def _all_read_groups(self):
        self.ensure_one()
        return self._owner_delegable_groups(
            self._groups_from_xmlids(_AGENT_READ_ONLY_GROUP_XMLIDS),
        )

    def _all_read_write_groups(self):
        self.ensure_one()
        hierarchy = self.env["res.groups"]._get_view_group_hierarchy()
        groups = self.env["res.groups"]
        owner_group_ids = set(self.owner_id.all_group_ids.ids)
        forbidden = self._forbidden_delegated_groups()
        for privilege in hierarchy["privileges"].values():
            candidates = self.env["res.groups"].browse(
                [group_id for group_id in privilege["group_ids"] if group_id in owner_group_ids],
            ).filtered(lambda group: not group.all_implied_ids & forbidden)
            if candidates:
                groups |= candidates[-1]
        groups |= self._groups_from_xmlids(_AGENT_FULL_ACCESS_EXTRA_GROUP_XMLIDS)
        return self._owner_delegable_groups(groups)

    def _replace_delegated_access(self, group_resolver):
        self._check_caller_can_manage()
        for agent in self:
            agent.write(
                {"delegated_group_ids": [Command.set(group_resolver(agent).ids)]},
            )
        return True

    def action_grant_all_read(self):
        return self._replace_delegated_access(lambda agent: agent._all_read_groups())

    def action_grant_all_read_write(self):
        return self._replace_delegated_access(lambda agent: agent._all_read_write_groups())

    def action_new_credential(self):
        self.ensure_one()
        self._check_caller_can_manage()
        if self.state != "active":
            raise UserError(_("Reactivate this Agent before creating an API key."))
        return {
            "type": "ir.actions.act_window",
            "name": _("New Agent API Key"),
            "res_model": "usl.agent.key.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {"default_agent_id": self.id},
        }

    def action_transfer_owner(self):
        self.ensure_one()
        if not self.env.user.has_group("base.group_system"):
            raise AccessError(_("Only an administrator can transfer Agent ownership."))
        return {
            "type": "ir.actions.act_window",
            "name": _("Transfer Agent ownership"),
            "res_model": "usl.agent.transfer.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {"default_agent_id": self.id},
        }

    @api.model
    def action_my_agents(self):
        if not self.env.user._is_internal() or self.env.user.usl_is_ai_agent:
            raise AccessError(_("Only internal human users can manage Agents."))
        action = self.env["ir.actions.actions"]._for_xml_id("usl_access_control.action_usl_agent")
        action["domain"] = [("owner_id", "=", self.env.uid)]
        return action

    @api.model
    def current_identity(self):
        agent = self.sudo().search([("user_id", "=", self.env.uid)], limit=1)
        if not agent:
            raise AgentPolicyAccessError(
                _("This API key belongs to a human user. Create an Agent key from My Agents."),
                "agent_principal_required",
            )
        if agent.state != "active" or not agent.owner_id.active or not agent.user_id.active:
            raise AgentPolicyAccessError(_("This Agent is suspended."), "agent_suspended")
        agent._reconcile_authority()
        if agent.state != "active":
            raise AgentPolicyAccessError(_("This Agent is suspended."), "agent_suspended")
        if agent.authority_reduced_at:
            raise AgentPolicyAccessError(
                _("This Agent's authority was reduced and requires owner review."),
                "agent_authority_reduced",
            )
        credential_id = getattr(request, "usl_agent_credential_id", None) if request else None
        credential = self.env["usl.agent.credential"].sudo().browse(credential_id).exists()
        return {
            "schema_version": 1,
            "principal_kind": "agent",
            "user_id": agent.user_id.id,
            "agent": {
                "id": agent.id,
                "name": agent.name,
                "purpose": agent.purpose,
                "state": agent.state,
            },
            "owner": {"id": agent.owner_id.id, "name": agent.owner_id.name},
            "credential": (
                {
                    "id": credential.id,
                    "name": credential.name,
                    "expires_at": fields.Datetime.to_string(credential.expiration_date),
                }
                if credential
                else None
            ),
            "company_id": agent.company_id.id,
            "company_ids": agent.company_ids.ids,
        }


class UslAgentCredential(models.Model):
    _name = "usl.agent.credential"
    _description = "Agent API Credential"
    _order = "create_date desc, id desc"

    name = fields.Char(required=True, readonly=True)
    agent_id = fields.Many2one("usl.agent", required=True, readonly=True, ondelete="cascade", index=True)
    native_key_id = fields.Integer(required=True, readonly=True, copy=False, index=True)
    expiration_date = fields.Datetime(required=True, readonly=True)
    last_used_at = fields.Datetime(readonly=True, copy=False)
    revoked_at = fields.Datetime(readonly=True, copy=False)
    revoked_by_id = fields.Many2one("res.users", readonly=True, copy=False)
    status = fields.Selection(
        [("active", "Active"), ("expired", "Expired"), ("revoked", "Revoked")],
        compute="_compute_status",
        search="_search_status",
    )

    _native_key_unique = models.Constraint(
        "UNIQUE(native_key_id)",
        "A native API key can belong to only one Agent credential.",
    )

    @api.depends("expiration_date", "revoked_at")
    def _compute_status(self):
        now = fields.Datetime.now()
        for credential in self:
            credential.status = (
                "revoked"
                if credential.revoked_at
                else "expired" if credential.expiration_date <= now else "active"
            )

    def _search_status(self, operator, value):
        if operator not in ("=", "!="):
            raise NotImplementedError()
        now = fields.Datetime.now()
        domains = {
            "revoked": [("revoked_at", "!=", False)],
            "expired": [("revoked_at", "=", False), ("expiration_date", "<=", now)],
            "active": [("revoked_at", "=", False), ("expiration_date", ">", now)],
        }
        matching_ids = self.sudo().search(domains[value]).ids
        return [("id", "in" if operator == "=" else "not in", matching_ids)]

    def _check_caller_can_manage(self):
        self.mapped("agent_id")._check_caller_can_manage()

    @check_identity
    def action_revoke(self):
        self.ensure_one()
        self._check_caller_can_manage()
        if self.revoked_at:
            return True
        self.env["res.users.apikeys"].with_user(SUPERUSER_ID).browse(self.native_key_id)._remove()
        self.sudo().with_context(usl_agent_credential_internal=True).write(
            {"revoked_at": fields.Datetime.now(), "revoked_by_id": self.env.uid},
        )
        return True

    def action_create_replacement(self):
        self.ensure_one()
        self._check_caller_can_manage()
        return {
            "type": "ir.actions.act_window",
            "name": _("Create replacement API key"),
            "res_model": "usl.agent.key.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {
                "default_agent_id": self.agent_id.id,
                "default_name": _("Replacement for %(name)s", name=self.name),
                "default_duration": "365",
            },
        }

    @api.model_create_multi
    def create(self, vals_list):
        if not self.env.context.get("usl_agent_credential_internal"):
            raise AccessError(_("Agent credentials can be created only through the protected key action."))
        return super().create(vals_list)

    def write(self, values):
        if not self.env.context.get("usl_agent_credential_internal"):
            raise AccessError(_("Agent credential metadata is managed by the security service."))
        return super().write(values)

    def unlink(self):
        raise UserError(_("Revoke Agent credentials instead of deleting their audit history."))


class UslAgentKeyWizard(models.TransientModel):
    _name = "usl.agent.key.wizard"
    _description = "Create Agent API Key"

    agent_id = fields.Many2one("usl.agent", required=True, readonly=True)
    name = fields.Char(required=True, default=lambda self: _("MCP integration"))
    duration = fields.Selection(
        [("90", "90 days"), ("365", "1 year"), ("1826", "5 years"), ("custom", "Custom date")],
        required=True,
        default="365",
    )
    expiration_date = fields.Datetime(compute="_compute_expiration_date", store=True, readonly=False)

    @api.depends("duration")
    def _compute_expiration_date(self):
        for wizard in self:
            if wizard.duration != "custom":
                wizard.expiration_date = fields.Datetime.now() + datetime.timedelta(days=int(wizard.duration))

    @api.constrains("expiration_date")
    def _check_expiration_date(self):
        now = fields.Datetime.now()
        maximum = now + datetime.timedelta(days=_AGENT_KEY_MAX_DAYS)
        for wizard in self:
            if not wizard.expiration_date or not now < wizard.expiration_date <= maximum:
                raise ValidationError(_("Agent API keys must expire within five years."))

    @check_identity
    def action_generate(self):
        self.ensure_one()
        self.agent_id._check_caller_can_manage()
        if self.agent_id.state != "active":
            raise UserError(_("Reactivate this Agent before creating an API key."))
        key = self.env["res.users.apikeys"].with_user(self.agent_id.user_id)._generate(
            "rpc",
            self.name,
            self.expiration_date,
        )
        self.env.cr.execute(
            """
            SELECT id
              FROM res_users_apikeys
             WHERE user_id = %s AND index = %s
             ORDER BY id DESC
             LIMIT 1
            """,
            [self.agent_id.user_id.id, key[:8]],
        )
        row = self.env.cr.fetchone()
        if not row:
            raise UserError(_("The Agent API key was created but could not be registered safely."))
        self.env["usl.agent.credential"].sudo().with_context(
            usl_agent_credential_internal=True,
        ).create(
            {
                "name": self.name,
                "agent_id": self.agent_id.id,
                "native_key_id": row[0],
                "expiration_date": self.expiration_date,
            },
        )
        self.unlink()
        return {
            "type": "ir.actions.act_window",
            "res_model": "res.users.apikeys.show",
            "name": _("Agent API Key Ready"),
            "views": [(False, "form")],
            "target": "new",
            "context": {"default_key": key},
        }


class UslAgentTransferWizard(models.TransientModel):
    _name = "usl.agent.transfer.wizard"
    _description = "Transfer Agent Ownership"

    agent_id = fields.Many2one("usl.agent", required=True, readonly=True)
    new_owner_id = fields.Many2one(
        "res.users",
        required=True,
        domain="[('active', '=', True), ('share', '=', False), ('usl_is_ai_agent', '=', False)]",
    )
    consequence = fields.Text(compute="_compute_consequence")

    @api.depends("new_owner_id")
    def _compute_consequence(self):
        for wizard in self:
            retained_groups = wizard.agent_id.delegated_group_ids & wizard.new_owner_id.all_group_ids
            retained_companies = wizard.agent_id.company_ids & wizard.new_owner_id.company_ids
            wizard.consequence = _(
                "%(groups)s access groups and %(companies)s companies will remain.",
                groups=len(retained_groups),
                companies=len(retained_companies),
            )

    @check_identity
    def action_transfer(self):
        self.ensure_one()
        if not self.env.user.has_group("base.group_system"):
            raise AccessError(_("Only an administrator can transfer Agent ownership."))
        if (
            not self.agent_id._caller_may_manage_owner(self.new_owner_id)
            or not self.new_owner_id.active
            or self.new_owner_id.share
            or self.new_owner_id.usl_is_ai_agent
        ):
            raise ValidationError(_("Select an active internal human owner."))
        self.agent_id.with_user(SUPERUSER_ID).with_context(usl_agent_internal=True).write(
            {"owner_id": self.new_owner_id.id},
        )
        self.agent_id._reconcile_authority()
        return {"type": "ir.actions.act_window_close"}


class ResUsersApikeys(models.Model):
    _inherit = "res.users.apikeys"

    def _check_credentials(self, *, scope, key):
        uid = super()._check_credentials(scope=scope, key=key)
        if not uid:
            # Native API-key authentication deliberately ignores inactive
            # users. Governed Agent users are inactive while suspended, so
            # verify only matching, unexpired governed-key candidates to
            # return the stable suspension code. Never authenticate through
            # this fallback: an exact match can only be denied.
            self.env.cr.execute(
                """
                SELECT native_key.user_id, native_key.key
                  FROM res_users_apikeys AS native_key
                  JOIN usl_agent_credential AS credential
                    ON credential.native_key_id = native_key.id
                  JOIN usl_agent AS agent
                    ON agent.id = credential.agent_id
                 WHERE native_key.index = %s
                   AND (native_key.scope IS NULL OR native_key.scope = %s)
                   AND (
                        native_key.expiration_date IS NULL
                        OR native_key.expiration_date >= now() at time zone 'utc'
                   )
                   AND credential.revoked_at IS NULL
                   AND credential.expiration_date > now() at time zone 'utc'
                """,
                [key[:8], scope],
            )
            exact_governed_key = any(
                KEY_CRYPT_CONTEXT.verify(key, candidate)
                for _user_id, candidate in self.env.cr.fetchall()
            )
            if exact_governed_key:
                raise AgentAuthenticationError(_("This Agent is suspended."), "agent_suspended")
            return uid
        # Bearer authentication runs before ``request.update_env(user=uid)``.
        # At that point the transaction may not yet have a default user
        # environment, which relational group computations require. Establish
        # the governance environment explicitly on the same transaction; the
        # returned actor remains ``uid`` and subsequent business calls still
        # execute with that Agent's ordinary ACLs and record rules.
        governance_env = api.Environment(self.env.cr, SUPERUSER_ID, {})
        agent = governance_env["usl.agent"].search([("user_id", "=", uid)], limit=1)
        user = governance_env["res.users"].browse(uid)
        if not agent and not user.usl_is_ai_agent:
            return uid
        if not agent:
            raise AgentAuthenticationError(
                _("This Agent identity is not governed."),
                "agent_principal_required",
            )
        agent._reconcile_authority()
        if agent.state != "active" or not agent.owner_id.active or not agent.user_id.active:
            raise AgentAuthenticationError(_("This Agent is suspended."), "agent_suspended")
        governance_env.cr.execute(
            "SELECT id FROM res_users_apikeys WHERE user_id = %s AND index = %s LIMIT 1",
            [uid, key[:8]],
        )
        row = governance_env.cr.fetchone()
        credential = governance_env["usl.agent.credential"].search(
            [("native_key_id", "=", row[0] if row else 0), ("agent_id", "=", agent.id)],
            limit=1,
        )
        if not credential or credential.status != "active":
            raise AccessDenied(_("This Agent credential is not active."))
        now = fields.Datetime.now()
        if not credential.last_used_at or credential.last_used_at < now - datetime.timedelta(minutes=1):
            credential.with_context(usl_agent_credential_internal=True).write({"last_used_at": now})
        if request:
            request.usl_agent_credential_id = credential.id
        return uid

    @api.model
    def generate(self, key, scope, name, expiration_date):
        if self.env.user.usl_is_ai_agent:
            raise AgentPolicyAccessError(
                _("Agents cannot create or rotate their own credentials."),
                "approval_required",
            )
        return super().generate(key, scope, name, expiration_date)

    @api.model
    def revoke(self, key):
        if self.env.user.usl_is_ai_agent:
            raise AgentPolicyAccessError(
                _("Agents cannot revoke their own credentials."),
                "approval_required",
            )
        return super().revoke(key)
