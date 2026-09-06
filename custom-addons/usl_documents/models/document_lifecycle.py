"""Upload, versioning, linking, trash and permanent deletion workflows."""

import base64
import hashlib
from datetime import timedelta

from odoo import (
    SUPERUSER_ID,
    _,
    api,
    fields,
    models,
)
from odoo.exceptions import AccessError, UserError, ValidationError

from .document import CONFIDENTIALITIES
from .paperless_client import PaperlessError


class UslDocument(models.Model):
    _inherit = "usl.document"

    def update_archive_metadata(self, values):
        """Write Paperless-authoritative metadata, then refresh the local cache."""
        self.ensure_one()
        self.check_access("write")
        allowed = {
            "name",
            "document_date",
            "correspondent_id",
            "document_type_id",
            "tag_ids",
        }
        if set(values or {}) - allowed:
            raise ValidationError(_("Unsupported document metadata field."))
        payload = {}
        if "name" in values:
            title = (values.get("name") or "").strip()
            if not title:
                raise ValidationError(_("A document title is required."))
            if title != self.name:
                payload["title"] = title
        if "document_date" in values:
            requested_date = fields.Date.to_date(values.get("document_date"))
            if requested_date != self.document_date:
                payload["created"] = (
                    fields.Date.to_string(requested_date) if requested_date else None
                )
        for local_field, remote_field, model_name in (
            ("correspondent_id", "correspondent", "usl.paperless.correspondent"),
            ("document_type_id", "document_type", "usl.paperless.document.type"),
        ):
            if local_field not in values:
                continue
            record = self.env[model_name].browse(
                int(values[local_field]) if values[local_field] else 0,
            ).exists()
            if record and not record.active:
                raise ValidationError(_("Choose an active Paperless metadata item."))
            if record != self[local_field]:
                payload[remote_field] = record.paperless_id if record else None
        if "tag_ids" in values:
            requested = {int(tag_id) for tag_id in values.get("tag_ids") or []}
            tags = self.env["usl.paperless.tag"].search(
                [("id", "in", list(requested)), ("active", "=", True)],
            )
            if set(tags.ids) != requested:
                raise ValidationError(_("One or more selected tags are unavailable."))
            if set(tags.ids) != set(self.tag_ids.ids):
                payload["tags"] = tags.mapped("paperless_id")
        if payload:
            self._paperless().update_document_metadata(self.paperless_id, payload)
            refreshed = self._paperless().get_document(self.paperless_id)
            cache_values = self._paperless_values(refreshed)
            cache_values.pop("source", None)
            self.sudo().with_context(
                usl_documents_cache_write=True,
            ).write(cache_values)
            self._synchronize_versions(refreshed.get("versions") or [])
        return self.document_detail(self.id)

    def set_company(self, company_id):
        """Apply Odoo-owned company policy and immediately refresh permissions."""
        self.ensure_one()
        self.check_access("write")
        self._require_manager()
        if self.availability_state != "available":
            raise UserError(
                _("The company cannot be changed while the document is unavailable."),
            )

        company = self.env["res.company"]
        if company_id:
            company = company.browse(int(company_id)).exists()
            if not company:
                raise ValidationError(_("That company is no longer available."))
            if company not in self.env.companies:
                raise AccessError(
                    _(
                        "Select the target company in Odoo's company switcher "
                        "before assigning this document.",
                    ),
                )
        elif self.review_state != "needs_attention":
            raise ValidationError(
                _("Only a document that needs review may be left without a company."),
            )

        if self.company_id == company:
            return self.document_detail(self.id)

        active_links = self.sudo().link_ids.filtered("active")
        if active_links and (
            not company
            or any(link.company_id != company for link in active_links)
        ):
            raise ValidationError(
                _(
                    "Remove links to records from another company before changing "
                    "this document's company.",
                ),
            )

        self.write({"company_id": company.id})
        self.action_sync_permissions()
        return self.document_detail(self.id)

    @api.model
    def upload_from_odoo(
        self,
        filename,
        content_base64,
        content_type,
        *,
        res_model=None,
        res_id=None,
        company_id=None,
        confidentiality="internal",
        source="odoo_upload",
        document_date=None,
        document_type_id=None,
        tag_ids=None,
        original_created_at=None,
        original_modified_at=None,
    ):
        content = self._upload_decode_content(filename, content_base64, confidentiality)
        source_record = self._upload_source_record(res_model, res_id)
        operation = self._upload_pending_operation()
        company = self._upload_company(source_record, res_model, company_id)
        archive_context, original_created_at, original_modified_at = (
            self._upload_archive_context(
                source_record,
                operation,
                company,
                confidentiality,
                document_date=document_date,
                document_type_id=document_type_id,
                tag_ids=tag_ids,
                original_created_at=original_created_at,
                original_modified_at=original_modified_at,
            )
        )
        confidentiality = archive_context.get("confidentiality") or confidentiality
        checksum = hashlib.sha256(content).hexdigest()
        metadata_hash = (
            operation.metadata_hash
            if operation and operation.metadata_hash
            else self._archive_metadata_hash(archive_context)
        )
        retry_operation = self._upload_retry_operation(checksum, metadata_hash)
        existing, matching_version = self._find_archive_fingerprint(
            checksum,
            metadata_hash,
            company=company,
            availability_state="available",
        )
        if existing:
            retry_operation.acknowledge()
            if source_record and self._paperless().configured:
                archive_context = self._prepare_archive_context(
                    source_record,
                    operation.source_attachment_id if operation else None,
                    context=archive_context,
                )
            return self._upload_reuse_document(
                existing,
                matching_version,
                operation,
                archive_context,
                checksum,
                metadata_hash,
            )
        trashed, _trashed_version = self._find_archive_fingerprint(
            checksum,
            metadata_hash,
            company=company,
            availability_state="trashed",
        )
        if trashed:
            raise UserError(
                _(
                    "Identical content and classification are already in Trash. "
                    "Restore that document before linking or uploading it again.",
                ),
            )
        if source_record:
            archive_context = self._prepare_archive_context(
                source_record,
                operation.source_attachment_id if operation else None,
                context=archive_context,
            )
        mirrored, mirrored_version, unknown_remote_matches = (
            self._upload_remote_matches(checksum, metadata_hash)
        )
        if mirrored:
            retry_operation.acknowledge()
            return self._upload_reuse_document(
                mirrored,
                mirrored_version,
                operation,
                archive_context,
                checksum,
                metadata_hash,
            )
        operation_values = self._upload_operation_values(
            filename,
            content_type,
            company,
            confidentiality,
            res_model,
            res_id,
            source,
            archive_context,
            checksum,
            metadata_hash,
            retry_operation,
            original_created_at,
            original_modified_at,
        )
        if unknown_remote_matches:
            operation_values.update(
                {
                    "state": "duplicate",
                    "error_message": _(
                        "Identical content exists outside your authorized Odoo archive "
                        "view, but its classification fingerprint cannot be verified. "
                        "A Documents administrator must classify it before reuse.",
                    ),
                },
            )
            operation = self._upload_store_operation(operation, operation_values)
            return {
                "state": "duplicate",
                "operation_id": operation.id,
                "message": operation.error_message,
            }
        operation = self._upload_store_operation(operation, operation_values)
        try:
            task_id = self._paperless().upload_multipart(
                content,
                filename,
                content_type,
                title=filename,
                created=archive_context.get("document_date"),
                correspondent_id=archive_context.get("correspondent_paperless_id"),
                document_type_id=archive_context.get("document_type_paperless_id"),
                tag_ids=archive_context.get("tag_paperless_ids"),
            )
            operation.sudo().write(
                {"state": "processing", "paperless_task_id": task_id},
            )
            retry_operation.acknowledge()
        except PaperlessError as error:
            operation.sudo().write({"state": "failed", "error_message": str(error)})
            raise
        return {
            "state": "processing",
            "operation_id": operation.id,
            "task_id": task_id,
            "message": _(
                "“%(document)s” was accepted and is being processed.",
            )
            % {"document": filename},
        }

    @api.model
    def _upload_decode_content(self, filename, content_base64, confidentiality):
        """Validate the upload request and return the decoded file content."""
        if not filename or not content_base64:
            raise ValidationError(_("Choose a non-empty file."))
        if confidentiality not in dict(CONFIDENTIALITIES):
            raise ValidationError(_("Invalid confidentiality policy."))
        try:
            content = base64.b64decode(content_base64, validate=True)
        except (ValueError, TypeError) as error:
            raise ValidationError(_("The uploaded file is not valid base64.")) from error
        maximum = self.env["ir.config_parameter"].sudo().get_int(
            "usl_documents.max_upload_bytes", 50 * 1024 * 1024,
        )
        if not content or len(content) > maximum:
            raise ValidationError(
                _("The file is empty or exceeds the %(size)s MB upload limit.")
                % {"size": maximum // (1024 * 1024)},
            )
        return content

    @api.model
    def _upload_source_record(self, res_model, res_id):
        """Return the readable source record of the upload, or False."""
        if not (res_model or res_id):
            return False
        if (
            res_model not in self.env["usl.document.link"]._allowed_models()
            or not res_id
        ):
            raise ValidationError(_("Invalid source record for archive ingestion."))
        source_record = self.env[res_model].browse(int(res_id)).exists()
        if not source_record:
            raise ValidationError(_("The source Odoo record no longer exists."))
        source_record.check_access("read")
        return source_record

    @api.model
    def _upload_pending_operation(self):
        """Return the pending operation a queued upload runs for, if any."""
        operation = self.env["usl.document.operation"]
        operation_id = self.env.context.get("usl_documents_operation_id")
        if operation_id and self.env.su:
            operation = operation.sudo().browse(int(operation_id)).exists()
            if not operation or operation.state != "pending":
                raise ValidationError(_("The attachment archive request is no longer pending."))
        return operation

    @api.model
    def _upload_company(self, source_record, res_model, company_id):
        """Resolve the legal company of the upload and check it is allowed."""
        record_company = False
        if source_record:
            record_company = (
                source_record
                if res_model == "res.company"
                else getattr(source_record, "company_id", False)
            )
        company = (
            record_company
            or (
                self.env["res.company"].browse(int(company_id)).exists()
                if company_id
                else self.env.company
            )
        )
        if company_id and record_company and int(company_id) != record_company.id:
            raise ValidationError(
                _("The upload company must match the source record's legal company."),
            )
        if not self.env.su and company not in self.env.user.company_ids:
            raise AccessError(_("You cannot archive a document for this company."))
        return company

    @api.model
    def _upload_archive_context(
        self,
        source_record,
        operation,
        company,
        confidentiality,
        *,
        document_date=None,
        document_type_id=None,
        tag_ids=None,
        original_created_at=None,
        original_modified_at=None,
    ):
        """Build the archive context and return it with the original timestamps."""
        document_type = self.env["usl.paperless.document.type"]
        if document_type_id:
            document_type = document_type.browse(int(document_type_id)).exists()
            if not document_type or not document_type.active:
                raise ValidationError(_("Choose an active Paperless document type."))
        requested_tags = {int(tag_id) for tag_id in (tag_ids or [])}
        tags = self.env["usl.paperless.tag"].search(
            [("id", "in", list(requested_tags)), ("active", "=", True)],
        )
        if set(tags.ids) != requested_tags:
            raise ValidationError(_("One or more selected tags are unavailable."))
        archive_context = (
            (
                operation.context_json
                if operation and operation.context_json
                else source_record.with_context(
                    usl_documents_policy_origin="documents_workspace",
                )._document_archive_context(
                    operation.source_attachment_id if operation else None,
                )
            )
            if source_record
            else {
                "company_id": company.id,
                "confidentiality": confidentiality,
                "accounting_evidence": False,
                "access_scope": "company",
                "archive_mode": "automatic",
                "document_role": "library",
                "attachment_origin": "documents_workspace",
                "policy_reason": "generic_documents_upload",
                "tags": [],
                "entity_tags": [],
                "tag_record_ids": [],
                "tag_paperless_ids": [],
                "related_records": [],
            }
        )
        if document_date:
            archive_context["document_date"] = fields.Date.to_string(document_date)
        if document_type:
            archive_context.update(
                {
                    "document_type": document_type.name,
                    "document_type_record_id": document_type.id,
                    "document_type_paperless_id": document_type.paperless_id or False,
                },
            )
        if tags:
            tag_names = set(tags.mapped("name"))
            paperless_tag_ids = {
                paperless_id
                for paperless_id in tags.mapped("paperless_id")
                if paperless_id
            }
            archive_context.update(
                {
                    "tags": sorted(
                        set(archive_context.get("tags") or []) | tag_names,
                    ),
                    "tag_record_ids": sorted(
                        set(archive_context.get("tag_record_ids") or [])
                        | set(tags.ids),
                    ),
                    "tag_paperless_ids": sorted(
                        set(archive_context.get("tag_paperless_ids") or [])
                        | paperless_tag_ids,
                    ),
                },
            )
        original_created_at = fields.Datetime.to_datetime(
            original_created_at or archive_context.get("original_created_at"),
        ) or fields.Datetime.now()
        original_modified_at = fields.Datetime.to_datetime(
            original_modified_at or archive_context.get("original_modified_at"),
        ) or original_created_at
        archive_context.update(
            {
                "original_created_at": fields.Datetime.to_string(
                    original_created_at,
                ),
                "original_modified_at": fields.Datetime.to_string(
                    original_modified_at,
                ),
            },
        )
        return archive_context, original_created_at, original_modified_at

    @api.model
    def _upload_retry_operation(self, checksum, metadata_hash):
        """Return the user's latest unacknowledged failed attempt for this file."""
        return self.env["usl.document.operation"].search(
            [
                ("checksum", "=", checksum),
                ("metadata_hash", "=", metadata_hash),
                ("user_id", "=", self.env.user.id),
                ("state", "in", ("failed", "duplicate")),
                ("acknowledged", "=", False),
            ],
            order="create_date desc, id desc",
            limit=1,
        )

    @api.model
    def _upload_remote_matches(self, checksum, metadata_hash):
        """Find a mirrored Paperless document with the same fingerprint.

        Return the matching mirrored document and version, and the remote
        matches that no authorized Odoo document mirrors.
        """
        remote_candidates = self._paperless().search(
            "", page=1, page_size=2, filters={"checksum": checksum},
        ).get("results", [])
        remote_matches = [
            item
            for item in remote_candidates
            if checksum
            in {
                item.get("checksum"),
                *[
                    version.get("checksum")
                    for version in (item.get("versions") or [])
                ],
            }
        ]
        unknown_remote_matches = []
        mirrored_match = self.browse()
        mirrored_version = self.env["usl.document.version"]
        for remote_match in remote_matches:
            remote_id = int(remote_match["id"])
            mirrored = self.search([("paperless_id", "=", remote_id)], limit=1)
            if not mirrored:
                unknown_remote_matches.append(remote_match)
                continue
            matches, version = mirrored._archive_fingerprint_version(
                checksum,
                metadata_hash,
            )
            if matches:
                mirrored_match = mirrored
                mirrored_version = version
                break
        return mirrored_match, mirrored_version, unknown_remote_matches

    @api.model
    def _upload_reuse_document(
        self,
        document,
        version,
        operation,
        archive_context,
        checksum,
        metadata_hash,
    ):
        """Attach the upload's classification to an identical existing document."""
        if version:
            archive_context = {
                **archive_context,
                "related_records": [
                    {
                        **target,
                        "version_id": version.paperless_version_id,
                    }
                    for target in archive_context.get("related_records") or []
                ],
            }
        document._apply_archive_context(
            archive_context,
            submitted_by=operation.user_id if operation else self.env.user,
            access_user=(
                operation._archive_context_access_user()
                if operation
                else self.env.user
            ),
        )
        if operation:
            operation.sudo().write(
                {
                    "state": "archived",
                    "checksum": checksum,
                    "metadata_hash": metadata_hash,
                    "document_id": document.id,
                    "context_json": archive_context,
                    "error_message": False,
                    "next_attempt_at": False,
                },
            )
        return {
            "state": "duplicate",
            "document_id": document.id,
            "message": _(
                "“%(document)s” already contains this exact file and "
                "classification; the existing archive document was reused.",
            )
            % {"document": document.name},
        }

    @api.model
    def _upload_operation_values(
        self,
        filename,
        content_type,
        company,
        confidentiality,
        res_model,
        res_id,
        source,
        archive_context,
        checksum,
        metadata_hash,
        retry_operation,
        original_created_at,
        original_modified_at,
    ):
        """Return the operation values of a new upload in the uploading state."""
        return {
            "name": filename,
            "state": "uploading",
            "checksum": checksum,
            "metadata_hash": metadata_hash,
            "mime_type": content_type,
            "company_id": company.id,
            "confidentiality": confidentiality,
            "res_model": res_model,
            "res_id": int(res_id) if res_id else 0,
            "source": source,
            "accounting_evidence": bool(archive_context.get("accounting_evidence")),
            "access_scope": archive_context.get("access_scope") or "linked_record",
            "archive_mode": archive_context.get("archive_mode") or "automatic",
            "document_role": archive_context.get("document_role") or "library",
            "attachment_origin": (
                archive_context.get("attachment_origin") or "documents_workspace"
            ),
            "policy_reason": (
                archive_context.get("policy_reason") or "generic_documents_upload"
            ),
            "context_json": archive_context,
            "original_created_at": original_created_at,
            "original_modified_at": original_modified_at,
            "retry_of_id": retry_operation.id,
            "retry_count": (retry_operation.retry_count + 1)
            if retry_operation
            else 0,
        }

    @api.model
    def _upload_store_operation(self, operation, values):
        """Write the values on the queued operation or create a new one."""
        if operation:
            operation.sudo().write(values)
            return operation
        return self.env["usl.document.operation"].sudo().create(values)

    def link_to_record(
        self,
        res_model,
        res_id,
        version_id=None,
        *,
        archive_mode="automatic",
        policy_role="library",
        attachment_origin="documents_workspace",
        policy_reason="manual_documents_link",
    ):
        self.ensure_one()
        return self.env["usl.document.link"].create_for_record(
            self,
            res_model,
            int(res_id),
            version_id=version_id,
            archive_mode=archive_mode,
            policy_role=policy_role,
            attachment_origin=attachment_origin,
            policy_reason=policy_reason,
        )

    def upload_new_version(
        self, filename, content_base64, content_type, version_label=None,
    ):
        self.ensure_one()
        self.check_access("write")
        if self.availability_state != "available":
            raise UserError(
                _("A replacement cannot be added while the root document is unavailable."),
            )
        try:
            content = base64.b64decode(content_base64, validate=True)
        except (ValueError, TypeError) as error:
            raise ValidationError(_("The replacement file is not valid base64.")) from error
        maximum = self.env["ir.config_parameter"].sudo().get_int(
            "usl_documents.max_upload_bytes", 50 * 1024 * 1024,
        )
        if not content or len(content) > maximum:
            raise ValidationError(
                _("The file is empty or exceeds the %(size)s MB upload limit.")
                % {"size": maximum // (1024 * 1024)},
            )
        checksum = hashlib.sha256(content).hexdigest()
        if checksum in ({self.checksum} | set(self.version_ids.mapped("checksum"))):
            return {
                "state": "duplicate",
                "document_id": self.id,
                "message": _(
                    "“%(document)s” already contains this exact file.",
                )
                % {"document": self.name},
            }
        return self._queue_new_version(
            content,
            filename,
            content_type,
            version_label=version_label or filename,
        )

    def _queue_new_version(
        self, content, filename, content_type, *, version_label, restored_from=None,
    ):
        self.ensure_one()
        checksum = hashlib.sha256(content).hexdigest()
        operation = self.env["usl.document.operation"].sudo().create(
            {
                "name": filename,
                "state": "uploading",
                "checksum": checksum,
                "mime_type": content_type,
                "company_id": self.company_id.id,
                "confidentiality": self.confidentiality,
                "source": self.source
                if self.source in dict(
                    self.env["usl.document.operation"]._fields["source"].selection,
                )
                else "odoo_upload",
                "target_document_id": self.id,
                "archive_mode": "automatic",
                "document_role": self.intake_role or "library",
                "attachment_origin": "documents_workspace",
                "policy_reason": "document_version_update",
            },
        )
        try:
            task_id = self._paperless().update_version(
                self.paperless_id,
                content,
                filename,
                content_type,
                version_label=version_label or filename,
            )
            operation.sudo().write(
                {"state": "processing", "paperless_task_id": task_id},
            )
        except PaperlessError as error:
            operation.sudo().write({"state": "failed", "error_message": str(error)})
            raise
        return {
            "state": "processing",
            "operation_id": operation.id,
            "task_id": task_id,
            "message": (
                _(
                    "An earlier file from “%(document)s” is being restored as its "
                    "new current version. "
                    "Earlier versions remain available.",
                )
                % {"document": self.name}
                if restored_from
                else _(
                    "“%(document)s” is receiving a new version. Earlier versions "
                    "remain available.",
                )
                % {"document": self.name}
            ),
        }

    def restore_version(self, paperless_version_id):
        self.ensure_one()
        self.check_access("write")
        if self.availability_state != "available":
            raise UserError(
                _("A version cannot be restored while the document is unavailable."),
            )
        version = self.version_ids.filtered(
            lambda item: item.paperless_version_id == str(paperless_version_id),
        )
        if not version:
            raise ValidationError(_("That file version is no longer available."))
        if version.is_current:
            raise ValidationError(_("That file is already the current version."))
        content, headers = self._paperless().download(
            self.paperless_id,
            version_id=version.paperless_version_id,
            original=True,
        )
        content_type = (
            headers.get("Content-Type")
            or headers.get("content-type")
            or version.mime_type
            or "application/octet-stream"
        ).split(";", 1)[0]
        filename = version.original_filename or self.original_filename or self.name
        return self._queue_new_version(
            content,
            filename,
            content_type,
            version_label=_("Restored from %s") % version.label,
            restored_from=version.paperless_version_id,
        )

    def restore_from_trash(self):
        self.ensure_one()
        self.check_access("write")
        if self.availability_state != "trashed":
            raise ValidationError(_("This document is not in Trash."))
        result = self._paperless().restore_trashed_documents([self.paperless_id])
        restored_ids = {int(item) for item in result.get("doc_ids", [])}
        if restored_ids and self.paperless_id not in restored_ids:
            raise UserError(
                _("Paperless did not confirm restoration of this document."),
            )
        payload = self._paperless().get_document(self.paperless_id)
        values = self._paperless_values(payload)
        values.pop("source", None)
        values.update(
            {
                "trashed_at": False,
                "trashed_by_id": False,
                "trashed_by_label": False,
                "retention_until": False,
                "deletion_approved_by_id": False,
                "deletion_approved_at": False,
                "deletion_reason": False,
            },
        )
        self.sudo().with_context(usl_documents_cache_write=True).write(values)
        self._synchronize_versions(payload.get("versions") or [])
        self.action_sync_permissions()
        return {
            "state": "restored",
            "document_id": self.id,
            "message": _(
                "“%(document)s” was restored. Its Odoo links and archive identity "
                "were preserved.",
            )
            % {"document": self.name},
        }

    def move_to_trash(self):
        """Move a document to Trash while retaining every Odoo relationship."""
        self.ensure_one()
        self.check_access("write")
        if self.availability_state != "available":
            raise ValidationError(_("Only an available document can be moved to Trash."))
        self._paperless().trash_document(self.paperless_id)
        now = fields.Datetime.now()
        retention_days = self.env["ir.config_parameter"].sudo().get_int(
            "usl_documents.paperless_trash_retention_days",
            30,
        )
        self.sudo().with_context(usl_documents_cache_write=True).write(
            {
                "availability_state": "trashed",
                "trashed_at": now,
                "trashed_by_id": self.env.user.id,
                "trashed_by_label": self.env.user.display_name,
                "retention_until": now + timedelta(days=max(0, retention_days)),
                "last_error": False,
                "permission_sync_state": "pending",
                "permission_sync_error": False,
                "permission_checked_at": False,
            },
        )
        self.message_post(
            body=_(
                "%(user)s moved this document to Paperless Trash. "
                "Its %(count)s active Odoo relationship(s) were preserved.",
            )
            % {
                "user": self.env.user.display_name,
                "count": len(self._accessible_active_links()),
            },
        )
        return {
            "state": "trashed",
            "document_id": self.id,
            "message": _(
                "“%(document)s” was moved to Trash. Linked Odoo records were kept.",
            )
            % {"document": self.name},
        }

    def approve_permanent_deletion(self, reason):
        self.ensure_one()
        self._require_manager()
        if self.availability_state != "trashed":
            raise ValidationError(_("Only a document in Trash can be approved."))
        if not (reason or "").strip():
            raise ValidationError(_("Record why permanent deletion is authorized."))
        self.sudo().with_context(usl_documents_cache_write=True).write(
            {
                "deletion_approved_by_id": self.env.user.id,
                "deletion_approved_at": fields.Datetime.now(),
                "deletion_reason": reason.strip(),
            },
        )
        return True

    def action_approve_permanent_deletion(self):
        for document in self:
            document.approve_permanent_deletion(document.deletion_reason)
        return True

    def action_permanently_delete_from_trash(self):
        for document in self:
            document.permanently_delete_from_trash()
        return True

    def permanently_delete_from_trash(self):
        """Delete an approved, expired, unlinked root and retain an Odoo tombstone."""
        self.ensure_one()
        self._require_manager()
        if self.availability_state != "trashed":
            raise ValidationError(_("Only a document in Trash can be deleted."))
        if self.retention_hold:
            raise UserError(_("A retention hold blocks permanent deletion."))
        if self.sudo().link_ids.filtered("active"):
            raise UserError(
                _("Remove every Odoo relationship before permanent deletion."),
            )
        if not self.deletion_approved_at or not self.deletion_reason:
            raise UserError(_("Approve permanent deletion and record a reason first."))
        if self.retention_until and self.retention_until > fields.Datetime.now():
            raise UserError(
                _("The archive retention period has not ended yet."),
            )
        self._paperless().permanently_delete_trashed_documents([self.paperless_id])
        self.sudo().with_context(usl_documents_cache_write=True).write(
            {
                "availability_state": "permanently_deleted",
                "permanently_deleted_at": fields.Datetime.now(),
                "last_error": False,
            },
        )
        self.message_post(
            body=_(
                "Paperless document %(paperless_id)s was permanently deleted. "
                "Approval: %(reason)s",
            )
            % {
                "paperless_id": self.paperless_id,
                "reason": self.deletion_reason,
            },
        )
        return True

    def unlink_from_record(self, res_model, res_id):
        self.ensure_one()
        self.check_access("write")
        if res_model not in self.env["usl.document.link"]._allowed_models():
            raise ValidationError(_("This Odoo model cannot carry archived documents."))
        record = self.env[res_model].browse(int(res_id)).exists()
        if not record:
            raise ValidationError(_("The linked Odoo record no longer exists."))
        record.check_access("write")
        links = self.env["usl.document.link"].sudo().search(
            [
                ("document_id", "=", self.id),
                ("res_model", "=", res_model),
                ("res_id", "=", int(res_id)),
                ("active", "=", True),
            ],
        )
        if not links:
            return False
        # The caller and target record were authorized above. Removing this
        # recoverable relationship can cascade to technical chatter metadata,
        # so execute that narrow cleanup as Odoo's service identity instead of
        # requiring the human-only permanent-deletion capability.
        links.with_user(SUPERUSER_ID).unlink()
        self._recompute_linked_record_access(sync_permissions=True)
        return True
