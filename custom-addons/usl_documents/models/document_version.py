"""Exact archived versions of a document."""

from odoo import api, fields, models


class UslDocumentVersion(models.Model):
    _name = "usl.document.version"
    _description = "Paperless Document File Version"
    _order = "is_current desc, created_at desc, id desc"

    document_id = fields.Many2one(
        "usl.document", required=True, index=True, ondelete="cascade", readonly=True,
    )
    paperless_version_id = fields.Char(required=True, index=True, readonly=True)
    label = fields.Char(required=True, readonly=True)
    created_at = fields.Datetime(readonly=True)
    original_filename = fields.Char(readonly=True)
    mime_type = fields.Char(readonly=True)
    checksum = fields.Char(index=True, readonly=True)
    metadata_hash = fields.Char(index=True, readonly=True, copy=False)
    archive_checksum = fields.Char(readonly=True)
    page_count = fields.Integer(readonly=True)
    is_current = fields.Boolean(index=True, readonly=True)
    is_received_original = fields.Boolean(index=True, readonly=True)
    submitted_by_id = fields.Many2one("res.users", readonly=True)
    submitted_at = fields.Datetime(readonly=True)
    source = fields.Selection(
        [
            ("odoo_upload", "Uploaded from Odoo"),
            ("odoo_attachment", "Archived Odoo attachment"),
            ("odoo_generated", "Odoo-generated authoritative output"),
            ("paperless", "External Paperless ingestion"),
        ],
        readonly=True,
    )
    original_created_at = fields.Datetime(readonly=True)
    original_modified_at = fields.Datetime(readonly=True)
    has_distinct_archive_file = fields.Boolean(
        compute="_compute_has_distinct_archive_file",
    )

    @api.depends("checksum", "archive_checksum")
    def _compute_has_distinct_archive_file(self):
        for version in self:
            version.has_distinct_archive_file = bool(
                version.archive_checksum
                and version.checksum
                and version.archive_checksum != version.checksum,
            )

    _document_version_unique = models.Constraint(
        "UNIQUE(document_id, paperless_version_id)",
        "A Paperless file version may only be mirrored once per document.",
    )

    def action_preview(self):
        self.ensure_one()
        self.document_id.check_access("read")
        return {
            "type": "ir.actions.act_url",
            "url": (
                f"/usl_documents/{self.document_id.id}/preview"
                f"?version={self.paperless_version_id}"
            ),
            "target": "new",
        }

    def action_download_original(self):
        self.ensure_one()
        self.document_id.check_access("read")
        return {
            "type": "ir.actions.act_url",
            "url": (
                f"/usl_documents/{self.document_id.id}/download"
                f"?original=1&version={self.paperless_version_id}"
            ),
            "target": "self",
        }

    def action_download_archive(self):
        self.ensure_one()
        self.document_id.check_access("read")
        return {
            "type": "ir.actions.act_url",
            "url": (
                f"/usl_documents/{self.document_id.id}/download"
                f"?original=0&version={self.paperless_version_id}"
            ),
            "target": "self",
        }

    def action_restore_as_current(self):
        self.ensure_one()
        return self.document_id.restore_version(self.paperless_version_id)
