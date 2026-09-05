from odoo import api, fields, models
from odoo.exceptions import UserError, ValidationError

from .agent_policy_tokens import (
    AGENT_COLLABORATION_CONTEXT_KEY,
    AGENT_COLLABORATION_TOKEN,
)


class UslAuditEvent(models.Model):
    _name = "usl.audit.event"
    _description = "USL Distribution Audit Event"
    _order = "occurred_at desc, id desc"
    _rec_name = "action_name"

    occurred_at = fields.Datetime(
        required=True,
        default=fields.Datetime.now,
        readonly=True,
        index=True,
    )
    actor_id = fields.Many2one(
        "res.users",
        required=True,
        ondelete="restrict",
        readonly=True,
        index=True,
    )
    actor_is_agent = fields.Boolean(required=True, readonly=True, index=True)
    agent_id = fields.Many2one("usl.agent", readonly=True, index=True, ondelete="restrict")
    owner_id = fields.Many2one("res.users", readonly=True, index=True, ondelete="restrict")
    credential_id = fields.Many2one(
        "usl.agent.credential",
        readonly=True,
        index=True,
        ondelete="set null",
    )
    company_id = fields.Many2one("res.company", readonly=True, index=True, ondelete="restrict")
    event_type = fields.Selection(
        [
            ("mutation", "Agent mutation"),
            ("protected_action", "Protected action"),
            ("api_call", "Agent API call"),
        ],
        required=True,
        readonly=True,
        index=True,
    )
    outcome = fields.Selection(
        [("succeeded", "Succeeded"), ("denied", "Denied"), ("failed", "Failed")],
        required=True,
        default="succeeded",
        readonly=True,
    )
    model_name = fields.Char(required=True, readonly=True, index=True)
    record_ids = fields.Text(readonly=True)
    record_count = fields.Integer(readonly=True)
    operation = fields.Selection(
        [
            ("create", "Create"),
            ("write", "Update"),
            ("unlink", "Delete"),
            ("action", "Action"),
            ("read", "Read"),
            ("call", "Call"),
        ],
        required=True,
        readonly=True,
    )
    action_name = fields.Char(required=True, readonly=True)
    action_key = fields.Char(readonly=True, index=True)
    policy_digest = fields.Char(readonly=True, index=True)
    changes_json = fields.Text(readonly=True)
    origin = fields.Char(required=True, readonly=True)
    correlation_id = fields.Char(readonly=True, index=True)
    request_id = fields.Char(readonly=True, index=True)
    remote_address = fields.Char(readonly=True)
    user_agent = fields.Char(readonly=True)

    @api.model
    def _record_event(self, values):
        """Create immutable evidence without granting callers create access."""
        if values.get("event_type") == "protected_action" and not all(
            values.get(field_name) for field_name in ("action_key", "policy_digest")
        ):
            raise ValidationError(
                self.env._(
                    "New protected-action audit events require an action key and policy digest.",
                ),
            )
        return self.sudo().with_context(
            {
                "usl_skip_distribution_audit": True,
                AGENT_COLLABORATION_CONTEXT_KEY: AGENT_COLLABORATION_TOKEN,
            },
        ).create(values)

    def write(self, values):
        raise UserError(self.env._("Distribution audit events are immutable."))

    def unlink(self):
        raise UserError(self.env._("Distribution audit events cannot be deleted."))
