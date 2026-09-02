import base64

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError

from odoo.addons.usl_documents.models.paperless_client import PaperlessError


class AccountBankStatement(models.Model):
    _name = "account.bank.statement"
    _inherit = ["account.bank.statement", "usl.document.link.mixin"]

    bank_evidence_document_id = fields.Many2one(
        "usl.document",
        string="Documents record",
        related="accepted_evidence_id.paperless_document_id",
        readonly=True,
    )
    bank_evidence_document_version = fields.Char(
        string="Official version",
        related="accepted_evidence_id.paperless_version",
        readonly=True,
    )

    bank_evidence_archive_state = fields.Selection(
        [
            ("not_requested", "Not in Documents"),
            ("pending", "Waiting for Documents"),
            ("processing", "Filing in Documents"),
            ("archived", "Available"),
            ("unavailable", "Documents unavailable"),
            ("failed", "Needs attention"),
        ],
        string="Documents status",
        compute="_compute_bank_evidence_archive",
    )
    bank_evidence_archive_error = fields.Char(
        string="Documents issue",
        compute="_compute_bank_evidence_archive",
    )

    def _document_archive_policy(self, attachment):
        policy = super()._document_archive_policy(attachment)
        self.ensure_one()
        evidence = self.accepted_evidence_id
        if evidence and attachment == evidence.attachment_id:
            policy.update(
                {
                    "archive_mode": "never",
                    "document_role": "evidence",
                    "policy_reason": "managed_bank_statement_evidence",
                    "confidentiality": "accounting",
                    "accounting_evidence": True,
                },
            )
        return policy

    @api.depends(
        "accepted_evidence_id.paperless_archive_state",
        "accepted_evidence_id.paperless_archive_error",
        "accepted_evidence_id.paperless_document_id.availability_state",
        "accepted_evidence_id.paperless_document_id.accounting_evidence",
        "accepted_evidence_id.paperless_document_id.confidentiality",
        "accepted_evidence_id.paperless_document_id.permission_sync_state",
        "accepted_evidence_id.paperless_document_id.retention_hold",
        "accepted_evidence_id.paperless_document_id.review_state",
        "accepted_evidence_id.paperless_document_id.tag_ids",
    )
    def _compute_bank_evidence_archive(self):
        for statement in self:
            evidence = statement.accepted_evidence_id
            state = evidence.paperless_archive_state if evidence else "not_requested"
            error = evidence.paperless_archive_error if evidence else False
            pdf_error = (
                evidence._pdf_integrity_error(evidence._content())
                if evidence and evidence.classification == "pdf"
                else False
            )
            if pdf_error:
                state = "failed"
                error = pdf_error
            integrity_error = (
                evidence._paperless_integrity_error()
                if evidence and state == "archived"
                else False
            )
            if integrity_error:
                state = "unavailable"
                error = integrity_error
            statement.bank_evidence_archive_state = state
            statement.bank_evidence_archive_error = error

    def _additional_bank_review_blockers(self):
        self.ensure_one()
        blockers = super()._additional_bank_review_blockers()
        evidence = self.accepted_evidence_id
        if not evidence:
            return blockers
        integrity_error = evidence._paperless_integrity_error()
        if integrity_error:
            blockers.append(integrity_error)
        return blockers

    def _bank_evidence_snapshot_values(self):
        self.ensure_one()
        values = super()._bank_evidence_snapshot_values()
        values["paperless_document_id"] = (
            self.accepted_evidence_id.paperless_document_id.id
        )
        return values

    def action_archive_bank_evidence(self):
        self.ensure_one()
        self._assert_account_user()
        evidence = self.accepted_evidence_id
        if not evidence:
            raise UserError(_("Accept the official statement before saving it in Documents."))
        evidence._process_bank_evidence_archive()
        self.invalidate_recordset()
        if evidence.paperless_archive_state == "failed":
            return self._bank_review_notification(
                evidence.paperless_archive_error
                or _(
                    "Documents could not save the official PDF. Try again; if the "
                    "problem continues, replace the PDF with the original from the bank.",
                ),
                "danger",
            )
        if evidence.paperless_archive_state == "archived":
            return self._bank_review_notification(
                _("The official statement is saved in Documents and its exact version is verified."),
                "success",
            )
        return self._bank_review_notification(
            _(
                "The official statement was sent to Documents. Certification becomes "
                "available after the exact saved version is verified.",
            ),
            "info",
        )

    def action_open_evidence(self):
        self.ensure_one()
        evidence = self.accepted_evidence_id
        if not evidence:
            return super().action_open_evidence()
        integrity_error = evidence._paperless_integrity_error()
        if integrity_error:
            raise UserError(integrity_error)
        document = evidence.paperless_document_id
        document.check_access("read")
        # The action definition itself is system metadata. Evidence access was
        # already checked on the governed Documents record above.
        action = (
            self.env.ref("usl_documents.action_documents_workspace").sudo().read()[0]
        )
        action["params"] = {
            "res_model": self._name,
            "res_id": self.id,
            "record_name": self.display_name,
            "linked_filter": True,
            "initial_document_id": document.id,
            "initial_version_id": evidence.paperless_version,
        }
        return action


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
        check_company=True,
        ondelete="set null",
    )
    paperless_document_id = fields.Many2one(
        "usl.document",
        copy=False,
        readonly=True,
        check_company=True,
        ondelete="set null",
    )
    paperless_archive_error = fields.Char(copy=False, readonly=True)
    paperless_requested_by_id = fields.Many2one(
        "res.users",
        copy=False,
        readonly=True,
    )
    paperless_archived_at = fields.Datetime(copy=False, readonly=True)

    def _bank_evidence_accepted(self):
        result = super()._bank_evidence_accepted()
        self.sudo().write(
            {
                "paperless_archive_state": "pending",
                "paperless_operation_id": False,
                "paperless_archive_error": False,
                "paperless_requested_by_id": self.env.user.id,
                "paperless_archived_at": False,
                "paperless_version": False,
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
            source_file._process_bank_evidence_archive()

    def _process_bank_evidence_archive(self):
        for source_file in self:
            self.env.cr.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                [f"account.bank.ingestion.file.archive:{source_file.id}"],
            )
            source_file.invalidate_recordset()
            if not source_file._paperless_integrity_error():
                continue
            if source_file.paperless_archive_state == "failed":
                source_file.sudo().write(
                    {
                        "paperless_archive_state": "pending",
                        "paperless_operation_id": False,
                        "paperless_archive_error": False,
                    },
                )
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
        return True

    def _archive_or_reconcile_bank_evidence(self):
        self.ensure_one()
        pdf_error = self._pdf_integrity_error(self._content())
        if pdf_error:
            self.sudo().write(
                {
                    "paperless_archive_state": "failed",
                    "paperless_archive_error": pdf_error,
                },
            )
            return None
        if self.paperless_archive_state == "processing":
            return self._reconcile_bank_evidence_operation()
        statement = self.statement_id
        if not statement or statement.accepted_evidence_id != self:
            return None
        exact_link = self._exact_linked_evidence()
        if exact_link:
            competing_links = self.env["usl.document.link"].sudo().search(
                [
                    ("res_model", "=", statement._name),
                    ("res_id", "=", statement.id),
                    ("active", "=", True),
                    ("id", "!=", exact_link.id),
                ],
            )
            if competing_links:
                competing_links.sudo().write({"active": False})
                statement.message_post(
                    body=_(
                        "Documents evidence repaired: the exact official statement "
                        "version was retained and %(count)s conflicting link(s) were "
                        "deactivated without deleting their documents.",
                        count=len(competing_links),
                    ),
                )
            self._pin_paperless_version(exact_link.document_id)
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
                result = existing_link.document_id.sudo().upload_new_version(
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
                    .sudo()
                    .upload_from_odoo(
                        self.filename,
                        content_base64,
                        self.mimetype or "application/pdf",
                        company_id=self.company_id.id,
                        confidentiality="accounting",
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
        if result.get("state") == "duplicate" and not document:
            raise UserError(
                result.get("message")
                or _(
                    "Documents found an existing copy that requires administrator "
                    "review before it can support this statement.",
                ),
            )
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

    def _exact_linked_evidence(self):
        """Return the sole linked document version matching the retained PDF."""
        self.ensure_one()
        links = self.env["usl.document.link"].sudo().search(
            [
                ("res_model", "=", self.statement_id._name),
                ("res_id", "=", self.statement_id.id),
                ("active", "=", True),
            ],
        )
        exact_links = links.filtered(
            lambda link: any(
                version.paperless_version_id == link.version_id
                and version.checksum == self.sha256
                for version in link.document_id.version_ids
            ),
        )
        if len(exact_links) > 1:
            raise UserError(
                _(
                    "Several Documents records are linked to this statement with "
                    "the exact official PDF. A Documents administrator must choose "
                    "one before retrying.",
                ),
            )
        return exact_links

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

    def _paperless_version_record(self):
        self.ensure_one()
        document = self.paperless_document_id.sudo()
        if not document or not self.paperless_version:
            return self.env["usl.document.version"]
        version = document.version_ids.filtered(
            lambda item: (
                item.paperless_version_id == self.paperless_version
                and item.checksum == self.sha256
            ),
        )[:1]
        # Return the matching cache row in the caller's environment. The checksum
        # comparison may use elevated rights, but preview/download must still apply
        # the accountant's Documents record rules.
        return self.env["usl.document.version"].browse(version.id)

    def _paperless_integrity_error(self):
        self.ensure_one()
        pdf_error = self._pdf_integrity_error(self._content())
        if pdf_error:
            return pdf_error
        if self.paperless_archive_state == "failed":
            return self.paperless_archive_error or _(
                "Documents could not save the official PDF. Try again; if the "
                "problem continues, replace the PDF with the original from the bank.",
            )
        if self.paperless_archive_state == "not_requested":
            return _(
                "The official PDF has been received but is not yet saved in Documents. "
                "Choose Save in Documents.",
            )
        if self.paperless_archive_state in ("pending", "processing"):
            return _(
                "Documents is saving and verifying the official PDF. Wait for this "
                "check to finish before certification.",
            )
        document = self.paperless_document_id.sudo()
        if not document or not self.paperless_version or not self._paperless_version_record():
            return _(
                "Documents has not verified a version with the exact official "
                "statement checksum.",
            )
        if document.company_id != self.company_id:
            return _("The Documents record belongs to a different company.")
        if (
            document.confidentiality != "accounting"
            or not document.accounting_evidence
            or document.review_state != "reviewed"
        ):
            return _(
                "Classify and review the original in Documents as accounting evidence "
                "before certification.",
            )
        if not document.retention_hold:
            return _(
                "Apply the accounting evidence retention hold before certification.",
            )
        if document.availability_state != "available":
            return _(
                "Restore the original in Documents before certification.",
            )
        if document.permission_sync_state != "synchronized":
            return _(
                "Synchronize access to the original in Documents before "
                "certification.",
            )
        banking_tags = self._paperless_banking_tags()
        if not banking_tags:
            return _(
                "Configure the Documents Banking classification before certification.",
            )
        if banking_tags - document.tag_ids:
            return _(
                "Classify the original in Documents as a Banking document before "
                "certification.",
            )
        link = self.env["usl.document.link"].sudo().search(
            [
                ("document_id", "=", document.id),
                ("res_model", "=", self.statement_id._name),
                ("res_id", "=", self.statement_id.id),
                ("active", "=", True),
            ],
            limit=1,
        )
        if not link or link.version_id != self.paperless_version:
            return _(
                "The exact Documents version is not linked to this bank statement.",
            )
        return False

    def _paperless_banking_tags(self):
        """Return the Paperless-owned metadata behind the Banking workspace."""
        self.ensure_one()
        banking_view = (
            self.env["usl.document.smart.view"]
            .sudo()
            .search(
                [
                    ("key", "=", "banking"),
                    ("active", "=", True),
                ],
                limit=1,
            )
        )
        return banking_view.tag_ids.filtered("active")

    def _pin_paperless_version(self, document):
        self.ensure_one()
        document = document.sudo()
        if document.company_id and document.company_id != self.company_id:
            raise UserError(
                _("The archived document belongs to a different company."),
            )
        version = document.version_ids.filtered(
            lambda item: item.checksum == self.sha256,
        )[:1]
        if not version:
            raise UserError(
                _(
                    "Documents did not return a file version matching the exact "
                    "official statement checksum.",
                ),
            )
        policy_values = {}
        if document.company_id != self.company_id:
            policy_values["company_id"] = self.company_id.id
        if document.confidentiality != "accounting":
            policy_values["confidentiality"] = "accounting"
        if not document.accounting_evidence:
            policy_values["accounting_evidence"] = True
        if not document.retention_hold:
            policy_values["retention_hold"] = True
        if document.review_state != "reviewed":
            policy_values["review_state"] = "reviewed"
        if policy_values:
            document.with_context(usl_documents_policy_write=True).write(policy_values)
        banking_tags = self._paperless_banking_tags()
        if not banking_tags:
            raise UserError(
                _(
                    "Documents has no Banking classification. Synchronize its "
                    "Paperless catalogs before retrying the archive.",
                ),
            )
        if banking_tags - document.tag_ids:
            document.with_user(self.env.ref("base.user_root")).update_archive_metadata(
                {"tag_ids": (document.tag_ids | banking_tags).ids},
            )
            document.invalidate_recordset(["tag_ids"])
        Link = self.env["usl.document.link"].sudo()
        links = Link.search(
            [
                ("res_model", "=", self.statement_id._name),
                ("res_id", "=", self.statement_id.id),
                ("active", "=", True),
            ],
        )
        if links and any(link.document_id != document for link in links):
            raise UserError(
                _(
                    "This statement is already linked to another Documents "
                    "record. A Documents administrator must resolve it.",
                ),
            )
        if links:
            links.write({"version_id": version.paperless_version_id})
        else:
            Link.create(
                {
                    "document_id": document.id,
                    "res_model": self.statement_id._name,
                    "res_id": self.statement_id.id,
                    "record_name": self.statement_id.display_name,
                    "company_id": self.company_id.id,
                    "linked_by_id": (
                        self.paperless_requested_by_id
                        or self.ingestion_id.config_id.responsible_user_id
                    ).id,
                    "version_id": version.paperless_version_id,
                },
            )
        document.with_user(self.env.ref("base.user_root")).action_sync_permissions()
        document.invalidate_recordset(["permission_sync_state"])
        if document.permission_sync_state != "synchronized":
            raise UserError(
                _(
                    "Documents stored the original but could not synchronize its "
                    "accounting access.",
                ),
            )
        self.sudo().write(
            {
                "paperless_archive_state": "archived",
                "paperless_document_id": document.id,
                "paperless_version": version.paperless_version_id,
                "paperless_archive_error": False,
                "paperless_archived_at": fields.Datetime.now(),
            },
        )


class AccountBankStatementCertification(models.Model):
    _inherit = "account.bank.statement.certification"

    paperless_document_id = fields.Many2one(
        "usl.document",
        readonly=True,
        check_company=True,
        ondelete="restrict",
    )


class UslDocumentLink(models.Model):
    _inherit = "usl.document.link"

    @api.model
    def _allowed_models(self):
        return super()._allowed_models() | {"account.bank.statement"}
