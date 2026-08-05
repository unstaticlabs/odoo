from odoo import api, fields, models
from odoo.exceptions import AccessError, ValidationError

from .constants import INTERNAL_OPERATION


class SignApproval(models.Model):
    _name = "usl.sign.approval"
    _description = "Attributable Odoo Approval"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "create_date desc, id desc"

    name = fields.Char(required=True, tracking=True)
    company_id = fields.Many2one(
        "res.company", required=True, default=lambda self: self.env.company, index=True,
    )
    record_ref = fields.Reference(
        lambda self: [
            (model.model, model.name)
            for model in self.env["ir.model"]
            .sudo()
            .search([("transient", "=", False), ("model", "not like", "usl.sign")])
        ],
        required=True,
    )
    requested_by_id = fields.Many2one(
        "res.users", required=True, default=lambda self: self.env.user, readonly=True,
    )
    approver_ids = fields.Many2many("res.users", required=True)
    state = fields.Selection(
        [
            ("pending", "Pending"),
            ("approved", "Approved"),
            ("rejected", "Rejected"),
            ("cancelled", "Cancelled"),
        ],
        default="pending",
        required=True,
        tracking=True,
    )
    decision_by_id = fields.Many2one("res.users", readonly=True)
    decision_at = fields.Datetime(readonly=True)
    decision_reason = fields.Text(readonly=True)
    policy_version = fields.Char(required=True, default="1")
    event_ids = fields.One2many("usl.sign.approval.event", "approval_id", readonly=True)

    @api.model_create_multi
    def create(self, vals_list):
        approvals = super().create(vals_list)
        for approval in approvals:
            approval._append_event("requested")
        return approvals

    def _append_event(self, event_type, reason=None):
        self.ensure_one()
        return self.env["usl.sign.approval.event"].sudo().with_context(
            usl_sign_approval_event_append=INTERNAL_OPERATION,
        ).create(
            {
                "approval_id": self.id,
                "event_type": event_type,
                "actor_id": self.env.user.id,
                "occurred_at": fields.Datetime.now(),
                "reason": reason,
            },
        )

    def _check_approver(self):
        if self.env.user not in self.approver_ids:
            msg = "Only a designated approver may decide this request."
            raise AccessError(msg)

    def action_approve(self, reason=None):
        for approval in self:
            approval._check_approver()
            if approval.state != "pending":
                msg = "Only a pending approval can be approved."
                raise ValidationError(msg)
            approval.with_context(usl_sign_approval_transition=INTERNAL_OPERATION).write(
                {
                    "state": "approved",
                    "decision_by_id": self.env.user.id,
                    "decision_at": fields.Datetime.now(),
                    "decision_reason": reason or "Approved in Odoo.",
                },
            )
            approval._append_event("approved", reason or "Approved in Odoo.")
        return True

    def action_reject(self, reason=None):
        if not reason:
            msg = "Record why the approval is rejected."
            raise ValidationError(msg)
        for approval in self:
            approval._check_approver()
            if approval.state != "pending":
                msg = "Only a pending approval can be rejected."
                raise ValidationError(msg)
            approval.with_context(usl_sign_approval_transition=INTERNAL_OPERATION).write(
                {
                    "state": "rejected",
                    "decision_by_id": self.env.user.id,
                    "decision_at": fields.Datetime.now(),
                    "decision_reason": reason,
                },
            )
            approval._append_event("rejected", reason)
        return True

    def action_open_reject(self):
        self.ensure_one()
        self._check_approver()
        if self.state != "pending":
            msg = "Only a pending approval can be rejected."
            raise ValidationError(msg)
        return self._decision_wizard_action("reject")

    def action_cancel(self, reason=None):
        if not reason:
            msg = "Record why the approval is cancelled."
            raise ValidationError(msg)
        for approval in self:
            if approval.state != "pending":
                msg = "Only a pending approval can be cancelled."
                raise ValidationError(msg)
            if approval.requested_by_id != self.env.user and not self.env.user.has_group(
                "usl_sign.group_sign_admin",
            ):
                msg = "Only the requester or a Sign manager may cancel it."
                raise AccessError(msg)
            approval.with_context(usl_sign_approval_transition=INTERNAL_OPERATION).write(
                {
                    "state": "cancelled",
                    "decision_by_id": self.env.user.id,
                    "decision_at": fields.Datetime.now(),
                    "decision_reason": reason,
                },
            )
            approval._append_event("cancelled", reason)
        return True

    def action_open_cancel(self):
        self.ensure_one()
        if self.state != "pending":
            msg = "Only a pending approval can be cancelled."
            raise ValidationError(msg)
        if self.requested_by_id != self.env.user and not self.env.user.has_group(
            "usl_sign.group_sign_admin",
        ):
            msg = "Only the requester or a Sign manager may cancel it."
            raise AccessError(msg)
        return self._decision_wizard_action("cancel")

    def _decision_wizard_action(self, decision):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": "Record approval decision",
            "res_model": "usl.sign.approval.decision.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {
                "default_approval_id": self.id,
                "default_decision": decision,
            },
        }

    def write(self, values):
        protected = {"state", "decision_by_id", "decision_at", "decision_reason"}
        if protected.intersection(values) and self.env.context.get(
            "usl_sign_approval_transition",
        ) is not INTERNAL_OPERATION:
            msg = "Use an approval decision action to change the decision."
            raise AccessError(msg)
        if self.filtered(lambda approval: approval.state != "pending") and set(values) - {
            "message_follower_ids",
            "activity_ids",
        }:
            msg = "A decided approval is immutable."
            raise ValidationError(msg)
        return super().write(values)

    def unlink(self):
        msg = "Approval decisions cannot be deleted; cancel them instead."
        raise AccessError(msg)


class SignApprovalEvent(models.Model):
    _name = "usl.sign.approval.event"
    _description = "Attributable Approval Event"
    _order = "occurred_at, id"

    approval_id = fields.Many2one(
        "usl.sign.approval", required=True, index=True, ondelete="restrict",
    )
    company_id = fields.Many2one(
        related="approval_id.company_id", store=True, readonly=True, index=True,
    )
    event_type = fields.Selection(
        [
            ("requested", "Requested"),
            ("approved", "Approved"),
            ("rejected", "Rejected"),
            ("cancelled", "Cancelled"),
        ],
        required=True,
        readonly=True,
    )
    actor_id = fields.Many2one("res.users", required=True, readonly=True, ondelete="restrict")
    occurred_at = fields.Datetime(required=True, readonly=True)
    reason = fields.Text(readonly=True)

    @api.model_create_multi
    def create(self, vals_list):
        if self.env.context.get("usl_sign_approval_event_append") is not INTERNAL_OPERATION:
            msg = "Approval events are appended by controlled actions."
            raise AccessError(msg)
        return super().create(vals_list)

    def write(self, values):
        msg = "Approval events are append-only."
        raise AccessError(msg)

    def unlink(self):
        msg = "Approval events cannot be deleted."
        raise AccessError(msg)


class SignApprovalDecisionWizard(models.TransientModel):
    _name = "usl.sign.approval.decision.wizard"
    _description = "Record Approval Decision"

    approval_id = fields.Many2one("usl.sign.approval", required=True)
    decision = fields.Selection(
        [("reject", "Reject"), ("cancel", "Cancel")], required=True,
    )
    reason = fields.Text(required=True)

    def action_confirm(self):
        self.ensure_one()
        reason = (self.reason or "").strip()
        if not reason:
            msg = "Record the reason for this decision."
            raise ValidationError(msg)
        if self.decision == "reject":
            self.approval_id.action_reject(reason)
        else:
            self.approval_id.action_cancel(reason)
        return {"type": "ir.actions.act_window_close"}
