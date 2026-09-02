import json
import logging
import uuid

from lxml import etree

from odoo import SUPERUSER_ID, api, models
from odoo.exceptions import AccessError
from odoo.fields import Domain

from .action_policy import ActionPolicyConfigurationError, load_action_policy
from .agent_policy_tokens import has_agent_collaboration_token
from .agent_secrets import is_agent_secret_field
from ..exceptions import AgentPolicyAccessError

_logger = logging.getLogger(__name__)

_AGENT_AUDIT_EXCLUDED_MODELS = frozenset(
    {
        "bus.bus",
        "ir.logging",
        "mail.mail",
        "mail.notification",
        "usl.audit.event",
    },
)

_SENSITIVE_MARKERS = (
    "api_key",
    "credential",
    "password",
    "private_key",
    "secret",
    "token",
)

_AGENT_HIDDEN_MODELS = frozenset(
    {
        "res.users.apikeys",
        "res.users.apikeys.description",
        "usl.agent",
        "usl.agent.credential",
        "usl.agent.key.wizard",
        "usl.agent.transfer.wizard",
        "ir.config_parameter",
    },
)

_AGENT_IDENTITY_MUTATION_MODELS = frozenset(
    {
        "auth.oauth.provider",
        "auth.passkey.key",
        "ir.model.access",
        "ir.rule",
        "res.groups",
        "res.groups.privilege",
        "res.users",
        "res.users.apikeys",
        "res.users.identitycheck",
        "usl.agent",
        "usl.agent.credential",
        "usl.oidc.identity",
    },
)


class Base(models.AbstractModel):
    _inherit = "base"

    @api.model
    def _usl_managed_agent(self):
        if self.env.uid == SUPERUSER_ID or not self._usl_actor_is_agent():
            return self.env["usl.agent"]
        return self.env["usl.agent"].sudo().with_context(active_test=False).search(
            [("user_id", "=", self.env.uid)],
            limit=1,
        )

    @api.model
    def _access_domain(self, operation):
        agent = self._usl_managed_agent()
        if not agent:
            return super()._access_domain(operation)
        if self._name in _AGENT_HIDDEN_MODELS:
            return Domain.FALSE
        if (
            operation != "read"
            and not agent._allows_model_operation(self._name, operation)
            and not has_agent_collaboration_token(self.env.context)
        ):
            return Domain.FALSE
        if operation == "read" and not agent._allows_model_operation(self._name, "read"):
            return Domain.FALSE
        owner_context = dict(self.env.context)
        requested_companies = set(owner_context.get("allowed_company_ids") or agent.company_ids.ids)
        allowed_companies = (
            requested_companies
            & set(agent.company_ids.ids)
            & set(agent.owner_id.company_ids.ids)
        )
        if not allowed_companies:
            return Domain.FALSE
        owner_context["allowed_company_ids"] = list(allowed_companies)
        owner_env = self.env(user=agent.owner_id, context=owner_context)
        owner_domain = self.with_env(owner_env)._access_domain(operation)
        # The owner is the record-rule authority ceiling. Applying the backing
        # technical user's personal follower/assignment rules as well would
        # incorrectly hide records that the owner can read. Company scope is
        # still intersected explicitly here and in the owner context.
        if "company_id" in self._fields:
            company_domain = Domain(
                [
                    "|",
                    ("company_id", "=", False),
                    ("company_id", "in", sorted(allowed_companies)),
                ],
            )
            return Domain.AND([owner_domain, company_domain])
        return owner_domain

    @api.model
    def _has_field_access(self, field, operation):
        if not super()._has_field_access(field, operation):
            return False
        agent = self._usl_managed_agent()
        if not agent:
            return True
        owner_context = dict(self.env.context)
        requested_companies = set(
            owner_context.get("allowed_company_ids") or agent.company_ids.ids,
        )
        allowed_companies = (
            requested_companies
            & set(agent.company_ids.ids)
            & set(agent.owner_id.company_ids.ids)
        )
        if not allowed_companies:
            return False
        owner_context["allowed_company_ids"] = list(allowed_companies)
        owner_env = self.env(user=agent.owner_id, context=owner_context)
        return self.with_env(owner_env)._has_field_access(field, operation)

    @api.model
    def _usl_reject_readonly_agent_mutation(self, operation):
        agent = self._usl_managed_agent()
        if (
            agent
            and not agent._allows_model_operation(self._name, operation)
            and not has_agent_collaboration_token(self.env.context)
        ):
            raise AgentPolicyAccessError(
                self.env._(
                    "This Agent has no read/write access for %(model)s and cannot %(operation)s records.",
                    model=self._name,
                    operation=operation,
                ),
                "agent_read_only_action_denied",
            )

    @api.model
    def _usl_reject_agent_identity_mutation(self, operation):
        if self._name in _AGENT_IDENTITY_MUTATION_MODELS and self._usl_managed_agent():
            raise AgentPolicyAccessError(
                self.env._(
                    "Agents cannot %(operation)s identities, access rights, Agents, or credentials.",
                    operation=operation,
                ),
                "approval_required",
            )

    @api.model
    def fields_get(self, allfields=None, attributes=None):
        result = super().fields_get(allfields=allfields, attributes=attributes)
        agent = self._usl_managed_agent()
        if agent:
            owner_context = dict(self.env.context)
            owner_context["allowed_company_ids"] = sorted(
                set(agent.company_ids.ids) & set(agent.owner_id.company_ids.ids),
            )
            owner_fields = self.with_env(
                self.env(user=agent.owner_id, context=owner_context),
            ).fields_get(allfields=allfields, attributes=attributes)
            result = {
                field_name: definition
                for field_name, definition in result.items()
                if field_name in owner_fields
                and not is_agent_secret_field(field_name, model_name=self._name)
            }
        return result

    @api.model
    def get_view(self, view_id=None, view_type="form", **options):
        result = super().get_view(view_id, view_type, **options)
        if (
            self._name.startswith("account.")
            and self.env.user.has_group("account.group_account_readonly")
            and not self.env.user.has_group("account.group_account_user")
        ):
            arch = etree.fromstring(result["arch"])
            arch.set("create", "false")
            arch.set("edit", "false")
            arch.set("delete", "false")
            if view_type == "form":
                for control in arch.xpath("//header/button | //header/widget"):
                    control.getparent().remove(control)
            result["arch"] = etree.tostring(arch, encoding="unicode")
        return result

    @api.model
    def _usl_access_group(self, xmlid):
        return self.env.ref(xmlid, raise_if_not_found=False)

    @api.model
    def _usl_actor_is_agent(self):
        group = self._usl_access_group("usl_access_control.group_ai_agent")
        return bool(group and group in self.env.user.all_group_ids)

    @api.model
    def _usl_actor_may_perform_irreversible_actions(self):
        if self._usl_actor_is_agent():
            return False
        if self.env.uid == SUPERUSER_ID:
            return True
        group = self._usl_access_group(
            "usl_access_control.group_irreversible_actions",
        )
        return bool(group and group in self.env.user.all_group_ids)

    @api.model
    def _usl_audit_origin(self):
        return (
            self.env.context.get("usl_agent_origin")
            or self.env.context.get("usl_action_origin")
            or "rpc_or_server"
        )

    @api.model
    def _usl_audit_correlation_id(self):
        return (
            self.env.context.get("usl_correlation_id")
            or self.env.context.get("request_id")
            or str(uuid.uuid4())
        )

    @api.model
    def _usl_log_denied_protected_action(
        self,
        *,
        action_key,
        action_name,
        policy_digest,
    ):
        _logger.warning(
            "USL_PROTECTED_ACTION_DENIED %s",
            json.dumps(
                {
                    "action": action_name,
                    "action_key": action_key,
                    "actor_id": self.env.uid,
                    "actor_is_agent": self._usl_actor_is_agent(),
                    "correlation_id": self._usl_audit_correlation_id(),
                    "model": self._name,
                    "origin": self._usl_audit_origin(),
                    "policy_digest": policy_digest,
                    "record_ids": self.ids,
                },
                sort_keys=True,
            ),
        )

    @api.model
    def _usl_qualified_action_policy(self):
        try:
            return load_action_policy()
        except ActionPolicyConfigurationError as error:
            _logger.error("USL_ACTION_POLICY_INVALID %s", error)
            raise AccessError(
                self.env._(
                    "The protected-action policy is invalid. "
                    "Contact a product administrator before retrying this action.",
                ),
            ) from error

    def _usl_require_irreversible_action(
        self,
        guard_key,
        action_name=None,
        *,
        exact_policy_key=False,
    ):
        # UID 1 is Odoo's non-interactive internal service identity. Module
        # loading, migrations and registry repair must remain possible even
        # when a policy artifact is absent or malformed. Ordinary sudo() keeps
        # the caller's uid, so it does not enter this recovery boundary.
        if self.env.uid == SUPERUSER_ID:
            return
        policy = self._usl_qualified_action_policy()
        try:
            entry = (
                policy.protected_action(guard_key)
                if exact_policy_key
                else policy.protected_guard(guard_key)
            )
        except ActionPolicyConfigurationError as error:
            _logger.error("USL_ACTION_POLICY_INVALID %s", error)
            raise AccessError(
                self.env._(
                    "The protected-action policy is invalid. "
                    "Contact a product administrator before retrying this action.",
                ),
            ) from error
        action_name = action_name or entry.action_name or entry.action_key
        if not self._usl_actor_may_perform_irreversible_actions():
            self._usl_log_denied_protected_action(
                action_key=entry.action_key,
                action_name=action_name,
                policy_digest=policy.qualified_policy_digest,
            )
            if self._usl_actor_is_agent():
                raise AgentPolicyAccessError(
                    self.env._(
                        "AI Agents cannot perform irreversible actions. "
                        "Use a recoverable workflow or ask an authorized human.",
                    ),
                    "agent_irreversible_action_denied",
                )
            raise AccessError(
                self.env._(
                    "This action requires the Irreversible Actions permission.",
                ),
            )
        self.env["usl.audit.event"]._record_event(
            {
                "actor_id": self.env.uid,
                "actor_is_agent": self._usl_actor_is_agent(),
                "event_type": "protected_action",
                "model_name": self._name,
                "record_ids": json.dumps(self.ids),
                "record_count": len(self),
                "operation": "action",
                "action_name": action_name,
                "action_key": entry.action_key,
                "policy_digest": policy.qualified_policy_digest,
                "origin": self._usl_audit_origin(),
                "correlation_id": self._usl_audit_correlation_id(),
            },
        )

    @api.model
    def _usl_agent_audit_enabled(self):
        return bool(
            not self.env.context.get("usl_skip_distribution_audit")
            and not self._abstract
            and not self._transient
            and self._name not in _AGENT_AUDIT_EXCLUDED_MODELS
            and self._usl_actor_is_agent(),
        )

    @api.model
    def _usl_audit_json_value(self, field_name, value):
        if any(marker in field_name.lower() for marker in _SENSITIVE_MARKERS):
            return "[REDACTED]"
        field = self._fields.get(field_name)
        if field and field.type == "binary":
            return "[BINARY]"
        if hasattr(value, "isoformat"):
            return value.isoformat()
        if isinstance(value, (str, int, float, bool)) or value is None:
            return value
        if isinstance(value, (list, tuple)):
            return [self._usl_audit_json_value(field_name, item) for item in value[:100]]
        if isinstance(value, dict):
            return {
                str(key): self._usl_audit_json_value(str(key), item)
                for key, item in list(value.items())[:100]
            }
        return str(value)

    def _usl_audit_before_values(self, field_names):
        readable = [name for name in field_names if name in self._fields]
        if not self or not readable:
            return {}
        result = {}
        for record in self[:100]:
            values = {}
            for name in readable:
                value = record[name]
                field = self._fields[name]
                if field.type == "many2one":
                    value = value.id
                elif field.type in ("one2many", "many2many"):
                    value = value.ids[:100]
                values[name] = self._usl_audit_json_value(name, value)
            result[str(record.id)] = values
        return result

    def _usl_record_agent_mutation(self, operation, values=None, before=None, ids=None):
        if not self._usl_agent_audit_enabled():
            return
        record_ids = list(ids if ids is not None else self.ids)
        changes = {}
        if before:
            changes["before"] = before
        if values is not None:
            changes["submitted"] = self._usl_audit_json_value("values", values)
        self.env["usl.audit.event"]._record_event(
            {
                "actor_id": self.env.uid,
                "actor_is_agent": True,
                "event_type": "mutation",
                "model_name": self._name,
                "record_ids": json.dumps(record_ids),
                "record_count": len(record_ids),
                "operation": operation,
                "action_name": f"{self._name}.{operation}",
                "changes_json": json.dumps(changes, sort_keys=True),
                "origin": self._usl_audit_origin(),
                "correlation_id": self._usl_audit_correlation_id(),
            },
        )

    @api.model_create_multi
    def create(self, vals_list):
        self._usl_reject_readonly_agent_mutation("create")
        self._usl_reject_agent_identity_mutation("create")
        policy_entry = (
            self._usl_qualified_action_policy().model_operation_guard(
                self._name,
                "create",
            )
            if self.env.uid != SUPERUSER_ID
            else None
        )
        if policy_entry:
            self._usl_require_irreversible_action(
                policy_entry.action_key,
                policy_entry.action_name or f"create {self._name}",
                exact_policy_key=True,
            )
        records = super().create(vals_list)
        if records._usl_agent_audit_enabled():
            records._usl_record_agent_mutation("create", values=vals_list)
        return records

    def write(self, values):
        self._usl_reject_readonly_agent_mutation("modify")
        self._usl_reject_agent_identity_mutation("modify")
        policy_entry = (
            self._usl_qualified_action_policy().model_operation_guard(
                self._name,
                "write",
            )
            if self.env.uid != SUPERUSER_ID
            else None
        )
        if policy_entry:
            self._usl_require_irreversible_action(
                policy_entry.action_key,
                policy_entry.action_name or f"modify {self._name}",
                exact_policy_key=True,
            )
        audit_enabled = self._usl_agent_audit_enabled()
        before = self._usl_audit_before_values(values) if audit_enabled else None
        result = super().write(values)
        if audit_enabled:
            self._usl_record_agent_mutation("write", values=values, before=before)
        return result

    def unlink(self):
        self._usl_reject_readonly_agent_mutation("delete")
        self._usl_reject_agent_identity_mutation("delete")
        policy_entry = (
            self._usl_qualified_action_policy().model_operation_guard(
                self._name,
                "unlink",
            )
            if self and self.env.uid != SUPERUSER_ID
            else None
        )
        if policy_entry:
            self._usl_require_irreversible_action(
                policy_entry.action_key,
                policy_entry.action_name or f"permanently delete {self._name}",
                exact_policy_key=True,
            )
        audit_enabled = self._usl_agent_audit_enabled()
        record_ids = self.ids
        before = (
            self._usl_audit_before_values(
                [name for name in ("display_name", "name", "state", "active") if name in self._fields],
            )
            if audit_enabled
            else None
        )
        result = super().unlink()
        if audit_enabled:
            self._usl_record_agent_mutation("unlink", before=before, ids=record_ids)
        return result
