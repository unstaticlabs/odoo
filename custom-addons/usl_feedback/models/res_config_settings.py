import requests

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

from odoo.addons.usl_feedback.services import GeminiClient, GeminiError

ALLOWED_MODELS = [
    ("gemini-3.7-flash", "Gemini 3.7 Flash"),
    ("gemini-3.6-flash", "Gemini 3.6 Flash"),
]


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    feedback_agent_enabled = fields.Boolean(
        string="Enable the feedback assistant",
        config_parameter="usl_feedback.gemini_enabled",
    )
    feedback_paid_tier_confirmed = fields.Boolean(
        string="Paid tier and seven-day retention confirmed",
        config_parameter="usl_feedback.gemini_paid_tier_confirmed",
    )
    feedback_gemini_model = fields.Selection(
        ALLOWED_MODELS,
        string="Gemini model",
        default="gemini-3.7-flash",
        config_parameter="usl_feedback.gemini_model",
    )
    feedback_mcp_url = fields.Char(
        string="Projects MCP URL",
        config_parameter="usl_feedback.mcp_url",
    )
    feedback_gemini_api_key_input = fields.Char(string="New Gemini API key")
    feedback_mcp_api_key_input = fields.Char(string="New Odoo API key")
    feedback_has_gemini_api_key = fields.Boolean(compute="_compute_feedback_secret_status")
    feedback_has_mcp_api_key = fields.Boolean(compute="_compute_feedback_secret_status")
    feedback_connection_status = fields.Selection(
        [
            ("not_tested", "Not tested"),
            ("ready", "Ready"),
            ("error", "Failed"),
        ],
        compute="_compute_feedback_connection_status",
    )
    feedback_connection_checked_at = fields.Datetime(
        compute="_compute_feedback_connection_status",
    )
    feedback_connection_detail = fields.Char(compute="_compute_feedback_connection_status")

    @api.depends_context("uid")
    def _compute_feedback_secret_status(self):
        params = self.env["ir.config_parameter"].sudo()
        gemini = bool(params.get_str("usl_feedback.gemini_api_key"))
        mcp = bool(params.get_str("usl_feedback.mcp_api_key"))
        for settings in self:
            settings.feedback_has_gemini_api_key = gemini
            settings.feedback_has_mcp_api_key = mcp

    @api.depends_context("uid")
    def _compute_feedback_connection_status(self):
        params = self.env["ir.config_parameter"].sudo()
        status = params.get_str("usl_feedback.connection_status") or "not_tested"
        if status not in {"not_tested", "ready", "error"}:
            status = "not_tested"
        checked_at = fields.Datetime.to_datetime(
            params.get_str("usl_feedback.connection_checked_at") or False,
        )
        detail = params.get_str("usl_feedback.connection_detail") or False
        for settings in self:
            settings.feedback_connection_status = status
            settings.feedback_connection_checked_at = checked_at
            settings.feedback_connection_detail = detail

    def set_values(self):
        self.ensure_one()
        if self.feedback_mcp_url:
            try:
                self.feedback_mcp_url = GeminiClient.validate_mcp_url(self.feedback_mcp_url)
            except ValueError as error:
                raise ValidationError(str(error)) from error
        if self.feedback_agent_enabled and not self.feedback_paid_tier_confirmed:
            raise ValidationError(
                _("Confirm the paid Gemini tier and seven-day retention before you enable the assistant."),
            )
        super().set_values()
        params = self.env["ir.config_parameter"].sudo()
        if self.feedback_gemini_api_key_input:
            params.set_str(
                "usl_feedback.gemini_api_key", self.feedback_gemini_api_key_input.strip(),
            )
        if self.feedback_mcp_api_key_input:
            params.set_str("usl_feedback.mcp_api_key", self.feedback_mcp_api_key_input.strip())
        self.sudo().write(
            {
                "feedback_gemini_api_key_input": False,
                "feedback_mcp_api_key_input": False,
            },
        )
        self._set_feedback_connection_status("not_tested", False)

    def action_clear_feedback_gemini_key(self):
        self.ensure_one()
        params = self.env["ir.config_parameter"].sudo()
        params.set_str("usl_feedback.gemini_api_key", None)
        params.set_bool("usl_feedback.gemini_enabled", False)
        self._set_feedback_connection_status("not_tested", False)
        return self._feedback_notification(
            _("Gemini API key removed"),
            _("The assistant is off until you save a new key."),
            "warning",
        )

    def action_clear_feedback_mcp_key(self):
        self.ensure_one()
        params = self.env["ir.config_parameter"].sudo()
        params.set_str("usl_feedback.mcp_api_key", None)
        params.set_bool("usl_feedback.gemini_enabled", False)
        self._set_feedback_connection_status("not_tested", False)
        return self._feedback_notification(
            _("Odoo API key removed"),
            _("The assistant is off until you save a new key."),
            "warning",
        )

    def action_test_feedback_agent(self):
        self.ensure_one()
        params = self.env["ir.config_parameter"].sudo()
        api_key = self.feedback_gemini_api_key_input or params.get_str(
            "usl_feedback.gemini_api_key",
        )
        mcp_key = self.feedback_mcp_api_key_input or params.get_str("usl_feedback.mcp_api_key")
        mcp_url = self.feedback_mcp_url or params.get_str("usl_feedback.mcp_url")
        if not all((api_key, mcp_key, mcp_url, self.feedback_gemini_model)):
            self._set_feedback_connection_status(
                "error", _("Save both API keys, the Projects MCP URL, and a Gemini model first."),
            )
            return self._feedback_notification(
                _("Connection test failed"),
                _("Save both API keys, the Projects MCP URL, and a Gemini model first."),
                "danger",
            )
        try:
            mcp_url = GeminiClient.validate_mcp_url(mcp_url)
            response = requests.post(
                mcp_url,
                headers={
                    "Content-Type": "application/json",
                    "Accept": "application/json, text/event-stream",
                    "X-Odoo-Url": params.get_str("web.base.url"),
                    "X-Odoo-Database": self.env.cr.dbname,
                    "X-Odoo-Api-Key": mcp_key,
                },
                json={
                    "jsonrpc": "2.0",
                    "id": "odoo-feedback-test",
                    "method": "initialize",
                    "params": {
                        "protocolVersion": "2025-03-26",
                        "capabilities": {},
                        "clientInfo": {"name": "odoo-feedback", "version": "1"},
                    },
                },
                timeout=(5, 15),
                allow_redirects=False,
            )
            response.raise_for_status()
            client = GeminiClient(api_key=api_key)
            client.test_model(self.feedback_gemini_model)
            client.test_mcp_interaction(
                model=self.feedback_gemini_model,
                mcp_url=mcp_url,
                mcp_headers={
                    "X-Odoo-Url": params.get_str("web.base.url"),
                    "X-Odoo-Database": self.env.cr.dbname,
                    "X-Odoo-Api-Key": mcp_key,
                },
            )
        except (GeminiError, ValueError, requests.RequestException) as error:
            detail = _(
                "Connection test failed: %(reason)s",
                reason=str(error),
            )
            self._set_feedback_connection_status("error", detail)
            return self._feedback_notification(
                _("Connection test failed"), detail, "danger",
            )
        self._set_feedback_connection_status(
            "ready", _("Gemini can read the feedback project through the read-only connection."),
        )
        return self._feedback_notification(
            _("Feedback assistant is ready"),
            _("Gemini can read the feedback project through the read-only connection."),
            "success",
        )

    def _set_feedback_connection_status(self, status, detail):
        params = self.env["ir.config_parameter"].sudo()
        checked_at = fields.Datetime.now() if status != "not_tested" else False
        params.set_str("usl_feedback.connection_status", status)
        params.set_str(
            "usl_feedback.connection_checked_at",
            fields.Datetime.to_string(checked_at) if checked_at else None,
        )
        params.set_str(
            "usl_feedback.connection_detail",
            str(detail)[:300] if detail else None,
        )
        self.feedback_connection_status = status
        self.feedback_connection_checked_at = checked_at
        self.feedback_connection_detail = detail or False

    @staticmethod
    def _feedback_notification(title, message, notification_type):
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": title,
                "message": message,
                "type": notification_type,
                "sticky": False,
            },
        }
