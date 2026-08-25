import base64

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError

from odoo.addons.usl_documents.models.paperless_client import PaperlessError


class AccountBankStatement(models.Model):
    _name = "account.bank.statement"
    _inherit = ["account.bank.statement", "usl.document.link.mixin"]

    bank_evidence_archive_state = fields.Selection(
        [
            ("not_requested", "Not sent"),
            ("pending", "Waiting for archive"),
            ("processing", "Archiving"),
            ("archived", "Archived"),
            ("failed", "Archive failed"),
        ],
        compute="_compute_bank_evidence_archive",
    )
    bank_evidence_archive_error = fields.Char(compute="_compute_bank_evidence_archive")

    @api.depends(
        "accepted_evidence_id.paperless_archive_state",
        "accepted_evidence_id.paperless_archive_error",
    )
    def _compute_bank_evidence_archive(self):
        for statement in self:
            evidence = statement.accepted_evidence_id
            statement.bank_evidence_archive_state = (
                evidence.paperless_archive_state if evidence else "not_requested"
            )
            statement.bank_evidence_archive_error = (
                evidence.paperless_archive_error if evidence else False
            )


class AccountBankIngestionFile(models.Model):
    _inherit = "account.bank.ingestion.file"

    paperless_archive_state = fields.Selection(
        [
            ("not_requested", "Not sent"),
            ("pending", "Waiting for archive"),
            ("processing", "Archiving"),
            ("archived", "Archived"),
            ("failed", "Archive failed"),
        ],
        default="not_requested",
        required=True,
        copy=False,
        readonly=True,
    )
    paperless_operation_id = fields.Many2one(
        "usl.document.operation",
        copy=False,
        readonly=True,
        ondelete="set null",
    )
    paperless_document_id = fields.Many2one(
        "usl.document",
        copy=False,
        readonly=True,
        ondelete="set null",
    )
    paperless_archive_error = fields.Char(copy=False, readonly=True)

    def _bank_evidence_accepted(self):
        result = super()._bank_evidence_accepted()
        self.sudo().write(
            {
                "paperless_archive_state": "pending",
                "paperless_operation_id": False,
                "paperless_archive_error": False,
            },
        )
        return result

    @api.model
    def _cron_archive_accepted_bank_evidence(self, limit=10):
        files = self.sudo().search(
            [
                ("classification", "=", "pdf"),
                ("evidence_status", "=", "accepted"),
                ("paperless_archive_state", "in", ("pending", "processing")),
            ],
            order="id",
            limit=limit,
        )
        for source_file in files:
            try:
                with self.env.cr.savepoint():
                    source_file._archive_or_reconcile_bank_evidence()
            except (PaperlessError, AccessError, UserError) as error:
                source_file.sudo().write(
                    {
                        "paperless_archive_state": "failed",
                        "paperless_archive_error": str(error),
                    },
                )

    def _archive_or_reconcile_bank_evidence(self):
        self.ensure_one()
        if self.paperless_archive_state == "processing":
            return self._reconcile_bank_evidence_operation()
        statement = self.statement_id
        if not statement or statement.accepted_evidence_id != self:
            return None
        content_base64 = base64.b64encode(self._content()).decode()
        existing_link = (
            self.env["usl.document.link"]
            .sudo()
            .search(
                [
                    ("res_model", "=", statement._name),
                    ("res_id", "=", statement.id),
                    ("active", "=", True),
                ],
                order="id",
                limit=1,
            )
        )
        try:
            if existing_link:
                result = existing_link.document_id.with_user(
                    self.ingestion_id.config_id.responsible_user_id,
                ).upload_new_version(
                    self.filename,
                    content_base64,
                    self.mimetype or "application/pdf",
                    version_label=_(
                        "Bank statement %(period)s",
                        period=self.period_end,
                    ),
                )
            else:
                result = (
                    self.env["usl.document"]
                    .with_user(self.ingestion_id.config_id.responsible_user_id)
                    .upload_from_odoo(
                        self.filename,
                        content_base64,
                        self.mimetype or "application/pdf",
                        res_model=statement._name,
                        res_id=statement.id,
                        company_id=self.company_id.id,
                        source="odoo_attachment",
                    )
                )
        except (PaperlessError, AccessError, UserError) as error:
            self.sudo().write(
                {
                    "paperless_archive_state": "failed",
                    "paperless_archive_error": str(error),
                },
            )
            return None
        operation = (
            self.env["usl.document.operation"].sudo().browse(result.get("operation_id"))
        )
        document = self.env["usl.document"].sudo().browse(result.get("document_id"))
        self.sudo().write(
            {
                "paperless_archive_state": (
                    "archived"
                    if result.get("state") == "duplicate" and document
                    else "processing"
                ),
                "paperless_operation_id": operation.id,
                "paperless_document_id": document.id,
                "paperless_archive_error": False,
            },
        )
        if document:
            self._pin_paperless_version(document)
        return None

    def _reconcile_bank_evidence_operation(self):
        self.ensure_one()
        operation = self.paperless_operation_id.sudo()
        if operation and operation.state == "processing":
            operation.poll()
        if operation and operation.state == "failed":
            self.sudo().write(
                {
                    "paperless_archive_state": "failed",
                    "paperless_archive_error": operation.error_message,
                },
            )
            return
        document = (
            operation.document_id
            or operation.target_document_id
            or self.paperless_document_id
        )
        if not document:
            return
        self._pin_paperless_version(document)

    def _pin_paperless_version(self, document):
        self.ensure_one()
        document = document.sudo()
        version = document.version_ids.filtered(
            lambda item: item.checksum == self.sha256,
        )[:1]
        self.sudo().write(
            {
                "paperless_archive_state": "archived",
                "paperless_document_id": document.id,
                "paperless_version": version.paperless_version_id or False,
                "paperless_archive_error": False,
            },
        )


class UslDocumentLink(models.Model):
    _inherit = "usl.document.link"

    @api.model
    def _allowed_models(self):
        return super()._allowed_models() | {"account.bank.statement"}
