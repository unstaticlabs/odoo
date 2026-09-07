"""Template editor commands, layout validation and document freezing before sending."""

import hashlib
import json
from datetime import timedelta
from io import BytesIO

from odoo import fields, models
from odoo.exceptions import ValidationError
from odoo.tools.pdf import PdfReader

from .constants import INTERNAL_OPERATION
from .template import (
    EDITOR_ROLE_COLORS,
    FIELD_PRESENTATION,
    _field_info,
    _field_kind,
    _validate_complete_editor_geometry,
    _validate_editor_geometry,
    _validate_editor_uuid,
)
from odoo.addons.usl_sign.services import field_content, field_value


class SignRequest(models.Model):
    _inherit = "sign.oca.request"

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

    @staticmethod
    def _strong_primary_signature_item_ids(layout):
        signature_by_role = {}
        fallback_by_role = {}
        for key, item in sorted(layout.items(), key=lambda row: int(row[0])):
            if item.get("field_type") != "signature":
                continue
            role_id = int(item["role_id"])
            fallback_by_role.setdefault(role_id, str(key))
            if item.get("kind") == "signature":
                signature_by_role.setdefault(role_id, str(key))
        return {
            signature_by_role.get(role_id, fallback_id)
            for role_id, fallback_id in fallback_by_role.items()
        }

    def _strong_reserved_field_descriptors(self, layout):
        primary_ids = self._strong_primary_signature_item_ids(layout)
        return [
            {
                "name": f"usl_sign_{int(key)}",
                "page": int(item["page"]),
                "position_x": float(item["position_x"]),
                "position_y": float(item["position_y"]),
                "width": float(item["width"]),
                "height": float(item["height"]),
            }
            for key, item in sorted(layout.items(), key=lambda row: int(row[0]))
            if str(key) not in primary_ids
        ]

    def _freeze_document(self):
        self.ensure_one()
        if self.original_data:
            return
        consolidated, page_map = self.env["usl.sign.request.document"]._consolidate(
            self.document_ids,
        )
        frozen_layout = json.loads(json.dumps(self.signatory_data or {}))
        if self.requested_trust == "strong_personal":
            reserved_fields = self._strong_reserved_field_descriptors(frozen_layout)
            if reserved_fields:
                consolidated = self._sign_dss_client().prepare_signing_fields(
                    consolidated,
                    reserved_fields,
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
                "frozen_layout": frozen_layout,
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
