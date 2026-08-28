from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError


class UslDocumentLink(models.Model):
    _inherit = "usl.document.link"

    @api.model
    def _allowed_models(self):
        return super()._allowed_models() | {"sign.oca.request"}


class SignRequestArchive(models.Model):
    _name = "sign.oca.request"
    _inherit = ["sign.oca.request", "usl.document.link.mixin"]

    archive_operation_id = fields.Many2one(
        "usl.document.operation", readonly=True, copy=False, ondelete="restrict",
    )
    archive_document_id = fields.Many2one(
        "usl.document", readonly=True, copy=False, ondelete="restrict",
        string="Archived signed document",
        help=(
            "The primary archived signed PDF. Native records point to the final "
            "validated result; external records point to the exact source-system result."
        ),
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
            " For an external record, this is the source system's original certificate."
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

    def action_refresh_archive(self):
        self._check_prepare_access()
        self._reconcile_archive()
        return {"type": "ir.actions.client", "tag": "reload"}

    def _action_open_archived_file(self, field_name):
        self.ensure_one()
        document = self[field_name]
        if not document:
            raise UserError(_("This archived file is not available yet."))
        document.check_access("read")
        return {
            "type": "ir.actions.act_window",
            "name": document.name,
            "res_model": "usl.document",
            "res_id": document.id,
            "views": [
                (self.env.ref("usl_documents.view_usl_document_form").id, "form"),
            ],
            "target": "current",
        }

    def action_open_archived_signed_document(self):
        return self._action_open_archived_file("archive_document_id")

    def action_open_archived_proof_package(self):
        return self._action_open_archived_file("archive_dossier_document_id")

    def _share_archived_files_with_participants(self):
        """Expose final private files only to this request's internal participants."""
        archive_actor = self.env.ref("base.user_root")
        for request in self:
            partners = (
                request.user_id.partner_id
                | request.coordinator_ids.partner_id
                | request.signer_ids.partner_id
            )
            for document in (
                request.archive_document_id | request.archive_dossier_document_id
            ):
                secured_document = (
                    document.with_user(archive_actor)
                    .sudo()
                    .with_company(request.company_id)
                )
                for partner in partners:
                    secured_document.link_to_record("res.partner", partner.id)
                # The new participant links change the effective Paperless ACL.
                # Synchronize even when initial ingestion already marked the
                # service-owned document permissions as current.
                secured_document.action_sync_permissions()

    @api.model
    def _cron_reconcile_archives(self):
        requests = self.search(
            [("archive_status", "in", ["pending", "processing", "failed"])], limit=100,
        )
        requests._reconcile_archive()
