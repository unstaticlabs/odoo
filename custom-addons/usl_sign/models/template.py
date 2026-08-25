import hashlib
import re
import uuid
from io import BytesIO

from odoo import api, fields, models
from odoo.exceptions import AccessError, ValidationError
from odoo.tools.pdf import PdfReader

from ..services import field_content, field_value
from .constants import INTERNAL_OPERATION

EDITOR_ROLE_COLORS = (
    "#E86A8D",
    "#FCD12A",
    "#56AE64",
    "#3EA8F9",
    "#9E8DF9",
    "#D7794D",
    "#00B591",
    "#E53935",
    "#CF75CB",
    "#000000",
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


class SignRole(models.Model):
    _inherit = "sign.oca.role"

    partner_selection_policy = fields.Selection(
        selection=[
            ("empty", "Choose for each request"),
            ("default", "Preselect one person"),
            ("expression", "Use the linked business record"),
        ],
        required=True,
        default="empty",
        help=(
            "Controls whether the person is chosen while preparing each request, "
            "preselected, or derived from the linked Odoo record."
        ),
    )


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
        "technical_type": field.field_type,
        "kind": kind,
        "icon": presentation["icon"],
        "default_width": presentation["width"],
        "default_height": presentation["height"],
        "default_value": field.default_value or False,
        "supports_placeholder": field.field_type == "text",
    }


def _validate_editor_uuid(operation_uuid):
    try:
        return str(uuid.UUID(operation_uuid))
    except (AttributeError, TypeError, ValueError) as error:
        msg = "The editor operation identifier is invalid."
        raise ValidationError(msg) from error


def _validate_editor_geometry(values):
    geometry = {
        key: float(values[key])
        for key in ("position_x", "position_y", "width", "height")
        if key in values
    }
    if any(value != value for value in geometry.values()):
        msg = "Field geometry must contain finite numbers."
        raise ValidationError(msg)
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
        msg = "The signing field must stay completely inside its PDF page."
        raise ValidationError(msg)


class SignTemplate(models.Model):
    _inherit = "sign.oca.template"

    description = fields.Text(translate=True)
    default_document_category = fields.Selection(
        [
            ("internal_decision", "Corporate decision document"),
            ("routine_agreement", "Routine agreement"),
            ("employment", "Employment document"),
            ("intellectual_property", "Intellectual property"),
            ("commercial", "Commercial agreement"),
            ("finance_guarantee", "Financing or guarantee"),
            ("mandate", "Mandate"),
            ("other", "Other"),
        ],
        string="Category",
        default="routine_agreement",
        required=True,
    )
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
    signer_role_count = fields.Integer(compute="_compute_signer_role_count")
    editor_revision = fields.Integer(default=1, required=True, copy=False, readonly=True)
    editor_operation_log = fields.Json(default=dict, copy=False, readonly=True)
    upload_operation_uuid = fields.Char(readonly=True, copy=False, index=True)
    editor_role_ids = fields.One2many(
        "usl.sign.template.role", "template_id", string="Editor role colors", copy=True,
    )
    default_trust = fields.Selection(
        [
            ("standard", "Standard"),
            ("strong_personal", "Strong personal"),
            ("qualified_external", "Qualified external"),
        ],
        compute="_compute_default_trust",
    )

    @api.depends("item_ids.role_id")
    def _compute_signer_role_count(self):
        for template in self:
            template.signer_role_count = len(template.item_ids.role_id)

    _upload_operation_unique = models.Constraint(
        "UNIQUE(upload_operation_uuid)",
        "A template upload operation may only be applied once.",
    )

    @api.model
    def create_from_documents(self, documents, operation_uuid, company_id=None):
        """Create one draft envelope from browser-uploaded PDFs, atomically."""
        if not self.env.user.has_group("usl_sign.group_sign_template_manager"):
            msg = "Only a Sign template manager can upload reusable templates."
            raise AccessError(msg)
        operation_uuid = _validate_editor_uuid(operation_uuid)
        existing = self.search(
            [("upload_operation_uuid", "=", operation_uuid)],
            limit=1,
        )
        if existing:
            return existing.configure()
        if not isinstance(documents, list) or not 1 <= len(documents) <= 20:
            msg = "Choose between one and twenty PDF documents."
            raise ValidationError(msg)
        company = self.env.company
        if company_id:
            company = self.env["res.company"].browse(int(company_id)).exists()
            if not company or company not in self.env.user.company_ids:
                msg = "You cannot create a template for this company."
                raise AccessError(msg)
        maximum = self.env["ir.config_parameter"].sudo().get_int(
            "usl_sign.max_template_upload_bytes",
            50 * 1024 * 1024,
        )
        prepared = []
        total_size = 0
        for sequence, document in enumerate(documents, start=1):
            if not isinstance(document, dict):
                msg = "The uploaded document description is invalid."
                raise ValidationError(msg)
            filename = (document.get("name") or "").strip()
            if not filename or len(filename) > 255 or not filename.lower().endswith(".pdf"):
                msg = "Every template document must have a PDF filename."
                raise ValidationError(msg)
            try:
                raw = field_content(document.get("data"), validate=True)
            except (TypeError, ValueError) as error:
                msg = "An uploaded PDF is not valid base64."
                raise ValidationError(msg) from error
            total_size += len(raw)
            if total_size > maximum:
                msg = "The template envelope exceeds the configured upload limit."
                raise ValidationError(msg)
            self.env["usl.sign.request.document"]._validate_pdf(raw)
            prepared.append(
                {
                    "sequence": sequence * 10,
                    "is_annex": sequence > 1,
                    "name": re.sub(r"(?i)\.pdf$", "", filename).strip() or filename,
                    "filename": filename,
                    "data": field_value(raw),
                },
            )
        primary = prepared[0]
        template = self.create(
            {
                "name": primary["name"],
                "filename": primary["filename"],
                "data": primary["data"],
                "company_id": company.id,
                "upload_operation_uuid": operation_uuid,
                "document_ids": [(0, 0, values) for values in prepared],
            },
        )
        return template.configure()

    @api.depends("policy_id", "policy_id.recommendation")
    def _compute_default_trust(self):
        for template in self:
            template.default_trust = template.policy_id.recommendation or "standard"

    @api.depends("request_count")
    def _compute_has_requests(self):
        for template in self:
            template.has_requests = bool(template.request_count)

    @api.depends("data")
    def _compute_document_sha256(self):
        for template in self:
            document_data = template.with_context(bin_size=False).data
            template.document_sha256 = (
                hashlib.sha256(field_content(document_data)).hexdigest()
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
        """Bootstrap template-local role choices without re-adding removed roles."""
        self.ensure_one()
        if self.editor_role_ids:
            return {mapping.role_id.id: mapping for mapping in self.editor_role_ids}
        roles = self.item_ids.mapped("role_id") or self.env["sign.oca.role"].search([])
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
        self._ensure_editor_roles()
        return [
            {
                "id": mapping.role_id.id,
                "name": mapping.role_id.name,
                "color": mapping.color,
                "sequence": mapping.sequence,
            }
            for mapping in self.editor_role_ids.sorted(lambda row: (row.sequence, row.id))
        ]

    def _next_editor_role_values(self, role):
        self.ensure_one()
        used_colors = set(self.editor_role_ids.mapped("color"))
        color = next(
            (candidate for candidate in EDITOR_ROLE_COLORS if candidate not in used_colors),
            EDITOR_ROLE_COLORS[len(self.editor_role_ids) % len(EDITOR_ROLE_COLORS)],
        )
        return {
            "template_id": self.id,
            "role_id": role.id,
            "sequence": (max(self.editor_role_ids.mapped("sequence") or [0]) + 10),
            "color": color,
        }

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
                        "technical_type": item.field_id.field_type,
                    }
                    for item in self.item_ids
                },
                "roles": self._editor_roles_info(),
                "fields": list(fields_info.values()),
                "revision": self.editor_revision,
                "readonly": bool(self.request_count or self.preparation_status == "ready"),
                "editor_mode": "template",
                "can_manage_roles": self.env.user.has_group(
                    "usl_sign.group_sign_template_manager",
                ),
            },
        )
        return info

    def _get_signatory_data(self):
        """Freeze USL semantic field types into requests generated from templates."""
        self.ensure_one()
        data = super()._get_signatory_data()
        fields_by_id = {
            item.field_id.id: item.field_id
            for item in self.item_ids
        }
        for item in data.values():
            field = fields_by_id.get(int(item["field_id"]))
            if field:
                item.update(
                    {
                        "kind": _field_kind(field),
                        "field_type": field.field_type,
                        "technical_type": field.field_type,
                    },
                )
        return data

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

    def _editor_page_count(self):
        self.ensure_one()
        try:
            return len(
                PdfReader(BytesIO(field_content(self.with_context(bin_size=False).data))).pages,
            )
        except Exception as error:
            msg = "Upload a readable PDF before editing its fields."
            raise ValidationError(msg) from error

    def _validate_editor_page(self, page):
        self.ensure_one()
        page_count = self._editor_page_count()
        if int(page) < 1 or int(page) > page_count:
            msg = "The selected PDF page does not exist."
            raise ValidationError(msg)

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
        self._ensure_editor_roles()
        if action in {"role_add", "role_remove"}:
            if not self.env.user.has_group("usl_sign.group_sign_template_manager"):
                msg = "Only a Sign template manager can change signer roles."
                raise AccessError(msg)
            if action == "role_add":
                name = (values.get("name") or "").strip()
                if not name or len(name) > 100:
                    msg = "Enter a signer role name of at most 100 characters."
                    raise ValidationError(msg)
                role_model = self.env["sign.oca.role"].sudo()
                role = role_model.search(
                    [("name", "=ilike", name)], limit=1,
                ) or role_model.create({"name": name})
                if role in self.editor_role_ids.mapped("role_id"):
                    msg = "This signer role is already available on the template."
                    raise ValidationError(msg)
                self.env["usl.sign.template.role"].create(
                    self._next_editor_role_values(role),
                )
                role_id = role.id
            else:
                role_id = int(command.get("role_id") or 0)
                mapping = self.editor_role_ids.filtered(
                    lambda row: row.role_id.id == role_id,
                )
                if len(mapping) != 1:
                    msg = "This signer role is not available on the template."
                    raise ValidationError(msg)
                if self.item_ids.filtered(lambda item: item.role_id.id == role_id):
                    msg = "Reassign or delete this role's fields before removing the role."
                    raise ValidationError(
                        msg,
                    )
                if len(self.editor_role_ids) == 1:
                    msg = "A template must keep at least one signer role."
                    raise ValidationError(msg)
                mapping.unlink()
            result = {
                "status": "ok",
                "revision": self.editor_revision + 1,
                "roles": self._editor_roles_info(),
                "role_id": role_id,
            }
            self._editor_store_result(operation_uuid, result)
            return result
        allowed = {
            "field_id", "role_id", "required", "placeholder", "page",
            "position_x", "position_y", "width", "height",
        }
        if set(values) - allowed:
            msg = "The editor command contains unsupported field values."
            raise ValidationError(msg)
        _validate_editor_geometry(values)
        item = False
        items = []
        deleted_id = False
        deleted_ids = []
        if action in {"create", "create_all_pages"}:
            if not values.get("field_id") or not values.get("role_id"):
                msg = "Choose both a field type and a signer before placing it."
                raise ValidationError(msg)
            field = self.env["sign.oca.field"].browse(values["field_id"]).exists()
            role = self.editor_role_ids.mapped("role_id").filtered(
                lambda row: row.id == int(values["role_id"]),
            )
            if not field or not role:
                msg = "The selected field type or signer role is unavailable."
                raise ValidationError(msg)
            if action == "create_all_pages" and _field_kind(field) != "initials":
                msg = "Only an Initials field can be placed on every page."
                raise ValidationError(msg)
            defaults = FIELD_PRESENTATION[_field_kind(field)]
            values.setdefault("width", defaults["width"])
            values.setdefault("height", defaults["height"])
            values.setdefault("required", field.field_type == "signature")
            values.setdefault("page", 1)
            page_count = self._editor_page_count()
            pages = (
                range(1, page_count + 1)
                if action == "create_all_pages"
                else [int(values["page"])]
            )
            for page in pages:
                if page < 1 or page > page_count:
                    msg = "The selected PDF page does not exist."
                    raise ValidationError(msg)
                item_values = {**values, "page": page}
                created = self.env["sign.oca.template.item"].create(
                    {"template_id": self.id, **item_values},
                )
                _validate_complete_editor_geometry(created.get_info())
                items.append(created)
            if action == "create":
                item = items[0]
                items = []
        elif action == "update":
            item = self.item_ids.filtered(lambda row: row.id == int(command.get("item_id", 0)))
            if len(item) != 1:
                msg = "The field no longer exists in this template."
                raise ValidationError(msg)
            if "field_id" in values:
                field = self.env["sign.oca.field"].browse(values["field_id"]).exists()
                if not field:
                    msg = "The selected field type is unavailable."
                    raise ValidationError(msg)
            if (
                "role_id" in values
                and not self.editor_role_ids.mapped("role_id").filtered(
                    lambda row: row.id == int(values["role_id"]),
                )
            ):
                msg = "The selected signer role is unavailable."
                raise ValidationError(msg)
            item.write(values)
            self._validate_editor_page(item.page)
            _validate_complete_editor_geometry(item.get_info())
        elif action == "delete":
            item = self.item_ids.filtered(lambda row: row.id == int(command.get("item_id", 0)))
            if len(item) != 1:
                msg = "The field no longer exists in this template."
                raise ValidationError(msg)
            deleted_id = item.id
            item.unlink()
            item = False
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
            batch = self.item_ids.filtered(lambda row: row.id in requested_ids)
            if len(batch) != len(requested_ids):
                msg = "One or more fields no longer exist in this template."
                raise ValidationError(msg)
            deleted_ids = batch.ids
            batch.unlink()
        else:
            msg = "The editor command action is unsupported."
            raise ValidationError(msg)
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
            "items": [
                {
                    **created.get_info(),
                    "kind": _field_kind(created.field_id),
                    "field_type": created.field_id.field_type,
                }
                for created in items
            ],
            "deleted_id": deleted_id,
            "deleted_ids": deleted_ids,
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
        raw = field_content(self.data)
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
                msg = "Role colors must use the #RRGGBB format."
                raise ValidationError(msg)


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
