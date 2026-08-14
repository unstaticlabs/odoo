import os

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

from ..services import StepCAClient


class ResCompany(models.Model):
    _inherit = "res.company"

    sign_oca_send_sign_request_copy = fields.Boolean(
        string="Send signers a copy of the final signed document",
        help=(
            "Once USL Sign has validated and archived the request, send each "
            "signer the final evidence dossier."
        ),
        default=True,
    )

    sign_default_policy_id = fields.Many2one(
        "usl.sign.policy", string="Default signing policy", ondelete="restrict",
    )
    sign_evidence_retention_years = fields.Integer(
        string="Evidence retention (years)", default=10,
    )
    sign_rfc3161_enabled = fields.Boolean(string="Use independent RFC 3161 timestamping")
    sign_opentimestamps_enabled = fields.Boolean(
        string="Daily Bitcoin existence proof",
        default=True,
        help=(
            "Submit each closed UTC day's signed evidence manifest to "
            "OpenTimestamps. Confirmation is asynchronous and never delays a "
            "signature request's completion."
        ),
    )
    sign_services_ready = fields.Boolean(compute="_compute_sign_services_ready")
    sign_services_message = fields.Char(compute="_compute_sign_services_ready")

    @api.constrains("sign_evidence_retention_years")
    def _check_retention(self):
        for company in self:
            if not 1 <= company.sign_evidence_retention_years <= 100:
                msg = "Evidence retention must be between 1 and 100 years."
                raise ValidationError(msg)

    def _compute_sign_services_ready(self):
        for company in self:
            missing = []
            if not os.getenv("USL_SIGN_DSS_URL"):
                missing.append("DSS")
            if not os.getenv("USL_SIGN_STEP_CA_URL"):
                missing.append("step-ca")
            if not os.getenv("USL_SIGN_STEP_CA_JWK_FILE"):
                missing.append("certificate provisioner")
            required_pocket = {
                "USL_POCKET_ID_ISSUER",
                "USL_POCKET_ID_SIGN_CLIENT_ID",
                "USL_POCKET_ID_SIGN_CLIENT_SECRET",
                "USL_POCKET_ID_SIGN_REQUIRED_GROUP",
            }
            if any(not os.getenv(name) for name in required_pocket):
                missing.append("Pocket ID Sign client")
            if os.getenv("USL_POCKET_ID_SIGN_FRESH_REQUIRED", "").lower() not in {
                "1",
                "true",
                "yes",
                "on",
            }:
                missing.append("fresh Pocket ID authorization")
            company.sign_services_ready = not missing
            company.sign_services_message = (
                _("Signing services are ready.")
                if not missing
                else _("Configure: %(services)s", services=", ".join(missing))
            )


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    sign_default_policy_id = fields.Many2one(
        related="company_id.sign_default_policy_id", readonly=False,
    )
    sign_evidence_retention_years = fields.Integer(
        related="company_id.sign_evidence_retention_years", readonly=False,
    )
    sign_rfc3161_enabled = fields.Boolean(
        related="company_id.sign_rfc3161_enabled", readonly=False,
    )
    sign_opentimestamps_enabled = fields.Boolean(
        related="company_id.sign_opentimestamps_enabled", readonly=False,
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
                "message": _("The DSS service is reachable."),
                "type": "success",
            },
        }

    def action_test_sign_certificate_service(self):
        self.ensure_one()
        StepCAClient().health()
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": "USL Sign",
                "message": _("The certificate service is reachable."),
                "type": "success",
            },
        }
