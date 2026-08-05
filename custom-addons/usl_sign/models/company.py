import os

from odoo import api, fields, models
from odoo.exceptions import ValidationError

from ..services import get_provider


BASE_URLS = {
    "sandbox": "https://api-sandbox.yousign.app/v3",
    "production": "https://api.yousign.app/v3",
}


class ResCompany(models.Model):
    _inherit = "res.company"

    sign_provider_enabled = fields.Boolean(string="Enable provider-backed signatures")
    sign_yousign_environment = fields.Selection(
        [("sandbox", "Sandbox"), ("production", "Production")],
        required=True,
        default="sandbox",
    )
    sign_yousign_workspace_id = fields.Char(string="Yousign workspace ID")
    sign_deliver_completed_to_signers = fields.Boolean(
        string="Email completed documents to signers"
    )
    sign_yousign_configured = fields.Boolean(
        compute="_compute_sign_yousign_configured"
    )
    sign_yousign_webhook_configured = fields.Boolean(
        compute="_compute_sign_yousign_configured"
    )
    sign_provider_ready = fields.Boolean(compute="_compute_sign_yousign_configured")
    sign_yousign_webhook_url = fields.Char(
        compute="_compute_sign_yousign_webhook_url"
    )

    def _yousign_env_name(self, suffix):
        self.ensure_one()
        return f"USL_YOUSIGN_{self.sign_yousign_environment.upper()}_{suffix}"

    @api.depends("sign_yousign_environment", "sign_provider_enabled")
    def _compute_sign_yousign_configured(self):
        for company in self:
            api_ready = bool(os.getenv(company._yousign_env_name("API_KEY")))
            webhook_ready = bool(
                os.getenv(company._yousign_env_name("WEBHOOK_SECRET"))
            )
            live_ready = (
                company.sign_yousign_environment != "production"
                or os.getenv("USL_SIGN_LIVE_ENABLED", "0") == "1"
            )
            company.sign_yousign_configured = api_ready
            company.sign_yousign_webhook_configured = webhook_ready
            company.sign_provider_ready = bool(
                company.sign_provider_enabled and api_ready and webhook_ready and live_ready
            )

    @api.depends_context("company")
    def _compute_sign_yousign_webhook_url(self):
        base_url = self.env["ir.config_parameter"].sudo().get_param("web.base.url")
        for company in self:
            company.sign_yousign_webhook_url = (
                f"{base_url}/sign/webhooks/yousign/{company.id}"
            )

    def _sign_provider_configuration(self, *, webhook=False):
        self.ensure_one()
        if not self.sign_provider_enabled:
            raise ValidationError(
                self.env._("Provider-backed signatures are not enabled for this company.")
            )
        if (
            self.sign_yousign_environment == "production"
            and os.getenv("USL_SIGN_LIVE_ENABLED", "0") != "1"
        ):
            raise ValidationError(
                self.env._(
                    "Production signing is disabled. Complete the activation runbook and set USL_SIGN_LIVE_ENABLED=1."
                )
            )
        api_key = os.getenv(self._yousign_env_name("API_KEY"))
        if not api_key:
            raise ValidationError(
                self.env._(
                    "The server credential %(variable)s is not configured.",
                    variable=self._yousign_env_name("API_KEY"),
                )
            )
        configuration = {
            "api_key": api_key,
            "base_url": BASE_URLS[self.sign_yousign_environment],
            "workspace_id": self.sign_yousign_workspace_id or False,
        }
        if webhook:
            secret = os.getenv(self._yousign_env_name("WEBHOOK_SECRET"))
            if not secret:
                raise ValidationError(
                    self.env._(
                        "The server credential %(variable)s is not configured.",
                        variable=self._yousign_env_name("WEBHOOK_SECRET"),
                    )
                )
            configuration["webhook_secret"] = secret
        return configuration

    def _sign_webhook_configuration(self):
        self.ensure_one()
        if not self.sign_provider_enabled:
            raise ValidationError(
                self.env._("Provider-backed signatures are not enabled for this company.")
            )
        secret = os.getenv(self._yousign_env_name("WEBHOOK_SECRET"))
        if not secret:
            raise ValidationError(
                self.env._(
                    "The server credential %(variable)s is not configured.",
                    variable=self._yousign_env_name("WEBHOOK_SECRET"),
                )
            )
        return {
            "webhook_secret": secret,
            "environment": self.sign_yousign_environment,
        }


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    sign_provider_enabled = fields.Boolean(
        related="company_id.sign_provider_enabled", readonly=False
    )
    sign_yousign_environment = fields.Selection(
        related="company_id.sign_yousign_environment", readonly=False
    )
    sign_yousign_workspace_id = fields.Char(
        related="company_id.sign_yousign_workspace_id", readonly=False
    )
    sign_deliver_completed_to_signers = fields.Boolean(
        related="company_id.sign_deliver_completed_to_signers", readonly=False
    )
    sign_yousign_configured = fields.Boolean(
        related="company_id.sign_yousign_configured"
    )
    sign_yousign_webhook_configured = fields.Boolean(
        related="company_id.sign_yousign_webhook_configured"
    )
    sign_yousign_webhook_url = fields.Char(
        related="company_id.sign_yousign_webhook_url"
    )

    def action_test_sign_provider(self):
        self.ensure_one()
        configuration = self.company_id._sign_provider_configuration()
        get_provider("yousign", configuration).healthcheck()
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": self.env._("Signature provider"),
                "message": self.env._("The Yousign connection is ready."),
                "type": "success",
                "sticky": False,
            },
        }
