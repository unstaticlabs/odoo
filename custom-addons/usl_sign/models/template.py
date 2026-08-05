import hashlib
import secrets
from base64 import b64decode
from datetime import timedelta

from odoo import api, fields, models
from odoo.exceptions import ValidationError


class SignTemplate(models.Model):
    _inherit = "sign.oca.template"

    description = fields.Text(translate=True)
    company_id = fields.Many2one(
        "res.company", required=True, default=lambda self: self.env.company, index=True
    )
    version = fields.Integer(required=True, default=1, readonly=True)
    policy_id = fields.Many2one(
        "usl.sign.policy",
        required=True,
        domain="[('company_id', '=', company_id), ('active', '=', True)]",
    )
    expiration_days = fields.Integer(default=30, required=True)
    reminder_days = fields.Integer(default=3, required=True)
    max_reminders = fields.Integer(default=5, required=True)
    signing_order = fields.Boolean(string="Require signer order")
    public_link_enabled = fields.Boolean(copy=False)
    public_access_token = fields.Char(copy=False, readonly=True, groups="usl_sign.group_sign_admin")
    public_expires_at = fields.Datetime(copy=False)
    public_url = fields.Char(compute="_compute_public_url")
    has_requests = fields.Boolean(compute="_compute_has_requests")
    document_sha256 = fields.Char(
        compute="_compute_document_sha256", store=True, readonly=True, index=True
    )
    preparation_status = fields.Selection(
        [
            ("draft", "Draft"),
            ("ready", "Ready"),
            ("review_required", "Review required"),
        ],
        required=True,
        default="draft",
        tracking=True,
    )
    preparation_note = fields.Char()

    @api.model
    def _default_policy(self, company):
        return self.env["usl.sign.policy"].search(
            [("company_id", "=", company.id), ("is_default", "=", True)], limit=1
        )

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            company = self.env["res.company"].browse(
                vals.get("company_id") or self.env.company.id
            )
            if not vals.get("policy_id"):
                vals["policy_id"] = self._default_policy(company).id
        return super().create(vals_list)

    @api.depends("request_count")
    def _compute_has_requests(self):
        for template in self:
            template.has_requests = bool(template.request_count)

    @api.depends("data")
    def _compute_document_sha256(self):
        for template in self:
            template.document_sha256 = (
                hashlib.sha256(b64decode(template.data)).hexdigest()
                if template.data
                else False
            )

    @api.depends("public_access_token", "public_link_enabled")
    def _compute_public_url(self):
        base_url = self.env["ir.config_parameter"].sudo().get_str("web.base.url")
        for template in self:
            template.public_url = (
                f"{base_url}/sign/public/{template.public_access_token}"
                if template.public_link_enabled and template.public_access_token
                else False
            )

    def _touch_version(self):
        for template in self.filtered("request_count"):
            super(SignTemplate, template).write({"version": template.version + 1})

    def _prepare_sign_oca_request_vals_from_record(self, record):
        self.ensure_one()
        vals = super()._prepare_sign_oca_request_vals_from_record(record)
        vals.update(
            {
                "company_id": self.company_id.id,
                "policy_id": self.policy_id.id,
                "requested_assurance": self.policy_id.assurance_level,
                "authentication_method": self.policy_id.authentication_method,
                "provider_code": self.policy_id.provider_code,
                "template_version": self.version,
                "reminder_days": self.reminder_days,
                "max_reminders": self.max_reminders,
                "signing_order": self.signing_order,
                "expires_at": fields.Datetime.now()
                + timedelta(days=self.expiration_days),
            }
        )
        return vals

    def write(self, vals):
        material = {
            "data",
            "company_id",
            "model_id",
            "policy_id",
            "expiration_days",
            "reminder_days",
            "max_reminders",
            "signing_order",
            "preparation_status",
        }
        if material.intersection(vals):
            self._touch_version()
        if "company_id" in vals and self.filtered("request_count"):
            raise ValidationError(
                self.env._("A template already used for requests cannot change company.")
            )
        return super().write(vals)

    @api.constrains(
        "policy_id", "company_id", "expiration_days", "reminder_days", "max_reminders"
    )
    def _check_usl_template(self):
        for template in self:
            if template.policy_id.company_id != template.company_id:
                raise ValidationError(
                    self.env._("The signature policy must belong to the template company.")
                )
            if template.expiration_days < 1 or template.reminder_days < 0:
                raise ValidationError(
                    self.env._("Expiration and reminder delays must be valid positive values.")
                )
            if not 0 <= template.max_reminders <= 10:
                raise ValidationError(
                    self.env._("The maximum reminder count must be between 0 and 10.")
                )

    def _public_link_status(self):
        self.ensure_one()
        if not self.active or not self.public_link_enabled or not self.public_access_token:
            return False, self.env._("This signing link is invalid or no longer available.")
        if self.public_expires_at and self.public_expires_at <= fields.Datetime.now():
            return False, self.env._("This signing link has expired.")
        roles = self.item_ids.mapped("role_id")
        if (
            len(roles) != 1
            or not self.policy_id.public_link_allowed
            or self.policy_id.assurance_level != "standard"
        ):
            return False, self.env._("This signing link is no longer eligible for public use.")
        if not self.company_id.sign_provider_ready:
            return False, self.env._("Signing is temporarily unavailable. Please try again later.")
        return True, False

    def _prepare_public_request_vals(self, partner):
        self.ensure_one()
        available, explanation = self._public_link_status()
        if not available:
            raise ValidationError(explanation)
        role = self.item_ids.mapped("role_id").ensure_one()
        return {
            "name": self.name,
            "template_id": self.id,
            "template_version": self.version,
            "company_id": self.company_id.id,
            "policy_id": self.policy_id.id,
            "requested_assurance": self.policy_id.assurance_level,
            "authentication_method": self.policy_id.authentication_method,
            "provider_code": self.policy_id.provider_code,
            "signatory_data": self._get_signatory_data(),
            "data": self.data,
            "filename": self.filename,
            "signing_order": False,
            "expires_at": fields.Datetime.now() + timedelta(days=self.expiration_days),
            "reminder_days": self.reminder_days,
            "max_reminders": self.max_reminders,
            "user_id": self.create_uid.id,
            "signer_ids": [
                (0, 0, {"partner_id": partner.id, "role_id": role.id, "sequence": 10})
            ],
        }

    def action_enable_public_link(self):
        for template in self:
            roles = template.item_ids.mapped("role_id")
            if len(roles) != 1:
                raise ValidationError(
                    self.env._("A reusable public link requires exactly one signer role.")
                )
            if not template.policy_id.public_link_allowed:
                raise ValidationError(
                    self.env._("This assurance policy does not permit reusable public links.")
                )
            if not template.item_ids or not template.item_ids.filtered("required"):
                raise ValidationError(
                    self.env._("Add at least one required field before enabling a public link.")
                )
            if template.preparation_status == "review_required":
                raise ValidationError(
                    self.env._("Review and correct this template before enabling a public link.")
                )
            template.write(
                {
                    "public_link_enabled": True,
                    "public_access_token": secrets.token_urlsafe(32),
                }
            )
        return True

    def action_disable_public_link(self):
        self.write({"public_link_enabled": False, "public_access_token": False})
        return True

    def copy_data(self, default=None):
        defaults = dict(default or {})
        defaults.update(
            {
                "version": 1,
                "public_link_enabled": False,
                "public_access_token": False,
                "public_expires_at": False,
            }
        )
        return super().copy_data(defaults)


class SignTemplateItem(models.Model):
    _inherit = "sign.oca.template.item"

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        records.mapped("template_id")._touch_version()
        return records

    def write(self, vals):
        templates = self.mapped("template_id")
        result = super().write(vals)
        templates._touch_version()
        return result

    def unlink(self):
        templates = self.mapped("template_id")
        result = super().unlink()
        templates._touch_version()
        return result


class SignField(models.Model):
    _inherit = "sign.oca.field"

    usl_kind = fields.Selection(
        [
            ("signature", "Signature"),
            ("initials", "Initials"),
            ("text", "Text"),
            ("signer_name", "Signer name"),
            ("date", "Date"),
            ("checkbox", "Checkbox"),
            ("company", "Company"),
            ("role", "Role"),
        ],
        required=True,
        default="text",
    )
