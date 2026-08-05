import base64
import hashlib
from io import BytesIO

from odoo import api, fields, models
from odoo.exceptions import ValidationError
from odoo.tools.pdf import PdfReader


class SignTemplate(models.Model):
    _inherit = "sign.oca.template"

    description = fields.Text(translate=True)
    company_id = fields.Many2one(
        "res.company", required=True, default=lambda self: self.env.company, index=True,
    )
    version = fields.Integer(required=True, default=1, readonly=True)
    previous_version_id = fields.Many2one(
        "sign.oca.template", readonly=True, copy=False, ondelete="restrict",
    )
    policy_id = fields.Many2one("usl.sign.policy", ondelete="restrict")
    signing_order = fields.Boolean(string="Require signer order")
    expiration_days = fields.Integer(default=30, required=True)
    reminder_days = fields.Integer(default=3, required=True)
    max_reminders = fields.Integer(default=5, required=True)
    preparation_status = fields.Selection(
        [
            ("draft", "Draft"),
            ("needs_fields", "Fields required"),
            ("ready", "Ready"),
        ],
        default="draft",
        required=True,
        readonly=True,
    )
    preparation_note = fields.Char(readonly=True)
    document_sha256 = fields.Char(compute="_compute_document_sha256", store=True)
    document_ids = fields.One2many(
        "usl.sign.template.document", "template_id", string="Documents and annexes",
    )
    has_requests = fields.Boolean(compute="_compute_has_requests")

    @api.depends("request_count")
    def _compute_has_requests(self):
        for template in self:
            template.has_requests = bool(template.request_count)

    @api.depends("data")
    def _compute_document_sha256(self):
        for template in self:
            # ``web_save`` reads Binary fields with ``bin_size=True``. A
            # stored compute may therefore run after create with a display
            # size (for example ``6.5 KB``) instead of the base64 payload.
            document_data = template.with_context(bin_size=False).data
            template.document_sha256 = (
                hashlib.sha256(base64.b64decode(document_data)).hexdigest()
                if document_data
                else False
            )

    @api.model_create_multi
    def create(self, vals_list):
        templates = super().create(vals_list)
        for template in templates.filtered(lambda row: row.data and not row.document_ids):
            self.env["usl.sign.template.document"].create(
                {
                    "template_id": template.id,
                    "name": template.name,
                    "filename": template.filename or f"{template.name}.pdf",
                    # The web client creates records with ``bin_size=True``;
                    # always copy the persisted bytes, not the display size.
                    "data": template.with_context(bin_size=False).data,
                },
            )
        return templates

    def _ensure_draft(self):
        self.ensure_one()
        if self.request_count or self.preparation_status == "ready":
            msg = "Published or used templates are immutable; create a new version."
            raise ValidationError(msg)

    def configure(self):
        self.ensure_one()
        if self.request_count or self.preparation_status == "ready":
            return self._copy_new_version()._version_form_action()
        action = super().configure()
        action["tag"] = "usl_sign_template_configure"
        return action

    def _copy_new_version(self):
        self.ensure_one()
        new_template = self.copy(
            {
                "name": self.name,
                "version": self.version + 1,
                "previous_version_id": self.id,
                "preparation_status": "draft",
                "active": True,
            },
        )
        self.active = False
        return new_template

    def action_new_version(self):
        new_template = self._copy_new_version()
        return new_template._version_form_action()

    def _version_form_action(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "res_model": "sign.oca.template",
            "res_id": self.id,
            "view_mode": "form",
            "target": "current",
        }

    def action_mark_ready(self):
        for template in self:
            template._validate_template()
            template.write(
                {
                    "preparation_status": "ready",
                    "preparation_note": "Template fields, roles and documents passed review.",
                },
            )
        return True

    def _validate_template(self):
        self.ensure_one()
        if self.policy_id.company_id and self.policy_id.company_id != self.company_id:
            msg = "The signing policy belongs to another company."
            raise ValidationError(msg)
        raw = base64.b64decode(self.data or b"")
        try:
            page_count = len(PdfReader(BytesIO(raw)).pages)
        except Exception as error:
            msg = "Upload a readable PDF before preparing the template."
            raise ValidationError(msg) from error
        if not self.item_ids:
            msg = "Place at least one field on the PDF."
            raise ValidationError(msg)
        for item in self.item_ids:
            if not item.field_id or not item.role_id:
                msg = "Every template field needs a field type and signer role."
                raise ValidationError(msg)
            if item.page < 1 or item.page > page_count:
                msg = "A field is placed on a page that does not exist."
                raise ValidationError(msg)
            if (
                item.position_x < 0
                or item.position_y < 0
                or item.width <= 0
                or item.height <= 0
                or item.position_x + item.width > 100
                or item.position_y + item.height > 100
            ):
                msg = "A template field is outside the PDF page."
                raise ValidationError(msg)
        roles = self.item_ids.mapped("role_id")
        signature_roles = self.item_ids.filtered(
            lambda item: item.field_id.usl_kind == "signature",
        ).mapped("role_id")
        missing = roles - signature_roles
        if missing:
            raise ValidationError(
                "Place a signature field for these roles: " + ", ".join(missing.mapped("name")),
            )

    def _prepare_sign_oca_request_vals_from_record(self, record):
        self.ensure_one()
        if not self.active or self.preparation_status != "ready":
            msg = "Review and mark this template ready first."
            raise ValidationError(msg)
        values = super()._prepare_sign_oca_request_vals_from_record(record)
        values.update(
            {
                "company_id": self.company_id.id,
                "policy_id": self.policy_id.id,
                "template_version": self.version,
                "reminder_days": self.reminder_days,
                "max_reminders": self.max_reminders,
                "signing_order": self.signing_order,
                "document_ids": [
                    (
                        0,
                        0,
                        {
                            "sequence": document.sequence,
                            "is_annex": document.is_annex,
                            "name": document.name,
                            "filename": document.filename,
                            "data": document.data,
                        },
                    )
                    for document in self.document_ids
                ],
            },
        )
        return values

    def write(self, values):
        material = {
            "data",
            "name",
            "description",
            "company_id",
            "model_id",
            "policy_id",
            "signing_order",
            "expiration_days",
            "reminder_days",
            "max_reminders",
            "item_ids",
            "document_ids",
        }
        if material.intersection(values) and self.filtered(
            lambda template: template.request_count or template.preparation_status == "ready",
        ):
            msg = "Published or used templates are immutable; create a new version."
            raise ValidationError(msg)
        if material.intersection(values) and "preparation_status" not in values:
            values.update(
                {
                    "preparation_status": "draft",
                    "preparation_note": "Review this version after its material change.",
                },
            )
        return super().write(values)

    def unlink(self):
        if self.filtered(
            lambda template: template.request_count or template.preparation_status == "ready",
        ):
            msg = "Published or used templates cannot be deleted; archive them."
            raise ValidationError(msg)
        return super().unlink()

    def copy_data(self, default=None):
        values = super().copy_data(default=default)
        for template, record_values in zip(self, values, strict=True):
            record_values["item_ids"] = [
                (
                    0,
                    0,
                    {
                        "field_id": item.field_id.id,
                        "role_id": item.role_id.id,
                        "required": item.required,
                        "page": item.page,
                        "position_x": item.position_x,
                        "position_y": item.position_y,
                        "width": item.width,
                        "height": item.height,
                        "placeholder": item.placeholder,
                    },
                )
                for item in template.item_ids
            ]
            record_values["document_ids"] = [
                (
                    0,
                    0,
                    {
                        "sequence": document.sequence,
                        "is_annex": document.is_annex,
                        "name": document.name,
                        "filename": document.filename,
                        "data": document.data,
                    },
                )
                for document in template.document_ids
            ]
        return values


class SignTemplateItem(models.Model):
    _inherit = "sign.oca.template.item"

    @api.model_create_multi
    def create(self, vals_list):
        templates = self.env["sign.oca.template"].browse(
            [values.get("template_id") for values in vals_list],
        )
        if templates.filtered(
            lambda template: template.request_count or template.preparation_status == "ready",
        ):
            msg = "Published or used templates are immutable; create a new version."
            raise ValidationError(msg)
        return super().create(vals_list)

    def write(self, values):
        if self.mapped("template_id").filtered(
            lambda template: template.request_count or template.preparation_status == "ready",
        ):
            msg = "Published or used templates are immutable; create a new version."
            raise ValidationError(msg)
        return super().write(values)

    def unlink(self):
        if self.mapped("template_id").filtered(
            lambda template: template.request_count or template.preparation_status == "ready",
        ):
            msg = "Published or used templates are immutable; create a new version."
            raise ValidationError(msg)
        return super().unlink()


class SignField(models.Model):
    _inherit = "sign.oca.field"

    usl_kind = fields.Selection(
        [
            ("signature", "Signature"),
            ("initials", "Initials"),
            ("text", "Text"),
            ("checkbox", "Checkbox"),
            ("date", "Date"),
            ("signer_name", "Signer name"),
            ("company", "Company"),
            ("role", "Role"),
        ],
        required=True,
        default="text",
    )
