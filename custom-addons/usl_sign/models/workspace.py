from pathlib import Path

from odoo import api, fields, models
from odoo.exceptions import AccessError, ValidationError


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
    def _completed_item(self, request):
        if request.managed_by_current_user:
            return self._request_item(request)
        signer = self.env["sign.oca.request.signer"].sudo().search(
            [
                ("request_id", "=", request.id),
                ("partner_id", "=", self.env.user.partner_id.id),
            ],
            order="sequence, id",
            limit=1,
        )
        item = self._request_item(request)
        item["action"] = {
            "type": "call",
            "model": "sign.oca.request.signer",
            "id": signer.id,
            "method": "action_open_request",
        }
        return item

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

        sections = {
            "sign_now": self._section(
                "sign.oca.request.signer",
                signer_domain,
                signer_item,
                order="request_id desc, sequence, id",
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
                self._completed_item,
                order="completed_at desc, id desc",
            ),
        }
        return {
            "can_start": self.env.user.has_group("usl_sign.group_sign_user"),
            "sections": sections,
        }


class SignStart(models.TransientModel):
    _name = "usl.sign.start"
    _description = "Start a signature request"
    signature_source = fields.Selection(
        [("upload", "Upload a PDF"), ("template", "Use a template")],
        string="Starting point",
        default="upload",
        required=True,
    )
    name = fields.Char(string="Document name")
    template_id = fields.Many2one(
        "sign.oca.template",
        string="Template",
        domain="[('active', '=', True), ('preparation_status', '=', 'ready')]",
    )
    document_data = fields.Binary(string="PDF")
    document_filename = fields.Char()
    signer_partner_id = fields.Many2one(
        "res.partner",
        string="Signer",
        domain="[('email', '!=', False)]",
    )
    message = fields.Text(string="Message")
    record_ref = fields.Reference(selection="_record_models", string="Linked record")
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

    @api.onchange("document_filename")
    def _onchange_document_filename(self):
        if self.document_filename and not self.name:
            self.name = Path(self.document_filename).stem.replace("_", " ").strip()

    def action_continue(self):
        self.ensure_one()
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
        if not self.signer_partner_id:
            msg = "Choose who should sign this document."
            raise ValidationError(msg)
        if not self.signer_partner_id.email:
            msg = "Add an email address for the signer before continuing."
            raise ValidationError(msg)
        request_name = (
            self.name
            or Path(self.document_filename).stem.replace("_", " ").strip()
            or "Document for signature"
        )
        request = self.env["sign.oca.request"].create(
            {
                "name": request_name,
                "company_id": self.company_id.id,
                "data": self.document_data,
                "filename": self.document_filename,
                "record_ref": f"{self.record_ref._name},{self.record_ref.id}"
                if self.record_ref
                else False,
                "responsible_message": self.message,
                "signer_ids": [
                    (
                        0,
                        0,
                        {
                            "partner_id": self.signer_partner_id.id,
                            "role_id": self.env.ref("sign_oca.sign_role_customer").id,
                            "sequence": 10,
                        },
                    ),
                ],
            },
        )
        return request.configure()
