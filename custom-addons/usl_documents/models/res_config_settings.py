from odoo import _, fields, models

from .paperless_client import PaperlessClient, PaperlessError


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    paperless_url = fields.Char(
        string="Paperless internal URL",
        config_parameter="usl_documents.paperless_url",
        help="Container-network URL used only by the Odoo server.",
    )
    paperless_public_url = fields.Char(
        string="Paperless public URL",
        config_parameter="usl_documents.paperless_public_url",
        help="Secured user-facing URL used for Open in Paperless.",
    )
    paperless_token = fields.Char(
        string="Paperless integration token",
        config_parameter="usl_documents.paperless_token",
    )
    paperless_timeout = fields.Integer(
        string="Paperless timeout",
        config_parameter="usl_documents.paperless_timeout",
        default=20,
    )
    paperless_stream_timeout = fields.Integer(
        string="Paperless streaming timeout",
        config_parameter="usl_documents.paperless_stream_timeout",
        default=60,
        help="Maximum server-to-server read time for one streamed binary request.",
    )
    paperless_service_user_id = fields.Integer(
        string="Paperless service user ID",
        config_parameter="usl_documents.paperless_service_user_id",
        help="Dedicated non-human Paperless owner used by server-side integration.",
    )
    paperless_last_sync = fields.Char(
        string="Last successful synchronization",
        compute="_compute_paperless_status",
    )
    paperless_health = fields.Char(compute="_compute_paperless_status")

    def _compute_paperless_status(self):
        params = self.env["ir.config_parameter"].sudo()
        last_sync = params.get_str("usl_documents.last_sync") or _("Never")
        for settings in self:
            settings.paperless_last_sync = last_sync
            client = PaperlessClient(self.env)
            if not client.configured:
                settings.paperless_health = _("Not configured")
                continue
            try:
                compatibility = client.compatibility()
                settings.paperless_health = _(
                    "Healthy — Paperless %(server)s, API %(api)s",
                ) % {
                    "server": compatibility["server_version"],
                    "api": compatibility["api_version"],
                }
            except PaperlessError as error:
                settings.paperless_health = str(error)

    def action_test_paperless(self):
        self.ensure_one()
        compatibility = PaperlessClient(self.env).compatibility()
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Paperless connection is healthy"),
                "message": _("Server %(server)s, API %(api)s")
                % {
                    "server": compatibility["server_version"],
                    "api": compatibility["api_version"],
                },
                "type": "success",
                "sticky": False,
            },
        }

    def action_install_paperless_policy(self):
        self.ensure_one()
        result = PaperlessClient(self.env).ensure_fail_closed_ingestion_policy()
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Paperless ingestion policy is active"),
                "message": _(
                    "Workflow %(workflow)s assigns every ingestion channel to "
                    "service identity %(owner)s until Odoo synchronizes access.",
                )
                % {
                    "workflow": result["workflow_name"],
                    "owner": result["owner_user_id"],
                },
                "type": "success",
                "sticky": False,
            },
        }
