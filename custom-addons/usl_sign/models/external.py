import hashlib
from urllib.parse import urlsplit

from odoo import api, fields, models
from odoo.exceptions import AccessError, ValidationError

from ..services import field_content
from .constants import INTERNAL_OPERATION


class SignExternalProvider(models.Model):
    _name = "usl.sign.external.provider"
    _description = "External Qualified-Signature Provider"
    _order = "company_id, recommendation_priority, name"

    name = fields.Char(required=True)
    active = fields.Boolean(default=True)
    company_id = fields.Many2one("res.company", index=True, ondelete="cascade")
    territory = fields.Char(required=True)
    supported_levels = fields.Char(required=True, default="QES")
    mobile_url = fields.Char(required=True)
    instructions = fields.Html(required=True, sanitize=True)
    commercial_notes = fields.Text()
    recommendation_priority = fields.Integer(default=10)
    reviewed_on = fields.Date(required=True)
    reviewed_by_id = fields.Many2one(
        "res.users", required=True, default=lambda self: self.env.user, ondelete="restrict",
    )

    @api.constrains("reviewed_on")
    def _check_review_date(self):
        if self.filtered(lambda provider: provider.reviewed_on > fields.Date.today()):
            msg = "An external-provider review date cannot be in the future."
            raise ValidationError(msg)

    @api.constrains("mobile_url")
    def _check_mobile_url(self):
        for provider in self:
            parsed = urlsplit(provider.mobile_url or "")
            if (
                parsed.scheme != "https"
                or not parsed.netloc
                or parsed.username
                or parsed.password
            ):
                msg = "The external-provider journey must use an HTTPS URL without embedded credentials."
                raise ValidationError(
                    msg,
                )


class SignExternalJourney(models.Model):
    _name = "usl.sign.external.journey"
    _description = "Provider-neutral External Signature Journey"
    _inherit = ["mail.thread", "mail.activity.mixin"]

    request_id = fields.Many2one(
        "sign.oca.request", required=True, index=True, ondelete="restrict",
    )
    company_id = fields.Many2one(related="request_id.company_id", store=True, index=True)
    provider_id = fields.Many2one(
        "usl.sign.external.provider", required=True, ondelete="restrict", tracking=True,
    )
    provider_snapshot = fields.Json(required=True, readonly=True)
    state = fields.Selection(
        [
            ("waiting", "Waiting for external signature"),
            ("imported", "Signed document imported"),
            ("validated", "Validated"),
            ("rejected", "Rejected"),
            ("cancelled", "Cancelled"),
        ],
        default="waiting",
        required=True,
        tracking=True,
    )
    frozen_sha256 = fields.Char(required=True, readonly=True)
    exported_at = fields.Datetime(readonly=True)
    signer_information = fields.Json(required=True, readonly=True)
    imported_pdf = fields.Binary(attachment=True, copy=False)
    imported_filename = fields.Char(copy=False)
    imported_sha256 = fields.Char(readonly=True, copy=False)
    proof_package = fields.Binary(attachment=True, copy=False)
    proof_filename = fields.Char(copy=False)
    imported_at = fields.Datetime(readonly=True)
    validation_id = fields.Many2one("usl.sign.validation", readonly=True)
    rejection_reason = fields.Text(readonly=True)
    provider_mobile_url = fields.Char(compute="_compute_provider_details")
    provider_instructions = fields.Html(
        compute="_compute_provider_details", sanitize=True,
    )
    provider_review_summary = fields.Char(compute="_compute_provider_details")
    signer_summary = fields.Text(compute="_compute_signer_summary")

    _request_unique = models.Constraint(
        "UNIQUE(request_id)", "A request can have only one external journey.",
    )

    @api.depends("provider_snapshot")
    def _compute_provider_details(self):
        for journey in self:
            snapshot = journey.provider_snapshot or {}
            journey.provider_mobile_url = snapshot.get("mobile_url")
            journey.provider_instructions = snapshot.get("instructions")
            reviewed_on = snapshot.get("reviewed_on") or "not recorded"
            reviewed_by = snapshot.get("reviewed_by") or "an authorized administrator"
            journey.provider_review_summary = (
                f"Reviewed on {reviewed_on} by {reviewed_by}. "
                f"Territory: {snapshot.get('territory') or 'not recorded'}. "
                f"Supported level: {snapshot.get('supported_levels') or 'not recorded'}."
            )

    @api.depends("signer_information")
    def _compute_signer_summary(self):
        for journey in self:
            lines = []
            for signer in journey.signer_information or []:
                identity = signer.get("name") or "Unnamed signer"
                if signer.get("email"):
                    identity = f"{identity} <{signer['email']}>"
                lines.append(
                    f"{signer.get('order') or len(lines) + 1}. {identity} — "
                    f"{signer.get('role') or 'Signer'}",
                )
            journey.signer_summary = "\n".join(lines)

    @api.model_create_multi
    def create(self, vals_list):
        if self.env.context.get("usl_sign_external_create") is not INTERNAL_OPERATION:
            msg = "External journeys are created by the controlled request action."
            raise AccessError(msg)
        for values in vals_list:
            provider = self.env["usl.sign.external.provider"].browse(
                values.get("provider_id"),
            )
            sign_request = self.env["sign.oca.request"].browse(values.get("request_id"))
            if not provider.active or (
                provider.company_id and provider.company_id != sign_request.company_id
            ):
                msg = "The external provider is not available to this company."
                raise ValidationError(msg)
            values["provider_snapshot"] = {
                "provider_id": provider.id,
                "name": provider.name,
                "territory": provider.territory,
                "supported_levels": provider.supported_levels,
                "mobile_url": provider.mobile_url,
                "instructions": provider.instructions,
                "commercial_notes": provider.commercial_notes,
                "recommendation_priority": provider.recommendation_priority,
                "reviewed_on": fields.Date.to_string(provider.reviewed_on),
                "reviewed_by_id": provider.reviewed_by_id.id,
                "reviewed_by": provider.reviewed_by_id.name,
            }
        return super().create(vals_list)

    def action_open_details(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": "Qualified external signature",
            "res_model": self._name,
            "res_id": self.id,
            "view_mode": "form",
            "views": [
                (self.env.ref("usl_sign.sign_external_journey_form").id, "form"),
            ],
            "target": "current",
        }

    def action_open_provider(self):
        self.ensure_one()
        url = (self.provider_snapshot or {}).get("mobile_url")
        parsed = urlsplit(url or "")
        if parsed.scheme != "https" or not parsed.netloc:
            msg = "The reviewed provider journey is not available."
            raise ValidationError(msg)
        self.request_id._append_event(
            "external_provider_opened",
            payload={
                "journey_id": self.id,
                "catalog_provider": (self.provider_snapshot or {}).get("name"),
            },
        )
        return {"type": "ir.actions.act_url", "target": "new", "url": url}

    def action_export(self):
        self.ensure_one()
        if self.request_id.state != "waiting_external":
            msg = "This request is not waiting for an external signature."
            raise ValidationError(msg)
        if not self.exported_at:
            self.with_context(usl_sign_external_transition=INTERNAL_OPERATION).write(
                {"exported_at": fields.Datetime.now()},
            )
            self.request_id._append_event(
                "external_document_exported",
                payload={
                    "journey_id": self.id,
                    "sha256": self.frozen_sha256,
                },
            )
        return {
            "type": "ir.actions.act_url",
            "target": "download",
            "url": f"/sign/external/{self.id}/document",
        }

    def action_open_import(self):
        self.ensure_one()
        if self.request_id.state != "waiting_external" or self.state != "waiting":
            msg = "This external journey is not waiting for a signed result."
            raise ValidationError(
                msg,
            )
        return {
            "type": "ir.actions.act_window",
            "name": "Import qualified signature",
            "res_model": "usl.sign.external.import.wizard",
            "view_mode": "form",
            "views": [
                (
                    self.env.ref("usl_sign.sign_external_import_wizard_form").id,
                    "form",
                ),
            ],
            "target": "new",
            "context": {"default_journey_id": self.id},
        }

    def action_import(self):
        self.ensure_one()
        if not self.imported_pdf or not self.proof_package:
            msg = "Import both the signed PDF and the provider proof package."
            raise ValidationError(msg)
        raw = field_content(self.imported_pdf)
        if not raw.startswith(b"%PDF-"):
            msg = "The imported signed document must be a PDF."
            raise ValidationError(msg)
        digest = hashlib.sha256(raw).hexdigest()
        self.with_context(usl_sign_external_transition=INTERNAL_OPERATION).write(
            {
                "state": "imported",
                "imported_sha256": digest,
                "imported_at": fields.Datetime.now(),
            },
        )
        self.request_id._transition("signed_to_import", "external_document_imported")
        return {
            "type": "ir.actions.act_window",
            "name": self.request_id.display_name,
            "res_model": "sign.oca.request",
            "res_id": self.request_id.id,
            "view_mode": "form",
            "target": "current",
        }

    def write(self, values):
        protected = {
            "state",
            "provider_snapshot",
            "exported_at",
            "imported_pdf",
            "imported_filename",
            "imported_sha256",
            "proof_package",
            "proof_filename",
            "imported_at",
            "validation_id",
            "rejection_reason",
        }
        if protected.intersection(values) and self.env.context.get(
            "usl_sign_external_transition",
        ) is not INTERNAL_OPERATION:
            msg = "Use a controlled external-signature action."
            raise AccessError(msg)
        if self.filtered(lambda journey: journey.state in {"validated", "rejected", "cancelled"}) and set(
            values,
        ) - {"message_follower_ids", "activity_ids"}:
            msg = "A closed external-signature journey is immutable."
            raise ValidationError(msg)
        return super().write(values)

    def unlink(self):
        msg = "External-signature journeys cannot be deleted."
        raise AccessError(msg)


class SignExternalImportWizard(models.TransientModel):
    _name = "usl.sign.external.import.wizard"
    _description = "Import External Qualified Signature"

    journey_id = fields.Many2one("usl.sign.external.journey", required=True)
    signed_pdf = fields.Binary(required=True)
    signed_filename = fields.Char(required=True)
    proof_package = fields.Binary(required=True)
    proof_filename = fields.Char(required=True)

    def action_import(self):
        self.ensure_one()
        self.journey_id.with_context(usl_sign_external_transition=INTERNAL_OPERATION).write(
            {
                "imported_pdf": self.signed_pdf,
                "imported_filename": self.signed_filename,
                "proof_package": self.proof_package,
                "proof_filename": self.proof_filename,
            },
        )
        return self.journey_id.action_import()
