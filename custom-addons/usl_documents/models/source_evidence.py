"""Exact source-file evidence attached through the native business record."""

import base64
import binascii
import hashlib
import re

from odoo import _, api, models
from odoo.exceptions import ValidationError


MAX_SOURCE_BYTES = 20 * 1024 * 1024


class SourceEvidence(models.Model):
    _inherit = "usl.document"

    @api.model
    @api.private
    def _attach_source_evidence(
        self, record, *, filename, mimetype, data_base64, sha256, byte_size,
        set_main_attachment=False,
    ):
        record.ensure_one()
        record.check_access("read")
        record.check_access("write")
        if record.company_id not in record.env.companies:
            raise ValidationError(_("Select the evidence target's company before uploading the file."))
        if (
            not isinstance(filename, str) or not filename or len(filename) > 255
            or any(ord(char) < 32 or char in "/\\" for char in filename)
        ):
            raise ValidationError(_("Provide the original source filename, without a folder path."))
        if mimetype not in (None, False) and (not isinstance(mimetype, str) or len(mimetype) > 255 or not re.fullmatch(r"[A-Za-z0-9.+-]+/[A-Za-z0-9.+-]+", mimetype)):
            raise ValidationError(_("Provide the source file's MIME type."))
        if type(byte_size) is not int or not 0 < byte_size <= MAX_SOURCE_BYTES:
            raise ValidationError(_("Source evidence must contain between one byte and 20 MiB."))
        if type(set_main_attachment) is not bool:
            raise ValidationError(_("Main attachment selection must be true or false."))
        if not isinstance(sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", sha256):
            raise ValidationError(_("Provide the SHA-256 checksum of the exact source bytes."))
        if not isinstance(data_base64, str) or len(data_base64) > ((MAX_SOURCE_BYTES + 2) // 3) * 4:
            raise ValidationError(_("The source evidence payload is invalid or too large."))
        try:
            content = base64.b64decode(data_base64, validate=True)
        except (ValueError, binascii.Error) as error:
            raise ValidationError(_("The source evidence payload is not valid binary data.")) from error
        if len(content) != byte_size or hashlib.sha256(content).hexdigest() != sha256:
            raise ValidationError(_("The source evidence size or checksum changed. Upload the original file again."))

        # The record lock serializes repeat uploads and explicit main selection.
        # Authorization precedes SQL; no record from another company is elevated.
        record.flush_recordset()
        record.env.cr.execute(
            'SELECT id FROM "%s" WHERE id = %%s FOR UPDATE' % record._table,
            [record.id],
        )
        record.invalidate_recordset()
        record.check_access("write")
        if hasattr(record, "_usl_agent_collaboration"):
            record = record._usl_agent_collaboration(operation="write")
        Attachment = record.env["ir.attachment"].with_context(image_no_postprocess=True)
        mime_inferred = not mimetype or mimetype == "application/octet-stream"
        if mime_inferred:
            mimetype = Attachment._compute_mimetype({"name": filename, "raw": content})
        with record.env.cr.savepoint():
            checksum = hashlib.sha1(content).hexdigest()
            attachment = Attachment.search([
                ("res_model", "=", record._name), ("res_id", "=", record.id),
                ("company_id", "=", record.company_id.id),
                ("res_field", "=", False), ("type", "=", "binary"),
                ("name", "=", filename), ("mimetype", "=", mimetype),
                ("checksum", "=", checksum), ("file_size", "=", byte_size),
            ], limit=1)
            reused = bool(attachment)
            if not attachment:
                # Advance the authorized target's MVCC version through its native
                # evidence field, retaining the existing main selection. A row
                # lock alone leaves a waiting REPEATABLE READ snapshot stale;
                # this write makes native RPC retry the whole conflicting
                # transaction, which then sees and reuses the committed file.
                record.write({
                    "message_main_attachment_id": record.message_main_attachment_id.id or False,
                })
                attachment = Attachment.create({
                    "name": filename, "mimetype": mimetype, "raw": content,
                    "type": "binary", "res_model": record._name,
                    "res_id": record.id, "company_id": record.company_id.id,
                    "res_field": False, "public": False, "access_token": False,
                    "url": False,
                })
            stored = attachment.raw
            stored = stored.content if hasattr(stored, "content") else stored
            if (
                attachment.name != filename or attachment.mimetype != mimetype
                or attachment.company_id != record.company_id
                or attachment.file_size != byte_size
                or hashlib.sha256(stored).hexdigest() != sha256
            ):
                raise ValidationError(_("Odoo could not retain this file's exact content and MIME type. No evidence was attached."))
            old_main = record.message_main_attachment_id
            if set_main_attachment:
                record._message_set_main_attachment_id(attachment, force=True, filter_xml=False)
            main_changed = old_main != record.message_main_attachment_id
            if not reused or main_changed:
                # Do not include attachment_ids: native post hooks would select a
                # main attachment even when the caller explicitly left it false.
                if reused:
                    body = _(
                        "Source evidence selected as main attachment: %(filename)s (SHA-256 %(checksum)s).",
                        filename=filename, checksum=sha256,
                    )
                else:
                    body = _(
                        "Source evidence uploaded: %(filename)s (%(size)s bytes, SHA-256 %(checksum)s).",
                        filename=filename, size=byte_size, checksum=sha256,
                    )
                record.message_post(
                    body=body,
                    message_type="comment", subtype_xmlid="mail.mt_note",
                )
            return {
                "attachment_id": attachment.id, "target_model": record._name,
                "target_id": record.id, "filename": filename, "mimetype": mimetype,
                "mime_inferred": mime_inferred,
                "byte_size": byte_size, "sha256": sha256,
                "checksum": attachment.checksum, "reused": reused,
                "main_attachment_id": record.message_main_attachment_id.id or False,
                "uploaded_by_id": attachment.create_uid.id,
                "uploaded_at": str(attachment.create_date),
                "archive_state": attachment.usl_documents_ledger_state,
            }


class ExpenseSourceEvidence(models.Model):
    _inherit = "hr.expense"

    def attach_source_evidence(self, **values):
        return self.env["usl.document"]._attach_source_evidence(self, **values)


class MoveSourceEvidence(models.Model):
    _inherit = "account.move"

    def attach_source_evidence(self, **values):
        return self.env["usl.document"]._attach_source_evidence(self, **values)
