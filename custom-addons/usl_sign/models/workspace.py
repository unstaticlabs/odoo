from urllib.parse import quote

from odoo import api, fields, models
from odoo.exceptions import AccessError, ValidationError

TRUST_SHORT_LABELS = {
    "standard": "Standard",
    "strong_personal": "Strong personal",
    "qualified_external": "Qualified external",
}


class SignWorkspace(models.AbstractModel):
    _name = "usl.sign.workspace"
    _description = "USL Sign workspace service"

    @api.model
    def _check_access(self):
        if not (
            self.env.user.has_group("usl_sign.group_sign_user")
            or self.env.user.has_group("usl_sign.group_sign_identity_reviewer")
            or self.env.user.has_group("usl_sign.group_sign_evidence_reviewer")
        ):
            msg = "USL Sign access is required."
            raise AccessError(msg)

    @api.model
    def _managed_domain(self):
        return [
            "|",
            ("user_id", "=", self.env.user.id),
            ("coordinator_ids", "in", [self.env.user.id]),
        ]

    @api.model
    def _completed_domain(self):
        """Apply the product completion contract again at the read boundary."""
        return [
            ("state", "=", "completed"),
            ("validation_status", "=", "valid"),
            ("evidence_status", "=", "complete"),
            ("archive_status", "=", "archived"),
            ("final_data", "!=", False),
            ("completion_certificate", "!=", False),
            ("dossier_data", "!=", False),
        ]

    @api.model
    def _request_item(self, request):
        return {
            "id": request.id,
            "model": request._name,
            "title": request.name,
            "subtitle": request.record_ref.display_name if request.record_ref else "",
            "status": request.lifecycle_stage_label,
            "progress": request.signer_progress,
            "next_step": request.next_step,
            "due": fields.Datetime.to_string(request.expires_at) if request.expires_at else False,
            "trust": request.requested_trust_short,
            "archive": request.archive_status,
            "action": {"type": "open", "model": request._name, "id": request.id},
        }

    @api.model
    def _section(self, model, domain, item_builder, *, order, limit=6):
        count = self.env[model].search_count(domain)
        records = self.env[model].search(domain, order=order, limit=limit)
        return {"count": count, "items": [item_builder(record) for record in records]}

    @api.model
    def get_landing(self):
        self._check_access()
        request_model = self.env["sign.oca.request"]
        partner = self.env.user.partner_id
        managed = self._managed_domain()
        signer_domain = [
            ("partner_id", "=", partner.id),
            ("state", "in", ["notified", "viewed", "authorized"]),
            ("request_id.state", "in", ["sent", "viewed", "partial"]),
        ]

        def signer_item(signer):
            return {
                "id": signer.id,
                "model": signer._name,
                "title": signer.request_id.name,
                "subtitle": f"{signer.request_id.user_id.name} · {signer.role_id.name}",
                "status": "Ready for your signature",
                "progress": signer.request_id.signer_progress,
                "next_step": "Review and sign",
                "due": fields.Datetime.to_string(signer.request_id.expires_at)
                if signer.request_id.expires_at
                else False,
                "trust": signer.request_id.requested_trust_short,
                "action": {
                    "type": "call",
                    "model": signer._name,
                    "id": signer.id,
                    "method": "sign",
                },
            }

        def approval_item(approval):
            return {
                "id": approval.id,
                "model": approval._name,
                "title": approval.name,
                "subtitle": approval.record_ref.display_name if approval.record_ref else "",
                "status": "Decision requested",
                "progress": approval.requested_by_id.name,
                "next_step": "Approve or reject",
                "due": False,
                "trust": "Odoo decision",
                "action": {"type": "open", "model": approval._name, "id": approval.id},
            }

        sections = {
            "sign_now": self._section(
                "sign.oca.request.signer",
                signer_domain,
                signer_item,
                order="request_id desc, sequence, id",
            ),
            "decide": self._section(
                "usl.sign.approval",
                [("state", "=", "pending"), ("approver_ids", "in", [self.env.user.id])],
                approval_item,
                order="create_date desc, id desc",
            ),
            "prepare": self._section(
                request_model._name,
                managed + [("state", "in", ["draft", "ready"])],
                self._request_item,
                order="write_date desc, id desc",
            ),
            "issues": self._section(
                request_model._name,
                managed
                + [
                    (
                        "state",
                        "in",
                        [
                            "waiting_enrollment",
                            "action_required",
                            "evidence_incomplete",
                            "validation_failed",
                        ],
                    ),
                ],
                self._request_item,
                order="write_date desc, id desc",
            ),
            "waiting": self._section(
                request_model._name,
                managed
                + [
                    (
                        "state",
                        "in",
                        [
                            "sent",
                            "viewed",
                            "partial",
                            "waiting_external",
                            "signed_to_import",
                            "validating",
                        ],
                    ),
                ],
                self._request_item,
                order="write_date desc, id desc",
            ),
            "completed": self._section(
                request_model._name,
                fields.Domain.AND(
                    [
                        self._completed_domain(),
                        [
                            "|",
                            "|",
                            ("user_id", "=", self.env.user.id),
                            ("coordinator_ids", "in", [self.env.user.id]),
                            ("signer_ids.partner_id", "=", partner.id),
                        ],
                    ],
                ),
                self._request_item,
                order="completed_at desc, id desc",
            ),
        }
        return {
            "can_start": self.env.user.has_group("usl_sign.group_sign_user"),
            "sections": sections,
        }

    @api.model
    def _download_url(self, record, field_name, filename):
        if not filename or not record[field_name]:
            return False
        return (
            f"/web/content/{record._name}/{record.id}/{field_name}/"
            f"{quote(filename)}?download=true"
        )

    @api.model
    def get_library(self, section="templates", search="", offset=0, limit=24):
        self._check_access()
        if section not in {"templates", "completed"}:
            msg = "Choose Templates or Completed Documents."
            raise ValidationError(msg)
        offset = max(0, int(offset or 0))
        limit = min(50, max(1, int(limit or 24)))
        search = (search or "").strip()[:100]
        if section == "templates":
            domain = [("active", "=", True)]
            if search:
                domain.append(("name", "ilike", search))
            model = self.env["sign.oca.template"]
            total = model.search_count(domain)
            templates = model.search(
                domain,
                order="preparation_status desc, write_date desc, id desc",
                offset=offset,
                limit=limit,
            )
            category_labels = dict(
                model._fields["default_document_category"]._description_selection(
                    self.env,
                ),
            )
            status_labels = dict(
                model._fields["preparation_status"]._description_selection(self.env),
            )
            items = [
                {
                    "id": template.id,
                    "title": template.name,
                    "description": template.description or "",
                    "category": category_labels.get(template.default_document_category),
                    "company": template.company_id.name,
                    "version": template.version,
                    "owner": template.create_uid.name,
                    "trust": TRUST_SHORT_LABELS.get(template.default_trust, "Standard"),
                    "status": status_labels.get(template.preparation_status),
                    "ready": template.preparation_status == "ready",
                    "usage": template.request_count,
                }
                for template in templates
            ]
        else:
            domain = self._completed_domain()
            if search:
                domain.append(("name", "ilike", search))
            model = self.env["sign.oca.request"]
            total = model.search_count(domain)
            requests = model.search(
                domain,
                order="completed_at desc, id desc",
                offset=offset,
                limit=limit,
            )
            items = [
                self._completed_library_item(request)
                for request in requests
            ]
        return {
            "section": section,
            "items": items,
            "total": total,
            "offset": offset,
            "limit": limit,
        }

    @api.model
    def _completed_library_item(self, request):
        proof_labels = dict(
            request._fields["evidence_status"]._description_selection(self.env),
        )
        archive_labels = dict(
            request._fields["archive_status"]._description_selection(self.env),
        )
        timestamp_labels = dict(
            request._fields["daily_timestamp_status"]._description_selection(self.env),
        )
        can_review = self.env.user.has_group("usl_sign.group_sign_evidence_reviewer")
        manifest = request.daily_timestamp_manifest_id if can_review else False
        confirmed_receipt = manifest.confirmed_receipt_id if manifest else False
        return {
            "id": request.id,
            "title": request.name,
            "record": request.record_ref.display_name if request.record_ref else "",
            "completed": fields.Datetime.to_string(request.completed_at)
            if request.completed_at
            else False,
            "signers": ", ".join(request.signer_ids.mapped("partner_id.name")),
            "trust": request.achieved_trust_short,
            "proof": proof_labels.get(request.evidence_status),
            "archive": archive_labels.get(request.archive_status),
            "timestamp": timestamp_labels.get(request.daily_timestamp_status)
            or "Not scheduled",
            "timestamp_message": request.daily_timestamp_message or "",
            "timestamp_manifest_id": manifest.id if manifest else False,
            "timestamp_manifest_url": self._download_url(
                manifest,
                "signed_envelope",
                manifest.signed_envelope_filename,
            )
            if manifest
            else False,
            "timestamp_pending_receipt_url": self._download_url(
                manifest.initial_receipt_id,
                "data",
                manifest.initial_receipt_id.name,
            )
            if manifest and manifest.initial_receipt_id
            else False,
            "timestamp_receipt_url": self._download_url(
                confirmed_receipt,
                "data",
                confirmed_receipt.name,
            )
            if confirmed_receipt
            else False,
            "timestamp_report_url": self._download_url(
                manifest,
                "verification_report",
                manifest.verification_report_filename,
            )
            if manifest
            else False,
            "timestamp_dossier_url": self._download_url(
                manifest,
                "proof_dossier",
                manifest.proof_dossier_filename,
            )
            if manifest
            else False,
            "final_url": self._download_url(
                request, "final_data", request.final_filename,
            ),
            "certificate_url": self._download_url(
                request,
                "completion_certificate",
                request.completion_filename,
            ),
            "dossier_url": self._download_url(
                request, "dossier_data", request.dossier_filename,
            ),
        }


class SignStart(models.TransientModel):
    _name = "usl.sign.start"
    _description = "Start a Sign journey"

    request_type = fields.Selection(
        [
            ("signature", "Request document signatures"),
            ("decision", "Request a business decision"),
        ],
        string="Journey",
        default="signature",
        required=True,
    )
    signature_source = fields.Selection(
        [("template", "Use a template"), ("upload", "Upload a PDF")],
        string="Starting point",
        default="template",
        required=True,
    )
    name = fields.Char(string="Request name", required=True)
    template_id = fields.Many2one(
        "sign.oca.template",
        string="Template",
        domain="[('active', '=', True), ('preparation_status', '=', 'ready')]",
    )
    document_data = fields.Binary(string="PDF")
    document_filename = fields.Char()
    record_ref = fields.Reference(selection="_record_models", string="Linked record")
    approver_ids = fields.Many2many(
        "res.users",
        string="Approvers",
        domain="[('share', '=', False), ('company_ids', 'in', company_id)]",
    )
    company_id = fields.Many2one(
        "res.company",
        required=True,
        default=lambda self: self.env.company,
    )

    @api.model
    def _record_models(self):
        return self.env["sign.oca.request"]._sign_business_record_models()

    @api.onchange("template_id")
    def _onchange_template_id(self):
        if self.template_id and not self.name:
            self.name = self.template_id.name

    def action_continue(self):
        self.ensure_one()
        if self.request_type == "decision":
            if not self.record_ref or not self.approver_ids:
                msg = "Choose the business record and at least one approver."
                raise ValidationError(msg)
            approval = self.env["usl.sign.approval"].create(
                {
                    "name": self.name,
                    "company_id": self.company_id.id,
                    "record_ref": f"{self.record_ref._name},{self.record_ref.id}",
                    "approver_ids": [(6, 0, self.approver_ids.ids)],
                },
            )
            return {
                "type": "ir.actions.act_window",
                "name": approval.name,
                "res_model": approval._name,
                "res_id": approval.id,
                "views": [(False, "form")],
                "target": "current",
            }
        if self.signature_source == "template":
            if not self.template_id:
                msg = "Choose a ready template."
                raise ValidationError(msg)
            action = self.env["ir.actions.actions"]._for_xml_id(
                "sign_oca.sign_oca_template_generate_act_window",
            )
            action["context"] = {
                "default_template_id": self.template_id.id,
                "default_request_name": self.name,
                "default_record_ref": (
                    f"{self.record_ref._name},{self.record_ref.id}"
                    if self.record_ref
                    else False
                ),
                "active_id": self.template_id.id,
                "active_model": self.template_id._name,
            }
            return action
        if not self.document_data or not self.document_filename:
            msg = "Upload the PDF that needs signatures."
            raise ValidationError(msg)
        request = self.env["sign.oca.request"].create(
            {
                "name": self.name,
                "company_id": self.company_id.id,
                "data": self.document_data,
                "filename": self.document_filename,
                "record_ref": f"{self.record_ref._name},{self.record_ref.id}"
                if self.record_ref
                else False,
            },
        )
        return {
            "type": "ir.actions.act_window",
            "name": request.name,
            "res_model": request._name,
            "res_id": request.id,
            "views": [(False, "form")],
            "target": "current",
        }
