import hashlib
import json
from datetime import timedelta
from urllib.parse import quote

from odoo import SUPERUSER_ID, _, api, fields, models
from odoo.exceptions import AccessError, ValidationError
from odoo.http import request as http_request
from odoo.tools import config
from odoo.tools.misc import format_datetime

from .constants import (
    AUTHENTICATION_METHODS,
    CANCELLABLE_REQUEST_STATES,
    DOCUMENT_CATEGORIES,
    EXPIRABLE_REQUEST_STATES,
    INTERNAL_OPERATION,
    MUTABLE_REQUEST_STATES,
    REQUEST_STATES,
    TERMINAL_REQUEST_STATES,
    TRUST_LEVELS,
)
from odoo.addons.usl_sign.services import (
    DSSClient,
    DSSRejectedError,
    DSSServiceError,
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

    def _signing_result_state(self):
        """Return the live, user-facing outcome after a signer finishes."""
        self.ensure_one()
        return {
            "has_pending_signers": any(
                signer.state != "signed" for signer in self.signer_ids
            ),
            "final_document_ready": bool(self.final_data),
        }

    def preview(self):
        """Open the validated result, never an editor overlay or unchecked file."""
        self.ensure_one()
        if self.state == "external_archived":
            return {
                "type": "ir.actions.act_url",
                "url": (
                    f"/web/content/{self._name}/{self.id}/data/"
                    f"{quote(self.filename)}?download=false"
                ),
                "target": "new",
            }
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
        # ``request.signer_ids`` contains every recipient id.  A recipient
        # may read only their own row, so traversing the whole relation can
        # fail as soon as another recipient is prefetched.  Resolve the
        # exact identity under sudo, then let normal rules protect any
        # later access to the selected row.
        Signer = self.env["sign.oca.request.signer"].sudo()
        # Keyed on origin ids: `signer.request_id.id` is always a real id while
        # `request.id` is a NewId during an onchange.
        assigned_by_request = {}
        for signer in Signer.search(
            [
                ("request_id", "in", self.ids),
                ("partner_id", "=", partner.id),
            ],
            order="sequence, id",
        ):
            assigned_by_request.setdefault(signer.request_id.id, Signer)
            assigned_by_request[signer.request_id.id] |= signer
        for request in self:
            assigned = assigned_by_request.get(request._origin.id, Signer)
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
    requested_trust = fields.Selection(TRUST_LEVELS, default="standard")
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
    signature_semantics_summary = fields.Char(compute="_compute_workspace_presentation")
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
    record_kind = fields.Selection(
        [
            ("native", "USL Sign"),
            ("external_archive", "Odoo Online (External)"),
        ],
        default="native",
        required=True,
        readonly=True,
        copy=False,
        index=True,
        help=(
            "USL Sign records use this application's signing and validation workflow. "
            "External records preserve a result produced elsewhere without claiming "
            "that USL Sign reran those checks."
        ),
    )

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
                "evidence_manifest", "evidence-manifest.json",
            )
            if sign_request.evidence_manifest
            else False,
            "total_requests": len(requests),
        }

    @api.model_create_multi
    def create(self, vals_list):
        external_values = [
            values
            for values in vals_list
            if values.get("record_kind") == "external_archive"
            or values.get("state") == "external_archived"
        ]
        if (
            external_values
            and self.env.context.get("usl_sign_external_archive")
            is not INTERNAL_OPERATION
        ):
            raise ValidationError(
                _("Use the controlled external-record operation for archived results."),
            )
        records = super().create(vals_list)
        for record in records:
            if record.record_kind == "native" and record.data and not record.document_ids:
                self.env["usl.sign.request.document"].create(
                    {
                        "request_id": record.id,
                        "name": record.name,
                        "filename": record.filename or f"{record.name}.pdf",
                        "data": record.data,
                    },
                )
            if record.record_kind == "external_archive":
                record._append_event(
                    "external_record_imported",
                    payload={"source": "Odoo Online", "name": record.name},
                )
            else:
                record._append_event("request_created", payload={"name": record.name})
                record.action_compute_recommendation(apply_timing_defaults=True)
        return records

    @api.model
    def _create_external_archive(self, values):
        """Create an immutable external result without asserting native completion."""
        forbidden = {
            "achieved_trust",
            "completion_certificate",
            "dossier_data",
            "evidence_manifest",
            "external_provider_id",
            "final_data",
            "policy_id",
            "recommended_trust",
            "requested_trust",
        }
        asserted = sorted(field for field in forbidden if values.get(field))
        if asserted:
            raise ValidationError(
                _(
                    "An external archive cannot assert native proof fields: %(fields)s",
                    fields=", ".join(asserted),
                ),
            )
        signed_document = self.env["usl.document"].browse(
            values.get("archive_document_id"),
        ).exists()
        source_certificate = self.env["usl.document"].browse(
            values.get("archive_dossier_document_id"),
        ).exists()
        if not signed_document or not source_certificate:
            raise ValidationError(
                _("Archive the signed PDF and external certificate before creating the record."),
            )
        if not values.get("signer_ids"):
            raise ValidationError(_("An external signing record must identify its signers."))
        signer_commands = []
        for command in values["signer_ids"]:
            if command[0] != 0:
                raise ValidationError(
                    _("External signer rows must be created with their archived record."),
                )
            signer_commands.append(
                (
                    0,
                    0,
                    {
                        **command[2],
                        "state": "external_recorded",
                        "authentication_method": "external_record",
                        "access_token": False,
                    },
                ),
            )
        controlled = {
            **values,
            "record_kind": "external_archive",
            "state": "external_archived",
            "requested_trust": False,
            "recommended_trust": False,
            "achieved_trust": False,
            "authentication_method": "external_record",
            "validation_status": "not_started",
            "evidence_status": "not_started",
            "archive_status": "archived",
            "active": True,
            "signer_ids": signer_commands,
        }
        record = self.with_context(
            usl_sign_external_archive=INTERNAL_OPERATION,
            usl_sign_transition=INTERNAL_OPERATION,
            usl_sign_freeze=INTERNAL_OPERATION,
        ).create(controlled)
        # Never return a recordset carrying the process-local mutation
        # capability.  The caller receives the archive in its original env.
        return record.with_env(self.env)

    @api.constrains(
        "record_kind",
        "state",
        "requested_trust",
        "recommended_trust",
        "achieved_trust",
        "validation_status",
        "evidence_status",
        "final_data",
        "completion_certificate",
        "dossier_data",
        "policy_id",
        "archive_status",
        "archive_document_id",
        "archive_dossier_document_id",
    )
    def _check_external_archive_claims(self):
        for request in self:
            if request.record_kind == "native":
                if not request.requested_trust:
                    raise ValidationError(_("A USL Sign request needs a signing method."))
                if request.state == "external_archived":
                    raise ValidationError(_("A USL Sign request cannot use external archive status."))
                continue
            if request.state != "external_archived":
                raise ValidationError(_("An external archive must remain externally archived."))
            claimed_fields = {
                "requested trust": request.requested_trust,
                "recommended trust": request.recommended_trust,
                "achieved trust": request.achieved_trust,
                "native validation": request.validation_status != "not_started",
                "native evidence": request.evidence_status != "not_started",
                "native final PDF": request.final_data,
                "USL completion certificate": request.completion_certificate,
                "USL proof package": request.dossier_data,
                "USL policy": request.policy_id,
                "incomplete Paperless storage": request.archive_status != "archived",
                "missing signed PDF archive": not request.archive_document_id,
                "missing source certificate archive": not request.archive_dossier_document_id,
            }
            asserted = sorted(label for label, value in claimed_fields.items() if value)
            if asserted:
                raise ValidationError(
                    _(
                        "An external archive cannot claim %(claims)s.",
                        claims=", ".join(asserted),
                    ),
                )

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
            "external_archived": _(
                "The externally signed document and its source certificate are archived.",
            ),
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
        "record_kind",
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
            "external_archived": "closed",
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
            "external_archived": _("Odoo Online (External)"),
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
            signed = len(
                signers.filtered(
                    lambda signer: signer.state in {"signed", "external_recorded"},
                ),
            )
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
                _("Not revalidated")
                if request.record_kind == "external_archive"
                else (
                    _("Verified")
                    if request.validation_status == "valid"
                    else _("Needs attention")
                )
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
            request.signature_semantics_summary = {
                "standard": _(
                    "Each signer creates a recorded attestation. The PDF receives one "
                    "platform integrity seal, not a personal certificate signature.",
                ),
                "strong_personal": _(
                    "Each signer adds a personal PDF signature in order. After the last "
                    "signer, the platform adds one final integrity seal.",
                ),
                "qualified_external": _(
                    "The external provider determines the PDF signatures. USL retains "
                    "the provider proof and independently validates the returned file.",
                ),
            }.get(request.requested_trust, "")
            if request.record_kind == "external_archive":
                request.requested_trust_short = _("Odoo Online (External)")
                request.signing_method_summary = _(
                    "This signed record came from Odoo Online. USL Sign preserved it "
                    "but did not rerun its signing, identity, trust, or revocation checks.",
                )
                request.signature_semantics_summary = _(
                    "Signature claims come from the preserved Odoo Online record and "
                    "are not reissued or reclassified by USL Sign.",
                )
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
            route = list(http_request.httprequest.access_route or [])
            ip_address = ip_address or (
                route[0]
                if config["proxy_mode"] and route
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

    @staticmethod
    def _server_network_context():
        if not http_request or not hasattr(http_request, "httprequest"):
            return {
                "observed": False,
                "source": "server_context_unavailable",
            }
        request = http_request.httprequest
        route = [str(address) for address in (request.access_route or [])]
        proxy_mode = bool(config["proxy_mode"])
        return {
            "observed": True,
            "source": "trusted_proxy_route" if proxy_mode else "direct_peer",
            "client_address": (
                route[0] if proxy_mode and route else request.remote_addr
            ),
            "peer_address": request.remote_addr,
            "proxy_chain": route[1:] if proxy_mode else [],
        }

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
        # Evidence is emitted by the controlled Sign workflow on behalf of a
        # signer.  Authentication evidence is deliberately hidden from normal
        # Sign users by record rules, so its creation cannot depend on the
        # signer's read scope.  Keep the signer/request attribution explicit
        # while using the service user only for this append-only write.
        return self.env["usl.sign.evidence"].with_user(SUPERUSER_ID).with_context(
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

    def _completion_certificate_pdf(self):
        self.ensure_one()
        return self._completion_certificate_render()["pdf"]

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
        if (
            values
            and not internal
            and self.filtered(lambda request: request.record_kind == "external_archive")
            and set(values) - {"message_follower_ids", "activity_ids"}
        ):
            raise ValidationError(_("Archived external signing records are immutable."))
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
