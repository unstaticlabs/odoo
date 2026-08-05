import base64
import hashlib
import re
import uuid
from io import BytesIO

from odoo import api, fields, models
from odoo.exceptions import ValidationError
from odoo.tools.pdf import PdfReader

from .constants import INTERNAL_OPERATION

EDITOR_ROLE_COLORS = (
    "#7C3AED",
    "#0369A1",
    "#047857",
    "#B45309",
    "#BE123C",
    "#0E7490",
    "#6D28D9",
    "#3F6212",
)

FIELD_PRESENTATION = {
    "signature": {"icon": "fa-pencil", "width": 28.0, "height": 7.0},
    "initials": {"icon": "fa-pencil-square-o", "width": 14.0, "height": 5.0},
    "signer_name": {"icon": "fa-user", "width": 24.0, "height": 4.5},
    "email": {"icon": "fa-envelope", "width": 28.0, "height": 4.5},
    "phone": {"icon": "fa-phone", "width": 22.0, "height": 4.5},
    "date": {"icon": "fa-calendar", "width": 18.0, "height": 4.5},
    "company": {"icon": "fa-building", "width": 24.0, "height": 4.5},
    "role": {"icon": "fa-id-badge", "width": 22.0, "height": 4.5},
    "checkbox": {"icon": "fa-check-square", "width": 4.5, "height": 4.5},
    "text": {"icon": "fa-font", "width": 28.0, "height": 5.0},
}


def _field_kind(field):
    """Return a stable semantic kind, including the OCA email/phone fields."""
    if field.usl_kind != "text":
        return field.usl_kind
    xml_id = field.get_external_id().get(field.id, "")
    if xml_id == "sign_oca.sign_field_email":
        return "email"
    if xml_id == "sign_oca.sign_field_phone":
        return "phone"
    return "text"


def _field_info(field):
    kind = _field_kind(field)
    presentation = FIELD_PRESENTATION[kind]
    return {
        "id": field.id,
        "name": field.name,
        "field_type": field.field_type,
        "kind": kind,
        "icon": presentation["icon"],
        "default_width": presentation["width"],
        "default_height": presentation["height"],
        "supports_placeholder": field.field_type == "text",
    }


def _validate_editor_uuid(operation_uuid):
    try:
        return str(uuid.UUID(operation_uuid))
    except (AttributeError, TypeError, ValueError) as error:
        raise ValidationError("The editor operation identifier is invalid.") from error


def _validate_editor_geometry(values):
    geometry = {
        key: float(values[key])
        for key in ("position_x", "position_y", "width", "height")
        if key in values
    }
    if any(not value == value for value in geometry.values()):
        raise ValidationError("Field geometry must contain finite numbers.")
    return geometry


def _validate_complete_editor_geometry(values):
    geometry = {key: float(values[key]) for key in ("position_x", "position_y", "width", "height")}
    if (
        geometry["position_x"] < 0
        or geometry["position_y"] < 0
        or geometry["width"] < 2
        or geometry["height"] < 2
        or geometry["position_x"] + geometry["width"] > 100
        or geometry["position_y"] + geometry["height"] > 100
    ):
        raise ValidationError("The signing field must stay completely inside its PDF page.")


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
    editor_revision = fields.Integer(default=1, required=True, copy=False, readonly=True)
    editor_operation_log = fields.Json(default=dict, copy=False, readonly=True)
    editor_role_ids = fields.One2many(
        "usl.sign.template.role", "template_id", string="Editor role colors", copy=True,
    )

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

    def _ensure_editor_roles(self):
        """Persist an accessible, stable color for every selectable OCA role."""
        self.ensure_one()
        roles = self.env["sign.oca.role"].search([])
        mappings = {mapping.role_id.id: mapping for mapping in self.editor_role_ids}
        used_colors = set(self.editor_role_ids.mapped("color"))
        for sequence, role in enumerate(roles, start=1):
            if role.id in mappings:
                continue
            color = next(
                (candidate for candidate in EDITOR_ROLE_COLORS if candidate not in used_colors),
                EDITOR_ROLE_COLORS[(sequence - 1) % len(EDITOR_ROLE_COLORS)],
            )
            mapping = self.env["usl.sign.template.role"].create(
                {
                    "template_id": self.id,
                    "role_id": role.id,
                    "sequence": sequence * 10,
                    "color": color,
                },
            )
            mappings[role.id] = mapping
            used_colors.add(color)
        return mappings

    def _editor_roles_info(self):
        self.ensure_one()
        mappings = self._ensure_editor_roles()
        return [
            {
                "id": role.id,
                "name": role.name,
                "color": mappings[role.id].color,
                "sequence": mappings[role.id].sequence,
            }
            for role in self.env["sign.oca.role"].search([])
        ]

    def get_info(self):
        self.ensure_one()
        info = super().get_info()
        fields_info = {
            field.id: _field_info(field)
            for field in self.env["sign.oca.field"].search([])
        }
        info.update(
            {
                "items": {
                    item.id: {
                        **item.get_info(),
                        "kind": fields_info[item.field_id.id]["kind"],
                        "field_type": item.field_id.field_type,
                    }
                    for item in self.item_ids
                },
                "roles": self._editor_roles_info(),
                "fields": list(fields_info.values()),
                "revision": self.editor_revision,
                "readonly": bool(self.request_count or self.preparation_status == "ready"),
                "editor_mode": "template",
            },
        )
        return info

    def _editor_store_result(self, operation_uuid, result):
        self.ensure_one()
        operation_log = dict(self.editor_operation_log or {})
        operation_log[operation_uuid] = result
        if len(operation_log) > 100:
            operation_log = dict(list(operation_log.items())[-100:])
        self.with_context(usl_sign_editor_internal=INTERNAL_OPERATION).write(
            {
                "editor_revision": result["revision"],
                "editor_operation_log": operation_log,
                "preparation_status": "draft",
                "preparation_note": "Review this version after its field layout changed.",
            },
        )

    def _validate_editor_page(self, page):
        self.ensure_one()
        try:
            page_count = len(
                PdfReader(BytesIO(base64.b64decode(self.with_context(bin_size=False).data))).pages,
            )
        except Exception as error:
            raise ValidationError("Upload a readable PDF before editing its fields.") from error
        if int(page) < 1 or int(page) > page_count:
            raise ValidationError("The selected PDF page does not exist.")

    def editor_apply_command(self, operation_uuid, expected_revision, command):
        """Apply one idempotent, revision-checked editor command."""
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
                "message": "This template changed in another editor. Reload before continuing.",
            }
        action = command.get("action")
        values = dict(command.get("values") or {})
        allowed = {
            "field_id", "role_id", "required", "placeholder", "page",
            "position_x", "position_y", "width", "height",
        }
        if set(values) - allowed:
            raise ValidationError("The editor command contains unsupported field values.")
        _validate_editor_geometry(values)
        item = False
        deleted_id = False
        if action == "create":
            if not values.get("field_id") or not values.get("role_id"):
                raise ValidationError("Choose both a field type and a signer before placing it.")
            field = self.env["sign.oca.field"].browse(values["field_id"]).exists()
            role = self.env["sign.oca.role"].browse(values["role_id"]).exists()
            if not field or not role:
                raise ValidationError("The selected field type or signer role is unavailable.")
            defaults = FIELD_PRESENTATION[_field_kind(field)]
            values.setdefault("width", defaults["width"])
            values.setdefault("height", defaults["height"])
            values.setdefault("required", field.field_type == "signature")
            values.setdefault("page", 1)
            self._validate_editor_page(values["page"])
            item = self.env["sign.oca.template.item"].create(
                {"template_id": self.id, **values},
            )
            _validate_complete_editor_geometry(item.get_info())
        elif action == "update":
            item = self.item_ids.filtered(lambda row: row.id == int(command.get("item_id", 0)))
            if len(item) != 1:
                raise ValidationError("The field no longer exists in this template.")
            if "field_id" in values:
                field = self.env["sign.oca.field"].browse(values["field_id"]).exists()
                if not field:
                    raise ValidationError("The selected field type is unavailable.")
            if (
                "role_id" in values
                and not self.env["sign.oca.role"].browse(values["role_id"]).exists()
            ):
                raise ValidationError("The selected signer role is unavailable.")
            item.write(values)
            self._validate_editor_page(item.page)
            _validate_complete_editor_geometry(item.get_info())
        elif action == "delete":
            item = self.item_ids.filtered(lambda row: row.id == int(command.get("item_id", 0)))
            if len(item) != 1:
                raise ValidationError("The field no longer exists in this template.")
            deleted_id = item.id
            item.unlink()
            item = False
        else:
            raise ValidationError("The editor command action is unsupported.")
        new_revision = self.editor_revision + 1
        result = {
            "status": "ok",
            "revision": new_revision,
            "item": (
                {
                    **item.get_info(),
                    "kind": _field_kind(item.field_id),
                    "field_type": item.field_id.field_type,
                }
                if item else False
            ),
            "deleted_id": deleted_id,
        }
        self._editor_store_result(operation_uuid, result)
        return result

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
        if (
            material.intersection(values)
            and "preparation_status" not in values
            and self.env.context.get("usl_sign_editor_internal") is not INTERNAL_OPERATION
        ):
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
            record_values["editor_role_ids"] = [
                (
                    0,
                    0,
                    {
                        "role_id": mapping.role_id.id,
                        "sequence": mapping.sequence,
                        "color": mapping.color,
                    },
                )
                for mapping in template.editor_role_ids
            ]
        return values


class SignTemplateRole(models.Model):
    _name = "usl.sign.template.role"
    _description = "USL Sign template role presentation"
    _order = "sequence, id"

    template_id = fields.Many2one(
        "sign.oca.template", required=True, ondelete="cascade", index=True,
    )
    role_id = fields.Many2one("sign.oca.role", required=True, ondelete="restrict")
    sequence = fields.Integer(default=10, required=True)
    color = fields.Char(required=True)

    _template_role_unique = models.Constraint(
        "UNIQUE(template_id, role_id)",
        "A signer role can have only one color in a template.",
    )

    @api.constrains("color")
    def _check_color(self):
        for mapping in self:
            if not re.fullmatch(r"#[0-9A-F]{6}", mapping.color or ""):
                raise ValidationError("Role colors must use the #RRGGBB format.")


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
