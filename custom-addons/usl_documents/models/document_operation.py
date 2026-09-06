"""Queued Paperless operations and their retry lifecycle."""

import logging
from datetime import timedelta

from odoo import (
    _,
    api,
    fields,
    models,
)
from odoo.exceptions import AccessError, UserError

from .document import CONFIDENTIALITIES
from .paperless_client import PaperlessError, PaperlessNotFound

_logger = logging.getLogger(__name__)


class UslDocumentOperation(models.Model):
    _name = "usl.document.operation"
    _description = "Document Ingestion Operation"
    _order = "create_date desc, id desc"

    _active_record_status_idx = models.Index(
        "(res_model, res_id, state) WHERE acknowledged IS NOT TRUE",
    )

    name = fields.Char(required=True, readonly=True)
    state = fields.Selection(
        [
            ("pending", "Pending"),
            ("uploading", "Uploading"),
            ("processing", "Processing"),
            ("archived", "Archived"),
            ("duplicate", "Duplicate reused"),
            ("failed", "Failed"),
        ],
        required=True,
        default="pending",
        index=True,
        readonly=True,
    )
    checksum = fields.Char(required=True, index=True, readonly=True)
    mime_type = fields.Char(readonly=True)
    company_id = fields.Many2one("res.company", required=True, readonly=True)
    confidentiality = fields.Selection(
        CONFIDENTIALITIES,
        required=True,
        default="internal",
        readonly=True,
        help="Odoo access policy captured when the ingestion request was created.",
    )
    user_id = fields.Many2one(
        "res.users", required=True, readonly=True, default=lambda self: self.env.user,
    )
    paperless_task_id = fields.Char(index=True, readonly=True)
    processing_started_at = fields.Datetime(readonly=True, index=True)
    document_id = fields.Many2one("usl.document", readonly=True, ondelete="restrict")
    target_document_id = fields.Many2one(
        "usl.document",
        string="Replacement root document",
        readonly=True,
        ondelete="restrict",
    )
    res_model = fields.Char(readonly=True)
    res_id = fields.Integer(readonly=True)
    source = fields.Selection(
        [
            ("odoo_upload", "Uploaded from Odoo"),
            ("odoo_attachment", "Archived Odoo attachment"),
            ("odoo_generated", "Odoo-generated authoritative output"),
        ],
        readonly=True,
    )
    original_created_at = fields.Datetime(readonly=True)
    original_modified_at = fields.Datetime(readonly=True)
    error_message = fields.Text(readonly=True)
    retry_count = fields.Integer(readonly=True)
    acknowledged = fields.Boolean(readonly=True)
    acknowledged_at = fields.Datetime(readonly=True)
    retry_of_id = fields.Many2one(
        "usl.document.operation", readonly=True, ondelete="set null",
    )

    @api.model_create_multi
    def create(self, values_list):
        if not self.env.su:
            raise AccessError(
                _("Ingestion operations can only be created by the upload workflow."),
            )
        now = fields.Datetime.now()
        for values in values_list:
            if values.get("state") == "processing":
                values.setdefault("processing_started_at", now)
        return super().create(values_list)

    def write(self, values):
        if not self.env.su:
            raise AccessError(
                _("Ingestion state can only be changed by the archive workflow."),
            )
        values = dict(values)
        if values.get("state") == "processing":
            values.setdefault("processing_started_at", fields.Datetime.now())
        elif "state" in values:
            values.setdefault("processing_started_at", False)
        return super().write(values)

    def _processing_is_stale(self, *, now=None):
        self.ensure_one()
        timeout_minutes = max(
            5,
            self.env["ir.config_parameter"].sudo().get_int(
                "usl_documents.processing_timeout_minutes",
                360,
            ),
        )
        started_at = self.processing_started_at or self.create_date
        return bool(
            started_at
            and started_at
            <= (now or fields.Datetime.now()) - timedelta(minutes=timeout_minutes),
        )

    def _fail_stale_processing(self):
        self.ensure_one()
        self.sudo().write(
            {
                "state": "failed",
                "error_message": _(
                    "Paperless did not return a final result before the processing "
                    "deadline. This operation was stopped instead of remaining "
                    "queued indefinitely. Check Paperless for the archived file "
                    "before retrying.",
                ),
            },
        )

    def _workspace_values(self):
        self.ensure_one()
        document = self.document_id or self.target_document_id
        return {
            "id": self.id,
            "name": self.name,
            "state": self.state,
            "status_label": {
                "pending": _("Queued"),
                "uploading": _("Sending to Documents"),
                "processing": _("Indexing in Documents"),
                "archived": _("Archived"),
                "duplicate": _("Needs attention"),
                "failed": _("Failed"),
            }[self.state],
            "error": self.error_message,
            "document_id": self.document_id.id,
            "document_name": document.name or self.name,
            "target_document_id": self.target_document_id.id,
            "created_at": self.create_date,
            "retry_count": self.retry_count,
        }

    @api.model
    def current_workspace_operation(self):
        if not self.env.user.has_group("usl_documents.group_documents_user"):
            return False
        operation = self.search(
            [("state", "in", ("pending", "uploading", "processing"))],
            order="create_date desc, id desc",
            limit=1,
        )
        return operation._workspace_values() if operation else False

    @api.model
    def workspace_failures(self):
        if not self.env.user.has_group("usl_documents.group_documents_user"):
            return []
        return [
            operation._workspace_values()
            for operation in self.search(
                [
                    ("state", "in", ("failed", "duplicate")),
                    ("acknowledged", "=", False),
                ],
                order="create_date desc, id desc",
                limit=20,
            )
        ]

    def acknowledge(self):
        acknowledged = self.filtered(
            lambda operation: operation.user_id == self.env.user
            or self.env.user.has_group("usl_documents.group_documents_manager"),
        )
        if len(acknowledged) != len(self):
            raise AccessError(_("You can only dismiss your own ingestion messages."))
        acknowledged.sudo().write(
            {
                "acknowledged": True,
                "acknowledged_at": fields.Datetime.now(),
            },
        )
        return True

    def _missing_related_record(self, archive_context):
        """Return the first deleted relationship required by this operation."""
        self.ensure_one()
        targets = list(archive_context.get("related_records") or [])
        if not targets and self.res_model and self.res_id:
            targets = [{"model": self.res_model, "id": self.res_id}]
        for target in targets:
            model_name = target.get("model")
            record_id = int(target.get("id") or 0)
            if (
                not model_name
                or model_name not in self.env
                or not record_id
                or not self.env[model_name].sudo().browse(record_id).exists()
            ):
                return model_name, record_id
        return False

    def _source_record_missing_message(self, missing):
        self.ensure_one()
        model_name, record_id = missing
        model = (
            self.env["ir.model"]._get(model_name)
            if model_name and model_name in self.env
            else False
        )
        label = model.name if model else model_name or _("business record")
        return _(
            "The source %(model)s #%(record_id)s was deleted before Documents "
            "finished linking the file. The archived file was kept for review; "
            "later uploads continue normally.",
            model=label,
            record_id=record_id,
        )

    def poll(self):
        for operation in self.filtered(
            lambda item: item.state == "processing" and item.paperless_task_id,
        ):
            try:
                task = self.env["usl.document"]._paperless().task(
                    operation.paperless_task_id,
                )
            except PaperlessError as error:
                if operation._processing_is_stale():
                    operation._fail_stale_processing()
                else:
                    operation.sudo().write({"error_message": str(error)})
                continue
            if not task:
                if operation._processing_is_stale():
                    operation._fail_stale_processing()
                continue
            status = str(task.get("status") or "").lower()
            if status in ("success", "successful"):
                related_ids = task.get("related_document_ids") or []
                paperless_id = (
                    related_ids[0]
                    if related_ids
                    else task.get("related_document")
                    or task.get("result")
                )
                if isinstance(paperless_id, str) and paperless_id.isdigit():
                    paperless_id = int(paperless_id)
                if not paperless_id and operation.target_document_id:
                    paperless_id = operation.target_document_id.paperless_id
                if not paperless_id:
                    continue
                if operation.target_document_id:
                    # The supported update-version task returns the newly
                    # consumed child document ID. Child versions are not exposed
                    # as root resources by /api/documents/{id}/, so refresh the
                    # root from the endpoint that created this task.
                    paperless_id = operation.target_document_id.paperless_id
                archive_context = getattr(operation, "context_json", False) or {}
                missing = operation._missing_related_record(archive_context)
                client = self.env["usl.document"]._paperless()
                try:
                    payload = client.get_document(paperless_id)
                except PaperlessNotFound:
                    operation.sudo().write(
                        {
                            "state": "failed",
                            "error_message": _(
                                "Paperless finished processing, but the archived "
                                "file is not accessible. Check its archive owner and "
                                "permissions, then retry.",
                            ),
                        },
                    )
                    continue
                metadata_catalog = None
                if (
                    isinstance(payload.get("correspondent"), int)
                    or isinstance(payload.get("document_type"), int)
                    or any(
                        isinstance(tag, int) for tag in (payload.get("tags") or [])
                    )
                ):
                    metadata_catalog = client.metadata_catalog()
                values = self.env["usl.document"]._paperless_values(
                    payload,
                    source=operation.source,
                    metadata_catalog=metadata_catalog,
                )
                document_cache = self.env["usl.document"].sudo()
                document = operation.target_document_id.sudo() or document_cache.search(
                    [("paperless_id", "=", paperless_id)], limit=1,
                )
                if document and not operation.target_document_id:
                    known_metadata_hashes = set(
                        document.version_ids.filtered(
                            lambda item: (
                                item.checksum == operation.checksum
                                and item.metadata_hash
                            ),
                        ).mapped("metadata_hash"),
                    )
                    if (
                        document.checksum == operation.checksum
                        and document.metadata_hash
                    ):
                        known_metadata_hashes.add(document.metadata_hash)
                    if (
                        known_metadata_hashes
                        and operation.metadata_hash not in known_metadata_hashes
                    ):
                        operation.sudo().write(
                            {
                                "state": "duplicate",
                                "document_id": document.id,
                                "error_message": _(
                                    "Paperless matched identical content to an "
                                    "archive document with a different "
                                    "classification fingerprint. Review the "
                                    "classification before linking it.",
                                ),
                            },
                        )
                        continue
                if document:
                    values.pop("source", None)
                    # A replacement operation carries the checksum of the new
                    # version. The cache follows Paperless's current version;
                    # every historical checksum, including the received
                    # original, remains on usl.document.version.
                    if not operation.target_document_id:
                        values["checksum"] = operation.checksum
                    values["metadata_hash"] = operation.metadata_hash
                    document.with_context(usl_documents_cache_write=True).write(values)
                else:
                    original_created_at = (
                        operation.original_created_at or operation.create_date
                    )
                    original_modified_at = (
                        operation.original_modified_at or original_created_at
                    )
                    values.update(
                        {
                            "company_id": operation.company_id.id,
                            "confidentiality": operation.confidentiality,
                            "accounting_evidence": bool(
                                getattr(operation, "accounting_evidence", False),
                            ),
                            "access_scope": (
                                getattr(operation, "access_scope", False)
                                or "company"
                            ),
                            "intake_role": (
                                getattr(operation, "document_role", False)
                                or "background"
                            ),
                            "review_state": "classified",
                            "submitted_by_id": operation.user_id.id,
                            "submitted_at": original_created_at,
                            "original_created_at": original_created_at,
                            "original_modified_at": original_modified_at,
                            "checksum": operation.checksum,
                            "metadata_hash": operation.metadata_hash,
                        },
                    )
                    document = document_cache.create(values)
                document._merge_original_timestamps(
                    operation.original_created_at or operation.create_date,
                    operation.original_modified_at
                    or operation.original_created_at
                    or operation.create_date,
                )
                document._synchronize_versions(payload.get("versions") or [])
                current_version = document.version_ids.filtered("is_current")
                if current_version:
                    current_version.sudo().write(
                        {
                            "submitted_by_id": operation.user_id.id,
                            "submitted_at": (
                                operation.original_created_at
                                or operation.create_date
                            ),
                            "original_created_at": (
                                operation.original_created_at
                                or operation.create_date
                            ),
                            "original_modified_at": (
                                operation.original_modified_at
                                or operation.original_created_at
                                or operation.create_date
                            ),
                            "source": operation.source,
                            "metadata_hash": operation.metadata_hash,
                        },
                    )
                if missing:
                    error_message = operation._source_record_missing_message(missing)
                    orphan_context = {
                        **archive_context,
                        "related_records": [],
                    }
                    if orphan_context:
                        document._apply_archive_context(
                            orphan_context,
                            submitted_by=operation.user_id,
                            access_user=operation._archive_context_access_user(),
                        )
                    if document.permission_sync_state != "synchronized":
                        document.with_user(
                            self.env.ref("base.user_root"),
                        ).action_sync_permissions()
                    document.sudo().with_context(
                        usl_documents_cache_write=True,
                    ).write(
                        {
                            "review_state": "needs_attention",
                            "last_error": error_message,
                        },
                    )
                    operation.sudo().write(
                        {
                            "state": "archived",
                            "document_id": document.id,
                            "error_message": False,
                            "review_reason": "missing_source",
                        },
                    )
                    continue
                if archive_context:
                    document._apply_archive_context(
                        archive_context,
                        submitted_by=operation.user_id,
                        access_user=operation._archive_context_access_user(),
                    )
                elif operation.res_model and operation.res_id:
                    record = (
                        self.env[operation.res_model]
                        .with_user(operation._archive_context_access_user())
                        .with_context(
                            allowed_company_ids=operation.company_id.ids,
                        )
                        .browse(operation.res_id)
                        .exists()
                    )
                    if not record:
                        raise UserError(_("The source Odoo record no longer exists."))
                    record.check_access("read")
                    document.sudo().with_context(
                        usl_documents_linked_by_id=operation.user_id.id,
                        usl_documents_defer_access_sync=True,
                    ).link_to_record(operation.res_model, operation.res_id)
                    document._recompute_linked_record_access(sync_permissions=True)
                if document.permission_sync_state != "synchronized":
                    document.with_user(
                        self.env.ref("base.user_root"),
                    ).action_sync_permissions()
                operation.sudo().write({
                    "state": "archived",
                    "document_id": document.id,
                    "error_message": False,
                })
            elif status in ("failure", "failed"):
                existing = self.env["usl.document"]
                if operation.checksum and operation.metadata_hash:
                    existing, matching_version = self.env[
                        "usl.document"
                    ]._find_archive_fingerprint(
                        operation.checksum,
                        operation.metadata_hash,
                        company=operation.company_id,
                        availability_state="available",
                    )
                    archive_context = operation.context_json or {}
                    if existing and (archive_context or not operation.res_model):
                        if matching_version:
                            archive_context = {
                                **archive_context,
                                "related_records": [
                                    {
                                        **target,
                                        "version_id": (
                                            matching_version.paperless_version_id
                                        ),
                                    }
                                    for target in archive_context.get(
                                        "related_records",
                                    )
                                    or []
                                ],
                            }
                        if archive_context:
                            existing._apply_archive_context(
                                archive_context,
                                submitted_by=operation.user_id,
                                access_user=(
                                    operation._archive_context_access_user()
                                ),
                            )
                        operation.sudo().write(
                            {
                                "state": "archived",
                                "document_id": existing.id,
                                "error_message": False,
                            },
                        )
                        continue
                result_data = task.get("result_data")
                operation.sudo().write({
                    "state": "failed",
                    "error_message": (
                        (
                            result_data.get("message")
                            or result_data.get("error_message")
                        )
                        if isinstance(result_data, dict)
                        else result_data
                    )
                    or task.get("result")
                    or task.get("message")
                    or _("Paperless processing failed."),
                })
            elif operation._processing_is_stale():
                operation._fail_stale_processing()
        return {
            operation.id: {
                "id": operation.id,
                "name": operation.name,
                "state": operation.state,
                "status_label": {
                    "pending": _("Queued"),
                    "uploading": _("Sending to Documents"),
                    "processing": _("Indexing in Documents"),
                    "archived": _("Archived"),
                    "duplicate": _("Needs attention"),
                    "failed": _("Failed"),
                }[operation.state],
                "document_id": operation.document_id.id,
                "document_name": (
                    operation.document_id.name
                    or operation.target_document_id.name
                    or operation.name
                ),
                "error": operation.error_message,
            }
            for operation in self
        }

    @api.model
    def cron_poll_operations(self):
        if not self.env.user.has_group(
            "usl_documents.group_documents_manager",
        ):
            raise AccessError(
                _("Only Documents administrators may run the ingestion scheduler."),
            )
        operations = self.search(
            [("state", "=", "processing")],
            order="create_date, id",
            limit=100,
        )
        backfill = operations.filtered(
            lambda item: item.attachment_origin == "backfill",
        )
        live = operations - backfill
        result = {}
        partitions = (
            (live, {}),
            (backfill, {"usl_documents_trusted_backfill_access": True}),
        )
        for operations, context in partitions:
            for operation in operations:
                scoped = operation.with_context(**context)
                try:
                    with self.env.cr.savepoint():
                        result.update(scoped.poll())
                except UserError as error:
                    _logger.warning(
                        "Document ingestion operation %s failed safely: %s",
                        operation.id,
                        error,
                    )
                    operation.sudo().write(
                        {
                            "state": "failed",
                            "error_message": str(error),
                        },
                    )
                    result[operation.id] = operation._workspace_values()
                except Exception:  # noqa: BLE001 - isolate independent queue items
                    _logger.warning(
                        "Document ingestion operation %s failed unexpectedly",
                        operation.id,
                        exc_info=True,
                    )
                    operation.sudo().write(
                        {
                            "state": "failed",
                            "error_message": _(
                                "Documents could not finish this file safely. "
                                "Retry the operation or ask a Documents "
                                "administrator to review it.",
                            ),
                        },
                    )
                    result[operation.id] = operation._workspace_values()
        return result
