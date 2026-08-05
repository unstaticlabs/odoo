import os

from odoo import api, fields, models
from odoo.exceptions import AccessError

from ..services import get_provider


class SignConfiguration(models.TransientModel):
    _name = "usl.sign.configuration"
    _description = "Sign Configuration"

    company_id = fields.Many2one(
        "res.company",
        required=True,
        default=lambda self: self.env.company,
        domain=lambda self: [("id", "in", self.env.companies.ids)],
    )
    provider_enabled = fields.Boolean(string="Enable provider-backed signatures")
    environment = fields.Selection(
        [("sandbox", "Sandbox"), ("production", "Production")], required=True
    )
    workspace_id = fields.Char(string="Yousign workspace ID")
    deliver_completed_to_signers = fields.Boolean(
        string="Email completed documents to signers"
    )
    evidence_retention_years = fields.Integer(string="Evidence retention (years)")
    api_credential_configured = fields.Boolean(
        string="Server credential configured", compute="_compute_status"
    )
    webhook_secret_configured = fields.Boolean(
        string="Webhook secret configured", compute="_compute_status"
    )
    production_gate_enabled = fields.Boolean(
        string="Production activation approved", compute="_compute_status"
    )
    provider_ready = fields.Boolean(string="Provider ready", compute="_compute_status")
    webhook_url = fields.Char(string="Webhook URL", compute="_compute_status")

    @api.model
    def default_get(self, field_names):
        values = super().default_get(field_names)
        company = self.env["res.company"].browse(
            values.get("company_id") or self.env.company.id
        )
        values.update(self._values_from_company(company))
        return values

    @api.model
    def _values_from_company(self, company):
        return {
            "company_id": company.id,
            "provider_enabled": company.sign_provider_enabled,
            "environment": company.sign_yousign_environment,
            "workspace_id": company.sign_yousign_workspace_id,
            "deliver_completed_to_signers": company.sign_deliver_completed_to_signers,
            "evidence_retention_years": company.sign_evidence_retention_years,
        }

    @api.onchange("company_id")
    def _onchange_company_id(self):
        if self.company_id:
            self.update(self._values_from_company(self.company_id))

    @api.depends("company_id", "environment", "provider_enabled")
    def _compute_status(self):
        base_url = self.env["ir.config_parameter"].sudo().get_str("web.base.url")
        for configuration in self:
            environment = configuration.environment or "sandbox"
            prefix = f"USL_YOUSIGN_{environment.upper()}"
            api_ready = bool(os.getenv(f"{prefix}_API_KEY"))
            webhook_ready = bool(os.getenv(f"{prefix}_WEBHOOK_SECRET"))
            live_ready = (
                environment != "production"
                or os.getenv("USL_SIGN_LIVE_ENABLED", "0") == "1"
            )
            configuration.api_credential_configured = api_ready
            configuration.webhook_secret_configured = webhook_ready
            configuration.production_gate_enabled = live_ready
            configuration.provider_ready = bool(
                configuration.provider_enabled
                and api_ready
                and webhook_ready
                and live_ready
            )
            configuration.webhook_url = (
                f"{base_url}/sign/webhooks/yousign/{configuration.company_id.id}"
                if configuration.company_id
                else False
            )

    def _check_company_access(self):
        self.ensure_one()
        if self.company_id not in self.env.companies:
            raise AccessError(self.env._("You cannot configure this company."))

    def _save_company_values(self):
        self.ensure_one()
        self._check_company_access()
        self.company_id.sudo().write(
            {
                "sign_provider_enabled": self.provider_enabled,
                "sign_yousign_environment": self.environment,
                "sign_yousign_workspace_id": self.workspace_id,
                "sign_deliver_completed_to_signers": self.deliver_completed_to_signers,
                "sign_evidence_retention_years": self.evidence_retention_years,
            }
        )

    def action_save(self):
        self._save_company_values()
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": self.env._("Sign configuration"),
                "message": self.env._("The Sign configuration was saved."),
                "type": "success",
                "sticky": False,
                "next": {"type": "ir.actions.client", "tag": "reload"},
            },
        }

    def action_test_provider(self):
        self._save_company_values()
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
