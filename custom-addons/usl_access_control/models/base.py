import json
import logging
import uuid

from lxml import etree

from odoo import SUPERUSER_ID, api, models
from odoo.exceptions import AccessError

_logger = logging.getLogger(__name__)

_IRREVERSIBLE_UNLINK_MODELS = frozenset(
    {
        "account.move",
        "account.payment",
        "b2c.accounting.session",
        "b2c.channel",
        "b2c.fulfilment.event",
        "b2c.order",
        "b2c.payment.event",
        "b2c.provider.evidence",
        "ir.attachment",
        "mail.message",
        "mail.tracking.value",
        "product.product",
        "product.template",
        "project.project",
        "project.task",
        "purchase.order",
        "res.partner",
        "sale.order",
        "stock.picking",
        "usl.document",
        "usl.platform.billing.payout",
        "usl.platform.billing.session",
        "usl.tese.payslip",
    },
)

_PROTECTED_TECHNICAL_MODELS = frozenset(
    {
        "auth.oauth.provider",
        "base.automation",
        "ir.actions.server",
        "ir.config_parameter",
        "ir.cron",
        "ir.default",
        "ir.logging",
        "ir.model",
        "ir.model.access",
        "ir.model.data",
        "ir.model.fields",
        "ir.model.fields.selection",
        "ir.module.module",
        "ir.rule",
        "ir.ui.menu",
        "ir.ui.view",
        "res.groups",
        "res.groups.privilege",
        "usl.oidc.identity",
    },
)

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


class Base(models.AbstractModel):
    _inherit = "base"

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
    def _usl_log_denied_protected_action(self, action_name):
        _logger.warning(
            "USL_PROTECTED_ACTION_DENIED %s",
            json.dumps(
                {
                    "action": action_name,
                    "actor_id": self.env.uid,
                    "actor_is_agent": self._usl_actor_is_agent(),
                    "correlation_id": self._usl_audit_correlation_id(),
                    "model": self._name,
                    "origin": self._usl_audit_origin(),
                    "record_ids": self.ids,
                },
                sort_keys=True,
            ),
        )

    def _usl_require_irreversible_action(self, action_name):
        # UID 1 is Odoo's non-interactive internal service identity. Module
        # loading, migrations and registry maintenance legitimately run here;
        # sudo() by an ordinary caller keeps that caller's uid and is not this
        # bypass.
        if self.env.uid == SUPERUSER_ID:
            return
        if not self._usl_actor_may_perform_irreversible_actions():
            self._usl_log_denied_protected_action(action_name)
            if self._usl_actor_is_agent():
                raise AccessError(
                    self.env._(
                        "AI Agents cannot perform irreversible actions. "
                        "Use a recoverable workflow or ask an authorized human.",
                    ),
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
        if self._name in _PROTECTED_TECHNICAL_MODELS:
            self._usl_require_irreversible_action(f"create {self._name}")
        records = super().create(vals_list)
        if records._usl_agent_audit_enabled():
            records._usl_record_agent_mutation("create", values=vals_list)
        return records

    def write(self, values):
        if self._name in _PROTECTED_TECHNICAL_MODELS:
            self._usl_require_irreversible_action(f"modify {self._name}")
        audit_enabled = self._usl_agent_audit_enabled()
        before = self._usl_audit_before_values(values) if audit_enabled else None
        result = super().write(values)
        if audit_enabled:
            self._usl_record_agent_mutation("write", values=values, before=before)
        return result

    def unlink(self):
        if self and self._name in _PROTECTED_TECHNICAL_MODELS | _IRREVERSIBLE_UNLINK_MODELS:
            self._usl_require_irreversible_action(f"permanently delete {self._name}")
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
