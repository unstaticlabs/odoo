import os
from urllib.parse import urlparse

from odoo import api, fields, models
from odoo.exceptions import ValidationError


class ResCompany(models.Model):
    _inherit = "res.company"

    sign_default_policy_id = fields.Many2one(
        "usl.sign.policy", string="Default signing policy", ondelete="restrict",
    )
    sign_deliver_completed_to_signers = fields.Boolean(
        string="Send completed dossier to signers", default=True,
    )
    sign_evidence_retention_years = fields.Integer(
        string="Evidence retention (years)", default=10,
    )
    sign_webauthn_rp_id = fields.Char(string="Passkey relying-party ID")
    sign_webauthn_origins = fields.Char(
        string="Allowed passkey origins",
        help="Comma-separated HTTPS origins. Local HTTP is accepted only in development.",
    )
    sign_rfc3161_enabled = fields.Boolean(string="Use independent RFC 3161 timestamping")
    sign_services_ready = fields.Boolean(compute="_compute_sign_services_ready")
    sign_services_message = fields.Char(compute="_compute_sign_services_ready")

    @api.constrains("sign_evidence_retention_years")
    def _check_retention(self):
        for company in self:
            if not 1 <= company.sign_evidence_retention_years <= 100:
                msg = "Evidence retention must be between 1 and 100 years."
                raise ValidationError(msg)

    @api.constrains("sign_webauthn_rp_id", "sign_webauthn_origins")
    def _check_webauthn_configuration(self):
        development = os.getenv("USL_DEPLOYMENT_ENV", "development") == "development"
        for company in self.filtered("sign_webauthn_origins"):
            for origin in company._sign_allowed_origins():
                parsed = urlparse(origin)
                if parsed.scheme != "https" and not (
                    development
                    and parsed.scheme == "http"
                    and parsed.hostname in {"localhost", "127.0.0.1", "odoo.localhost"}
                ):
                    msg = "Passkey origins must use HTTPS."
                    raise ValidationError(msg)
                if parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
                    msg = "Passkey origins cannot contain paths or parameters."
                    raise ValidationError(msg)

    def _sign_allowed_origins(self):
        self.ensure_one()
        return {
            origin.strip().rstrip("/")
            for origin in (self.sign_webauthn_origins or "").split(",")
            if origin.strip()
        }

    @api.depends("sign_webauthn_rp_id", "sign_webauthn_origins")
    def _compute_sign_services_ready(self):
        for company in self:
            missing = []
            if not os.getenv("USL_SIGN_DSS_URL"):
                missing.append("DSS")
            if not os.getenv("USL_SIGN_STEP_CA_URL"):
                missing.append("step-ca")
            if not os.getenv("USL_SIGN_STEP_CA_JWK_FILE"):
                missing.append("certificate provisioner")
            if not company.sign_webauthn_rp_id or not company._sign_allowed_origins():
                missing.append("passkey origin")
            company.sign_services_ready = not missing
            company.sign_services_message = (
                "Signing services are ready."
                if not missing
                else "Configure " + ", ".join(missing) + "."
            )


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    sign_default_policy_id = fields.Many2one(
        related="company_id.sign_default_policy_id", readonly=False,
    )
    sign_deliver_completed_to_signers = fields.Boolean(
        related="company_id.sign_deliver_completed_to_signers", readonly=False,
    )
    sign_evidence_retention_years = fields.Integer(
        related="company_id.sign_evidence_retention_years", readonly=False,
    )
    sign_webauthn_rp_id = fields.Char(
        related="company_id.sign_webauthn_rp_id", readonly=False,
    )
    sign_webauthn_origins = fields.Char(
        related="company_id.sign_webauthn_origins", readonly=False,
    )
    sign_rfc3161_enabled = fields.Boolean(
        related="company_id.sign_rfc3161_enabled", readonly=False,
    )
    sign_services_ready = fields.Boolean(related="company_id.sign_services_ready")
    sign_services_message = fields.Char(related="company_id.sign_services_message")

    def action_test_sign_services(self):
        self.ensure_one()
        self.env["sign.oca.request"]._sign_dss_client().health()
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": "USL Sign",
                "message": "The DSS service is reachable.",
                "type": "success",
            },
        }
