from odoo import api, fields, models


class UslDocumentLink(models.Model):
    _inherit = "usl.document.link"

    @api.model
    def _allowed_models(self):
        return super()._allowed_models() | {"sign.oca.request", "usl.sign.approval"}


class SignRequestArchive(models.Model):
    _inherit = "sign.oca.request"

    archive_operation_id = fields.Many2one(
        "usl.document.operation", readonly=True, copy=False, ondelete="restrict",
    )
    archive_document_id = fields.Many2one(
        "usl.document", readonly=True, copy=False, ondelete="restrict",
    )
    archive_status = fields.Selection(
        [
            ("not_ready", "Not ready"),
            ("pending", "Pending"),
            ("processing", "Processing"),
            ("archived", "Archived"),
            ("failed", "Failed"),
        ],
        default="not_ready",
        required=True,
        readonly=True,
        copy=False,
    )
    archive_last_error = fields.Text(readonly=True, copy=False)

    def action_retry_archive(self):
        for request in self:
            request._archive_dossier(force=True)
        return True

    @api.model
    def _cron_reconcile_archives(self):
        requests = self.search(
            [("archive_status", "in", ["pending", "processing", "failed"])], limit=100,
        )
        requests._reconcile_archive()
