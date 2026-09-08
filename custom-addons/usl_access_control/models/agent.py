import uuid
from copy import deepcopy

from odoo import SUPERUSER_ID, Command, _, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.http import request

from ..exceptions import AgentPolicyAccessError
from .action_policy import ActionPolicyConfigurationError, load_agent_readonly_policy

_AGENT_KEY_MAX_DAYS = 5 * 365 + 1
_AGENT_CREDENTIAL_TOUCH_LOCK_NAMESPACE = 5_590_092

_AGENT_VISIBLE_STANDALONE_READER_PRIVILEGES = (
    ("account.group_account_readonly", "account.res_groups_privilege_accounting"),
)


def _agent_key_path_allowed(path):
    return bool(
        path.startswith("/json/2/")
        or path == "/doc-bearer/index.json"
        or (path.startswith("/doc-bearer/") and path.endswith(".json")),
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
    access_mode = fields.Selection(
        [
            ("read_only", "Read-only"),
            ("read_write", "Read/write"),
            ("mixed", "Mixed"),
        ],
        required=True,
        default="read_only",
        index=True,
        help=(
            "Summarizes whether delegated applications are read-only, read/write, or mixed."
        ),
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
    read_only_group_ids = fields.Many2many(
        "res.groups",
        "usl_agent_read_only_group_rel",
        "agent_id",
        "group_id",
        string="Read-only application access",
        help="Explicit delegated application roles whose mutations are blocked by Agent policy.",
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
    expense_receipt_waiver_authority = fields.Boolean(
        string="May record receipt decisions",
        default=False,
        copy=False,
        help=(
            "Continuing an expense without a receipt is a documented decision "
            "rather than ordinary data entry, so an Agent cannot take it on "
            "its own. Enabling this is the owner's explicit acceptance that "
            "this Agent may record the decision on their authority. The Agent "
            "still has to preview the decision and confirm it in a second "
            "call, and the owner stays accountable for what it records."
        ),
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
    read_profile_update_available = fields.Boolean(
        compute="_compute_read_profile_update_available",
    )
    settings_access = fields.Selection(
        [
            ("no_access", "No access"),
            ("read_only", "Read-only"),
            ("read_write", "Read/write"),
        ],
        string="Settings",
        compute="_compute_settings_access",
    )
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
        "access_mode",
        "delegated_group_ids",
        "read_only_group_ids",
        "approved_effective_group_ids",
        "owner_id.all_group_ids",
    )
    def _compute_read_profile_update_available(self):
        for agent in self:
            if not agent.owner_id:
                agent.read_profile_update_available = False
                continue
            available = agent._all_read_groups().all_implied_ids
            delegated = agent._effective_groups(agent.delegated_group_ids)
            agent.read_profile_update_available = bool(
                available - (agent.approved_effective_group_ids & delegated),
            )

    @api.depends(
        "delegated_group_ids",
        "read_only_group_ids",
        "owner_id.all_group_ids",
    )
    def _compute_settings_access(self):
        settings_group = self.env.ref("base.group_system", raise_if_not_found=False)
        for agent in self:
            if (
                not settings_group
                or settings_group not in agent.owner_id.all_group_ids
                or settings_group not in agent._effective_groups(
                    agent.delegated_group_ids,
                )
            ):
                agent.settings_access = "no_access"
            else:
                agent.settings_access = (
                    "read_only"
                    if settings_group in agent.read_only_group_ids
                    else "read_write"
                )

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

    @api.depends("access_mode", "delegated_group_ids", "company_ids")
    def _compute_access_summary(self):
        for agent in self:
            agent.access_summary = _(
                "%(mode)s · %(apps)s access groups · %(companies)s companies",
                mode=dict(agent._fields["access_mode"].selection).get(
                    agent.access_mode,
                    agent.access_mode,
                ),
                apps=len(agent.delegated_group_ids),
                companies=len(agent.company_ids),
            )

    @api.depends(
        "access_mode",
        "delegated_group_ids",
        "owner_id",
        "owner_id.all_group_ids",
    )
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
            for group_xmlid, privilege_xmlid in _AGENT_VISIBLE_STANDALONE_READER_PRIVILEGES:
                group = self.env.ref(group_xmlid, raise_if_not_found=False)
                privilege = self.env.ref(privilege_xmlid, raise_if_not_found=False)
                if (
                    group
                    and privilege
                    and group.id in allowed_ids
                    and group.id in value["groups"]
                    and privilege.id in value["privileges"]
                    and group.id not in value["privileges"][privilege.id]["group_ids"]
                ):
                    value["privileges"][privilege.id]["group_ids"].insert(0, group.id)
            settings_group = self.env.ref("base.group_system", raise_if_not_found=False)
            if settings_group and settings_group.id in allowed_ids:
                settings_scope_id = "usl_agent_settings"
                value["privileges"][settings_scope_id] = {
                    "id": settings_scope_id,
                    "name": _("Settings"),
                    "description": _("Ordinary product configuration; identities and secrets stay blocked."),
                    "placeholder": _("No access"),
                    "category_id": "usl_agent_administration",
                    "sequence": 1000,
                    "group_ids": [settings_group.id],
                }
                value["categories"].append(
                    {
                        "id": "usl_agent_administration",
                        "name": _("Administration"),
                        "sequence": 1000,
                        "privilege_ids": [settings_scope_id],
                    },
                )
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
            and (owner == self.env.user or self.env.user.has_group("base.group_system")),
        )

    def _check_caller_can_manage(self):
        for agent in self:
            if not self._caller_may_manage_owner(agent.owner_id):
                raise AccessError(_("You can manage only Agents you own."))

    @api.model
    def _validate_authority_values(
        self,
        owner,
        companies,
        groups,
        default_company,
        read_only_groups=None,
    ):
        if not owner or not owner.active or owner.share or owner.usl_is_ai_agent:
            raise ValidationError(_("An Agent requires one active internal human owner."))
        if not companies or not companies <= owner.company_ids:
            raise ValidationError(_("Agent companies must remain within the owner's companies."))
        if default_company not in companies:
            raise ValidationError(_("The default company must be one of the Agent's companies."))
        forbidden = self._forbidden_delegated_groups()
        if groups.filtered(lambda group: group.all_implied_ids & forbidden):
            raise ValidationError(
                _("Agents can never receive Irreversible Actions access."),
            )
        if not groups <= owner.all_group_ids:
            raise ValidationError(_("Agent access cannot exceed the owner's effective access."))
        read_only_groups = read_only_groups or self.env["res.groups"]
        if not read_only_groups <= groups:
            raise ValidationError(_("Read-only access must be part of the Agent's delegated access."))

    def _check_receipt_waiver_authority(self):
        """A receipt decision the Agent could not write is not an authority.

        The owner grants this on top of ordinary Expenses access, never
        instead of it, so the flag is refused while the Agent cannot write
        an expense at all.
        """
        for agent in self:
            if agent.expense_receipt_waiver_authority and not agent._allows_model_operation(
                "hr.expense",
                "write",
            ):
                raise ValidationError(
                    _(
                        "Give %s write access to Expenses before allowing it "
                        "to record receipt decisions.",
                        agent.name,
                    ),
                )
        return True

    @api.model
    def _access_mode_from_groups(self, groups, read_only_groups):
        if not groups or groups <= read_only_groups:
            return "read_only"
        if not read_only_groups:
            return "read_write"
        return "mixed"

    @api.model
    def _effective_groups(self, delegated_groups):
        agent_group = self.env.ref("usl_access_control.group_ai_agent")
        return (delegated_groups | agent_group).all_implied_ids

    def _allows_model_operation(self, model_name, operation):
        self.ensure_one()
        if operation not in {"read", "create", "write", "unlink"}:
            return False
        roots = self.delegated_group_ids
        if operation != "read":
            roots -= self.read_only_group_ids
        if not roots:
            return False
        effective_groups = roots.all_implied_ids
        permission_field = f"perm_{operation}"
        access_rows = self.env["ir.model.access"].sudo().search(
            [
                ("model_id.model", "=", model_name),
                ("active", "=", True),
                (permission_field, "=", True),
            ],
        )
        generic_groups = self._groups_from_xmlids(
            ("base.group_public", "base.group_portal", "base.group_user"),
        )
        return any(
            access.group_id
            and access.group_id in effective_groups
            and (operation == "read" or access.group_id not in generic_groups)
            for access in access_rows
        )

    def _api_method_access(self, model_name, method_name):
        """Return the effective Agent policy classification for one JSON-2 call."""
        self.ensure_one()
        if model_name == "usl.agent" and method_name == "submit_mcp_feedback":
            return "collaboration" if self.state == "active" else None
        try:
            access = load_agent_readonly_policy().access_for(model_name, method_name)
        except ActionPolicyConfigurationError:
            return None
        operation = method_name if method_name in {"read", "create", "write", "unlink"} else "write"
        if access in {"read_only", "collaboration"}:
            return access if self._allows_model_operation(model_name, "read") else None
        if access == "write" and self._allows_model_operation(model_name, operation):
            return access
        return None

    def _model_is_read_only(self, model_name):
        self.ensure_one()
        return self._allows_model_operation(model_name, "read") and not any(
            self._allows_model_operation(model_name, operation)
            for operation in ("create", "write", "unlink")
        )

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
            access_mode = values.get("access_mode", "read_only")
            if access_mode == "read_only" and not groups:
                groups = self._profile_groups_for_owner(owner)
            read_only_groups = self.env["res.groups"].browse(
                self._ids_from_commands(
                    values.get("read_only_group_ids"),
                    groups.ids if access_mode == "read_only" else [],
                ),
            ).exists()
            default_company = self.env["res.company"].browse(
                values.get("company_id") or owner.company_id.id,
            ).exists()
            self._validate_authority_values(
                owner,
                companies,
                groups,
                default_company,
                read_only_groups,
            )
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
                read_only_group_ids=[Command.set(read_only_groups.ids)],
                approved_effective_group_ids=[Command.set(self._effective_groups(groups).ids)],
                access_mode=self._access_mode_from_groups(groups, read_only_groups),
            )
            records |= super().create(clean)
        records._check_receipt_waiver_authority()
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

    def write(self, vals):
        if self.env.context.get("usl_agent_internal"):
            return super().write(vals)
        self._check_caller_can_manage()
        if "owner_id" in vals:
            raise ValidationError(_("Use Transfer ownership to change an Agent owner."))
        if "access_mode" in vals and not self.env.context.get(
            "usl_agent_profile_change",
        ):
            raise ValidationError(
                _("Use an access-profile action to change the Agent access mode."),
            )
        authority_fields = {
            "company_ids",
            "company_id",
            "delegated_group_ids",
            "read_only_group_ids",
        }
        if len(self) > 1 and authority_fields & vals.keys():
            return all(agent.write(dict(vals)) for agent in self)
        for agent in self:
            companies = agent.company_ids
            groups = agent.delegated_group_ids
            read_only_groups = agent.read_only_group_ids
            default_company = agent.company_id
            if "company_ids" in vals:
                companies = self.env["res.company"].browse(
                    self._ids_from_commands(vals["company_ids"], companies.ids),
                ).exists()
            if "delegated_group_ids" in vals:
                groups = self.env["res.groups"].browse(
                    self._ids_from_commands(vals["delegated_group_ids"], groups.ids),
                ).exists()
            if "read_only_group_ids" in vals:
                read_only_groups = self.env["res.groups"].browse(
                    self._ids_from_commands(
                        vals["read_only_group_ids"],
                        read_only_groups.ids,
                    ),
                ).exists()
            if "company_id" in vals:
                default_company = self.env["res.company"].browse(vals["company_id"]).exists()
            self._validate_authority_values(
                agent.owner_id,
                companies,
                groups,
                default_company,
                read_only_groups,
            )
            if "delegated_group_ids" in vals or "read_only_group_ids" in vals:
                vals = {
                    **vals,
                    "access_mode": self._access_mode_from_groups(
                        groups,
                        read_only_groups,
                    ),
                }
        result = super().write(vals)
        for agent in self:
            if "delegated_group_ids" in vals:
                effective_group_ids = agent._effective_groups(agent.delegated_group_ids).ids
                agent.with_user(SUPERUSER_ID).with_context(usl_agent_internal=True).write(
                    {"approved_effective_group_ids": [Command.set(effective_group_ids)]},
                )
            agent._sync_backing_user()
        self._check_receipt_waiver_authority()
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
            read_only = agent.read_only_group_ids & delegated
            if delegated != agent.delegated_group_ids:
                updates["delegated_group_ids"] = [Command.set(delegated.ids)]
                updates.update(
                    authority_reduced_at=fields.Datetime.now(),
                    authority_reduction_reason=_("Owner authority was reduced; review Agent access."),
                )
            if read_only != agent.read_only_group_ids:
                updates["read_only_group_ids"] = [Command.set(read_only.ids)]
            effective_mode = agent._access_mode_from_groups(delegated, read_only)
            if effective_mode != agent.access_mode:
                updates["access_mode"] = effective_mode
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
            if agent.expense_receipt_waiver_authority and not agent._allows_model_operation(
                "hr.expense",
                "write",
            ):
                agent.with_user(SUPERUSER_ID).with_context(usl_agent_internal=True).write(
                    {
                        "expense_receipt_waiver_authority": False,
                        "authority_reduced_at": fields.Datetime.now(),
                        "authority_reduction_reason": _(
                            "Expense access was reduced; the Agent can no longer "
                            "record receipt decisions.",
                        ),
                    },
                )
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

    def _all_read_groups(self):
        self.ensure_one()
        return self._profile_groups_for_owner(self.owner_id)

    @api.model
    def _profile_groups_for_owner(self, owner):
        hierarchy = self.env["res.groups"]._get_view_group_hierarchy()
        owner_group_ids = set(owner.all_group_ids.ids)
        forbidden = self._forbidden_delegated_groups()
        groups = self.env["res.groups"]
        for privilege in hierarchy["privileges"].values():
            candidates = self.env["res.groups"].browse(
                [group_id for group_id in privilege["group_ids"] if group_id in owner_group_ids],
            ).filtered(lambda group: not group.all_implied_ids & forbidden)
            if candidates:
                groups |= candidates[-1]
        settings_group = self.env.ref("base.group_system", raise_if_not_found=False)
        if settings_group and settings_group in owner.all_group_ids:
            groups |= settings_group
        return (groups & owner.all_group_ids).filtered(
            lambda group: not group.all_implied_ids & forbidden,
        )

    def _all_read_write_groups(self):
        self.ensure_one()
        return self._profile_groups_for_owner(self.owner_id)

    def _replace_delegated_access(self, group_resolver, *, access_mode, title, message):
        self._check_caller_can_manage()
        granted_group_count = 0
        for agent in self:
            groups = group_resolver(agent)
            granted_group_count += len(groups)
            agent.with_context(
                usl_agent_profile_change=True,
            ).write(
                {
                    "access_mode": access_mode,
                    "delegated_group_ids": [Command.set(groups.ids)],
                    "read_only_group_ids": [
                        Command.set(groups.ids if access_mode == "read_only" else []),
                    ],
                },
            )
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": title,
                "message": message % {"count": granted_group_count},
                "type": "success",
                "sticky": False,
                "next": {"type": "ir.actions.client", "tag": "soft_reload"},
            },
        }

    def action_grant_all_read(self):
        return self._replace_delegated_access(
            lambda agent: agent._all_read_groups(),
            access_mode="read_only",
            title=_("Read-only profile applied"),
            message=_(
                "%(count)s owner-scoped access roles are now visible in read-only mode.",
            ),
        )

    def action_grant_all_read_write(self):
        return self._replace_delegated_access(
            lambda agent: agent._all_read_write_groups(),
            access_mode="read_write",
            title=_("Read/write profile applied"),
            message=_("%(count)s owner-approved application roles granted."),
        )

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
        request_agent_id = (
            getattr(request, "usl_agent_reconciled_id", None)
            if request
            else None
        )
        if request_agent_id != agent.id:
            agent._reconcile_authority()
        if agent.state != "active":
            raise AgentPolicyAccessError(_("This Agent is suspended."), "agent_suspended")
        credential_id = getattr(request, "usl_agent_credential_id", None) if request else None
        credential = self.env["usl.agent.credential"].sudo().browse(credential_id).exists()
        hierarchy = agent.view_group_hierarchy
        effective_applications = [
            {
                "id": int(privilege_id),
                "name": definition["name"],
                "access": (
                    "read_only"
                    if set(definition["group_ids"]) & set(agent.read_only_group_ids.ids)
                    else "read_write"
                ),
            }
            for privilege_id, definition in hierarchy.get("privileges", {}).items()
            if str(privilege_id).isdigit()
            and set(definition.get("group_ids", ())) & set(agent.delegated_group_ids.ids)
        ]
        if agent.settings_access != "no_access":
            effective_applications.append(
                {
                    "id": "settings",
                    "name": _("Settings"),
                    "access": agent.settings_access,
                },
            )
        return {
            "schema_version": 3,
            "principal_kind": "agent",
            "user_id": agent.user_id.id,
            "agent": {
                "id": agent.id,
                "name": agent.name,
                "purpose": agent.purpose,
                "state": agent.state,
                "access_mode": agent.access_mode,
                "authority_reduced": bool(agent.authority_reduced_at),
                "partner_id": agent.user_id.partner_id.id,
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
            "companies": [
                {"id": company.id, "name": company.name}
                for company in agent.company_ids
            ],
            "effective_applications": effective_applications,
            "effective_group_ids": agent.user_id.all_group_ids.ids,
        }
