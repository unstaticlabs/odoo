from odoo import api, fields, models
from odoo.exceptions import ValidationError


class UslDocumentLink(models.Model):
    _inherit = "usl.document.link"

    @api.model
    def _allowed_models(self):
        return super()._allowed_models() | {"sign.oca.request"}


class SignRequestArchive(models.Model):
    _inherit = "sign.oca.request"

    archive_operation_id = fields.Many2one(
        "usl.document.operation", readonly=True, copy=False, ondelete="restrict",
    )
    archive_document_id = fields.Many2one(
        "usl.document", readonly=True, copy=False, ondelete="restrict",
        string="Archived signed document",
        help="The final validated signed PDF. This is the primary archived document.",
    )
    dossier_archive_operation_id = fields.Many2one(
        "usl.document.operation", readonly=True, copy=False, ondelete="restrict",
    )
    archive_dossier_document_id = fields.Many2one(
        "usl.document", readonly=True, copy=False, ondelete="restrict",
        string="Archived proof package",
        help=(
            "The companion PDF/A-3 proof package containing the signed PDF, "
            "source files, completion certificate, manifest, and validation reports."
        ),
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
        self._check_prepare_access()
        for request in self:
            if (
                request.state != "evidence_incomplete"
                or request.archive_status != "failed"
            ):
                msg = "Final storage can only be retried after an archival failure."
                raise ValidationError(msg)
            request._archive_dossier(force=True)
        return True

    @api.model
    def _cron_reconcile_archives(self):
        requests = self.search(
            [("archive_status", "in", ["pending", "processing", "failed"])], limit=100,
        )
        requests._reconcile_archive()
