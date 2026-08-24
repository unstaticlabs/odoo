import base64
import hashlib
import json
import logging

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError

from ..services import DSSServiceError
from .constants import INTERNAL_OPERATION


DECISION_STATES = [
    ("draft", "Draft"),
    ("waiting", "Waiting for decisions"),
    ("finalizing", "Finalizing proof"),
    ("completed", "Completed"),
    ("action_required", "Action required"),
]

_logger = logging.getLogger(__name__)


def _canonical(payload):
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode()


def _request_evidence_context():
    values = {
        "ip_address": False,
        "user_agent": False,
        "authentication_method": "odoo_session",
    }
    try:
        from odoo.http import request

        if request and request.httprequest:
            values["ip_address"] = request.httprequest.remote_addr
            values["user_agent"] = (request.httprequest.user_agent.string or "")[:500]
    except (ImportError, RuntimeError):
        pass
    return values


class SignApproval(models.Model):
    _name = "usl.sign.approval"
    _description = "Decision Request"
    # mail.thread supplies the notification API used by scheduled activities.
    # The decision form intentionally has no chatter, and no decision fields are
    # tracked, so this technical mixin does not reintroduce chatter noise.
    _inherit = ["mail.activity.mixin", "mail.thread"]
    _order = "create_date desc, id desc"

    name = fields.Char(string="Decision", required=True)
    company_id = fields.Many2one(
        "res.company", required=True, default=lambda self: self.env.company, index=True,
    )
    record_ref = fields.Reference(
        selection="_record_models", string="Linked record", required=True,
    )
    requested_by_id = fields.Many2one(
        "res.users", required=True, default=lambda self: self.env.user, readonly=True,
    )
    approver_ids = fields.Many2many(
        "res.users",
        "usl_sign_decision_approver_rel",
        "approval_id",
        "user_id",
        string="Decision-makers",
        required=True,
    )
    decision_rule = fields.Selection(
        [("any", "Any one decides"), ("all", "Everyone must approve")],
        required=True,
        default="any",
    )
    due_date = fields.Date()
    state = fields.Selection(DECISION_STATES, required=True, default="draft", readonly=True)
    outcome = fields.Selection(
        [
            ("pending", "Pending"),
            ("approved", "Approved"),
            ("rejected", "Rejected"),
            ("cancelled", "Cancelled"),
        ],
        required=True,
        default="pending",
        readonly=True,
    )
    outcome_by_id = fields.Many2one("res.users", readonly=True, ondelete="restrict")
    outcome_at = fields.Datetime(readonly=True)
    outcome_reason = fields.Text(readonly=True)
    policy_version = fields.Char(required=True, default="decision-evidence-v1")
    response_ids = fields.One2many(
        "usl.sign.approval.response", "approval_id", string="Responses", readonly=True,
    )
    event_ids = fields.One2many(
        "usl.sign.approval.event", "approval_id", string="Evidence timeline", readonly=True,
    )
    response_progress = fields.Char(compute="_compute_presentation")
    next_step = fields.Char(compute="_compute_presentation")
    can_respond = fields.Boolean(compute="_compute_presentation")
    can_send = fields.Boolean(compute="_compute_presentation")
    can_cancel = fields.Boolean(compute="_compute_presentation")
    can_retry = fields.Boolean(compute="_compute_presentation")
    signed_manifest = fields.Binary(readonly=True, attachment=True, copy=False)
    signed_manifest_filename = fields.Char(readonly=True, copy=False)
    signed_manifest_sha256 = fields.Char(readonly=True, copy=False, index=True)
    receipt_data = fields.Binary(
        string="Decision proof", readonly=True, attachment=True, copy=False,
    )
    receipt_filename = fields.Char(readonly=True, copy=False)
    receipt_sha256 = fields.Char(readonly=True, copy=False, index=True)
    validation_report = fields.Binary(readonly=True, attachment=True, copy=False)
    validation_report_filename = fields.Char(readonly=True, copy=False)
    proof_status = fields.Selection(
        [
            ("not_ready", "Not ready"),
            ("building", "Building"),
            ("valid", "Valid"),
            ("incomplete", "Incomplete"),
        ],
        required=True,
        default="not_ready",
        readonly=True,
    )
    archive_status = fields.Selection(
        [
            ("not_ready", "Not ready"),
            ("processing", "Archiving"),
            ("archived", "Archived"),
            ("failed", "Failed"),
        ],
        required=True,
        default="not_ready",
        readonly=True,
    )
    archive_operation_id = fields.Many2one(
        "usl.document.operation", readonly=True, copy=False, ondelete="restrict",
    )
    archive_document_id = fields.Many2one(
        "usl.document", readonly=True, copy=False, ondelete="restrict",
    )
    last_error = fields.Char(readonly=True, copy=False)
    recovery_action = fields.Char(readonly=True, copy=False)
    completed_at = fields.Datetime(readonly=True, copy=False, index=True)

    @api.model
    def _record_models(self):
        return self.env["sign.oca.request"]._sign_business_record_models()

    @api.depends("state", "outcome", "response_ids.state", "approver_ids")
    @api.depends_context("uid")
    def _compute_presentation(self):
        for approval in self:
            decided = len(
                approval.response_ids.filtered(lambda response: response.state != "pending"),
            )
            approval.response_progress = f"{decided} of {len(approval.approver_ids)} responded"
            my_response = approval.response_ids.filtered(
                lambda response: response.user_id == self.env.user
                and response.state == "pending",
            )
            approval.can_respond = approval.state == "waiting" and bool(my_response)
            approval.can_send = approval.state == "draft" and (
                approval.requested_by_id == self.env.user
                or self.env.user.has_group("usl_sign.group_sign_admin")
            )
            approval.can_cancel = approval.state in {"draft", "waiting"} and (
                approval.requested_by_id == self.env.user
                or self.env.user.has_group("usl_sign.group_sign_admin")
            )
            approval.can_retry = approval.state == "action_required" and (
                approval.requested_by_id == self.env.user
                or self.env.user.has_group("usl_sign.group_sign_admin")
                or self.env.user.has_group("usl_sign.group_sign_evidence_reviewer")
            )
            approval.next_step = {
                "draft": _("Review the decision-makers and send the request."),
                "waiting": _("Record your decision.")
                if my_response
                else _("Waiting for the named decision-makers."),
                "finalizing": _(
                    "The decision is fixed; proof is being validated and archived.",
                ),
                "completed": _(
                    "The decision proof is valid, archived, and ready to retrieve.",
                ),
                "action_required": approval.recovery_action
                or _("Retry proof finalization."),
            }.get(approval.state, "")

    @api.model_create_multi
    def create(self, values_list):
        if not self.env.user.has_group("usl_sign.group_sign_user") and not self.env.su:
            raise AccessError("Sign user access is required to request a recorded decision.")
        approvals = super().create(values_list)
        for approval in approvals:
            approval._validate_participants_and_record()
            if not approval.response_ids:
                self.env["usl.sign.approval.response"].sudo().with_context(
                    usl_sign_decision_response_create=INTERNAL_OPERATION,
                ).create(
                    [
                        {"approval_id": approval.id, "user_id": user.id}
                        for user in approval.approver_ids
                    ],
                )
            approval._append_event(
                "created", payload={"decision_rule": approval.decision_rule},
            )
        return approvals

    def _validate_participants_and_record(self):
        for approval in self:
            if not approval.approver_ids:
                raise ValidationError("Choose at least one decision-maker.")
            if any(
                approval.company_id not in user.company_ids
                for user in approval.approver_ids
            ):
                raise ValidationError(
                    "Every decision-maker must have access to the decision company.",
                )
            record = approval.record_ref.exists()
            if not record or record._name not in dict(approval._record_models()):
                raise ValidationError("Choose a supported business record.")
            record.check_access("read")
            record_company = (
                record
                if record._name == "res.company"
                else getattr(record, "company_id", False)
            )
            if record_company and record_company != approval.company_id:
                raise ValidationError(
                    "The decision and linked record must belong to the same company.",
                )

    def action_send(self):
        for approval in self:
            if not approval.can_send:
                raise AccessError(
                    "Only the requester or a Sign administrator can send this decision request.",
                )
            if approval.state != "draft":
                raise ValidationError("Only a draft decision request can be sent.")
            approval._validate_participants_and_record()
            approval._operational_write({"state": "waiting"})
            approval._append_event(
                "sent", payload={"approver_ids": approval.approver_ids.ids},
            )
            for response in approval.response_ids:
                approval.activity_schedule(
                    "mail.mail_activity_data_todo",
                    user_id=response.user_id.id,
                    summary=_("Decision requested: %s") % approval.name,
                    note=_(
                        "Open the linked decision request and approve or reject it.",
                    ),
                    date_deadline=approval.due_date,
                )
        return True

    def action_open_approve(self):
        return self._decision_wizard_action("approve")

    def action_open_reject(self):
        return self._decision_wizard_action("reject")

    def action_open_cancel(self):
        return self._decision_wizard_action("cancel")

    def _decision_wizard_action(self, decision):
        self.ensure_one()
        if decision in {"approve", "reject"} and not self.can_respond:
            raise AccessError("Only a pending named decision-maker can respond.")
        if decision == "cancel" and not self.can_cancel:
            raise AccessError(
                "Only the requester or a Sign administrator can cancel this request.",
            )
        return {
            "type": "ir.actions.act_window",
            "name": "Confirm decision",
            "res_model": "usl.sign.approval.decision.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {
                "default_approval_id": self.id,
                "default_decision": decision,
            },
        }

    def _record_response(self, decision, reason):
        self.ensure_one()
        if self.state != "waiting" or decision not in {"approved", "rejected"}:
            raise ValidationError(
                "This decision request is no longer awaiting your response.",
            )
        response = self.response_ids.filtered(
            lambda item: item.user_id == self.env.user and item.state == "pending",
        )
        if len(response) != 1:
            raise AccessError("Only a pending named decision-maker can respond.")
        now = fields.Datetime.now()
        response.sudo().with_context(
            usl_sign_decision_response_write=INTERNAL_OPERATION,
        ).write(
            {"state": decision, "responded_at": now, "reason": reason or False},
        )
        self._append_event(decision, reason=reason, response=response)
        self.activity_ids.filtered(
            lambda activity: activity.user_id == self.env.user,
        ).action_done()
        outcome = False
        if self.decision_rule == "any" or decision == "rejected":
            outcome = decision
        elif not self.response_ids.filtered(lambda item: item.state == "pending"):
            outcome = "approved"
        if outcome:
            self._finalize_outcome(outcome, self.env.user, reason)
        return True

    def _finalize_outcome(self, outcome, actor, reason):
        self.ensure_one()
        self._operational_write(
            {
                "state": "finalizing",
                "outcome": outcome,
                "outcome_by_id": actor.id,
                "outcome_at": fields.Datetime.now(),
                "outcome_reason": reason or False,
                "proof_status": "building",
                "archive_status": "not_ready",
                "last_error": False,
                "recovery_action": False,
            },
        )
        self._append_event(
            "outcome_recorded", reason=reason, payload={"outcome": outcome},
        )
        self.activity_ids.action_done()
        self._build_and_archive_proof()

    def action_retry_proof(self):
        for approval in self:
            if not approval.can_retry:
                raise AccessError(
                    "Only the requester, a Sign administrator, or an evidence reviewer can retry decision proof.",
                )
            if approval.state != "action_required":
                raise ValidationError("Only a decision needing action can be retried.")
            approval._operational_write({"state": "finalizing", "last_error": False})
            if approval.receipt_data:
                approval._archive_receipt(force=True)
            else:
                approval._build_and_archive_proof()
        return True

    def _build_and_archive_proof(self):
        for approval in self:
            try:
                envelope, receipt, validation = approval._build_decision_proof()
            except Exception as error:  # noqa: BLE001 -- normalized fail-closed state
                _logger.exception(
                    "Decision proof finalization failed for decision %s",
                    approval.id,
                )
                approval._operational_write(
                    {
                        "state": "action_required",
                        "proof_status": "incomplete",
                        "last_error": "Decision proof could not be generated or validated.",
                        "recovery_action": (
                            "Restore DSS evidence services and retry proof finalization."
                        ),
                    },
                )
                approval._append_event(
                    "proof_failed", payload={"error": type(error).__name__},
                )
                continue
            manifest_name = f"{approval.name}-signed-decision-manifest.json"
            receipt_name = f"{approval.name}-decision-proof.pdf"
            validation_name = f"{approval.name}-decision-validation.json"
            approval._operational_write(
                {
                    "signed_manifest": base64.b64encode(envelope),
                    "signed_manifest_filename": manifest_name,
                    "signed_manifest_sha256": hashlib.sha256(envelope).hexdigest(),
                    "receipt_data": base64.b64encode(receipt),
                    "receipt_filename": receipt_name,
                    "receipt_sha256": hashlib.sha256(receipt).hexdigest(),
                    "validation_report": base64.b64encode(
                        json.dumps(validation, sort_keys=True, indent=2).encode(),
                    ),
                    "validation_report_filename": validation_name,
                    "proof_status": "valid",
                },
            )
            approval._append_event(
                "proof_validated",
                payload={"receipt_sha256": approval.receipt_sha256},
            )
            approval._archive_receipt()

    def _decision_manifest_payload(self):
        self.ensure_one()
        head = self.event_ids.verify_chain()
        return {
            "format": "usl-odoo-decision-proof-v1",
            "decision_id": self.id,
            "decision": self.name,
            "company_id": self.company_id.id,
            "linked_record": {
                "model": self.record_ref._name,
                "id": self.record_ref.id,
                "name": self.record_ref.display_name,
            },
            "requested_by": {
                "id": self.requested_by_id.id,
                "name": self.requested_by_id.name,
            },
            "decision_rule": self.decision_rule,
            "outcome": self.outcome,
            "outcome_at": fields.Datetime.to_string(self.outcome_at),
            "outcome_by": {
                "id": self.outcome_by_id.id,
                "name": self.outcome_by_id.name,
            },
            "outcome_reason": self.outcome_reason or None,
            "policy_version": self.policy_version,
            "responses": [
                {
                    "user_id": response.user_id.id,
                    "name": response.user_id.name,
                    "response": response.state,
                    "responded_at": (
                        fields.Datetime.to_string(response.responded_at)
                        if response.responded_at
                        else None
                    ),
                    "reason": response.reason or None,
                }
                for response in self.response_ids.sorted(lambda item: item.id)
            ],
            "event_chain_head": head.event_hash if head else None,
        }

    def _event_chain_artifact(self):
        return _canonical(
            {
                "format": "usl-odoo-decision-event-chain-v1",
                "decision_id": self.id,
                "events": [
                    event.evidence_values()
                    for event in self.event_ids.sorted("sequence")
                ],
            },
        )

    def _build_decision_proof(self):
        self.ensure_one()
        client = self.env["sign.oca.request"]._sign_dss_client()
        manifest = _canonical(self._decision_manifest_payload())
        signed = client.sign_manifest(manifest)
        envelope = _canonical(
            {
                "format": "usl-signed-odoo-decision-proof-v1",
                "manifest": base64.b64encode(manifest).decode(),
                "manifest_sha256": signed["manifestSha256"],
                "signature": signed["signature"],
                "signature_algorithm": signed["signatureAlgorithm"],
                "certificate_chain": signed["certificateChain"],
            },
        )
        result = client.build_dossier(
            title=f"Decision proof — {self.name}",
            summary=[
                f"Company: {self.company_id.name}",
                f"Linked record: {self.record_ref.display_name}",
                f"Outcome: {dict(self._fields['outcome']._description_selection(self.env)).get(self.outcome)}",
                f"Decision rule: {dict(self._fields['decision_rule']._description_selection(self.env)).get(self.decision_rule)}",
                f"Requested by: {self.requested_by_id.name}",
                f"Outcome recorded: {fields.Datetime.to_string(self.outcome_at)}",
                (
                    "This is evidence of an attributable Odoo business decision, "
                    "not an electronic signature."
                ),
            ],
            artifacts=[
                {
                    "name": f"{self.name}-signed-decision-manifest.json",
                    "content": envelope,
                    "mimetype": "application/json",
                    "relationship": "Data",
                    "description": "Signed canonical decision manifest",
                },
                {
                    "name": f"{self.name}-decision-event-chain.json",
                    "content": self._event_chain_artifact(),
                    "mimetype": "application/json",
                    "relationship": "Data",
                    "description": "Tamper-evident decision event chain",
                },
            ],
        )
        receipt = base64.b64decode(result["document"])
        preflight = client.validate_pdfa(receipt)
        if not preflight.get("compliant"):
            raise DSSServiceError(
                "veraPDF rejected the decision proof before sealing.",
            )
        sealed = client.seal(
            receipt,
            request_reference=f"ODOO-DECISION-{self.id}",
            timestamp=self.company_id.sign_rfc3161_enabled,
        )
        receipt = base64.b64decode(sealed["document"])
        pdfa = client.validate_pdfa(receipt)
        validation = client.validate(receipt, expected_level="standard")
        if (
            not pdfa.get("compliant")
            or validation.get("status") != "valid"
            or int(validation.get("signatureCount") or 0) < 1
        ):
            raise DSSServiceError(
                "The sealed decision proof did not pass independent validation.",
            )
        return envelope, receipt, {"pdfa": pdfa, "signature": validation}

    def _archive_receipt(self, force=False):
        for approval in self:
            if not approval.receipt_data or approval.proof_status != "valid":
                raise ValidationError(
                    "Generate and validate the decision proof before archival.",
                )
            if approval.archive_status in {"processing", "archived"} and not force:
                continue
            try:
                result = (
                    self.env["usl.document"]
                    .with_user(self.env.ref("base.user_root"))
                    .sudo()
                    .with_company(approval.company_id)
                    .upload_from_odoo(
                        approval.receipt_filename,
                        approval.receipt_data,
                        "application/pdf",
                        res_model=approval._name,
                        res_id=approval.id,
                        company_id=approval.company_id.id,
                        confidentiality="private",
                        source="odoo_generated",
                    )
                )
                if result.get("state") == "duplicate" and result.get("document_id"):
                    approval._operational_write(
                        {
                            "archive_status": "archived",
                            "archive_operation_id": False,
                            "archive_document_id": result["document_id"],
                        },
                    )
                elif result.get("operation_id"):
                    approval._operational_write(
                        {
                            "archive_status": "processing",
                            "archive_operation_id": result["operation_id"],
                        },
                    )
                else:
                    raise ValueError("Paperless returned no archive relationship.")
            except Exception as error:  # noqa: BLE001 -- normalized recovery state
                approval._operational_write(
                    {
                        "state": "action_required",
                        "archive_status": "failed",
                        "last_error": "Paperless could not archive the decision proof.",
                        "recovery_action": (
                            "Restore Paperless and retry proof finalization."
                        ),
                    },
                )
                approval._append_event(
                    "archive_failed", payload={"error": type(error).__name__},
                )
                continue
            approval._append_event(
                "archive_queued", payload={"state": result.get("state")},
            )
            approval._reconcile_archive()

    def _reconcile_archive(self):
        for approval in self:
            operation = approval.sudo().archive_operation_id
            if operation and operation.state == "processing":
                try:
                    operation.poll()
                    operation.invalidate_recordset()
                except Exception:  # noqa: BLE001 -- safe retry state
                    approval._operational_write(
                        {
                            "state": "action_required",
                            "archive_status": "failed",
                            "last_error": (
                                "Paperless archival status could not be confirmed."
                            ),
                            "recovery_action": (
                                "Restore Paperless and retry proof finalization."
                            ),
                        },
                    )
                    continue
            if operation and operation.state == "archived" and operation.document_id:
                approval._operational_write(
                    {
                        "archive_status": "archived",
                        "archive_document_id": operation.document_id.id,
                    },
                )
            elif operation and operation.state == "failed":
                approval._operational_write(
                    {
                        "state": "action_required",
                        "archive_status": "failed",
                        "last_error": "Paperless rejected the decision proof.",
                        "recovery_action": (
                            "Restore Paperless and retry proof finalization."
                        ),
                    },
                )
                continue
            if approval.archive_status == "archived" and approval.state == "finalizing":
                approval._operational_write(
                    {
                        "state": "completed",
                        "completed_at": fields.Datetime.now(),
                        "last_error": False,
                        "recovery_action": False,
                    },
                )
                approval._append_event(
                    "completed",
                    payload={
                        "receipt_sha256": approval.receipt_sha256,
                        "archive_document_id": approval.archive_document_id.id,
                    },
                )
        return True

    @api.model
    def _cron_reconcile(self):
        approvals = self.search(
            [
                ("state", "in", ["finalizing", "action_required"]),
                ("archive_status", "in", ["processing", "failed"]),
            ],
            limit=100,
        )
        approvals._reconcile_archive()

    def action_open_record(self):
        self.ensure_one()
        self.record_ref.check_access("read")
        return {
            "type": "ir.actions.act_window",
            "name": self.record_ref.display_name,
            "res_model": self.record_ref._name,
            "res_id": self.record_ref.id,
            "view_mode": "form",
            "views": [(False, "form")],
        }

    def action_open_archive(self):
        self.ensure_one()
        if not self.archive_document_id:
            raise UserError("The decision proof has not been archived yet.")
        return self.archive_document_id.action_open_paperless()

    def _append_event(
        self,
        event_type,
        reason=None,
        payload=None,
        response=None,
    ):
        self.ensure_one()
        self.env.cr.execute(
            "SELECT id FROM usl_sign_approval WHERE id = %s FOR UPDATE",
            [self.id],
        )
        previous = self.event_ids.sorted("sequence")[-1:]
        evidence_context = _request_evidence_context()
        occurred_at = fields.Datetime.now()
        sequence = (previous.sequence or 0) + 1
        event_payload = {
            "decision_id": self.id,
            "event_type": event_type,
            "actor_id": self.env.user.id,
            "occurred_at": fields.Datetime.to_string(occurred_at),
            "workflow_state": self.state,
            "outcome": self.outcome,
            "reason": reason or None,
            "response_id": response.id if response else None,
            "data": payload or {},
        }
        payload_sha256 = hashlib.sha256(_canonical(event_payload)).hexdigest()
        previous_hash = previous.event_hash or ""
        event_hash = hashlib.sha256(
            f"{sequence}|{previous_hash}|{payload_sha256}".encode(),
        ).hexdigest()
        return self.env["usl.sign.approval.event"].sudo().with_context(
            usl_sign_approval_event_append=INTERNAL_OPERATION,
        ).create(
            {
                "approval_id": self.id,
                "sequence": sequence,
                "event_type": event_type,
                "actor_id": self.env.user.id,
                "occurred_at": occurred_at,
                "reason": reason,
                "response_id": response.id if response else False,
                "authentication_method": evidence_context["authentication_method"],
                "ip_address": evidence_context["ip_address"],
                "user_agent": evidence_context["user_agent"],
                "payload": event_payload,
                "previous_hash": previous_hash or False,
                "payload_sha256": payload_sha256,
                "event_hash": event_hash,
            },
        )

    def _operational_write(self, values):
        return self.sudo().with_context(
            usl_sign_approval_transition=INTERNAL_OPERATION,
        ).write(values)

    def write(self, values):
        protected = {
            "state",
            "outcome",
            "outcome_by_id",
            "outcome_at",
            "outcome_reason",
            "signed_manifest",
            "signed_manifest_filename",
            "signed_manifest_sha256",
            "receipt_data",
            "receipt_filename",
            "receipt_sha256",
            "validation_report",
            "validation_report_filename",
            "proof_status",
            "archive_status",
            "archive_operation_id",
            "archive_document_id",
            "last_error",
            "recovery_action",
            "completed_at",
        }
        internal = (
            self.env.context.get("usl_sign_approval_transition") is INTERNAL_OPERATION
        )
        if protected.intersection(values) and not internal:
            raise AccessError("Use the controlled decision and recovery actions.")
        if (
            not internal
            and not self.env.user.has_group("usl_sign.group_sign_admin")
            and self.filtered(lambda approval: approval.requested_by_id != self.env.user)
        ):
            raise AccessError("Only the requester can edit a draft decision request.")
        if not internal and self.filtered(lambda approval: approval.state != "draft"):
            raise ValidationError("A sent decision request is immutable.")
        result = super().write(values)
        if not internal and {"record_ref", "company_id", "approver_ids"}.intersection(values):
            self._validate_participants_and_record()
            for approval in self:
                approval.response_ids.sudo().with_context(
                    usl_sign_decision_response_create=INTERNAL_OPERATION,
                ).unlink()
                self.env["usl.sign.approval.response"].sudo().with_context(
                    usl_sign_decision_response_create=INTERNAL_OPERATION,
                ).create(
                    [
                        {"approval_id": approval.id, "user_id": user.id}
                        for user in approval.approver_ids
                    ],
                )
        return result

    def unlink(self):
        raise AccessError(
            "Decision requests cannot be deleted; cancel them with a recorded outcome.",
        )


class SignApprovalResponse(models.Model):
    _name = "usl.sign.approval.response"
    _description = "Decision-maker Response"
    _order = "id"

    approval_id = fields.Many2one(
        "usl.sign.approval", required=True, index=True, ondelete="restrict",
    )
    company_id = fields.Many2one(
        related="approval_id.company_id", store=True, index=True,
    )
    user_id = fields.Many2one(
        "res.users", required=True, readonly=True, ondelete="restrict",
    )
    state = fields.Selection(
        [
            ("pending", "Waiting"),
            ("approved", "Approved"),
            ("rejected", "Rejected"),
        ],
        required=True,
        default="pending",
        readonly=True,
    )
    responded_at = fields.Datetime(readonly=True)
    reason = fields.Text(readonly=True)

    _approval_user_unique = models.Constraint(
        "UNIQUE(approval_id, user_id)",
        "A decision-maker can have only one response per request.",
    )

    @api.model_create_multi
    def create(self, values_list):
        if (
            self.env.context.get("usl_sign_decision_response_create")
            is not INTERNAL_OPERATION
        ):
            raise AccessError("Decision responses are created with the request.")
        return super().create(values_list)

    def write(self, values):
        if (
            self.env.context.get("usl_sign_decision_response_write")
            is not INTERNAL_OPERATION
        ):
            raise AccessError("Use a decision action to record a response.")
        return super().write(values)

    def unlink(self):
        if (
            self.env.context.get("usl_sign_decision_response_create")
            is not INTERNAL_OPERATION
        ):
            raise AccessError("Decision responses cannot be removed.")
        return super().unlink()


class SignApprovalEvent(models.Model):
    _name = "usl.sign.approval.event"
    _description = "Decision Evidence Event"
    _order = "sequence, id"

    approval_id = fields.Many2one(
        "usl.sign.approval", required=True, index=True, ondelete="restrict",
    )
    company_id = fields.Many2one(
        related="approval_id.company_id", store=True, readonly=True, index=True,
    )
    sequence = fields.Integer(required=True, readonly=True)
    event_type = fields.Char(required=True, readonly=True)
    actor_id = fields.Many2one(
        "res.users", required=True, readonly=True, ondelete="restrict",
    )
    occurred_at = fields.Datetime(required=True, readonly=True)
    reason = fields.Text(readonly=True)
    response_id = fields.Many2one(
        "usl.sign.approval.response", readonly=True, ondelete="restrict",
    )
    authentication_method = fields.Char(readonly=True)
    ip_address = fields.Char(readonly=True)
    user_agent = fields.Char(readonly=True)
    payload = fields.Json(readonly=True)
    previous_hash = fields.Char(readonly=True)
    payload_sha256 = fields.Char(required=True, readonly=True)
    event_hash = fields.Char(required=True, readonly=True, index=True)

    _approval_sequence_unique = models.Constraint(
        "UNIQUE(approval_id, sequence)",
        "Decision event sequences must be unique.",
    )

    def evidence_values(self):
        self.ensure_one()
        return {
            "sequence": self.sequence,
            "event_type": self.event_type,
            "actor_id": self.actor_id.id,
            "occurred_at": fields.Datetime.to_string(self.occurred_at),
            "reason": self.reason or None,
            "authentication_method": self.authentication_method,
            "ip_address": self.ip_address or None,
            "user_agent": self.user_agent or None,
            "payload": self.payload,
            "previous_hash": self.previous_hash or None,
            "payload_sha256": self.payload_sha256,
            "event_hash": self.event_hash,
        }

    def verify_chain(self):
        previous_hash = ""
        expected_sequence = 1
        events = self.sorted("sequence")
        for event in events:
            if (
                event.sequence != expected_sequence
                or (event.previous_hash or "") != previous_hash
            ):
                raise ValidationError(
                    "The decision event sequence is incomplete or reordered.",
                )
            payload_sha256 = hashlib.sha256(_canonical(event.payload)).hexdigest()
            expected_hash = hashlib.sha256(
                f"{event.sequence}|{previous_hash}|{payload_sha256}".encode(),
            ).hexdigest()
            if (
                payload_sha256 != event.payload_sha256
                or expected_hash != event.event_hash
            ):
                raise ValidationError(
                    "The decision event chain failed integrity verification.",
                )
            previous_hash = event.event_hash
            expected_sequence += 1
        return events[-1:]

    @api.model_create_multi
    def create(self, values_list):
        if (
            self.env.context.get("usl_sign_approval_event_append")
            is not INTERNAL_OPERATION
        ):
            raise AccessError(
                "Decision evidence events are appended by controlled actions.",
            )
        return super().create(values_list)

    def write(self, values):
        raise AccessError("Decision evidence events are append-only.")

    def unlink(self):
        raise AccessError("Decision evidence events cannot be deleted.")


class SignApprovalDecisionWizard(models.TransientModel):
    _name = "usl.sign.approval.decision.wizard"
    _description = "Confirm Business Decision"

    approval_id = fields.Many2one("usl.sign.approval", required=True)
    decision = fields.Selection(
        [
            ("approve", "Approve"),
            ("reject", "Reject"),
            ("cancel", "Cancel request"),
        ],
        required=True,
    )
    reason = fields.Text()
    consequence = fields.Char(compute="_compute_consequence")

    @api.depends("approval_id", "approval_id.decision_rule", "decision")
    def _compute_consequence(self):
        for wizard in self:
            if wizard.decision == "cancel":
                wizard.consequence = (
                    "The cancellation will be recorded and a decision proof will be archived."
                )
            elif wizard.approval_id.decision_rule == "any":
                wizard.consequence = "Your response will decide this request immediately."
            elif wizard.decision == "reject":
                wizard.consequence = "A rejection closes this request immediately."
            else:
                wizard.consequence = (
                    "The request completes after every named decision-maker approves."
                )

    def action_confirm(self):
        self.ensure_one()
        reason = (self.reason or "").strip()
        if self.decision in {"reject", "cancel"} and not reason:
            raise ValidationError(
                "Record the reason for rejecting or cancelling this decision.",
            )
        if self.decision == "cancel":
            if not self.approval_id.can_cancel:
                raise AccessError(
                    "Only the requester or a Sign administrator can cancel this request.",
                )
            if self.approval_id.state not in {"draft", "waiting"}:
                raise ValidationError(
                    "This decision request can no longer be cancelled.",
                )
            self.approval_id._finalize_outcome("cancelled", self.env.user, reason)
        else:
            self.approval_id._record_response(
                "approved" if self.decision == "approve" else "rejected",
                reason,
            )
        return {"type": "ir.actions.act_window_close"}


class SignRequestDecisionArchiveCron(models.Model):
    _inherit = "sign.oca.request"

    @api.model
    def _cron_sign_operations(self):
        result = super()._cron_sign_operations()
        self.env["usl.sign.approval"]._cron_reconcile()
        return result
