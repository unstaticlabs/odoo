import base64
import hashlib
import json
import secrets
from datetime import timedelta
from io import BytesIO
from urllib.parse import quote

from cryptography import x509
from markupsafe import escape
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.http import request as http_request
from odoo.tools.misc import format_datetime
from odoo.tools.pdf import PdfReader, PdfWriter

from .constants import (
    AUTHENTICATION_METHODS,
    CANCELLABLE_REQUEST_STATES,
    DOCUMENT_CATEGORIES,
    EXPIRABLE_REQUEST_STATES,
    INTERNAL_OPERATION,
    MUTABLE_REQUEST_STATES,
    REQUEST_STATES,
    SIGNER_STATES,
    TERMINAL_REQUEST_STATES,
    TRUST_LEVELS,
)
from .document import _add_page
from .template import (
    EDITOR_ROLE_COLORS,
    FIELD_PRESENTATION,
    _field_info,
    _field_kind,
    _validate_complete_editor_geometry,
    _validate_editor_geometry,
    _validate_editor_uuid,
)
from odoo.addons.usl_sign.services import (
    DSSClient,
    DSSRejectedError,
    DSSServiceError,
    base64_text,
    field_content,
    field_value,
)

TRANSITIONS = {
    "draft": {"ready", "cancelled"},
    "ready": {
        "sent",
        "waiting_enrollment",
        "waiting_external",
        "cancelled",
        "action_required",
    },
    "sent": {"viewed", "partial", "waiting_enrollment", "validating", "declined", "expired", "cancelled", "action_required"},
    "viewed": {"partial", "waiting_enrollment", "validating", "declined", "expired", "cancelled", "action_required"},
    "partial": {"waiting_enrollment", "validating", "declined", "expired", "cancelled", "action_required"},
    "waiting_enrollment": {"sent", "partial", "expired", "cancelled", "action_required"},
    "waiting_external": {"signed_to_import", "expired", "cancelled", "action_required"},
    "signed_to_import": {"validating", "cancelled", "validation_failed", "action_required"},
    "validating": {"evidence_incomplete", "validation_failed", "action_required"},
    "evidence_incomplete": {"completed", "action_required"},
    "action_required": {
        "ready",
        "sent",
        "partial",
        "waiting_enrollment",
        "waiting_external",
        "signed_to_import",
        "validating",
        "evidence_incomplete",
        "cancelled",
    },
}


class SignRequest(models.Model):
    _inherit = "sign.oca.request"
    _order = "create_date desc, id desc"

    def preview(self):
        """Open the validated result, never an editor overlay or unchecked file."""
        self.ensure_one()
        if (
            self.state in {"evidence_incomplete", "completed"}
            and self.validation_status == "valid"
            and self.final_data
            and self.final_filename
        ):
            return {
                "type": "ir.actions.act_url",
                "url": (
                    f"/web/content/{self._name}/{self.id}/final_data/"
                    f"{quote(self.final_filename)}?download=false"
                ),
                "target": "new",
            }
        return super().preview()

    @api.depends("signer_ids", "signer_ids.is_allow_signature")
    @api.depends_context("uid")
    def _compute_signer_id(self):
        """Resolve only the signer assigned to this exact Odoo identity.

        OCA Sign intentionally groups contacts by commercial partner.  That is
        useful for ordinary business relationships, but it is too broad for a
        personal signing authorization because sibling contacts must never be
        interchangeable.
        """
        partner = self.env.user.partner_id
        for request in self:
            # ``request.signer_ids`` contains every recipient id.  A recipient
            # may read only their own row, so traversing the whole relation can
            # fail as soon as another recipient is prefetched.  Resolve the
            # exact identity under sudo, then let normal rules protect any
            # later access to the selected row.
            assigned = self.env["sign.oca.request.signer"].sudo().search(
                [
                    ("request_id", "=", request.id),
                    ("partner_id", "=", partner.id),
                ],
                order="sequence, id",
            )
            allowed = assigned.filtered("is_allow_signature")
            request.signer_id = allowed[:1] if allowed else assigned[:1]

    record_ref = fields.Reference(
        selection="_sign_business_record_models",
        string="Related business record",
    )

    state = fields.Selection(
        REQUEST_STATES, default="draft", required=True, copy=False, tracking=True,
    )
    document_category = fields.Selection(
        DOCUMENT_CATEGORIES,
        default="routine_agreement",
        required=True,
    )
    signer_type = fields.Selection(
        [
            ("internal", "Internal user"),
            ("recurring", "Known recurring signer"),
            ("occasional", "Occasional external signer"),
        ],
        default="occasional",
        required=True,
    )
    risk_level = fields.Selection(
        [("low", "Low"), ("material", "Material"), ("maximum", "Maximum")],
        default="low",
        required=True,
    )
    formal_qes_required = fields.Boolean()
    policy_id = fields.Many2one("usl.sign.policy", ondelete="restrict")
    policy_version = fields.Char(readonly=True, copy=False)
    policy_snapshot = fields.Json(readonly=True, copy=False)
    signer_snapshot = fields.Json(readonly=True, copy=False)
    consent_text_snapshot = fields.Text(readonly=True, copy=False)
    recommended_trust = fields.Selection(TRUST_LEVELS, readonly=True, copy=False)
    requested_trust = fields.Selection(TRUST_LEVELS, default="standard", required=True)
    achieved_trust = fields.Selection(TRUST_LEVELS, readonly=True, copy=False)
    recommendation_reason = fields.Text(readonly=True, copy=False)
    recommendation_consequence = fields.Text(readonly=True, copy=False)
    override_reason = fields.Text(copy=False)
    authentication_method = fields.Selection(
        AUTHENTICATION_METHODS, readonly=True, copy=False,
    )
    document_ids = fields.One2many(
        "usl.sign.request.document", "request_id", string="Documents",
    )
    page_map = fields.Json(readonly=True, copy=False)
    template_version = fields.Integer(readonly=True, copy=False)
    frozen_layout = fields.Json(readonly=True, copy=False)
    editor_revision = fields.Integer(default=1, required=True, copy=False, readonly=True)
    editor_operation_log = fields.Json(default=dict, copy=False, readonly=True)
    original_data = fields.Binary(readonly=True, copy=False, attachment=True)
    original_filename = fields.Char(readonly=True, copy=False)
    original_sha256 = fields.Char(readonly=True, copy=False, index=True)
    final_data = fields.Binary(readonly=True, copy=False, attachment=True)
    final_filename = fields.Char(readonly=True, copy=False)
    final_sha256 = fields.Char(readonly=True, copy=False, index=True)
    completion_certificate = fields.Binary(readonly=True, copy=False, attachment=True)
    completion_filename = fields.Char(readonly=True, copy=False)
    evidence_manifest = fields.Binary(readonly=True, copy=False, attachment=True)
    dossier_data = fields.Binary(readonly=True, copy=False, attachment=True)
    dossier_filename = fields.Char(readonly=True, copy=False)
    evidence_ids = fields.One2many("usl.sign.evidence", "request_id")
    event_ids = fields.One2many("usl.sign.event", "request_id")
    validation_ids = fields.One2many("usl.sign.validation", "request_id")
    evidence_count = fields.Integer(compute="_compute_evidence_count")
    evidence_status = fields.Selection(
        [
            ("not_started", "Not started"),
            ("building", "Building"),
            ("complete", "Complete"),
            ("incomplete", "Incomplete"),
        ],
        default="not_started",
        readonly=True,
        copy=False,
    )
    validation_status = fields.Selection(
        [
            ("not_started", "Not started"),
            ("pending", "Pending"),
            ("valid", "Valid"),
            ("invalid", "Invalid"),
            ("indeterminate", "Indeterminate"),
        ],
        default="not_started",
        readonly=True,
        copy=False,
    )
    external_provider_id = fields.Many2one(
        "usl.sign.external.provider", ondelete="restrict",
    )
    external_journey_id = fields.One2many(
        "usl.sign.external.journey", "request_id", readonly=True,
    )
    sent_at = fields.Datetime(readonly=True, copy=False)
    completed_at = fields.Datetime(readonly=True, copy=False)
    expires_at = fields.Datetime(copy=False)
    reminder_days = fields.Integer(default=3)
    max_reminders = fields.Integer(default=5)
    reminder_count = fields.Integer(default=0, readonly=True, copy=False)
    last_reminder_at = fields.Datetime(readonly=True, copy=False)
    signing_order = fields.Boolean()
    responsible_message = fields.Text()
    next_step = fields.Char(compute="_compute_next_step")
    coordinator_ids = fields.Many2many(
        "res.users",
        "usl_sign_request_coordinator_rel",
        "request_id",
        "user_id",
        string="Coordinators",
        domain="[('share', '=', False), ('company_ids', 'in', company_id)]",
        help="Named colleagues who may prepare, monitor, remind, and retry this request.",
    )
    lifecycle_stage = fields.Selection(
        [
            ("draft", "Draft"),
            ("ready", "Ready"),
            ("sent", "Sent"),
            ("progress", "In progress"),
            ("checks", "Finishing"),
            ("closed", "Closed"),
        ],
        compute="_compute_workspace_presentation",
    )
    lifecycle_stage_label = fields.Char(compute="_compute_workspace_presentation")
    signer_progress = fields.Char(compute="_compute_workspace_presentation")
    signer_names_summary = fields.Char(compute="_compute_workspace_presentation")
    requested_trust_short = fields.Char(compute="_compute_workspace_presentation")
    recommended_trust_short = fields.Char(compute="_compute_workspace_presentation")
    achieved_trust_short = fields.Char(compute="_compute_workspace_presentation")
    completed_proof_label = fields.Char(compute="_compute_workspace_presentation")
    completed_storage_label = fields.Char(compute="_compute_workspace_presentation")
    blocking_summary = fields.Char(compute="_compute_workspace_presentation")
    signing_method_summary = fields.Char(compute="_compute_workspace_presentation")
    due_date_summary = fields.Char(compute="_compute_workspace_presentation")
    document_preview_url = fields.Char(
        compute="_compute_document_presentation",
        string="Document preview",
    )
    document_thumbnail_url = fields.Char(
        compute="_compute_document_presentation",
        string="Document thumbnail",
    )
    has_signing_fields = fields.Boolean(compute="_compute_workspace_presentation")
    strong_enrollment_missing = fields.Boolean(compute="_compute_workspace_presentation")
    strong_enrollment_summary = fields.Char(compute="_compute_workspace_presentation")
    can_coordinate = fields.Boolean(compute="_compute_user_capabilities")
    can_send = fields.Boolean(compute="_compute_user_capabilities")
    managed_by_current_user = fields.Boolean(
        compute="_compute_managed_by_current_user",
        search="_search_managed_by_current_user",
    )
    last_error = fields.Text(readonly=True, copy=False)
    recovery_action = fields.Char(readonly=True, copy=False)

    @api.depends(
        "state",
        "validation_status",
        "filename",
        "final_filename",
        "archive_document_id",
        "archive_document_id.availability_state",
        "archive_document_id.permission_sync_state",
    )
    @api.depends_context("uid", "allowed_company_ids")
    def _compute_document_presentation(self):
        can_use_documents = self.env.user.has_group(
            "usl_documents.group_documents_user",
        )
        for sign_request in self:
            field_name = "data"
            filename = sign_request.filename or f"{sign_request.name}.pdf"
            if (
                sign_request.state in {"evidence_incomplete", "completed"}
                and sign_request.validation_status == "valid"
                and sign_request.final_filename
            ):
                field_name = "final_data"
                filename = sign_request.final_filename
            sign_request.document_preview_url = (
                f"/web/content/{sign_request._name}/{sign_request.id}/{field_name}/"
                f"{quote(filename)}?download=false"
            )
            sign_request.document_thumbnail_url = False
            document = sign_request.archive_document_id
            if not can_use_documents or not document:
                continue
            try:
                document.check_access("read")
            except AccessError:
                continue
            if (
                document.availability_state == "available"
                and document.permission_sync_state == "synchronized"
            ):
                sign_request.document_thumbnail_url = (
                    f"/usl_documents/{document.id}/thumbnail"
                )

    @api.model
    def _sign_business_record_models(self):
        """Offer business records, not every technical model in the registry."""
        preferred = {
            "res.partner",
            "hr.employee",
            "hr.contract",
            "project.project",
            "project.task",
            "sale.order",
            "purchase.order",
            "account.move",
            "account.payment",
            "account.analytic.account",
        }
        preferred.update(
            self.env["sign.oca.template"]
            .sudo()
            .search([("active", "=", True), ("model", "!=", False)])
            .mapped("model"),
        )
        models = self.env["ir.model"].sudo().search(
            [("model", "in", sorted(preferred)), ("transient", "=", False)],
            order="name, model",
        )
        return [(model.model, model.name) for model in models]

    @api.model
    def _sign_dss_client(self):
        return DSSClient()

    @api.model
    def get_business_record_summary(self, res_model, res_id):
        if (
            not res_model or not res_id or res_model.startswith(("usl.sign", "sign.oca")) or not (self.env.user.has_group("usl_sign.group_sign_user") or self.env.user.has_group("usl_sign.group_sign_evidence_reviewer"))
        ):
            return False
        target_model = self.env.get(res_model)
        if target_model is None or target_model._transient:
            return False
        target = target_model.browse(int(res_id)).exists()
        if not target:
            return False
        target.check_access("read")
        requests = self.search(
            [("record_ref", "=", f"{res_model},{target.id}")],
            order="create_date desc, id desc",
            limit=20,
        )
        if not requests:
            return False
        active = requests.filtered(
            lambda sign_request: sign_request.state not in TERMINAL_REQUEST_STATES,
        )
        sign_request = active[:1] or requests[:1]

        def content_url(field_name, filename):
            if not filename:
                return False
            return (
                f"/web/content/{sign_request._name}/{sign_request.id}/{field_name}/"
                f"{quote(filename)}?download=true"
            )

        return {
            "kind": "signature",
            "record_model": sign_request._name,
            "record_id": sign_request.id,
            "request_id": sign_request.id,
            "request_name": sign_request.name,
            "state": sign_request.state,
            "state_label": sign_request.lifecycle_stage_label,
            "next_step": sign_request.next_step,
            "requested_trust": sign_request.requested_trust_short,
            "achieved_trust": sign_request.achieved_trust_short
            if sign_request.achieved_trust
            else False,
            "archive_state": dict(
                sign_request._fields["archive_status"]._description_selection(self.env),
            ).get(sign_request.archive_status),
            "final_url": content_url("final_data", sign_request.final_filename),
            "certificate_url": content_url(
                "completion_certificate", sign_request.completion_filename,
            ),
            "evidence_url": content_url(
                "evidence_manifest", f"{sign_request.name}-signed-evidence-manifest.json",
            )
            if sign_request.evidence_manifest
            else False,
            "total_requests": len(requests),
        }

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        for record in records:
            if record.data and not record.document_ids:
                self.env["usl.sign.request.document"].create(
                    {
                        "request_id": record.id,
                        "name": record.name,
                        "filename": record.filename or f"{record.name}.pdf",
                        "data": record.data,
                    },
                )
            record._append_event("request_created", payload={"name": record.name})
            record.action_compute_recommendation(apply_timing_defaults=True)
        return records

    @api.depends("evidence_ids")
    def _compute_evidence_count(self):
        for request in self:
            request.evidence_count = len(request.evidence_ids)

    @api.depends(
        "state",
        "archive_status",
        "last_error",
        "signing_order",
        "signer_ids.state",
        "signer_ids.sequence",
        "signer_ids.partner_id.name",
        "signatory_data",
    )
    @api.depends_context("lang")
    def _compute_next_step(self):
        messages = {
            "draft": _("Add the fields people need to complete."),
            "ready": _("Review the request, then send it."),
            "waiting_enrollment": _("A signer needs to finish identity setup."),
            "waiting_external": _("Waiting for the signed document from the external provider."),
            "signed_to_import": _("Check the imported signed document."),
            "validating": _("Checking the signed document."),
            "evidence_incomplete": _(
                "The signature is recorded. Retry Paperless storage for the signed PDF "
                "and proof package.",
            ),
            "validation_failed": _(
                "The signed document did not pass its checks. Review the issue before continuing.",
            ),
            "completed": _("The final document is ready."),
            "declined": _(
                "The signer declined; decide whether to create a replacement.",
            ),
            "expired": _(
                "Create a replacement request if signatures are still needed.",
            ),
            "cancelled": _("This request is closed."),
            "action_required": _("This request needs attention before it can continue."),
        }
        for request in self:
            if request.state == "draft":
                request.next_step = (
                    _("Check the signing fields, then continue.")
                    if request.signatory_data
                    else _("Place the fields the signer needs to complete.")
                )
                continue
            if request.state in {"sent", "viewed", "partial"}:
                # Only expose a business-safe summary.  Recipient rows include
                # private invitation and ceremony material and remain visible
                # only to their owner, the requester, and coordinators.
                waiting = request.sudo().signer_ids.filtered(
                    lambda signer: signer.state not in {"signed", "declined"},
                ).sorted(lambda signer: (signer.sequence, signer.id))
                if len(waiting) == 1 or (request.signing_order and waiting):
                    request.next_step = _(
                        "Waiting for %(signer)s.",
                        signer=waiting[0].partner_id.name,
                    )
                elif waiting:
                    request.next_step = _(
                        "Waiting for %(count)s signers.",
                        count=len(waiting),
                    )
                else:
                    request.next_step = _("Finishing the signed document.")
                continue
            request.next_step = messages.get(request.state, request.last_error or "")

    @api.depends(
        "state",
        "requested_trust",
        "achieved_trust",
        "validation_status",
        "archive_status",
        "signer_ids.state",
        "signer_ids.signed_on",
        "signer_ids.partner_id",
        "signer_ids.partner_id.name",
        "signer_ids.partner_id.sign_enrollment_ids.state",
        "signer_ids.role_id.name",
        "company_id",
        "last_error",
        "recovery_action",
        "recommended_trust",
        "expires_at",
        "signatory_data",
    )
    @api.depends_context("lang")
    def _compute_workspace_presentation(self):
        missing_by_request = self._missing_strong_enrollments_by_request()
        stage_by_state = {
            "draft": "draft",
            "ready": "ready",
            "sent": "sent",
            "viewed": "progress",
            "partial": "progress",
            "waiting_enrollment": "progress",
            "waiting_external": "progress",
            "signed_to_import": "checks",
            "validating": "checks",
            "evidence_incomplete": "checks",
            "action_required": "checks",
            "validation_failed": "closed",
            "completed": "closed",
            "declined": "closed",
            "expired": "closed",
            "cancelled": "closed",
        }
        status_labels = {
            "draft": _("Draft"),
            "ready": _("Ready to send"),
            "sent": _("Sent"),
            "viewed": _("In progress"),
            "partial": _("In progress"),
            "waiting_enrollment": _("Waiting for identity setup"),
            "waiting_external": _("With external provider"),
            "signed_to_import": _("Ready to check"),
            "validating": _("Checking result"),
            "evidence_incomplete": _("Final storage needs attention"),
            "action_required": _("Needs attention"),
            "validation_failed": _("Result rejected"),
            "completed": _("Completed"),
            "declined": _("Declined"),
            "expired": _("Expired"),
            "cancelled": _("Cancelled"),
        }
        trust_labels = {
            "standard": _("Standard"),
            "strong_personal": _("Strong personal"),
            "qualified_external": _("Qualified external"),
        }
        for request in self:
            stage = stage_by_state.get(request.state, "progress")
            request.lifecycle_stage = stage
            request.lifecycle_stage_label = status_labels.get(
                request.state,
                _("In progress"),
            )
            signers = request.sudo().signer_ids
            total = len(signers)
            signed = len(signers.filtered(lambda signer: signer.state == "signed"))
            request.signer_progress = (
                _("%(signed)s of %(total)s signed", signed=signed, total=total)
                if total
                else _("No signers")
            )
            request.signer_names_summary = ", ".join(
                f"{signer.partner_id.name} ({signer.role_id.name})"
                for signer in signers.sorted(lambda row: (row.sequence, row.id))
            ) or _("No signers")
            missing_enrollments = missing_by_request[request.id]
            request.strong_enrollment_missing = bool(missing_enrollments)
            request.strong_enrollment_summary = (
                _(
                    "Strong signing cannot start yet. An identity reviewer must send "
                    "setup instructions and approve: %(names)s.",
                    names=", ".join(missing_enrollments.mapped("partner_id.name")),
                )
                if missing_enrollments
                else ""
            )
            request.requested_trust_short = trust_labels.get(request.requested_trust, "")
            request.recommended_trust_short = trust_labels.get(
                request.recommended_trust,
                "",
            )
            request.achieved_trust_short = trust_labels.get(request.achieved_trust, "")
            request.completed_proof_label = (
                _("Verified")
                if request.validation_status == "valid"
                else _("Needs attention")
            )
            request.completed_storage_label = (
                _("Stored")
                if request.archive_status == "archived"
                else _("Needs attention")
            )
            request.has_signing_fields = bool(request.signatory_data)
            request.due_date_summary = (
                format_datetime(self.env, request.expires_at, dt_format="short")
                if request.expires_at
                else _("No deadline")
            )
            request.signing_method_summary = {
                "standard": _(
                    "A private signing link, clear consent, and a complete record of what happened.",
                ),
                "strong_personal": _(
                    "The signer confirms with Pocket ID and adds a personal digital signature.",
                ),
                "qualified_external": _(
                    "A qualified provider signs the document; the result is checked here before completion.",
                ),
            }.get(request.requested_trust, "")
            if request.state == "evidence_incomplete" and request.archive_status == "failed":
                request.blocking_summary = _(
                    "The document is signed and valid, but its final copy could not be stored. Try again.",
                )
            elif request.state == "validation_failed":
                request.blocking_summary = _(
                    "The signed document failed verification. Open Method, result & proof for details.",
                )
            else:
                request.blocking_summary = request.next_step or ""

    def _missing_strong_enrollments_by_request(self):
        """Batch strong-identity readiness without per-request queries."""
        signer_model = self.env["sign.oca.request.signer"]
        strong_requests = self.filtered(
            lambda request: request.requested_trust == "strong_personal",
        )
        enrollments = self.env["usl.sign.enrollment"].sudo().search(
            [
                ("partner_id", "in", strong_requests.signer_ids.partner_id.ids),
                ("company_id", "in", strong_requests.company_id.ids),
                ("state", "=", "active"),
            ],
        )
        active_identities = {
            (enrollment.partner_id.id, enrollment.company_id.id)
            for enrollment in enrollments
        }
        return {
            request.id: (
                request.signer_ids.filtered(
                    lambda signer: (
                        signer.partner_id.id,
                        request.company_id.id,
                    )
                    not in active_identities,
                )
                if request in strong_requests
                else signer_model
            )
            for request in self
        }

    def _missing_strong_enrollments(self):
        """Return strong signers without an active, company-scoped identity."""
        self.ensure_one()
        return self._missing_strong_enrollments_by_request()[self.id]

    def _user_can_coordinate(self):
        self.ensure_one()
        return bool(
            self.env.su
            or self.env.user.has_group("usl_sign.group_sign_admin")
            or self.user_id == self.env.user
            or self.env.user in self.coordinator_ids,
        )

    @api.depends("user_id", "coordinator_ids")
    @api.depends_context("uid")
    def _compute_user_capabilities(self):
        is_admin = self.env.su or self.env.user.has_group("usl_sign.group_sign_admin")
        for request in self:
            request.can_coordinate = bool(
                is_admin
                or request.user_id == self.env.user
                or self.env.user in request.coordinator_ids,
            )
            request.can_send = bool(is_admin or request.user_id == self.env.user)

    @api.depends("user_id", "coordinator_ids")
    @api.depends_context("uid")
    def _compute_managed_by_current_user(self):
        review_all = self.env.su or self.env.user.has_group(
            "usl_sign.group_sign_evidence_reviewer",
        )
        for request in self:
            request.managed_by_current_user = bool(
                review_all
                or request.user_id == self.env.user
                or self.env.user in request.coordinator_ids,
            )

    @api.model
    def _search_managed_by_current_user(self, operator, value):
        if operator not in {"=", "!="}:
            raise NotImplementedError()
        wanted = bool(value)
        if operator == "!=":
            wanted = not wanted
        review_all = self.env.su or self.env.user.has_group(
            "usl_sign.group_sign_evidence_reviewer",
        )
        if review_all:
            return fields.Domain.TRUE if wanted else fields.Domain.FALSE
        managed = fields.Domain.OR(
            [
                [("user_id", "=", self.env.user.id)],
                [("coordinator_ids", "in", [self.env.user.id])],
            ],
        )
        return managed if wanted else ~managed

    def _internal_signer_users(self):
        """Return invited backend users without exposing unrelated contacts."""
        self.ensure_one()
        users = self.sudo().signer_ids.partner_id.user_ids.filtered(
            lambda user: (
                user.active
                and not user.share
                and self.company_id in user.company_ids
                and user != self.user_id
            ),
        )
        return users.sorted(lambda user: (user.name.casefold(), user.id))

    def _share_confirmation_action(self, message):
        self.ensure_one()
        recipients = self._internal_signer_users()
        if not recipients:
            return False
        wizard = self.env["usl.sign.share.confirm"].create(
            {
                "request_id": self.id,
                "recipient_names": ", ".join(recipients.mapped("name")),
                "recipient_count": len(recipients),
                "message": message or "",
            },
        )
        return {
            "type": "ir.actions.act_window",
            "name": _("Share and send"),
            "res_model": wizard._name,
            "res_id": wizard.id,
            "views": [(self.env.ref("usl_sign.sign_share_confirm_form").id, "form")],
            "target": "new",
        }

    def _check_prepare_access(self):
        for request in self:
            if not request._user_can_coordinate():
                msg = "Only the requester or a named coordinator may prepare this request."
                raise AccessError(msg)

    def _check_owner_access(self):
        for request in self:
            if not (
                self.env.su
                or self.env.user.has_group("usl_sign.group_sign_admin")
                or request.user_id == self.env.user
            ):
                msg = "Only the requester or a Sign administrator may do this."
                raise AccessError(msg)

    @api.onchange(
        "document_category", "signer_type", "risk_level", "formal_qes_required",
    )
    def _onchange_recommendation_inputs(self):
        for request in self:
            request.action_compute_recommendation(apply_timing_defaults=True)

    def action_compute_recommendation(self, apply_timing_defaults=True):
        for request in self:
            policy = self.env["usl.sign.policy"].recommend(
                request.company_id,
                category=request.document_category,
                signer_type=request.signer_type,
                risk_level=request.risk_level,
                formal_qes=request.formal_qes_required,
            )
            if not policy:
                request.update(
                    {
                        "policy_id": False,
                        "recommended_trust": "qualified_external"
                        if request.formal_qes_required
                        else "standard",
                        "recommendation_reason": "No matching company policy exists; use the conservative default.",
                        "recommendation_consequence": "An administrator should create a reviewed policy before sending.",
                    },
                )
                continue
            values = {
                "policy_id": policy.id,
                "recommended_trust": policy.recommendation,
                "recommendation_reason": policy.reason,
                "recommendation_consequence": policy.consequence,
            }
            if apply_timing_defaults:
                values.update(
                    {
                        "reminder_days": policy.reminder_days,
                        "max_reminders": policy.max_reminders,
                    },
                )
            request.update(values)
        return True

    def action_open_signing_method(self):
        self.ensure_one()
        self._check_owner_access()
        if self.state != "draft":
            msg = "The signing method is fixed after the request is prepared."
            raise ValidationError(msg)
        return {
            "type": "ir.actions.act_window",
            "name": _("Signing method"),
            "res_model": "usl.sign.request.method",
            "views": [(False, "form")],
            "target": "new",
            "context": {"default_request_id": self.id},
        }

    def _append_event(self, event_type, **values):
        self.ensure_one()
        ip_address = values.pop("ip_address", None)
        user_agent = values.pop("user_agent", None)
        if http_request and hasattr(http_request, "httprequest"):
            ip_address = ip_address or (
                http_request.httprequest.access_route[-1]
                if http_request.httprequest.access_route
                else http_request.httprequest.remote_addr
            )
            user_agent = user_agent or http_request.httprequest.headers.get(
                "User-Agent", "",
            )
        return self.env["usl.sign.event"]._append(
            self,
            event_type,
            ip_address=ip_address,
            user_agent=user_agent,
            **values,
        )

    def _transition(self, new_state, event_type, *, payload=None, signer=None):
        self.ensure_one()
        old_state = self.state
        if new_state == old_state:
            return False
        if new_state not in TRANSITIONS.get(old_state, set()):
            raise ValidationError(f"Invalid signature transition: {old_state} → {new_state}.")
        self.with_context(usl_sign_transition=INTERNAL_OPERATION).write({"state": new_state})
        self._append_event(
            event_type,
            state_from=old_state,
            state_to=new_state,
            signer=signer,
            payload=payload or {},
        )
        return True

    def _create_evidence(self, kind, name, raw, *, mimetype, signer=None, metadata=None):
        self.ensure_one()
        digest = hashlib.sha256(raw).hexdigest()
        existing = self.evidence_ids.filtered(
            lambda row: row.kind == kind and row.name == name and row.sha256 == digest,
        )[:1]
        if existing:
            return existing
        return self.env["usl.sign.evidence"].with_context(
            usl_sign_evidence_create=INTERNAL_OPERATION,
        ).create(
            {
                "request_id": self.id,
                "signer_id": signer.id if signer else False,
                "kind": kind,
                "name": name,
                "data": field_value(raw),
                "mimetype": mimetype,
                "metadata": metadata or {},
            },
        )

    def _validate_preparation(self):
        self.ensure_one()
        if not self.signer_ids:
            msg = "Add at least one signer."
            raise ValidationError(msg)
        if self.requested_trust != "qualified_external" and self.signer_ids.filtered(
            lambda signer: not signer.partner_id.email,
        ):
            msg = "Every local signer needs an email address for a private invitation."
            raise ValidationError(msg)
        if self.requested_trust != "qualified_external" and not self.company_id.partner_id.email:
            msg = "Configure a company email address before sending invitations."
            raise ValidationError(msg)
        if len(self.signer_ids.mapped("role_id")) != len(self.signer_ids):
            msg = "Each signer must have a distinct role."
            raise ValidationError(msg)
        if not self.signatory_data:
            msg = "Place at least one signing field on the document."
            raise ValidationError(msg)
        self._validate_layout()
        role_ids = set(self.signer_ids.mapped("role_id").ids)
        field_role_ids = {
            int(item.get("role_id")) for item in self.signatory_data.values()
        }
        if role_ids - field_role_ids:
            msg = "Every signer role needs at least one field."
            raise ValidationError(msg)
        if self.formal_qes_required and self.requested_trust != "qualified_external":
            msg = "A formal QES requirement cannot be overridden."
            raise ValidationError(msg)
        if self.requested_trust != self.recommended_trust:
            if not self.override_reason:
                msg = "Record why the recommended trust level is overridden."
                raise ValidationError(msg)
            if not self.env.user.has_group("usl_sign.group_sign_trust_override"):
                msg = "Trust-level override access is required."
                raise AccessError(msg)
        if self.requested_trust == "qualified_external" and not self.external_provider_id:
            msg = "Choose a reviewed external provider before sending."
            raise ValidationError(msg)
        if self.policy_id.company_id and self.policy_id.company_id != self.company_id:
            msg = "The signing policy belongs to another company."
            raise ValidationError(msg)
        if self.external_provider_id and (
            not self.external_provider_id.active
            or (self.external_provider_id.company_id
            and self.external_provider_id.company_id != self.company_id)
        ):
            msg = "Choose an active external provider reviewed for this company."
            raise ValidationError(msg)
        planned_authentication = (
            "pocket_id_passkey"
            if self.requested_trust == "strong_personal"
            else self.policy_id.default_authentication or "secure_link"
        )
        if planned_authentication in {"portal", "pocket_id"}:
            for signer in self.signer_ids:
                users = signer.partner_id.user_ids.filtered("active")
                if planned_authentication == "portal" and not users:
                    raise ValidationError(
                        f"{signer.partner_id.name} needs an active Odoo account for portal authentication.",
                    )
                if planned_authentication == "pocket_id" and not users.filtered(
                    lambda user: user.oauth_provider_id.usl_pocketid,
                ):
                    raise ValidationError(
                        f"{signer.partner_id.name} needs an active Pocket ID account.",
                    )
        if self.record_ref and "company_id" in self.record_ref._fields:
            record_company = self.record_ref.company_id
            if record_company and record_company != self.company_id:
                msg = "The linked business record belongs to another company."
                raise ValidationError(msg)

    def _validate_layout(self):
        self.ensure_one()
        try:
            consolidated, _page_map = self.env[
                "usl.sign.request.document"
            ]._consolidate(self.document_ids)
            page_count = len(PdfReader(BytesIO(consolidated)).pages)
        except Exception as error:
            msg = "Every request document must remain a readable PDF."
            raise ValidationError(msg) from error
        role_ids = set(self.signer_ids.mapped("role_id").ids)
        field_ids = set(self.env["sign.oca.field"].search([]).ids)
        for configured in (self.signatory_data or {}).values():
            if not isinstance(configured, dict):
                msg = "A signing field has an invalid structure."
                raise ValidationError(msg)
            try:
                page = int(configured["page"])
                role_id = int(configured["role_id"])
                field_id = int(configured["field_id"])
                position_x = float(configured["position_x"])
                position_y = float(configured["position_y"])
                width = float(configured["width"])
                height = float(configured["height"])
            except (KeyError, TypeError, ValueError) as error:
                msg = "A signing field is incomplete or malformed."
                raise ValidationError(msg) from error
            if role_id not in role_ids or field_id not in field_ids:
                msg = "Every field must use a request signer role and approved field type."
                raise ValidationError(msg)
            if not 1 <= page <= page_count:
                msg = "A signing field is placed on a page that does not exist."
                raise ValidationError(msg)
            if (
                position_x < 0
                or position_y < 0
                or width <= 0
                or height <= 0
                or position_x + width > 100
                or position_y + height > 100
            ):
                msg = "A signing field is outside the PDF page."
                raise ValidationError(msg)
            if len(configured.get("placeholder") or "") > 500:
                msg = "A signing-field placeholder is too long."
                raise ValidationError(msg)

    def action_mark_ready(self):
        self._check_prepare_access()
        for request in self:
            if request.state != "draft":
                msg = "Only a draft request can be marked ready."
                raise ValidationError(msg)
            # Recheck trust inputs without silently replacing timing the
            # requester already reviewed and customized on the draft.
            request.action_compute_recommendation(apply_timing_defaults=False)
            request._validate_preparation()
            request._transition("ready", "request_ready")
        return True

    def _ensure_draft(self):
        self.ensure_one()
        self._check_prepare_access()
        if not self.signer_ids:
            msg = "Add at least one signer before placing fields."
            raise ValidationError(msg)
        if self.state != "draft":
            msg = "Only a draft request can be edited."
            raise ValidationError(msg)

    def configure(self):
        action = super().configure()
        action["tag"] = "usl_sign_request_configure"
        return action

    def _editor_roles_info(self):
        self.ensure_one()
        template_colors = {
            mapping.role_id.id: mapping.color
            for mapping in self.template_id.editor_role_ids
        }
        result = []
        seen = set()
        for index, signer in enumerate(self.signer_ids.sorted("sequence")):
            if signer.role_id.id in seen:
                continue
            seen.add(signer.role_id.id)
            result.append(
                {
                    "id": signer.role_id.id,
                    "name": signer.role_id.name,
                    "signer_name": signer.partner_id.name,
                    "color": template_colors.get(
                        signer.role_id.id,
                        EDITOR_ROLE_COLORS[index % len(EDITOR_ROLE_COLORS)],
                    ),
                    "sequence": signer.sequence or (index + 1) * 10,
                },
            )
        return result

    def get_info(self):
        self.ensure_one()
        info = super().get_info()
        fields_info = {
            field.id: _field_info(field)
            for field in self.env["sign.oca.field"].search([])
        }
        items = {}
        for key, item in (self.signatory_data or {}).items():
            field = fields_info.get(int(item["field_id"]))
            items[str(key)] = {
                **item,
                "kind": field["kind"] if field else "text",
                "field_type": field["field_type"] if field else item.get("field_type", "text"),
                "technical_type": (
                    field["technical_type"]
                    if field
                    else item.get("technical_type", item.get("field_type", "text"))
                ),
            }
        info.update(
            {
                "items": items,
                "roles": self._editor_roles_info(),
                "fields": list(fields_info.values()),
                "revision": self.editor_revision,
                "readonly": self.state != "draft",
                "editor_mode": "request",
            },
        )
        return info

    def _editor_store_result(self, operation_uuid, result, signatory_data):
        self.ensure_one()
        operation_log = dict(self.editor_operation_log or {})
        operation_log[operation_uuid] = result
        if len(operation_log) > 100:
            operation_log = dict(list(operation_log.items())[-100:])
        self.with_context(usl_sign_editor_internal=INTERNAL_OPERATION).write(
            {
                "signatory_data": signatory_data,
                "editor_revision": result["revision"],
                "editor_operation_log": operation_log,
            },
        )

    def _editor_page_count(self):
        self.ensure_one()
        try:
            return len(
                PdfReader(BytesIO(field_content(self.with_context(bin_size=False).data))).pages,
            )
        except Exception as error:
            msg = "Attach a readable PDF before editing its fields."
            raise ValidationError(msg) from error

    def _validate_editor_page(self, page):
        self.ensure_one()
        page_count = self._editor_page_count()
        if int(page) < 1 or int(page) > page_count:
            msg = "The selected PDF page does not exist."
            raise ValidationError(msg)

    def editor_apply_command(self, operation_uuid, expected_revision, command):
        self.ensure_one()
        self._ensure_draft()
        operation_uuid = _validate_editor_uuid(operation_uuid)
        previous = (self.editor_operation_log or {}).get(operation_uuid)
        if previous:
            return previous
        if int(expected_revision) != self.editor_revision:
            return {
                "status": "conflict",
                "revision": self.editor_revision,
                "message": "This request changed in another editor. Reload before continuing.",
            }
        action = command.get("action")
        values = dict(command.get("values") or {})
        allowed = {
            "field_id", "role_id", "required", "placeholder", "page",
            "position_x", "position_y", "width", "height",
        }
        if set(values) - allowed:
            msg = "The editor command contains unsupported field values."
            raise ValidationError(msg)
        _validate_editor_geometry(values)
        data = {str(key): dict(value) for key, value in (self.signatory_data or {}).items()}
        item = False
        items = []
        deleted_id = False
        deleted_ids = []
        if action in {"create", "create_all_pages", "copy_all_pages"}:
            if action == "copy_all_pages":
                source_id = str(int(command.get("item_id", 0)))
                if source_id not in data:
                    msg = "The field no longer exists in this request."
                    raise ValidationError(msg)
                values = {
                    key: value
                    for key, value in data[source_id].items()
                    if key in allowed
                }
            if not values.get("field_id") or not values.get("role_id"):
                msg = "Choose both a field type and a signer before placing it."
                raise ValidationError(msg)
            field = self.env["sign.oca.field"].browse(values["field_id"]).exists()
            allowed_roles = self.signer_ids.mapped("role_id")
            role = allowed_roles.filtered(lambda row: row.id == int(values["role_id"]))
            if not field or len(role) != 1:
                msg = "The selected field type or signer is unavailable."
                raise ValidationError(msg)
            if (
                action in {"create_all_pages", "copy_all_pages"}
                and _field_kind(field) != "initials"
            ):
                msg = "Only an Initials field can be placed on every page."
                raise ValidationError(msg)
            next_item_id = max([int(key) for key in data] or [0]) + 1
            next_tabindex = max(
                [int(value.get("tabindex") or 0) for value in data.values()] or [0],
            ) + 1
            presentation = FIELD_PRESENTATION[_field_kind(field)]
            page_count = self._editor_page_count()
            if action == "copy_all_pages":
                compare_keys = allowed - {"page"}
                occupied_pages = {
                    int(existing["page"])
                    for existing in data.values()
                    if all(existing.get(key) == values.get(key) for key in compare_keys)
                }
                pages = [
                    page for page in range(1, page_count + 1)
                    if page not in occupied_pages
                ]
            else:
                pages = (
                    range(1, page_count + 1)
                    if action == "create_all_pages"
                    else [int(values.get("page") or 1)]
                )
            for offset, page in enumerate(pages):
                if page < 1 or page > page_count:
                    msg = "The selected PDF page does not exist."
                    raise ValidationError(msg)
                created = {
                    "id": next_item_id + offset,
                    "tabindex": next_tabindex + offset,
                    "field_id": field.id,
                    "field_type": field.field_type,
                    "kind": _field_kind(field),
                    "required": field.field_type == "signature",
                    "name": field.name,
                    "role_id": role.id,
                    "position_x": 0,
                    "position_y": 0,
                    "width": presentation["width"],
                    "height": presentation["height"],
                    "value": False,
                    "default_value": field.default_value,
                    "placeholder": "",
                    **values,
                    "page": page,
                }
                data[str(created["id"])] = created
                _validate_complete_editor_geometry(created)
                items.append(created)
            if action == "create":
                item = items[0]
                items = []
        elif action == "update":
            item_id = str(int(command.get("item_id", 0)))
            if item_id not in data:
                msg = "The field no longer exists in this request."
                raise ValidationError(msg)
            if "field_id" in values:
                field = self.env["sign.oca.field"].browse(values["field_id"]).exists()
                if not field:
                    msg = "The selected field type is unavailable."
                    raise ValidationError(msg)
                values.update(
                    {
                        "name": field.name,
                        "kind": _field_kind(field),
                        "field_type": field.field_type,
                        "default_value": field.default_value,
                    },
                )
            if "role_id" in values and int(values["role_id"]) not in self.signer_ids.role_id.ids:
                msg = "The selected signer is unavailable."
                raise ValidationError(msg)
            data[item_id].update(values)
            item = data[item_id]
            self._validate_editor_page(item["page"])
            _validate_complete_editor_geometry(item)
        elif action == "delete":
            item_id = str(int(command.get("item_id", 0)))
            if item_id not in data:
                msg = "The field no longer exists in this request."
                raise ValidationError(msg)
            deleted_id = int(item_id)
            data.pop(item_id)
        elif action == "delete_many":
            raw_ids = command.get("item_ids")
            if not isinstance(raw_ids, list) or not raw_ids:
                msg = "Choose the fields to remove."
                raise ValidationError(msg)
            try:
                requested_ids = list(dict.fromkeys(int(item_id) for item_id in raw_ids))
            except (TypeError, ValueError) as error:
                msg = "The fields to remove are invalid."
                raise ValidationError(msg) from error
            requested_keys = [str(item_id) for item_id in requested_ids]
            if any(item_id not in data for item_id in requested_keys):
                msg = "One or more fields no longer exist in this request."
                raise ValidationError(msg)
            for item_id in requested_keys:
                data.pop(item_id)
            deleted_ids = requested_ids
        else:
            msg = "The editor command action is unsupported."
            raise ValidationError(msg)
        new_revision = self.editor_revision + 1
        result = {
            "status": "ok",
            "revision": new_revision,
            "item": item,
            "items": items,
            "deleted_id": deleted_id,
            "deleted_ids": deleted_ids,
        }
        self._editor_store_result(operation_uuid, result, data)
        return result

    def _freeze_document(self):
        self.ensure_one()
        if self.original_data:
            return
        consolidated, page_map = self.env["usl.sign.request.document"]._consolidate(
            self.document_ids,
        )
        digest = hashlib.sha256(consolidated).hexdigest()
        consent_text = (
            "I have reviewed this exact document and authorize my strong personal "
            "electronic signature using my Pocket ID passkey."
            if self.requested_trust == "strong_personal"
            else "I have reviewed this document and consent to use an electronic "
            "signature for this request."
        )
        policy_snapshot = {
            "policy_id": self.policy_id.id or None,
            "name": self.policy_id.name or None,
            "version": self.policy_id.version or "unconfigured",
            "recommendation": self.recommended_trust,
            "reason": self.recommendation_reason,
            "consequence": self.recommendation_consequence,
            "requested_trust": self.requested_trust,
            "override_reason": self.override_reason or None,
            "authentication": self.policy_id.default_authentication or "secure_link",
            "expiration_days": self.policy_id.expiration_days or 30,
            "reminder_days": self.reminder_days,
            "max_reminders": self.max_reminders,
        }
        signer_snapshot = [
            {
                "signer_id": signer.id,
                "partner_id": signer.partner_id.id,
                "name": signer.partner_id.name,
                "email": signer.partner_id.email,
                "role_id": signer.role_id.id,
                "role": signer.role_id.name,
                "sequence": signer.sequence,
            }
            for signer in self.signer_ids.sorted(lambda row: (row.sequence, row.id))
        ]
        self.with_context(usl_sign_freeze=INTERNAL_OPERATION).write(
            {
                "data": field_value(consolidated),
                "original_data": field_value(consolidated),
                "original_filename": self.filename or f"{self.name}.pdf",
                "original_sha256": digest,
                "current_hash": digest,
                "page_map": page_map,
                "frozen_layout": json.loads(json.dumps(self.signatory_data or {})),
                "template_version": self.template_id.version if self.template_id else 1,
                "policy_version": self.policy_id.version if self.policy_id else "unconfigured",
                "policy_snapshot": policy_snapshot,
                "signer_snapshot": signer_snapshot,
                "consent_text_snapshot": consent_text,
                "expires_at": self.expires_at
                or fields.Datetime.now()
                + timedelta(days=self.policy_id.expiration_days if self.policy_id else 30),
            },
        )
        for document in self.document_ids:
            self._create_evidence(
                "source",
                document.filename,
                field_content(document.data),
                mimetype="application/pdf",
                metadata={
                    "sha256": document.source_sha256,
                    "sequence": document.sequence,
                    "annex": document.is_annex,
                },
            )
        self._create_evidence(
            "frozen",
            self.original_filename,
            consolidated,
            mimetype="application/pdf",
            metadata={"sha256": digest, "page_map": page_map},
        )
        self._append_event("document_frozen", payload={"sha256": digest})

    def action_send(self, sign_now=False, message=""):
        del sign_now
        self._check_owner_access()
        for request in self:
            if request.state != "ready":
                msg = "Only a ready request can be sent."
                raise ValidationError(msg)
            request._validate_preparation()
            if self.env.context.get("usl_sign_share_confirmed") is not INTERNAL_OPERATION:
                confirmation = request._share_confirmation_action(message)
                if confirmation:
                    return confirmation
            request._freeze_document()
            request.responsible_message = message or request.responsible_message
            if request.requested_trust == "qualified_external":
                request.with_context(usl_sign_transition=INTERNAL_OPERATION).write(
                    {"authentication_method": "external_provider"},
                )
                request._prepare_external_journey()
                request._transition("waiting_external", "external_journey_prepared")
                continue
            if request.requested_trust == "strong_personal":
                request.signing_order = True
                missing = request.signer_ids.filtered(lambda signer: not signer._active_enrollment())
                if missing:
                    request._transition(
                        "waiting_enrollment",
                        "strong_enrollment_required",
                        payload={"signer_ids": missing.ids},
                    )
                    continue
            request.with_context(usl_sign_transition=INTERNAL_OPERATION).write(
                {
                    "sent_at": fields.Datetime.now(),
                    "authentication_method": "pocket_id_passkey"
                    if request.requested_trust == "strong_personal"
                    else request.policy_id.default_authentication or "secure_link",
                },
            )
            request._transition("sent", "request_sent")
            for signer in request.signer_ids.sorted(lambda row: (row.sequence, row.id)):
                if request.signing_order and signer != request.signer_ids.sorted(
                    lambda row: (row.sequence, row.id),
                )[0]:
                    continue
                signer._send_signer_invitation()
        return True

    def action_resume_after_enrollment(self):
        self._check_prepare_access()
        for request in self:
            if request.state != "waiting_enrollment":
                continue
            missing = request.signer_ids.filtered(lambda signer: not signer._active_enrollment())
            if missing:
                msg = "Every strong signer must complete enrolment first."
                raise ValidationError(msg)
            request.with_context(usl_sign_transition=INTERNAL_OPERATION).write(
                {
                    "sent_at": fields.Datetime.now(),
                    "authentication_method": "pocket_id_passkey",
                },
            )
            target_state = "partial" if request.signer_ids.filtered("signed_on") else "sent"
            request._transition(target_state, "strong_enrollment_complete")
            pending = request.signer_ids.filtered(
                lambda signer: signer.state not in {"signed", "declined", "expired", "cancelled"},
            ).sorted(lambda row: (row.sequence, row.id))
            if not pending:
                msg = "No signer remains available after enrolment."
                raise ValidationError(msg)
            pending[0]._send_signer_invitation()
        return True

    def _prepare_external_journey(self):
        self.ensure_one()
        if self.external_journey_id:
            return self.external_journey_id
        signer_info = [
            {
                "name": signer.partner_id.name,
                "email": signer.partner_id.email,
                "role": signer.role_id.name,
                "order": order,
            }
            for order, signer in enumerate(
                self.signer_ids.sorted(lambda row: (row.sequence, row.id)), start=1,
            )
        ]
        return self.env["usl.sign.external.journey"].with_context(
            usl_sign_external_create=INTERNAL_OPERATION,
        ).create(
            {
                "request_id": self.id,
                "provider_id": self.external_provider_id.id,
                "frozen_sha256": self.original_sha256,
                "signer_information": signer_info,
            },
        )

    def action_validate_external(self):
        self.ensure_one()
        self._check_prepare_access()
        journey = self.external_journey_id
        if self.state != "signed_to_import" or not journey.imported_pdf:
            msg = "Import the externally signed document first."
            raise ValidationError(msg)
        signed_data = field_content(journey.imported_pdf)
        frozen_data = field_content(self.original_data)
        self._create_evidence(
            "external",
            journey.imported_filename or f"{self.name}-external-signed.pdf",
            signed_data,
            mimetype="application/pdf",
            metadata={
                "artifact": "external_signed_pdf_submission",
                "trust": "unverified_input",
            },
        )
        self._create_evidence(
            "external",
            journey.proof_filename or "external-proof-package.bin",
            field_content(journey.proof_package),
            mimetype="application/octet-stream",
            metadata={
                "artifact": "external_provider_proof_submission",
                "trust": "unverified_input",
            },
        )
        self._transition("validating", "external_validation_started")
        try:
            match = self._sign_dss_client().revision_matches(frozen_data, signed_data)
            self._create_evidence(
                "validation",
                f"{self.name}-external-revision-comparison.json",
                json.dumps(match, sort_keys=True, indent=2).encode(),
                mimetype="application/json",
                metadata={"engine": "EU DSS", "check": "first pre-signature revision"},
            )
            if not match.get("matches"):
                explanation = "The signed revision does not match the frozen export."
                journey.with_context(usl_sign_external_transition=INTERNAL_OPERATION).write(
                    {"state": "rejected", "rejection_reason": explanation},
                )
                self._record_validation_failure(explanation)
                return False
            validation = self._sign_dss_client().validate(
                signed_data,
                expected_level="qualified_external",
                expected_signers=self.signer_ids.mapped("partner_id.name"),
            )
            report_evidence = self._store_dss_reports(validation)
            if validation.get("status") != "valid" or validation.get(
                "achievedTrust",
            ) != "qualified_external":
                explanation = (
                    validation.get("summary")
                    or "The imported document did not achieve a qualified signature."
                )
                journey.with_context(usl_sign_external_transition=INTERNAL_OPERATION).write(
                    {"state": "rejected", "rejection_reason": explanation},
                )
                self._record_validation_failure(explanation, validation=validation)
                return False
            result = self._complete_validated_document(
                signed_data, validation, report_evidence=report_evidence,
            )
            if result:
                journey.with_context(usl_sign_external_transition=INTERNAL_OPERATION).write(
                    {
                        "state": "validated",
                        "validation_id": result.id,
                        "rejection_reason": False,
                    },
                )
            return bool(result)
        except DSSRejectedError as error:
            journey.with_context(usl_sign_external_transition=INTERNAL_OPERATION).write(
                {"state": "rejected", "rejection_reason": str(error)},
            )
            self._record_validation_failure(str(error))
            return False
        except DSSServiceError as error:
            self.with_context(usl_sign_transition=INTERNAL_OPERATION).write(
                {
                    "validation_status": "indeterminate",
                    "last_error": str(error),
                    "recovery_action": "Restore the DSS validation service and retry the imported document.",
                },
            )
            self._transition(
                "action_required",
                "external_validation_service_error",
                payload={"reason": str(error)},
            )
            return False

    def _record_validation_failure(self, explanation, *, validation=None):
        self.ensure_one()
        if validation:
            report_evidence = self._store_dss_reports(validation)
            achieved = validation.get("achievedTrust")
            if achieved not in dict(TRUST_LEVELS):
                achieved = False
            self.env["usl.sign.validation"].with_context(
                usl_sign_validation_create=INTERNAL_OPERATION,
            ).create(
                {
                    "request_id": self.id,
                    "engine_version": validation.get("engineVersion") or "6.4",
                    "expected_trust": self.requested_trust,
                    "achieved_trust": achieved,
                    "status": "invalid",
                    "signature_count": validation.get("signatureCount", 0),
                    "qualified_provider": validation.get("qualifiedProvider"),
                    "certificate_summary": validation.get("certificates") or {},
                    "timestamp_summary": validation.get("timestamps") or {},
                    "revocation_summary": validation.get("revocation") or {},
                    "summary": explanation,
                    "report_evidence_id": report_evidence.id,
                },
            )
        self.with_context(usl_sign_transition=INTERNAL_OPERATION).write(
            {
                "validation_status": "invalid",
                "last_error": explanation,
                "recovery_action": "Create a replacement request or correct the imported document.",
            },
        )
        self._transition(
            "validation_failed", "validation_failed", payload={"reason": explanation},
        )

    def action_create_replacement(self):
        self.ensure_one()
        self._check_owner_access()
        if self.state != "validation_failed":
            msg = "A replacement is available only after validation has failed."
            raise ValidationError(msg)
        documents = self.document_ids.sorted(lambda row: (row.sequence, row.id))
        if not documents:
            msg = "The source documents are unavailable; an administrator must inspect the proof."
            raise ValidationError(msg)
        layout = json.loads(json.dumps(self.frozen_layout or self.signatory_data or {}))
        for field in layout.values():
            field["value"] = False
        primary = documents[0]
        record_ref = (
            f"{self.record_ref._name},{self.record_ref.id}" if self.record_ref else False
        )
        replacement = self.create(
            {
                "name": f"Replacement — {self.name}",
                "data": primary.data,
                "filename": primary.filename,
                "company_id": self.company_id.id,
                "user_id": self.user_id.id,
                "coordinator_ids": [(6, 0, self.coordinator_ids.ids)],
                "record_ref": record_ref,
                "template_id": self.template_id.id,
                "document_category": self.document_category,
                "signer_type": self.signer_type,
                "risk_level": self.risk_level,
                "formal_qes_required": self.formal_qes_required,
                "policy_id": self.policy_id.id,
                "requested_trust": self.requested_trust,
                "override_reason": self.override_reason,
                "external_provider_id": self.external_provider_id.id,
                "reminder_days": self.reminder_days,
                "max_reminders": self.max_reminders,
                "signing_order": self.signing_order,
                "responsible_message": self.responsible_message,
                "signatory_data": layout,
                "signer_ids": [
                    (
                        0,
                        0,
                        {
                            "partner_id": signer.partner_id.id,
                            "role_id": signer.role_id.id,
                            "sequence": signer.sequence,
                        },
                    )
                    for signer in self.signer_ids.sorted(
                        lambda row: (row.sequence, row.id),
                    )
                ],
            },
        )
        generated_primary = replacement.document_ids[:1]
        generated_primary.write(
            {
                "name": primary.name,
                "filename": primary.filename,
                "sequence": primary.sequence,
                "is_annex": primary.is_annex,
            },
        )
        for document in documents[1:]:
            self.env["usl.sign.request.document"].create(
                {
                    "request_id": replacement.id,
                    "name": document.name,
                    "filename": document.filename,
                    "sequence": document.sequence,
                    "is_annex": document.is_annex,
                    "data": document.data,
                    "mimetype": document.mimetype,
                },
            )
        self._append_event(
            "replacement_created",
            payload={"replacement_request_id": replacement.id},
        )
        replacement._append_event(
            "created_as_replacement",
            payload={"source_request_id": self.id},
        )
        return {
            "type": "ir.actions.act_window",
            "name": replacement.display_name,
            "res_model": self._name,
            "res_id": replacement.id,
            "view_mode": "form",
            "target": "current",
        }

    def _start_final_validation(self):
        self.ensure_one()
        if self.state not in {"sent", "viewed", "partial", "validating"}:
            msg = "This request is not ready for final validation."
            raise ValidationError(msg)
        if self.state != "validating":
            self._transition("validating", "validation_started")
        working = field_content(self.data)
        try:
            sealed = self._sign_dss_client().seal(
                working,
                request_reference=f"USL-SIGN-{self.id}",
                timestamp=self.company_id.sign_rfc3161_enabled,
            )
            final_data = base64.b64decode(sealed["document"])
            validation = self._sign_dss_client().validate(
                final_data, expected_level=self.requested_trust,
            )
            report_evidence = self._store_dss_reports(validation)
            if validation.get("status") != "valid":
                self._record_validation_failure(
                    validation.get("summary") or "DSS rejected the PDF.",
                    validation=validation,
                )
                return False
            return self._complete_validated_document(
                final_data, validation, report_evidence=report_evidence,
            )
        except DSSServiceError as error:
            self.with_context(usl_sign_transition=INTERNAL_OPERATION).write(
                {
                    "validation_status": "indeterminate",
                    "last_error": str(error),
                    "recovery_action": "Restore the local signature services and retry validation.",
                },
            )
            self._transition(
                "action_required", "validation_service_error", payload={"reason": str(error)},
            )
            return False

    def _store_dss_reports(self, validation):
        self.ensure_one()
        reports = validation.get("reports") or {}
        summary_payload = {key: value for key, value in validation.items() if key != "reports"}
        summary_raw = json.dumps(
            summary_payload, sort_keys=True, indent=2, ensure_ascii=False,
        ).encode()
        summary_digest = hashlib.sha256(summary_raw).hexdigest()[:12]
        summary_evidence = self._create_evidence(
            "validation",
            f"{self.name}-dss-validation-summary-{summary_digest}.json",
            summary_raw,
            mimetype="application/json",
            metadata={"engine": "EU DSS", "version": validation.get("engineVersion") or "6.4"},
        )
        for report_name in ("diagnostic", "detailed", "simple", "etsi"):
            report = reports.get(report_name)
            if not report:
                continue
            raw = report.encode() if isinstance(report, str) else bytes(report)
            digest = hashlib.sha256(raw).hexdigest()[:12]
            self._create_evidence(
                "validation",
                f"{self.name}-dss-{report_name}-{digest}.xml",
                raw,
                mimetype="application/xml",
                metadata={
                    "engine": "EU DSS",
                    "version": validation.get("engineVersion") or "6.4",
                    "report": report_name,
                },
            )
        return summary_evidence

    def _store_pdf_certificate_chains(self, cross_validation):
        """Preserve the exact certificates embedded in every PDF signature.

        pyHanko only extracts the CMS certificate bytes here. EU DSS remains
        authoritative for trust, qualification, timestamps and revocation.
        """
        self.ensure_one()
        signatures = cross_validation.get("signatures") or []
        if not signatures:
            msg = "The completed PDF contains no extractable signature certificate."
            raise DSSServiceError(msg)
        payload = {
            "format": "usl-sign-pdf-certificate-chains-v1",
            "extracted_by": cross_validation.get("engine") or "pyHanko",
            "engine_version": cross_validation.get("engine_version") or "0.36.2",
            "trust_authority": "EU DSS validation reports",
            "signatures": [],
        }
        try:
            for signature in signatures:
                encoded_chain = signature.get("certificate_chain") or []
                if not encoded_chain:
                    msg = "A completed PDF signature has no embedded certificate chain."
                    raise DSSServiceError(msg)
                chain = []
                for encoded in encoded_chain:
                    der = base64.b64decode(encoded, validate=True)
                    certificate = x509.load_der_x509_certificate(der)
                    not_before = (
                        certificate.not_valid_before_utc
                        if hasattr(certificate, "not_valid_before_utc")
                        else certificate.not_valid_before
                    )
                    not_after = (
                        certificate.not_valid_after_utc
                        if hasattr(certificate, "not_valid_after_utc")
                        else certificate.not_valid_after
                    )
                    chain.append(
                        {
                            "der": encoded,
                            "sha256": hashlib.sha256(der).hexdigest(),
                            "subject": certificate.subject.rfc4514_string(),
                            "issuer": certificate.issuer.rfc4514_string(),
                            "serial_number": format(certificate.serial_number, "x"),
                            "not_before": not_before.isoformat(),
                            "not_after": not_after.isoformat(),
                        },
                    )
                payload["signatures"].append(
                    {
                        "field_name": signature.get("field_name"),
                        "certificate_chain": chain,
                    },
                )
        except (TypeError, ValueError) as error:
            msg = "The completed PDF contains malformed certificate evidence."
            raise DSSServiceError(msg) from error
        return self._create_evidence(
            "certificate",
            f"{self.name}-embedded-pdf-certificate-chains.json",
            json.dumps(payload, sort_keys=True, indent=2).encode(),
            mimetype="application/json",
            metadata={
                "operation": "CMS certificate extraction",
                "trust_authority": "EU DSS",
                "signature_count": len(payload["signatures"]),
            },
        )

    def _complete_validated_document(self, final_data, validation, *, report_evidence=None):
        self.ensure_one()
        achieved = validation.get("achievedTrust")
        if achieved != self.requested_trust:
            self._record_validation_failure(
                "The validated trust level does not meet the requested trust level.",
                validation=validation,
            )
            return False
        try:
            cross_validation = self._sign_dss_client().cross_validate(final_data)
        except DSSServiceError as error:
            cross_validation = {
                "engine": "pyHanko",
                "engine_version": "0.36.2",
                "status": "error",
                "summary": str(error),
            }
        self._create_evidence(
            "validation",
            f"{self.name}-pyhanko-cross-validation.json",
            json.dumps(
                cross_validation, sort_keys=True, indent=2, ensure_ascii=False,
            ).encode(),
            mimetype="application/json",
            metadata={"engine": "pyHanko", "version": "0.36.2"},
        )
        dss_signature_count = validation.get("signatureCount")
        counts_disagree = (
            isinstance(dss_signature_count, int)
            and dss_signature_count != cross_validation.get("signature_count")
        )
        if cross_validation.get("status") != "valid" or counts_disagree:
            self.with_context(usl_sign_transition=INTERNAL_OPERATION).write(
                {
                    "validation_status": "indeterminate",
                    "last_error": "DSS and pyHanko did not independently agree on the completed PDF.",
                    "recovery_action": "Have an evidence reviewer inspect both validation reports.",
                },
            )
            self._transition(
                "action_required",
                "cross_validation_disagreement",
                payload={
                    "dss_signature_count": dss_signature_count,
                    "pyhanko_signature_count": cross_validation.get("signature_count"),
                    "pyhanko_status": cross_validation.get("status"),
                },
            )
            return False
        try:
            self._store_pdf_certificate_chains(cross_validation)
        except DSSServiceError as error:
            self.with_context(usl_sign_transition=INTERNAL_OPERATION).write(
                {
                    "validation_status": "indeterminate",
                    "last_error": str(error),
                    "recovery_action": "Have an evidence reviewer inspect the embedded PDF certificates.",
                },
            )
            self._transition(
                "action_required",
                "certificate_evidence_incomplete",
                payload={"reason": str(error)},
            )
            return False
        report_evidence = report_evidence or self._store_dss_reports(validation)
        if self.requested_trust == "qualified_external":
            now = fields.Datetime.now()
            for signer in self.signer_ids:
                signer.with_context(usl_sign_signer_transition=INTERNAL_OPERATION).write(
                    {
                        "state": "signed",
                        "signed_on": now,
                        "signature_hash": hashlib.sha256(final_data).hexdigest(),
                        "signed_document_sha256": hashlib.sha256(final_data).hexdigest(),
                        "authentication_method": "external_provider",
                        "consent_text": "Consent and signing authorization captured by the external qualified provider; see the imported proof package.",
                        "consent_version": "external-provider-proof-v1",
                        "consented_at": now,
                        "access_revoked": True,
                    },
                )
                self._create_evidence(
                    "consent",
                    f"{self.name}-{signer.id}-external-signing-result.json",
                    json.dumps(
                        {
                            "signer_id": signer.id,
                            "partner_id": signer.partner_id.id,
                            "name": signer.partner_id.name,
                            "authentication_method": "external_provider",
                            "validated_at": fields.Datetime.to_string(now),
                            "document_sha256": hashlib.sha256(final_data).hexdigest(),
                            "proof": "See external provider proof package and DSS reports.",
                        },
                        sort_keys=True,
                    ).encode(),
                    mimetype="application/json",
                    signer=signer,
                )
        validation_record = self.env["usl.sign.validation"].with_context(
            usl_sign_validation_create=INTERNAL_OPERATION,
        ).create(
            {
                "request_id": self.id,
                "engine_version": validation.get("engineVersion") or "6.4",
                "expected_trust": self.requested_trust,
                "achieved_trust": achieved,
                "status": "valid",
                "signature_count": validation.get("signatureCount", 0),
                "qualified_provider": validation.get("qualifiedProvider"),
                "certificate_summary": validation.get("certificates") or {},
                "timestamp_summary": validation.get("timestamps") or {},
                "revocation_summary": validation.get("revocation") or {},
                "summary": validation.get("summary") or "DSS validation passed.",
                "report_evidence_id": report_evidence.id,
            },
        )
        digest = hashlib.sha256(final_data).hexdigest()
        self.with_context(usl_sign_freeze=INTERNAL_OPERATION).write(
            {
                "final_data": field_value(final_data),
                "final_filename": f"{self.name}-signed.pdf",
                "final_sha256": digest,
                "data": field_value(final_data),
                "current_hash": digest,
                "achieved_trust": achieved,
                "validation_status": "valid",
                "evidence_status": "building",
                "last_error": False,
                "recovery_action": False,
            },
        )
        self._create_evidence(
            "signed",
            self.final_filename,
            final_data,
            mimetype="application/pdf",
        )
        self._append_event(
            "validation_passed",
            payload={"validation_id": validation_record.id, "sha256": digest},
        )
        try:
            self._build_completion_evidence()
        except (DSSServiceError, UserError) as error:
            self.with_context(usl_sign_transition=INTERNAL_OPERATION).write(
                {
                    "evidence_status": "incomplete",
                    "last_error": str(error),
                    "recovery_action": "Restore the evidence services and retry validation.",
                },
            )
            self._transition(
                "action_required",
                "evidence_build_failed",
                payload={"reason": type(error).__name__},
            )
            return False
        self._transition("evidence_incomplete", "evidence_package_built")
        self._archive_dossier()
        return validation_record

    def _completion_certificate_pdf(self):
        self.ensure_one()
        trust_labels = dict(TRUST_LEVELS)
        stream = BytesIO()
        pdf = canvas.Canvas(stream, pagesize=A4)
        _width, height = A4
        pdf.setTitle(f"Completion certificate - {self.name}")
        pdf.setFont("Helvetica-Bold", 18)
        pdf.drawString(50, height - 60, f"{self.company_id.name} completion certificate")
        pdf.setFont("Helvetica", 10)
        lines = [
            f"Request: {self.name}",
            f"Company: {self.company_id.name}",
            f"Requested trust: {trust_labels.get(self.requested_trust, self.requested_trust)}",
            f"Achieved trust: {trust_labels.get(self.achieved_trust, self.achieved_trust or 'Not established')}",
            f"Original SHA-256: {self.original_sha256}",
            f"Final SHA-256: {self.final_sha256}",
            f"Policy version: {self.policy_version}",
            f"Validation: EU DSS 6.4 - {self.validation_status}",
        ]
        y = height - 95
        for line in lines:
            pdf.drawString(50, y, line)
            y -= 17
        y -= 10
        pdf.setFont("Helvetica-Bold", 12)
        pdf.drawString(50, y, "Signers")
        y -= 18
        pdf.setFont("Helvetica", 10)
        for signer in self.signer_ids.sorted(lambda row: (row.sequence, row.id)):
            pdf.drawString(
                50,
                y,
                f"{signer.partner_id.name} — {signer.role_id.name} — {signer.state} — {signer.authentication_method or ''}",
            )
            y -= 17
        head = self.event_ids.verify_chain()
        if head:
            y -= 10
            pdf.drawString(50, y, f"Evidence event head: {head.event_hash}")
        pdf.setFont("Helvetica-Oblique", 8)
        pdf.drawString(
            50,
            50,
            "Standard evidence and the USL platform seal are not personal, advanced, qualified, certified,",
        )
        pdf.drawString(
            50,
            40,
            "or handwritten-equivalent signatures. This certificate summarizes evidence; EU DSS remains authoritative.",
        )
        pdf.save()
        return stream.getvalue()

    def _build_completion_evidence(self):
        self.ensure_one()
        certificate = self._completion_certificate_pdf()
        certificate_evidence = self._create_evidence(
            "completion",
            f"{self.name}-completion-certificate.pdf",
            certificate,
            mimetype="application/pdf",
        )
        self.with_context(usl_sign_freeze=INTERNAL_OPERATION).write(
            {
                "completion_certificate": field_value(certificate),
                "completion_filename": certificate_evidence.name,
            },
        )
        head = self.event_ids.verify_chain()
        snapshot_payload = {
            "format": "usl-sign-frozen-snapshot-v1",
            "request_id": self.id,
            "template_version": self.template_version,
            "documents": [
                {
                    "sequence": document.sequence,
                    "filename": document.filename,
                    "sha256": document.source_sha256,
                    "annex": document.is_annex,
                }
                for document in self.document_ids.sorted(lambda row: (row.sequence, row.id))
            ],
            "page_map": self.page_map,
            "field_layout": self.frozen_layout,
            "policy": self.policy_snapshot,
            "signers": self.signer_snapshot,
            "consent_text": self.consent_text_snapshot,
        }
        self._create_evidence(
            "snapshot",
            f"{self.name}-frozen-snapshot.json",
            json.dumps(
                snapshot_payload, sort_keys=True, indent=2, ensure_ascii=False,
            ).encode(),
            mimetype="application/json",
        )
        lifecycle_payload = {
            "format": "usl-sign-event-chain-v1",
            "request_id": self.id,
            "events": [
                {
                    "sequence": event.sequence,
                    "event_type": event.event_type,
                    "occurred_at": fields.Datetime.to_string(event.occurred_at),
                    "actor_id": event.actor_id.id or None,
                    "signer_id": event.signer_id.id or None,
                    "authentication_method": event.authentication_method or None,
                    "ip_address": event.ip_address or None,
                    "user_agent": event.user_agent or None,
                    "state_from": event.state_from or None,
                    "state_to": event.state_to or None,
                    "payload": event.payload,
                    "previous_hash": event.previous_hash or None,
                    "payload_sha256": event.payload_sha256,
                    "event_hash": event.event_hash,
                }
                for event in self.event_ids.sorted(lambda row: row.sequence)
            ],
        }
        self._create_evidence(
            "lifecycle",
            f"{self.name}-event-chain.json",
            json.dumps(
                lifecycle_payload, sort_keys=True, indent=2, ensure_ascii=False,
            ).encode(),
            mimetype="application/json",
        )
        manifest_payload = {
            "format": "usl-sign-evidence-manifest-v1",
            "request_id": self.id,
            "request_name": self.name,
            "company_id": self.company_id.id,
            "requested_trust": self.requested_trust,
            "achieved_trust": self.achieved_trust,
            "original_sha256": self.original_sha256,
            "final_sha256": self.final_sha256,
            "event_head": head.event_hash if head else None,
            "policy_version": self.policy_version,
            "policy_snapshot": self.policy_snapshot,
            "signer_snapshot": self.signer_snapshot,
            "consent_sha256": hashlib.sha256(
                (self.consent_text_snapshot or "").encode(),
            ).hexdigest(),
            "artifacts": [
                {
                    "kind": evidence.kind,
                    "name": evidence.name,
                    "sha256": evidence.sha256,
                }
                for evidence in self.evidence_ids
                if evidence.kind not in {"manifest", "dossier"}
            ],
        }
        manifest = json.dumps(
            manifest_payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
        ).encode()
        try:
            signed = self._sign_dss_client().sign_manifest(manifest)
        except DSSServiceError as error:
            raise UserError(f"The evidence manifest could not be signed: {error}") from error
        manifest = json.dumps(
            {
                "format": "usl-sign-detached-manifest-signature-v1",
                "manifest": base64.b64encode(manifest).decode(),
                "manifest_sha256": signed["manifestSha256"],
                "signature": signed["signature"],
                "signature_algorithm": signed["signatureAlgorithm"],
                "certificate_chain": signed["certificateChain"],
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        manifest_evidence = self._create_evidence(
            "manifest",
            f"{self.name}-signed-evidence-manifest.json",
            manifest,
            mimetype="application/json",
        )
        self.with_context(usl_sign_freeze=INTERNAL_OPERATION).write(
            {"evidence_manifest": manifest_evidence.data},
        )
        dossier = self._build_dossier_pdf(manifest)
        try:
            preflight = self._sign_dss_client().validate_pdfa(dossier)
            if not preflight.get("compliant"):
                msg = "veraPDF rejected the archival dossier preflight as non-conformant."
                raise DSSServiceError(  # noqa: TRY301 - normalized to an Odoo archival error below
                    msg,
                )
            self._create_evidence(
                "validation",
                f"{self.name}-verapdf-pdfa-3b-preflight.json",
                json.dumps(preflight, sort_keys=True, indent=2, ensure_ascii=False).encode(),
                mimetype="application/json",
                metadata={"engine": "veraPDF", "version": "1.30.2", "profile": "PDF/A-3b"},
            )
            dossier = self._build_dossier_pdf(manifest)
            sealed = self._sign_dss_client().seal(
                dossier,
                request_reference=f"USL-SIGN-DOSSIER-{self.id}",
                timestamp=self.company_id.sign_rfc3161_enabled,
            )
            dossier = base64.b64decode(sealed["document"])
            pdfa_validation = self._sign_dss_client().validate_pdfa(dossier)
            if not pdfa_validation.get("compliant"):
                msg = "veraPDF rejected the sealed archival dossier as non-conformant."
                raise DSSServiceError(  # noqa: TRY301 - normalized to an Odoo archival error below
                    msg,
                )
        except DSSServiceError as error:
            raise UserError(f"The archival dossier could not be sealed: {error}") from error
        self._create_evidence(
            "validation",
            f"{self.name}-verapdf-pdfa-3b-final-report.json",
            json.dumps(
                pdfa_validation,
                sort_keys=True,
                indent=2,
                ensure_ascii=False,
            ).encode(),
            mimetype="application/json",
            metadata={"engine": "veraPDF", "version": "1.30.2", "profile": "PDF/A-3b"},
        )
        dossier_evidence = self._create_evidence(
            "dossier",
            f"{self.name}-proof-package.pdf",
            dossier,
            mimetype="application/pdf",
        )
        self.with_context(usl_sign_freeze=INTERNAL_OPERATION).write(
            {
                "dossier_data": dossier_evidence.data,
                "dossier_filename": dossier_evidence.name,
                "evidence_status": "complete",
            },
        )

    def _build_dossier_pdf(self, manifest):
        self.ensure_one()
        trust_labels = dict(TRUST_LEVELS)
        artifacts = [
            {
                "name": f"final-{self.final_filename}",
                "content": field_content(self.final_data),
                "mimetype": "application/pdf",
                "relationship": "Data",
                "description": "Final independently validated signed document",
            },
            {
                "name": f"manifest-{self.name}-signed-evidence-manifest.json",
                "content": manifest,
                "mimetype": "application/json",
                "relationship": "Supplement",
                "description": "Platform-signed canonical evidence manifest",
            },
        ]
        for evidence in self.evidence_ids.filtered(
            lambda row: row.kind
            not in {"authentication", "manifest", "dossier", "signed"},
        ):
            artifacts.append(
                {
                    "name": f"{evidence.kind}-{evidence.id}-{evidence.name}",
                    "content": field_content(evidence.data),
                    "mimetype": evidence.mimetype,
                    "relationship": "Supplement",
                    "description": dict(evidence._fields["kind"].selection).get(
                        evidence.kind, "Evidence artifact",
                    ),
                },
            )
        for evidence in self.evidence_ids.filtered(
            lambda row: row.kind == "authentication",
        ):
            metadata = evidence.metadata or {}
            summary = {
                "format": "usl-sign-authentication-summary-v1",
                "artifact_sha256": evidence.sha256,
                "ceremony_id": metadata.get("ceremony_id"),
                "issuer": metadata.get("issuer"),
                "subject_fingerprint": metadata.get("subject_fingerprint"),
                "auth_time": metadata.get("auth_time"),
                "claims": metadata.get("claims"),
                "validation": metadata.get("validation"),
            }
            artifacts.append(
                {
                    "name": f"authentication-summary-{evidence.id}.json",
                    "content": json.dumps(
                        summary,
                        sort_keys=True,
                        separators=(",", ":"),
                        ensure_ascii=False,
                    ).encode(),
                    "mimetype": "application/json",
                    "relationship": "Supplement",
                    "description": "Validated Pocket ID passkey authorization summary",
                },
            )
        result = self._sign_dss_client().build_dossier(
            title=f"{self.company_id.name} signing evidence - {self.name}",
            summary=[
                "Purpose: audit proof package; use the separately archived signed PDF as the document of record",
                f"Signed PDF embedded in this package: final-{self.final_filename}",
                "To extract the original files, open this PDF's attachments panel in a compatible PDF reader",
                f"Company: {self.company_id.name}",
                f"Requested trust: {trust_labels.get(self.requested_trust, self.requested_trust)}",
                f"Achieved trust: {trust_labels.get(self.achieved_trust, self.achieved_trust or 'Not established')}",
                f"Original SHA-256: {self.original_sha256}",
                f"Final SHA-256: {self.final_sha256}",
                f"Policy version: {self.policy_version}",
                "PDF signature validation authority: EU DSS 6.4",
                "Archive format: PDF/A-3b with associated evidence files",
            ],
            artifacts=artifacts,
        )
        return base64.b64decode(result["document"])

    def _archive_dossier(self, force=False):
        for request in self:
            if not request.final_data or not request.dossier_data:
                msg = "Build the signed document and complete proof package before archival."
                raise ValidationError(msg)
            if request.archive_status in {"pending", "processing", "archived"} and not force:
                continue
            # Attribute the controlled server-side upload to the requester so
            # private Documents ownership remains meaningful. ``sudo`` below
            # supplies the service privilege; the signer never needs archive
            # permissions.
            archive_actor = request.user_id
            artifacts = (
                (
                    "signed_document",
                    "archive_operation_id",
                    "archive_document_id",
                    request.final_filename,
                    request.final_data,
                ),
                (
                    "proof_package",
                    "dossier_archive_operation_id",
                    "archive_dossier_document_id",
                    request.dossier_filename,
                    request.dossier_data,
                ),
            )
            queued_states = {}
            try:
                for (
                    artifact_key,
                    operation_field,
                    document_field,
                    filename,
                    data,
                ) in artifacts:
                    if request[document_field]:
                        queued_states[artifact_key] = "already_archived"
                        continue
                    operation = request[operation_field]
                    if operation and operation.state in {"uploading", "processing"}:
                        queued_states[artifact_key] = operation.state
                        continue
                    # These files are generated by the controlled Sign service,
                    # not by the last signer. Keep archival under Odoo's system
                    # identity so a signer never needs Documents permissions.
                    result = (
                        self.env["usl.document"]
                        .with_user(archive_actor)
                        .sudo()
                        .with_company(request.company_id)
                        .upload_from_odoo(
                            filename,
                            base64_text(field_content(data)),
                            "application/pdf",
                            res_model=request._name,
                            res_id=request.id,
                            company_id=request.company_id.id,
                            confidentiality="private",
                            source="odoo_generated",
                        )
                    )
                    if not isinstance(result, dict) or result.get("state") not in {
                        "duplicate",
                        "pending",
                        "processing",
                    }:
                        msg = "Unexpected Paperless archival response."
                        raise ValueError(msg)  # noqa: TRY301
                    artifact_values = {
                        "archive_last_error": False,
                        "last_error": False,
                        "recovery_action": False,
                    }
                    if result["state"] == "duplicate" and result.get("document_id"):
                        artifact_values.update(
                            {
                                operation_field: False,
                                document_field: result["document_id"],
                            },
                        )
                    elif result.get("operation_id"):
                        artifact_values[operation_field] = result["operation_id"]
                    else:
                        msg = "Paperless returned no canonical document or operation relationship."
                        raise ValueError(msg)  # noqa: TRY301
                    request.with_context(usl_sign_transition=INTERNAL_OPERATION).write(
                        artifact_values,
                    )
                    queued_states[artifact_key] = result["state"]
            # The Paperless boundary may raise connector, HTTP, ORM or response-shape
            # errors. All must become the same safe, recoverable product state.
            except Exception as error:  # noqa: BLE001
                request.with_context(usl_sign_transition=INTERNAL_OPERATION).write(
                    {
                        "archive_status": "failed",
                        "archive_last_error": "Paperless rejected or could not accept the signed document or proof package.",
                        "last_error": "Paperless archival failed.",
                        "recovery_action": (
                            "Check that Paperless is available, then retry final storage."
                        ),
                        "evidence_status": "incomplete",
                    },
                )
                request._append_event(
                    "archive_failed", payload={"error": type(error).__name__},
                )
                continue
            request.invalidate_recordset()
            archive_complete = bool(
                request.archive_document_id and request.archive_dossier_document_id,
            )
            request.with_context(usl_sign_transition=INTERNAL_OPERATION).write(
                {
                    "archive_status": "archived" if archive_complete else "processing",
                    "archive_last_error": False,
                    "last_error": False,
                    "recovery_action": False,
                    "evidence_status": "complete",
                },
            )
            request._append_event("archive_queued", payload=queued_states)
            request._reconcile_archive()

    def _reconcile_archive(self):
        for request in self:
            failed_operation = False
            for operation_field, document_field in (
                ("archive_operation_id", "archive_document_id"),
                ("dossier_archive_operation_id", "archive_dossier_document_id"),
            ):
                operation = request.sudo()[operation_field]
                if operation and operation.state == "processing":
                    try:
                        operation.poll()
                        operation.invalidate_recordset()
                    # Connector implementations expose different transport
                    # exceptions; fail closed while keeping archival retryable.
                    except Exception as error:  # noqa: BLE001
                        request.with_context(usl_sign_transition=INTERNAL_OPERATION).write(
                            {
                                "archive_status": "failed",
                                "archive_last_error": "Paperless archival status could not be confirmed.",
                                "last_error": "Paperless archival reconciliation failed.",
                                "recovery_action": (
                                    "Check that Paperless is available, then retry final storage."
                                ),
                                "evidence_status": "incomplete",
                            },
                        )
                        request._append_event(
                            "archive_reconciliation_failed",
                            payload={
                                "artifact": document_field,
                                "error": type(error).__name__,
                            },
                        )
                        failed_operation = True
                        break
                if operation and operation.state == "archived" and operation.document_id:
                    request.with_context(usl_sign_transition=INTERNAL_OPERATION).write(
                        {document_field: operation.document_id.id},
                    )
                elif operation and operation.state in {"duplicate", "failed"}:
                    request.with_context(usl_sign_transition=INTERNAL_OPERATION).write(
                        {
                            "archive_status": "failed",
                            "archive_last_error": (
                                operation.error_message
                                or "The archived file needs an administrator to classify it."
                            ),
                            "last_error": "Paperless archival needs attention.",
                            "recovery_action": (
                                "A matching file in Paperless needs classification. "
                                "Resolve it there, then retry final storage."
                            ),
                            "evidence_status": "incomplete",
                        },
                    )
                    failed_operation = True
                    break
            if failed_operation:
                continue
            request.invalidate_recordset()
            archive_complete = bool(
                request.archive_document_id and request.archive_dossier_document_id,
            )
            if archive_complete:
                try:
                    request._share_archived_files_with_participants()
                except Exception as error:  # noqa: BLE001
                    request.with_context(usl_sign_transition=INTERNAL_OPERATION).write(
                        {
                            "archive_status": "failed",
                            "archive_last_error": (
                                "The final files are stored, but participant access "
                                "could not be synchronized with Paperless."
                            ),
                            "last_error": "Final archive access synchronization failed.",
                            "recovery_action": (
                                "Check Paperless user mappings and permissions, then "
                                "retry final storage."
                            ),
                            "evidence_status": "incomplete",
                        },
                    )
                    request._append_event(
                        "archive_reconciliation_failed",
                        payload={
                            "artifact": "participant_access",
                            "error": type(error).__name__,
                        },
                    )
                    continue
            request.with_context(usl_sign_transition=INTERNAL_OPERATION).write(
                {
                    "archive_status": "archived" if archive_complete else "processing",
                    "archive_last_error": False,
                    "last_error": False,
                    "recovery_action": False,
                    "evidence_status": "complete",
                },
            )
            if request.archive_status == "archived" and request.state == "evidence_incomplete":
                complete_signers = all(
                    signer.state == "signed" for signer in request.signer_ids
                )
                completion_ready = (
                    complete_signers
                    and request.validation_status == "valid"
                    and request.achieved_trust == request.requested_trust
                    and request.evidence_status == "complete"
                    and request.archive_document_id
                    and request.archive_dossier_document_id
                )
                if not completion_ready:
                    request.with_context(usl_sign_transition=INTERNAL_OPERATION).write(
                        {
                            "last_error": "The completion gate found missing signatures, validation, evidence, or archive linkage.",
                            "recovery_action": "Have an evidence reviewer inspect the completion gate.",
                        },
                    )
                    request._transition("action_required", "completion_gate_failed")
                    continue
                request.with_context(usl_sign_transition=INTERNAL_OPERATION).write(
                    {
                        "completed_at": fields.Datetime.now(),
                        "last_error": False,
                        "recovery_action": False,
                    },
                )
                request._transition("completed", "request_completed")
                request._send_completed_dossier()

    def _send_completed_dossier(self):
        """Queue the final archived dossier once, using OCA's delivery setting."""
        for request in self:
            if (
                request.state != "completed"
                or not request.company_id.sign_oca_send_sign_request_copy
                or request.event_ids.filtered(
                    lambda event: event.event_type == "completed_dossier_queued",
                )
            ):
                continue
            attachment = self.env["ir.attachment"].sudo().search(
                [
                    ("res_model", "=", request._name),
                    ("res_id", "=", request.id),
                    ("res_field", "=", "dossier_data"),
                ],
                limit=1,
            )
            if not attachment:
                msg = "The completed evidence dossier attachment is missing."
                raise ValidationError(msg)
            partners = request.signer_ids.mapped("partner_id")
            body = self.env["ir.qweb"]._render(
                "usl_sign.sign_completion_delivery_body",
                {"record": request},
                engine="ir.qweb",
                minimal_qcontext=True,
            )
            self.env["mail.thread"].message_notify(
                body=body,
                partner_ids=partners.ids,
                subject=self.env._("Completed signature dossier: %(name)s", name=request.name),
                subtype_id=self.env.ref("mail.mt_comment").id,
                mail_auto_delete=False,
                email_layout_xmlid="mail.mail_notification_light",
                attachment_ids=attachment.ids,
            )
            request._append_event(
                "completed_dossier_queued",
                payload={
                    "partner_ids": partners.ids,
                    "filename": request.dossier_filename,
                    "sha256": hashlib.sha256(field_content(request.dossier_data)).hexdigest(),
                },
            )

    def action_send_reminder(self):
        self._check_prepare_access()
        return self._send_due_reminders(force=True)

    def _send_due_reminders(self, force=False):
        now = fields.Datetime.now()
        for request in self.filtered(lambda row: row.state in {"sent", "viewed", "partial"}):
            if request.reminder_count >= request.max_reminders:
                if force:
                    msg = "The reminder limit has been reached."
                    raise ValidationError(msg)
                continue
            due = (request.last_reminder_at or request.sent_at) + timedelta(
                days=request.reminder_days,
            )
            if not force and due > now:
                continue
            pending = request.signer_ids.filtered(lambda signer: signer.state in {"notified", "viewed"})
            if request.signing_order and pending:
                pending = pending.sorted(lambda row: (row.sequence, row.id))[:1]
            if not pending:
                if force:
                    msg = "No signer is currently eligible for a reminder."
                    raise ValidationError(msg)
                continue
            for signer in pending:
                signer._send_signer_invitation(force=True, reminder=True)
            request.with_context(usl_sign_transition=INTERNAL_OPERATION).write(
                {
                    "reminder_count": request.reminder_count + 1,
                    "last_reminder_at": now,
                },
            )
            request._append_event("reminder_sent", payload={"signer_ids": pending.ids})
        return True

    def _expire_request(self):
        for request in self.filtered(lambda row: row.state in EXPIRABLE_REQUEST_STATES):
            request.signer_ids.filtered(lambda signer: signer.state != "signed").with_context(
                usl_sign_signer_transition=INTERNAL_OPERATION,
            ).write({"state": "expired", "access_revoked": True})
            request._close_outstanding_work("request_expired")
            request._create_evidence(
                "expiration",
                f"{request.name}-expiration.json",
                json.dumps({"expired_at": fields.Datetime.to_string(fields.Datetime.now())}).encode(),
                mimetype="application/json",
            )
            request._transition("expired", "request_expired")

    @api.model
    def _cron_sign_operations(self):
        now = fields.Datetime.now()
        signer_model = self.env["sign.oca.request.signer"]
        actionable_signers = signer_model.search(
            [
                ("state", "in", ["notified", "viewed", "authorized"]),
                ("request_id.state", "in", ["sent", "viewed", "partial"]),
                ("access_revoked", "=", False),
            ],
            limit=500,
        )
        activity_type = self.env.ref("usl_sign.mail_activity_type_sign_document")
        active_activities = self.env["mail.activity"].sudo().search(
            [
                ("activity_type_id", "=", activity_type.id),
                ("res_model", "=", "sign.oca.request.signer"),
                ("active", "=", True),
            ],
        )
        activity_signers = signer_model.browse(active_activities.mapped("res_id")).exists()
        (actionable_signers | activity_signers)._ensure_internal_signing_activities()
        failed_delivery = self.env["sign.oca.request.signer"].search(
            [
                ("request_id.state", "in", ["sent", "viewed", "partial"]),
                ("invitation_mail_id.state", "=", "exception"),
                ("invitation_fallback_at", "=", False),
            ],
            limit=100,
        )
        odoo_available = failed_delivery.filtered(
            lambda signer: signer._has_internal_signing_access(),
        )
        for signer in odoo_available:
            signer.with_context(usl_sign_signer_transition=INTERNAL_OPERATION).write(
                {"invitation_fallback_at": now},
            )
            signer.request_id._append_event(
                "invitation_available_in_odoo",
                signer=signer,
                payload={"email_delivery": "failed"},
            )
        failed_delivery -= odoo_available
        for request in failed_delivery.mapped("request_id"):
            signers = failed_delivery.filtered(lambda signer: signer.request_id == request)
            request.with_context(usl_sign_transition=INTERNAL_OPERATION).write(
                {
                    "last_error": "A private signing invitation could not be delivered.",
                    "recovery_action": "Correct the Odoo mail configuration and retry the invitation.",
                },
            )
            request._transition(
                "action_required",
                "invitation_delivery_failed",
                payload={"signer_ids": signers.ids},
            )
        self.search(
            [
                ("state", "in", list(EXPIRABLE_REQUEST_STATES)),
                ("expires_at", "!=", False),
                ("expires_at", "<=", now),
            ],
            limit=100,
        )._expire_request()
        self.search(
            [("state", "in", ["sent", "viewed", "partial"])], limit=100,
        )._send_due_reminders()
        ceremonies = self.env["usl.sign.ceremony"].search(
            [
                ("state", "in", ["challenge", "authorizing", "authorized"]),
                ("expires_at", "<=", now),
            ],
            limit=200,
        )
        authorized_signers = ceremonies.filtered(
            lambda ceremony: ceremony.state == "authorized",
        ).mapped("signer_id")
        for ceremony in ceremonies:
            ceremony.request_id._append_event(
                "strong_ceremony_expired",
                signer=ceremony.signer_id,
                authentication_method="pocket_id_passkey",
                payload={"ceremony_id": ceremony.id},
            )
        ceremonies.with_context(usl_sign_ceremony_transition=INTERNAL_OPERATION).write(
            {
                "state": "expired",
                "failure_code": "ceremony_expired",
                "data_to_sign": False,
                "dss_signing_context": False,
            },
        )
        authorized_signers.filtered(
            lambda signer: not signer.signed_on
            and signer.request_id.state in {"sent", "viewed", "partial"},
        ).with_context(usl_sign_signer_transition=INTERNAL_OPERATION).write(
            {"state": "notified"},
        )
        stale_signers = self.env["sign.oca.request.signer"].search(
            [
                "|",
                ("access_expires_at", "<=", now),
                "|",
                ("session_expires_at", "<=", now),
                ("email_otp_expires_at", "<=", now),
            ],
            limit=500,
        )
        for signer in stale_signers:
            values = {}
            if signer.access_expires_at and signer.access_expires_at <= now:
                values.update({"access_token_sha256": False, "access_expires_at": False})
            if signer.session_expires_at and signer.session_expires_at <= now:
                values.update({"session_token_sha256": False, "session_expires_at": False})
            if signer.email_otp_expires_at and signer.email_otp_expires_at <= now:
                values.update(
                    {
                        "otp_exchange_token_sha256": False,
                        "otp_exchange_expires_at": False,
                        "email_otp_salt": False,
                        "email_otp_sha256": False,
                        "email_otp_expires_at": False,
                    },
                )
            if values:
                signer.with_context(usl_sign_signer_transition=INTERNAL_OPERATION).write(
                    values,
                )
        self._cron_reconcile_archives()

    def cancel(self):
        self._check_owner_access()
        for request in self:
            if request.state in TERMINAL_REQUEST_STATES:
                continue
            if request.state not in CANCELLABLE_REQUEST_STATES:
                msg = "This request cannot be cancelled during validation or evidence finalization."
                raise ValidationError(msg)
            request.signer_ids.filtered(lambda signer: signer.state != "signed").with_context(
                usl_sign_signer_transition=INTERNAL_OPERATION,
            ).write({"state": "cancelled", "access_revoked": True})
            request._close_outstanding_work("request_cancelled")
            request._create_evidence(
                "cancellation",
                f"{request.name}-cancellation.json",
                json.dumps({"cancelled_at": fields.Datetime.to_string(fields.Datetime.now())}).encode(),
                mimetype="application/json",
            )
            request._transition("cancelled", "request_cancelled")
        return True

    def _close_outstanding_work(self, failure_code):
        self.ensure_one()
        self.signer_ids._close_internal_signing_activities()
        self.external_journey_id.filtered(
            lambda journey: journey.state not in {"validated", "rejected", "cancelled"},
        ).with_context(usl_sign_external_transition=INTERNAL_OPERATION).write({"state": "cancelled"})
        self.env["usl.sign.ceremony"].search(
            [
                ("request_id", "=", self.id),
                ("state", "in", ["challenge", "authorizing", "authorized"]),
            ],
        ).with_context(usl_sign_ceremony_transition=INTERNAL_OPERATION).write(
            {
                "state": "failed",
                "failure_code": failure_code,
                "data_to_sign": False,
                "dss_signing_context": False,
            },
        )

    def action_retry_validation(self):
        self._check_prepare_access()
        for request in self:
            if request.state != "action_required":
                continue
            failed_delivery = request.signer_ids.filtered(
                lambda signer: signer.invitation_mail_id.state == "exception"
                and not signer.invitation_fallback_at,
            )
            if failed_delivery:
                odoo_available = failed_delivery.filtered(
                    lambda signer: signer._has_internal_signing_access(),
                )
                odoo_available.with_context(
                    usl_sign_signer_transition=INTERNAL_OPERATION,
                ).write({"invitation_fallback_at": fields.Datetime.now()})
                retry_delivery = failed_delivery - odoo_available
                if retry_delivery:
                    retry_delivery.mapped("invitation_mail_id").sudo().unlink()
                    retry_delivery._send_signer_invitation(force=True)
                request.with_context(usl_sign_transition=INTERNAL_OPERATION).write(
                    {"last_error": False, "recovery_action": False},
                )
                target_state = (
                    "partial" if request.signer_ids.filtered("signed_on") else "sent"
                )
                request._transition(
                    target_state,
                    "invitation_delivery_retried",
                    payload={"signer_ids": failed_delivery.ids},
                )
                continue
            if (
                request.requested_trust == "qualified_external"
                and request.external_journey_id.imported_pdf
            ):
                request._transition(
                    "signed_to_import", "external_validation_retry_ready",
                )
                request.action_validate_external()
                continue
            if request.final_data and request.validation_status == "valid":
                if not request.dossier_data:
                    request._build_completion_evidence()
                request._transition("evidence_incomplete", "evidence_retry_ready")
                request._archive_dossier(force=request.archive_status == "failed")
                continue
            request._transition("validating", "validation_retried")
            request._start_final_validation()
        return True

    def write(self, values):
        internal = any(
            self.env.context.get(key) is INTERNAL_OPERATION
            for key in (
                "usl_sign_transition",
                "usl_sign_freeze",
                "usl_sign_working_pdf",
                "usl_sign_editor_internal",
            )
        )
        if values and not internal and not self.env.su:
            chatter_fields = {"message_follower_ids", "activity_ids"}
            trust_fields = {"requested_trust", "override_reason"}
            owner_fields = {"user_id", "coordinator_ids"}
            for request in self:
                is_admin = self.env.user.has_group("usl_sign.group_sign_admin")
                is_owner = request.user_id == self.env.user
                is_coordinator = self.env.user in request.coordinator_ids
                is_trust_reviewer = self.env.user.has_group(
                    "usl_sign.group_sign_trust_override",
                )
                if is_admin or is_owner:
                    continue
                if is_coordinator:
                    if owner_fields.intersection(values) or trust_fields.intersection(values):
                        msg = "Only the requester may change sharing or the requested trust level."
                        raise AccessError(
                            msg,
                        )
                    continue
                if is_trust_reviewer and set(values) <= trust_fields:
                    continue
                if set(values) <= chatter_fields:
                    continue
                msg = "Only the requester or a named coordinator may change this request."
                raise AccessError(
                    msg,
                )
        if "state" in values and self.env.context.get("usl_sign_transition") is not INTERNAL_OPERATION:
            msg = "Use a signature lifecycle action to change state."
            raise ValidationError(msg)
        frozen_fields = {
            "document_category",
            "signer_type",
            "risk_level",
            "formal_qes_required",
            "original_data",
            "original_filename",
            "original_sha256",
            "frozen_layout",
            "page_map",
            "template_id",
            "template_version",
            "policy_id",
            "policy_version",
            "policy_snapshot",
            "signer_snapshot",
            "consent_text_snapshot",
            "requested_trust",
            "recommended_trust",
            "recommendation_reason",
            "recommendation_consequence",
            "override_reason",
            "company_id",
            "external_provider_id",
            "expires_at",
            "reminder_days",
            "max_reminders",
            "signing_order",
            "responsible_message",
            "signer_ids",
            "document_ids",
        }
        if frozen_fields.intersection(values) and self.env.context.get("usl_sign_freeze") is not INTERNAL_OPERATION:
            if self.filtered(lambda request: request.state not in MUTABLE_REQUEST_STATES):
                msg = "A sent request is immutable; create a replacement."
                raise ValidationError(msg)
        controlled_fields = {
            "authentication_method",
            "achieved_trust",
            "final_data",
            "final_filename",
            "final_sha256",
            "completion_certificate",
            "completion_filename",
            "evidence_manifest",
            "dossier_data",
            "dossier_filename",
            "evidence_status",
            "validation_status",
            "sent_at",
            "completed_at",
            "reminder_count",
            "last_reminder_at",
            "last_error",
            "recovery_action",
            "archive_operation_id",
            "archive_document_id",
            "dossier_archive_operation_id",
            "archive_dossier_document_id",
            "archive_status",
            "archive_last_error",
        }
        if controlled_fields.intersection(values) and not (
            self.env.context.get("usl_sign_transition") is INTERNAL_OPERATION
            or self.env.context.get("usl_sign_freeze") is INTERNAL_OPERATION
        ):
            msg = "Use a controlled signature operation to change protected evidence."
            raise ValidationError(msg)
        if {"data", "signatory_data", "current_hash"}.intersection(values) and not (
            self.env.context.get("usl_sign_working_pdf") is INTERNAL_OPERATION
            or self.env.context.get("usl_sign_freeze") is INTERNAL_OPERATION
            or self.filtered(lambda request: request.state in MUTABLE_REQUEST_STATES)
        ):
            msg = "Only the controlled signing ceremony may change the PDF."
            raise ValidationError(msg)
        return super().write(values)

    def unlink(self):
        self._check_owner_access()
        if self.filtered(lambda request: request.state != "draft"):
            msg = "Only draft requests can be deleted."
            raise ValidationError(msg)
        return super().unlink()


class SignRequestSigner(models.Model):
    _inherit = "sign.oca.request.signer"
    _order = "sequence, id"

    state = fields.Selection(SIGNER_STATES, default="draft", required=True, copy=False)
    sequence = fields.Integer(string="Signing order", default=10)
    sender_id = fields.Many2one(
        related="request_id.user_id",
        string="Sender",
        readonly=True,
    )
    request_state = fields.Selection(
        related="request_id.state",
        string="Request status",
        readonly=True,
    )
    overall_status = fields.Char(
        related="request_id.lifecycle_stage_label",
        string="Overall status",
        readonly=True,
    )
    personal_status = fields.Char(
        compute="_compute_personal_presentation",
        string="Your status",
    )
    personal_next_step = fields.Char(
        compute="_compute_personal_presentation",
        string="What happens next",
    )
    can_open_signing_identity = fields.Boolean(
        compute="_compute_personal_presentation",
    )
    can_open_external_signing = fields.Boolean(
        compute="_compute_personal_presentation",
    )
    request_due_at = fields.Datetime(
        related="request_id.expires_at",
        string="Due",
        readonly=True,
    )
    requested_trust_short = fields.Char(
        related="request_id.requested_trust_short",
        string="Trust",
        readonly=True,
    )
    document_name = fields.Char(
        related="request_id.name",
        string="Document",
        readonly=True,
    )
    document_preview_url = fields.Char(
        related="request_id.document_preview_url",
        string="Document preview",
        readonly=True,
    )
    document_thumbnail_url = fields.Char(
        related="request_id.document_thumbnail_url",
        string="Document thumbnail",
        readonly=True,
    )

    @api.depends(
        "state",
        "request_id.state",
        "request_id.external_journey_id",
    )
    @api.depends_context("lang", "uid")
    def _compute_personal_presentation(self):
        partner_ids = self.mapped("partner_id").ids
        company_ids = self.mapped("request_id.company_id").ids
        enrollments = self.env["usl.sign.enrollment"].sudo().search(
            [
                ("partner_id", "in", partner_ids),
                ("company_id", "in", company_ids),
                ("state", "!=", "revoked"),
            ],
        )
        enrollment_by_identity = {
            (enrollment.partner_id.id, enrollment.company_id.id): enrollment
            for enrollment in enrollments
        }
        request_labels = {
            "completed": _("Signed"),
            "declined": _("Closed after a decline"),
            "expired": _("Expired"),
            "cancelled": _("Cancelled"),
            "validation_failed": _("Result needs review"),
        }
        signer_labels = {
            "signed": _("Signed"),
            "declined": _("Declined"),
            "expired": _("Expired"),
            "cancelled": _("Cancelled"),
        }
        for signer in self:
            owns_assignment = signer.partner_id == self.env.user.partner_id
            enrollment = enrollment_by_identity.get(
                (signer.partner_id.id, signer.request_id.company_id.id),
                self.env["usl.sign.enrollment"],
            )
            signer.can_open_signing_identity = bool(
                owns_assignment
                and signer.request_id.state == "waiting_enrollment"
                and enrollment,
            )
            signer.can_open_external_signing = bool(
                owns_assignment
                and signer.request_id.state == "waiting_external"
                and signer.request_id.external_journey_id,
            )
            if signer.state in signer_labels:
                signer.personal_status = signer_labels[signer.state]
                signer.personal_next_step = (
                    _("Open the completed files.")
                    if signer.state == "signed"
                    else _("Nothing else is needed from you.")
                )
            elif signer.state in {"notified", "viewed", "authorized"}:
                signer.personal_status = _("Ready to sign")
                signer.personal_next_step = _("Review the document and sign it.")
            elif signer.request_id.state == "waiting_enrollment":
                if enrollment and enrollment.state == "pending_pocket":
                    signer.personal_status = _("Identity setup needed")
                    signer.personal_next_step = _(
                        "Use your setup invitation to connect Pocket ID.",
                    )
                elif enrollment and enrollment.state == "pending_review":
                    signer.personal_status = _("Identity review in progress")
                    signer.personal_next_step = _(
                        "Your identity is connected. An identity reviewer must approve it.",
                    )
                elif enrollment and enrollment.state == "active":
                    signer.personal_status = _("Waiting for sender")
                    signer.personal_next_step = _(
                        "Your identity is ready. The sender must continue the request.",
                    )
                else:
                    signer.personal_status = _("Waiting for sender")
                    signer.personal_next_step = _(
                        "The sender must arrange identity setup before you can sign.",
                    )
            elif signer.request_id.state == "waiting_external":
                signer.personal_status = _("External signing")
                signer.personal_next_step = (
                    _("Open the provider instructions to continue.")
                    if signer.request_id.external_journey_id
                    else _("The sender is preparing the external signing journey.")
                )
            elif signer.request_id.state in request_labels:
                signer.personal_status = request_labels[signer.request_id.state]
                signer.personal_next_step = (
                    _("Open the completed files.")
                    if signer.request_id.state == "completed"
                    else _("Nothing else is needed from you.")
                )
            elif signer.request_id.state in {"sent", "viewed", "partial"}:
                signer.personal_status = _("Waiting for your turn")
                signer.personal_next_step = _(
                    "Another signer goes first. You will be notified when it is your turn.",
                )
            elif signer.request_id.state in {
                "signed_to_import",
                "validating",
                "evidence_incomplete",
                "action_required",
            }:
                signer.personal_status = _("Final checks")
                signer.personal_next_step = _(
                    "Nothing is needed from you while the sender completes the final checks.",
                )
            else:
                signer.personal_status = _("Not sent yet")
                signer.personal_next_step = _("The sender is still preparing the request.")

    def action_open_signing_identity(self):
        self.ensure_one()
        if self.partner_id != self.env.user.partner_id:
            msg = _("Only the assigned signer can open this signing identity.")
            raise AccessError(msg)
        enrollment = self.env["usl.sign.enrollment"].search(
            [
                ("partner_id", "=", self.partner_id.id),
                ("company_id", "=", self.request_id.company_id.id),
                ("state", "!=", "revoked"),
            ],
            limit=1,
        )
        if not enrollment:
            msg = _("The sender has not started identity setup yet.")
            raise UserError(msg)
        return {
            "type": "ir.actions.act_window",
            "name": _("My Signing Identity"),
            "res_model": "usl.sign.enrollment",
            "res_id": enrollment.id,
            "views": [
                (self.env.ref("usl_sign.sign_enrollment_my_form").id, "form"),
            ],
            "target": "current",
        }

    def action_open_external_signing(self):
        self.ensure_one()
        if self.partner_id != self.env.user.partner_id:
            msg = _("Only the assigned signer can open this signing journey.")
            raise AccessError(msg)
        journey = self.request_id.external_journey_id
        if self.request_id.state != "waiting_external" or not journey:
            msg = _("External signing instructions are not available yet.")
            raise UserError(msg)
        return journey.action_open_details()

    def action_open_request(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": self.request_id.name,
            "res_model": "sign.oca.request",
            "res_id": self.request_id.id,
            "views": [
                (self.env.ref("usl_sign.sign_request_signer_result_form").id, "form"),
            ],
            "target": "current",
        }

    def action_open_my_signature(self):
        """Open the useful destination directly from a My Signatures row."""
        self.ensure_one()
        if self.partner_id != self.env.user.partner_id:
            msg = _("Only the assigned signer can open this signing journey.")
            raise AccessError(msg)
        if self.state == "signed" or self.request_id.state in {
            "completed",
            "evidence_incomplete",
            "validation_failed",
        }:
            return self.action_open_request()
        if self.can_open_signing_identity:
            return self.action_open_signing_identity()
        if self.can_open_external_signing:
            return self.action_open_external_signing()
        if self.is_allow_signature:
            return self.sign()
        return {
            "type": "ir.actions.act_window",
            "name": self.request_id.name,
            "res_model": self._name,
            "res_id": self.id,
            "views": [
                (self.env.ref("usl_sign.my_signature_form_usl").id, "form"),
            ],
            "target": "current",
        }

    def sign(self):
        self.ensure_one()
        if not self.is_allow_signature:
            msg = "You are not allowed to sign this document."
            raise ValidationError(msg)
        return {
            "type": "ir.actions.act_url",
            "url": self.access_url,
            "target": "new",
        }

    access_token_sha256 = fields.Char(readonly=True, copy=False, index=True)
    access_expires_at = fields.Datetime(readonly=True, copy=False)
    session_token_sha256 = fields.Char(readonly=True, copy=False, index=True)
    session_expires_at = fields.Datetime(readonly=True, copy=False)
    otp_exchange_token_sha256 = fields.Char(readonly=True, copy=False, index=True)
    otp_exchange_expires_at = fields.Datetime(readonly=True, copy=False)
    email_otp_salt = fields.Char(readonly=True, copy=False)
    email_otp_sha256 = fields.Char(readonly=True, copy=False)
    email_otp_expires_at = fields.Datetime(readonly=True, copy=False)
    email_otp_failure_count = fields.Integer(default=0, readonly=True, copy=False)
    email_otp_blocked_until = fields.Datetime(readonly=True, copy=False)
    email_otp_verified_at = fields.Datetime(readonly=True, copy=False)
    access_revoked = fields.Boolean(readonly=True, copy=False)
    viewed_at = fields.Datetime(readonly=True, copy=False)
    declined_at = fields.Datetime(readonly=True, copy=False)
    invitation_sent_at = fields.Datetime(readonly=True, copy=False)
    invitation_count = fields.Integer(default=0, readonly=True, copy=False)
    invitation_mail_id = fields.Many2one(
        "mail.mail", readonly=True, copy=False, ondelete="set null",
    )
    invitation_fallback_at = fields.Datetime(readonly=True, copy=False)
    invitation_delivery_state = fields.Selection(
        [
            ("not_queued", "Not queued"),
            ("queued", "Queued"),
            ("sent", "Sent"),
            ("failed", "Failed"),
            ("available_in_odoo", "Available in Odoo"),
            ("cancelled", "Cancelled"),
            ("resolved", "No longer needed"),
        ],
        compute="_compute_invitation_delivery_state",
    )
    reminder_sent_at = fields.Datetime(readonly=True, copy=False)
    reminder_count = fields.Integer(default=0, readonly=True, copy=False)
    authentication_method = fields.Selection(
        AUTHENTICATION_METHODS, readonly=True, copy=False,
    )
    consent_text = fields.Text(readonly=True, copy=False)
    consent_version = fields.Char(readonly=True, copy=False)
    consented_at = fields.Datetime(readonly=True, copy=False)
    signed_document_sha256 = fields.Char(readonly=True, copy=False)
    certificate_serial = fields.Char(readonly=True, copy=False)
    decline_reason = fields.Text(readonly=True, copy=False)
    access_failure_count = fields.Integer(default=0, readonly=True, copy=False)
    access_failure_window_at = fields.Datetime(readonly=True, copy=False)
    access_blocked_until = fields.Datetime(readonly=True, copy=False)
    last_access_failure_at = fields.Datetime(readonly=True, copy=False)

    @api.depends(
        "invitation_mail_id",
        "invitation_mail_id.state",
        "invitation_sent_at",
        "invitation_fallback_at",
        "signed_on",
    )
    def _compute_invitation_delivery_state(self):
        mapping = {
            "outgoing": "queued",
            "sent": "sent",
            "exception": "failed",
            "cancel": "cancelled",
        }
        for signer in self:
            if signer.signed_on:
                signer.invitation_delivery_state = "resolved"
                continue
            if signer.invitation_fallback_at:
                signer.invitation_delivery_state = "available_in_odoo"
                continue
            # Requesters may inspect delivery, but they must not gain access to the
            # internal outgoing-mail record.  Resolve it under sudo and expose only
            # the bounded product status below.
            mail_state = signer.sudo().invitation_mail_id.state
            signer.invitation_delivery_state = mapping.get(
                mail_state,
                "sent" if signer.invitation_sent_at else "not_queued",
            )

    @api.depends("signed_on", "partner_id", "state", "request_id.state")
    @api.depends_context("uid")
    def _compute_is_allow_signature(self):
        current_partner = self.env.user.partner_id
        for signer in self:
            order_ready = not signer.request_id.sudo().signer_ids.filtered(
                lambda other: signer.request_id.signing_order
                and other.sequence < signer.sequence
                and other.state != "signed",
            )
            signer.is_allow_signature = bool(
                signer.state in {"notified", "viewed", "authorized"}
                and not signer.access_revoked
                and signer.partner_id == current_partner
                and signer.request_id.state in {"sent", "viewed", "partial"}
                and order_ready,
            )

    @api.depends("access_token_sha256", "session_token_sha256")
    def _compute_access_url(self):
        for signer in self:
            signer.access_url = f"/sign/user/{signer.id}"

    @api.model_create_multi
    def create(self, vals_list):
        request_ids = {
            values.get("request_id") for values in vals_list if values.get("request_id")
        }
        if not self.env.su:
            for request in self.env["sign.oca.request"].browse(request_ids):
                if not request._user_can_coordinate():
                    msg = "Only the requester or a named coordinator may add signers."
                    raise AccessError(msg)
        for values in vals_list:
            values["access_token"] = False
        return super().create(vals_list)

    def _active_enrollment(self):
        self.ensure_one()
        return self.env["usl.sign.enrollment"].search(
            [
                ("partner_id", "=", self.partner_id.id),
                ("company_id", "=", self.request_id.company_id.id),
                ("state", "=", "active"),
            ],
            limit=1,
        )

    def _has_internal_signing_access(self):
        self.ensure_one()
        return bool(self._internal_signing_users())

    def _internal_signing_users(self):
        """Backend users who can open this exact signing assignment."""
        sign_users = self.env.ref("usl_sign.group_sign_user").sudo().all_user_ids
        users = self.sudo().partner_id.user_ids.filtered(
            lambda user: (
                user.active
                and not user.share
                and user in sign_users
                and self.request_id.company_id in user.company_ids
            ),
        )
        return users.sorted(lambda user: (user.name.casefold(), user.id))

    def _signing_activity_deadline(self):
        self.ensure_one()
        return (
            fields.Date.to_date(self.request_id.expires_at)
            if self.request_id.expires_at
            else fields.Date.context_today(self)
        )

    def _ensure_internal_signing_activities(self):
        """Create one current native activity per internal signer user.

        Invitations and reminders can be retried, so this intentionally updates
        an existing assignment instead of creating notification duplicates.
        """
        activity_type = self.env.ref("usl_sign.mail_activity_type_sign_document")
        activity_model = self.env["mail.activity"].sudo()
        for signer in self.exists():
            if (
                signer.state not in {"notified", "viewed", "authorized"}
                or signer.access_revoked
                or signer.request_id.state not in {"sent", "viewed", "partial"}
            ):
                signer._close_internal_signing_activities()
                continue
            # Serialize invitation retries on the signer so duplicate HTTP jobs
            # cannot create duplicate activities.
            self.env.cr.execute(
                "SELECT id FROM sign_oca_request_signer WHERE id = %s FOR UPDATE",
                [signer.id],
            )
            users = signer._internal_signing_users()
            existing = activity_model.search(
                [
                    ("activity_type_id", "=", activity_type.id),
                    ("res_model", "=", signer._name),
                    ("res_id", "=", signer.id),
                    ("active", "=", True),
                ],
                order="id",
            )
            stale = existing.filtered(lambda activity: activity.user_id not in users)
            stale.unlink()
            for user in users:
                user_activities = (existing - stale).filtered(
                    lambda activity: activity.user_id == user,
                )
                summary = _(
                    "Review and sign: %(document)s",
                    document=signer.request_id.name,
                )
                note = _(
                    "<p><strong>%(sender)s</strong> asked you to review and sign "
                    "<strong>%(document)s</strong> as %(role)s.</p>"
                    "<p>Open this activity and choose <strong>Review and sign</strong>.</p>",
                    sender=escape(signer.request_id.user_id.name),
                    document=escape(signer.request_id.name),
                    role=escape(signer.role_id.name),
                )
                values = {
                    "summary": summary,
                    "note": note,
                    "date_deadline": signer._signing_activity_deadline(),
                }
                if user_activities:
                    user_activities[:1].write(values)
                    user_activities[1:].unlink()
                    continue
                # The signing invitation already owns email delivery. Quick
                # update keeps this as an Odoo inbox/activity notification and
                # avoids sending a second generic activity email.
                signer.sudo().with_context(
                    lang=user.lang,
                    mail_activity_quick_update=True,
                ).activity_schedule(
                    "usl_sign.mail_activity_type_sign_document",
                    user_id=user.id,
                    **values,
                )
        return True

    def _close_internal_signing_activities(self):
        if self:
            self.sudo().activity_unlink(
                ["usl_sign.mail_activity_type_sign_document"],
            )
        return True

    def _issue_access_token(self):
        self.ensure_one()
        token = secrets.token_urlsafe(32)
        self.with_context(usl_sign_signer_transition=INTERNAL_OPERATION).write(
            {
                "access_token": False,
                "access_token_sha256": hashlib.sha256(token.encode()).hexdigest(),
                "access_expires_at": min(
                    filter(
                        None,
                        [
                            self.request_id.expires_at,
                            fields.Datetime.now() + timedelta(days=7),
                        ],
                    ),
                ),
                "access_revoked": False,
            },
        )
        return token

    def _exchange_access_token(self, token):
        self.ensure_one()
        if self.signed_on or self.request_id.state not in {"sent", "viewed", "partial"}:
            # Terminal/revoked links are simply unavailable.  Do not mutate an
            # immutable signer or let repeated public replays grow evidence.
            msg = "This signing link is invalid, expired, or revoked."
            raise AccessError(msg)
        self._check_exchange_rate_limit()
        try:
            self._check_token(token, session=False)
        except AccessError:
            self._record_exchange_failure()
            raise
        if self.request_id.authentication_method == "email_otp":
            return self._issue_email_otp()
        session_token = secrets.token_urlsafe(32)
        self.with_context(usl_sign_signer_transition=INTERNAL_OPERATION).write(
            {
                "access_token_sha256": False,
                "access_expires_at": False,
                "session_token_sha256": hashlib.sha256(session_token.encode()).hexdigest(),
                "session_expires_at": fields.Datetime.now() + timedelta(minutes=30),
                "access_failure_count": 0,
                "access_failure_window_at": False,
                "access_blocked_until": False,
            },
        )
        return session_token

    @staticmethod
    def _email_otp_digest(code, salt):
        return hashlib.pbkdf2_hmac(
            "sha256", code.encode(), bytes.fromhex(salt), 200_000,
        ).hex()

    def _send_ephemeral_email(self, *, subject, body, queue=False):
        self.ensure_one()
        if not self.partner_id.email:
            msg = "The signer has no email address."
            raise UserError(msg)
        author = self.request_id.company_id.partner_id
        mail = self.env["mail.mail"].sudo().create(
            {
                "subject": subject,
                "body_html": body,
                "email_to": self.partner_id.email,
                "email_from": author.email_formatted,
                "author_id": author.id,
                "auto_delete": True,
                "model": self._name,
                "res_id": self.id,
            },
        )
        if queue:
            return mail
        try:
            mail.send(raise_exception=True)
        except Exception as error:
            mail.exists().unlink()
            msg = "The private signing email could not be delivered."
            raise UserError(msg) from error

    def _issue_email_otp(self):
        self.ensure_one()
        code = f"{secrets.randbelow(100_000_000):08d}"
        salt = secrets.token_hex(16)
        exchange_token = secrets.token_urlsafe(32)
        now = fields.Datetime.now()
        self.with_context(usl_sign_signer_transition=INTERNAL_OPERATION).write(
            {
                "access_token_sha256": False,
                "access_expires_at": False,
                "otp_exchange_token_sha256": hashlib.sha256(
                    exchange_token.encode(),
                ).hexdigest(),
                "otp_exchange_expires_at": now + timedelta(minutes=10),
                "email_otp_salt": salt,
                "email_otp_sha256": self._email_otp_digest(code, salt),
                "email_otp_expires_at": now + timedelta(minutes=10),
                "email_otp_failure_count": 0,
                "email_otp_blocked_until": False,
            },
        )
        body = (
            f"<p>Hello {escape(self.partner_id.name)},</p>"
            f"<p>Your one-time {escape(self.request_id.company_id.name)} document-signing "
            f"verification code is <strong>{code}</strong>.</p>"
            "<p>It expires in 10 minutes. Do not forward it.</p>"
        )
        self._send_ephemeral_email(
            subject=f"Your {self.request_id.company_id.name} signing code",
            body=body,
        )
        self.request_id._append_event(
            "email_otp_issued",
            signer=self,
            authentication_method="email_otp",
            payload={"expires_in_minutes": 10},
        )
        return {"otp_required": True, "exchange_token": exchange_token}

    def _verify_email_otp(self, exchange_token, code):
        self.ensure_one()
        self.env.cr.execute(
            "SELECT id FROM sign_oca_request_signer WHERE id = %s FOR UPDATE",
            [self.id],
        )
        self.invalidate_recordset()
        now = fields.Datetime.now()
        exchange_digest = hashlib.sha256((exchange_token or "").encode()).hexdigest()
        if (
            not self.otp_exchange_token_sha256
            or not secrets.compare_digest(
                self.otp_exchange_token_sha256, exchange_digest,
            )
            or not self.otp_exchange_expires_at
            or self.otp_exchange_expires_at <= now
            or not self.email_otp_expires_at
            or self.email_otp_expires_at <= now
            or self.access_revoked
        ):
            msg = "The verification session is invalid or expired."
            raise AccessError(msg)
        if self.email_otp_blocked_until and self.email_otp_blocked_until > now:
            msg = "Too many incorrect verification codes were entered."
            raise AccessError(msg)
        candidate = self._email_otp_digest((code or "").strip(), self.email_otp_salt)
        if not secrets.compare_digest(self.email_otp_sha256 or "", candidate):
            failures = self.email_otp_failure_count + 1
            self.with_context(usl_sign_signer_transition=INTERNAL_OPERATION).write(
                {
                    "email_otp_failure_count": failures,
                    "email_otp_blocked_until": now + timedelta(minutes=15)
                    if failures >= 5
                    else False,
                },
            )
            self.request_id._append_event(
                "email_otp_rejected",
                signer=self,
                authentication_method="email_otp",
                payload={"attempt_count": failures, "blocked": failures >= 5},
            )
            msg = "The verification code is incorrect."
            raise AccessError(msg)
        session_token = secrets.token_urlsafe(32)
        self.with_context(usl_sign_signer_transition=INTERNAL_OPERATION).write(
            {
                "session_token_sha256": hashlib.sha256(session_token.encode()).hexdigest(),
                "session_expires_at": now + timedelta(minutes=30),
                "otp_exchange_token_sha256": False,
                "otp_exchange_expires_at": False,
                "email_otp_salt": False,
                "email_otp_sha256": False,
                "email_otp_expires_at": False,
                "email_otp_failure_count": 0,
                "email_otp_blocked_until": False,
                "email_otp_verified_at": now,
            },
        )
        self.request_id._append_event(
            "email_otp_verified",
            signer=self,
            authentication_method="email_otp",
        )
        return session_token

    def _check_exchange_rate_limit(self):
        self.ensure_one()
        self.env.cr.execute(
            "SELECT id FROM sign_oca_request_signer WHERE id = %s FOR UPDATE",
            [self.id],
        )
        self.invalidate_recordset(
            ["access_failure_count", "access_failure_window_at", "access_blocked_until"],
        )
        now = fields.Datetime.now()
        if self.access_blocked_until and self.access_blocked_until > now:
            msg = "This signing link is temporarily unavailable."
            raise AccessError(msg)
        if self.access_failure_window_at and self.access_failure_window_at < now - timedelta(
            minutes=15,
        ):
            self.with_context(usl_sign_signer_transition=INTERNAL_OPERATION).write(
                {
                    "access_failure_count": 0,
                    "access_failure_window_at": False,
                    "access_blocked_until": False,
                },
            )

    def _record_exchange_failure(self):
        self.ensure_one()
        now = fields.Datetime.now()
        window = self.access_failure_window_at or now
        count = self.access_failure_count + 1
        values = {
            "access_failure_count": count,
            "access_failure_window_at": window,
            "last_access_failure_at": now,
        }
        if count >= 5:
            values["access_blocked_until"] = now + timedelta(minutes=15)
        self.with_context(usl_sign_signer_transition=INTERNAL_OPERATION).write(values)
        self.request_id._append_event(
            "signing_link_rejected",
            signer=self,
            payload={"attempt_count": count, "blocked": count >= 5},
        )

    def _check_token(self, token, *, session=True):
        self.ensure_one()
        digest = hashlib.sha256((token or "").encode()).hexdigest()
        expected = self.session_token_sha256 if session else self.access_token_sha256
        expiry = self.session_expires_at if session else self.access_expires_at
        if (
            not expected
            or not secrets.compare_digest(expected, digest)
            or not expiry
            or expiry <= fields.Datetime.now()
            or self.access_revoked
        ):
            msg = "This signing link is invalid, expired, or revoked."
            raise AccessError(msg)
        if self.request_id.state not in {"sent", "viewed", "partial"}:
            msg = "This request is not available for signature."
            raise AccessError(msg)
        if self.signed_on:
            msg = "This signer has already completed the request."
            raise AccessError(msg)
        if self.request_id.signing_order and self.request_id.signer_ids.filtered(
            lambda other: other.sequence < self.sequence and other.state != "signed",
        ):
            msg = "Another signer must complete the document first."
            raise AccessError(msg)
        return True

    def _send_signer_invitation(self, force=False, reminder=False):
        for signer in self:
            if signer.invitation_sent_at and not force:
                continue
            token = signer._issue_access_token()
            link = f"{signer.get_base_url()}/sign/document/{signer.id}/{token}"
            body = self.env["ir.qweb"]._render(
                "usl_sign.sign_invitation_body",
                {"signer": signer, "link": link, "reminder": reminder},
                engine="ir.qweb",
                minimal_qcontext=True,
            )
            queued_mail = signer._send_ephemeral_email(
                subject="Signature reminder" if reminder else "Document to review and sign",
                body=body,
                queue=True,
            )
            values = {
                "invitation_sent_at": fields.Datetime.now(),
                "invitation_count": signer.invitation_count + 1,
                "invitation_mail_id": queued_mail.id,
                "state": "notified",
            }
            if reminder:
                values.update(
                    {
                        "reminder_sent_at": fields.Datetime.now(),
                        "reminder_count": signer.reminder_count + 1,
                    },
                )
            signer.with_context(usl_sign_signer_transition=INTERNAL_OPERATION).write(values)
            signer.request_id._append_event(
                "invitation_queued",
                signer=signer,
                payload={"reminder": reminder},
            )
            signer._ensure_internal_signing_activities()
        return True

    def _mark_viewed(self):
        for signer in self.filtered(lambda row: not row.viewed_at):
            signer.with_context(usl_sign_signer_transition=INTERNAL_OPERATION).write(
                {"viewed_at": fields.Datetime.now(), "state": "viewed"},
            )
            if signer.request_id.state == "sent":
                signer.request_id._transition("viewed", "document_viewed", signer=signer)

    def action_sign(
        self,
        items,
        access_token=False,
        document_sha256=False,
        latitude=False,
        longitude=False,
        consent=False,
    ):
        self.ensure_one()
        # Serialize Standard submissions on the request. Without this lock, two
        # unordered signers can both render from the same revision and the last
        # transaction to commit can erase the first signer's fields.
        self.env.cr.execute(
            "SELECT id FROM sign_oca_request WHERE id = %s FOR UPDATE",
            [self.request_id.id],
        )
        self.request_id.invalidate_recordset(["data", "current_hash", "state"])
        self.invalidate_recordset(
            ["access_revoked", "session_token_sha256", "session_expires_at", "state"],
        )
        self._check_token(access_token, session=True)
        if self.request_id.requested_trust != "standard":
            msg = "This request requires the strong personal ceremony."
            raise ValidationError(msg)
        if not consent:
            msg = "Explicit electronic-signature consent is required."
            raise ValidationError(msg)
        current_sha256 = hashlib.sha256(field_content(self.request_id.data)).hexdigest()
        if (
            not isinstance(document_sha256, str)
            or len(document_sha256) != 64
            or not secrets.compare_digest(document_sha256, current_sha256)
        ):
            msg = _(
                "The document changed after you reviewed it. Reload it, review the "
                "latest revision, and sign again.",
            )
            raise ValidationError(msg)
        return self._apply_standard_signature(
            items,
            reviewed_document_sha256=current_sha256,
            latitude=latitude,
            longitude=longitude,
        )

    def _apply_standard_signature(
        self,
        items,
        *,
        reviewed_document_sha256,
        latitude=False,
        longitude=False,
    ):
        request = self.request_id
        frozen_layout = json.loads(json.dumps(request.frozen_layout or {}))
        completed_layout = json.loads(
            json.dumps(request.signatory_data or request.frozen_layout or {}),
        )
        if not isinstance(items, dict):
            msg = "The signing payload is invalid."
            raise ValidationError(msg)
        reader = PdfReader(BytesIO(field_content(request.data)))
        writer = PdfWriter()
        pages = dict(enumerate(reader.pages, start=1))
        for key, configured in frozen_layout.items():
            if int(configured["role_id"]) != self.role_id.id:
                continue
            submitted = items.get(str(key), items.get(key))
            if not isinstance(submitted, dict) or "value" not in submitted:
                msg = "The signing payload is incomplete."
                raise ValidationError(msg)
            item = json.loads(json.dumps(configured))
            value = submitted.get("value")
            if item.get("field_type") == "check":
                if not isinstance(value, bool):
                    msg = "A checkbox value must be true or false."
                    raise ValidationError(msg)
            elif value is not False and value is not None and not isinstance(value, str):
                msg = "A signing field contains an invalid value."
                raise ValidationError(msg)
            elif isinstance(value, str) and len(value) > 2_000_000:
                msg = "A signing field exceeds the accepted size."
                raise ValidationError(msg)
            item["value"] = value
            self._check_signable(item)
            page_number = int(item["page"])
            if page_number not in pages:
                msg = "A signing field references an invalid page."
                raise ValidationError(msg)
            page = pages[page_number]
            box = getattr(page, "mediabox", None) or page.mediaBox
            overlay = self._get_pdf_page(item, box)
            if item.get("required") and item.get("value") and not overlay:
                msg = "A required signing field could not be rendered."
                raise ValidationError(msg)
            if overlay:
                merge = getattr(page, "merge_page", None) or getattr(page, "mergePage")
                merge(overlay)
            pages[page_number] = page
            completed_layout[str(key)] = item
        for page in pages.values():
            _add_page(writer, page)
        stream = BytesIO()
        writer.write(stream)
        signed_pdf = stream.getvalue()
        digest = hashlib.sha256(signed_pdf).hexdigest()
        now = fields.Datetime.now()
        consent_text = request.consent_text_snapshot
        request.with_context(usl_sign_working_pdf=INTERNAL_OPERATION).write(
            {
                "data": field_value(signed_pdf),
                "current_hash": digest,
                "signatory_data": completed_layout,
            },
        )
        self.with_context(usl_sign_signer_transition=INTERNAL_OPERATION).write(
            {
                "signed_on": now,
                "signature_hash": digest,
                "signed_document_sha256": digest,
                "state": "signed",
                "latitude": latitude,
                "longitude": longitude,
                "authentication_method": request.authentication_method or "secure_link",
                "consent_text": consent_text,
                "consent_version": "1",
                "consented_at": now,
                "access_revoked": True,
                "session_token_sha256": False,
                "session_expires_at": False,
            },
        )
        consent_payload = {
            "signer_id": self.id,
            "partner_id": self.partner_id.id,
            "consent": consent_text,
            "version": "1",
            "consented_at": fields.Datetime.to_string(now),
            "reviewed_document_sha256": reviewed_document_sha256,
            "signed_document_sha256": digest,
            "authentication_method": self.authentication_method,
            "field_values_sha256": hashlib.sha256(
                json.dumps(
                    {
                        key: value
                        for key, value in completed_layout.items()
                        if int(value.get("role_id") or 0) == self.role_id.id
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                    ensure_ascii=False,
                ).encode(),
            ).hexdigest(),
        }
        request._create_evidence(
            "consent",
            f"{request.name}-{self.id}-consent.json",
            json.dumps(consent_payload, sort_keys=True).encode(),
            mimetype="application/json",
            signer=self,
        )
        request._append_event(
            "standard_signature_applied",
            signer=self,
            authentication_method=self.authentication_method,
            payload={
                "reviewed_document_sha256": reviewed_document_sha256,
                "signed_document_sha256": digest,
                "consent_version": "1",
            },
        )
        self._close_internal_signing_activities()
        self._activate_next_signer_or_finish()
        return {"type": "ir.actions.act_url", "url": "/sign/result/success"}

    def _activate_next_signer_or_finish(self):
        self.ensure_one()
        request = self.request_id
        pending = request.signer_ids.filtered(lambda signer: signer.state != "signed").sorted(
            lambda row: (row.sequence, row.id),
        )
        if pending:
            if request.state in {"sent", "viewed"}:
                request._transition("partial", "request_partially_signed", signer=self)
            if request.signing_order:
                pending[0]._send_signer_invitation()
            return
        request._start_final_validation()

    def action_decline(self, reason, access_token=False):
        self.ensure_one()
        self._check_token(access_token, session=True)
        if not reason:
            msg = "Record why the document is declined."
            raise ValidationError(msg)
        now = fields.Datetime.now()
        self.with_context(usl_sign_signer_transition=INTERNAL_OPERATION).write(
            {
                "state": "declined",
                "declined_at": now,
                "decline_reason": reason,
                "access_revoked": True,
            },
        )
        self.request_id._create_evidence(
            "decline",
            f"{self.request_id.name}-{self.id}-decline.json",
            json.dumps(
                {"signer_id": self.id, "reason": reason, "declined_at": fields.Datetime.to_string(now)},
                sort_keys=True,
            ).encode(),
            mimetype="application/json",
            signer=self,
        )
        self.request_id.signer_ids.filtered(
            lambda signer: signer != self and signer.state != "signed",
        ).with_context(usl_sign_signer_transition=INTERNAL_OPERATION).write(
            {"state": "cancelled", "access_revoked": True},
        )
        self.request_id._close_outstanding_work("request_declined")
        self.request_id._transition("declined", "request_declined", signer=self)
        return True

    def get_info(self, access_token=False):
        self.ensure_one()
        if access_token:
            self._check_token(access_token, session=True)
        layout = json.loads(json.dumps(self.request_id.frozen_layout or {}))
        field_ids = {
            int(item["field_id"])
            for item in layout.values()
            if isinstance(item, dict) and item.get("field_id")
        }
        fields_by_id = {
            field.id: field
            for field in self.env["sign.oca.field"].sudo().browse(field_ids).exists()
        }
        for item in layout.values():
            if not isinstance(item, dict):
                continue
            field = fields_by_id.get(int(item.get("field_id") or 0))
            if not field:
                item.setdefault("kind", "text")
                item.setdefault("technical_type", item.get("field_type", "text"))
                continue
            item.update(
                {
                    "name": item.get("name") or field.name,
                    "kind": _field_kind(field),
                    "field_type": field.field_type,
                    "technical_type": field.field_type,
                    "default_value": item.get("default_value") or field.default_value or False,
                },
            )
        return {
            "role_id": self.role_id.id if not self.signed_on else False,
            "name": self.request_id.name,
            "company_name": self.request_id.company_id.name,
            "signer_role_name": self.role_id.name,
            "items": layout,
            "to_sign": not self.signed_on and not self.access_revoked,
            "ask_location": self.request_id.ask_location,
            "partner": {
                "id": self.partner_id.id,
                "name": self.partner_id.name,
                "email": self.partner_id.email,
                "phone": self.partner_id.phone,
            },
            "trust_label": dict(TRUST_LEVELS)[self.request_id.requested_trust],
            "consent_text": self.request_id.consent_text_snapshot,
            "document_sha256": hashlib.sha256(
                field_content(self.request_id.data),
            ).hexdigest(),
        }

    def write(self, values):
        protected = {
            "state",
            "signed_on",
            "signature_hash",
            "access_token_sha256",
            "access_token",
            "access_expires_at",
            "session_token_sha256",
            "session_expires_at",
            "otp_exchange_token_sha256",
            "otp_exchange_expires_at",
            "email_otp_salt",
            "email_otp_sha256",
            "email_otp_expires_at",
            "email_otp_failure_count",
            "email_otp_blocked_until",
            "email_otp_verified_at",
            "access_revoked",
            "viewed_at",
            "declined_at",
            "invitation_sent_at",
            "invitation_count",
            "invitation_mail_id",
            "invitation_fallback_at",
            "reminder_sent_at",
            "reminder_count",
            "authentication_method",
            "consent_text",
            "consent_version",
            "consented_at",
            "signed_document_sha256",
            "certificate_serial",
            "decline_reason",
            "access_failure_count",
            "access_failure_window_at",
            "access_blocked_until",
            "last_access_failure_at",
        }
        if protected.intersection(values) and self.env.context.get(
            "usl_sign_signer_transition",
        ) is not INTERNAL_OPERATION:
            msg = "Use a controlled signer action to change signing evidence."
            raise ValidationError(msg)
        if (
            values
            and self.env.context.get("usl_sign_signer_transition") is not INTERNAL_OPERATION
            and not self.env.su
        ):
            for signer in self:
                if not signer.request_id._user_can_coordinate():
                    msg = "Only the requester or a named coordinator may edit signers."
                    raise AccessError(
                        msg,
                    )
                if signer.request_id.state != "draft":
                    msg = "Signers can only be edited while the request is a draft."
                    raise ValidationError(msg)
        if self.filtered(lambda signer: signer.signed_on) and set(values) - {
            "message_follower_ids",
            "activity_ids",
        }:
            msg = "A completed signer record is immutable."
            raise ValidationError(msg)
        if {"request_id", "partner_id", "role_id", "sequence"}.intersection(values) and self.filtered(
            lambda signer: signer.request_id.state not in MUTABLE_REQUEST_STATES,
        ):
            msg = "Signer identities, roles and order are frozen after sending."
            raise ValidationError(msg)
        return super().write(values)

    def unlink(self):
        if not self.env.su:
            for signer in self:
                if not signer.request_id._user_can_coordinate():
                    msg = "Only the requester or a named coordinator may remove signers."
                    raise AccessError(msg)
        if self.filtered(lambda signer: signer.request_id.state != "draft"):
            msg = "Signers can only be removed while the request is a draft."
            raise ValidationError(msg)
        return super().unlink()
