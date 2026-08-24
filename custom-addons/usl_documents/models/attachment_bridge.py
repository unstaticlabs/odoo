import base64
import hashlib
import json
import re
from datetime import timedelta

from odoo import Command, _, api, fields, models
from odoo.exceptions import UserError

from .paperless_client import PaperlessError, PaperlessUnavailable


class DocumentContextTag(models.Model):
    _name = "usl.document.context.tag"
    _description = "Stable Business Entity Document Tag"
    _rec_name = "tag_name"
    _order = "namespace, tag_name"

    namespace = fields.Char(required=True, index=True, readonly=True)
    res_model = fields.Char(required=True, index=True, readonly=True)
    res_id = fields.Integer(required=True, index=True, readonly=True)
    tag_name = fields.Char(required=True, readonly=True)
    company_id = fields.Many2one(
        "res.company", required=True, index=True, ondelete="restrict", readonly=True,
    )
    tag_id = fields.Many2one(
        "usl.paperless.tag", required=True, ondelete="restrict", readonly=True,
    )
    active = fields.Boolean(default=True, readonly=True)

    _entity_unique = models.Constraint(
        "UNIQUE(namespace, res_model, res_id)",
        "A business entity can have only one stable archive tag per namespace.",
    )

    @api.model
    def ensure_for_descriptor(self, descriptor, company):
        namespace = str(descriptor.get("namespace") or "").strip()
        res_model = str(descriptor.get("model") or "").strip()
        res_id = int(descriptor.get("id") or 0)
        entity_name = str(descriptor.get("name") or "").strip()
        if not namespace or not res_model or not res_id or not entity_name:
            raise UserError(_("A contextual document tag is incomplete."))
        document_model = self.env["usl.document"]
        parent = document_model._ensure_context_tag(
            descriptor.get("parent") or namespace.title(),
        )
        mapping = self.sudo().search(
            [
                ("namespace", "=", namespace),
                ("res_model", "=", res_model),
                ("res_id", "=", res_id),
            ],
            limit=1,
        )
        prefix = {
            "project": _("Project"),
            "platform": _("Platform"),
        }.get(namespace, namespace.title())
        desired_name = f"{prefix} · {entity_name}"
        conflicting = self.sudo().search(
            [
                ("id", "!=", mapping.id),
                ("tag_name", "=ilike", desired_name),
                ("active", "=", True),
            ],
            limit=1,
        )
        if conflicting:
            desired_name = f"{desired_name} — {company.display_name}"
        if mapping:
            values = {}
            if mapping.tag_name != desired_name:
                mapping.tag_id.with_user(self.env.ref("base.user_root")).write(
                    {"name": desired_name, "parent_id": parent.id},
                )
                values["tag_name"] = desired_name
            if mapping.company_id != company:
                values["company_id"] = company.id
            if values:
                mapping.sudo().write(values)
            return mapping.tag_id
        tag = document_model._ensure_context_tag(desired_name, parent=parent)
        self.sudo().create(
            {
                "namespace": namespace,
                "res_model": res_model,
                "res_id": res_id,
                "tag_name": desired_name,
                "company_id": company.id,
                "tag_id": tag.id,
            },
        )
        return tag


class UslDocument(models.Model):
    _inherit = "usl.document"

    @api.model
    def _archive_metadata_hash(self, context):
        """Fingerprint stable classification without making record links unique."""

        def normalized_text(value):
            return str(value or "").strip().casefold()

        entity_tags = sorted(
            (
                normalized_text(descriptor.get("namespace")),
                normalized_text(descriptor.get("model")),
                int(descriptor.get("id") or 0),
            )
            for descriptor in (context.get("entity_tags") or [])
        )
        metadata = {
            "company_id": int(context.get("company_id") or 0),
            "confidentiality": normalized_text(context.get("confidentiality")),
            "accounting_evidence": bool(context.get("accounting_evidence")),
            "access_scope": normalized_text(context.get("access_scope")),
            "tags": sorted(
                normalized_text(tag) for tag in (context.get("tags") or [])
            ),
            "entity_tags": entity_tags,
            "document_type": normalized_text(context.get("document_type")),
            "correspondent_partner_id": int(
                context.get("correspondent_partner_id") or 0,
            ),
            "document_date": str(context.get("document_date") or ""),
        }
        canonical = json.dumps(
            metadata,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )
        return hashlib.sha256(canonical.encode()).hexdigest()

    def _archive_fingerprint_version(self, checksum, metadata_hash):
        """Return the exact matching version, or an empty recordset for current."""
        self.ensure_one()
        matching_version = self.version_ids.filtered(
            lambda version: (
                version.checksum == checksum
                and version.metadata_hash == metadata_hash
            ),
        )[:1]
        if matching_version:
            return True, matching_version
        return (
            self.checksum == checksum and self.metadata_hash == metadata_hash,
            self.env["usl.document.version"],
        )

    @api.model
    def _find_archive_fingerprint(
        self,
        checksum,
        metadata_hash,
        *,
        company,
        availability_state,
    ):
        candidates = self.sudo().search(
            [
                ("availability_state", "=", availability_state),
                "|",
                ("company_id", "=", company.id),
                ("company_id", "=", False),
                "|",
                ("checksum", "=", checksum),
                ("version_ids.checksum", "=", checksum),
            ],
        )
        for candidate in candidates:
            matches, version = candidate._archive_fingerprint_version(
                checksum,
                metadata_hash,
            )
            if matches:
                return candidate, version
        return self.browse(), self.env["usl.document.version"]

    @api.model
    def _ensure_context_tag(self, name, *, parent=False):
        name = str(name or "").strip()
        if not name:
            return self.env["usl.paperless.tag"]
        tag_model = self.env["usl.paperless.tag"].sudo()
        tag = tag_model.search([("name", "=ilike", name), ("active", "=", True)], limit=1)
        if tag:
            if parent and tag.parent_id != parent:
                tag.with_user(self.env.ref("base.user_root")).write(
                    {"parent_id": parent.id},
                )
            return tag
        return tag_model.with_user(self.env.ref("base.user_root")).create(
            {
                "name": name,
                "parent_id": parent.id if parent else False,
                "matching_algorithm": "0",
                "is_insensitive": True,
            },
        )

    @api.model
    def _ensure_context_document_type(self, name):
        name = str(name or "").strip()
        if not name:
            return self.env["usl.paperless.document.type"]
        model = self.env["usl.paperless.document.type"].sudo()
        record = model.search([("name", "=ilike", name), ("active", "=", True)], limit=1)
        return record or model.with_user(self.env.ref("base.user_root")).create(
            {
                "name": name,
                "matching_algorithm": "0",
                "is_insensitive": True,
            },
        )

    @api.model
    def _prepare_archive_context(self, source_record, attachment=None):
        source_record.ensure_one()
        context = source_record._document_archive_context(attachment)
        company = self.env["res.company"].browse(context["company_id"]).exists()
        tags = self.env["usl.paperless.tag"]
        for name in context.get("tags") or []:
            tags |= self._ensure_context_tag(name)
        for descriptor in context.get("entity_tags") or []:
            tags |= self.env["usl.document.context.tag"].ensure_for_descriptor(
                descriptor, company,
            )
        document_type = self._ensure_context_document_type(
            context.get("document_type"),
        )
        correspondent = self.env["usl.paperless.correspondent"]
        if context.get("correspondent_partner_id"):
            result = correspondent.with_user(
                self.env.ref("base.user_root"),
            ).create_from_partner(context["correspondent_partner_id"])
            correspondent = correspondent.browse(result["id"])
        return {
            **context,
            "tag_record_ids": tags.ids,
            "tag_paperless_ids": tags.mapped("paperless_id"),
            "document_type_record_id": document_type.id or False,
            "document_type_paperless_id": document_type.paperless_id or False,
            "correspondent_record_id": correspondent.id or False,
            "correspondent_paperless_id": correspondent.paperless_id or False,
        }

    def _apply_archive_context(self, context, *, submitted_by=None):
        for document in self:
            actor = submitted_by or self.env.user
            for target in context.get("related_records") or []:
                record = self.env[target["model"]].with_user(actor).browse(
                    int(target["id"]),
                ).exists()
                if not record:
                    raise UserError(_("A related business record no longer exists."))
                record.check_access("read")
            tags = self.env["usl.paperless.tag"].browse(
                context.get("tag_record_ids") or [],
            ).exists()
            payload = {}
            conflicts = []
            combined_tags = document.tag_ids | tags
            if combined_tags != document.tag_ids:
                payload["tags"] = combined_tags.mapped("paperless_id")
            if not document.document_type_id and context.get(
                "document_type_paperless_id",
            ):
                payload["document_type"] = context["document_type_paperless_id"]
            elif (
                document.document_type_id
                and context.get("document_type_record_id")
                and document.document_type_id.id
                != context["document_type_record_id"]
            ):
                conflicts.append(_("document type"))
            if not document.correspondent_id and context.get(
                "correspondent_paperless_id",
            ):
                payload["correspondent"] = context["correspondent_paperless_id"]
            elif (
                document.correspondent_id
                and context.get("correspondent_record_id")
                and document.correspondent_id.id
                != context["correspondent_record_id"]
            ):
                conflicts.append(_("correspondent"))
            if not document.document_date and context.get("document_date"):
                payload["created"] = context["document_date"]
            if payload:
                refreshed = document._paperless().update_document_metadata(
                    document.paperless_id, payload,
                )
                cache_values = document._paperless_values(refreshed)
                cache_values.pop("source", None)
                document.sudo().with_context(
                    usl_documents_cache_write=True,
                ).write(cache_values)
            incoming_confidentiality = context.get("confidentiality")
            confidentiality_order = {
                "internal": 0,
                "accounting": 1,
                "hr": 2,
                "private": 3,
            }
            confidentiality = document.confidentiality or incoming_confidentiality
            if (
                confidentiality
                and incoming_confidentiality
                and confidentiality != incoming_confidentiality
            ):
                conflicts.append(_("confidentiality"))
                confidentiality = max(
                    (confidentiality, incoming_confidentiality),
                    key=confidentiality_order.get,
                )
            access_scopes = {
                document.access_scope,
                context.get("access_scope"),
            }
            if conflicts:
                document.sudo().with_context(
                    usl_documents_cache_write=True,
                ).write(
                    {
                        "review_state": "needs_attention",
                        "last_error": _(
                            "This file is linked from records suggesting different "
                            "archive metadata (%s). Review its classification.",
                        )
                        % ", ".join(conflicts),
                    },
                )
            policy = {
                "company_id": document.company_id.id or context.get("company_id"),
                "confidentiality": confidentiality or "internal",
                "accounting_evidence": (
                    document.accounting_evidence
                    or bool(context.get("accounting_evidence"))
                ),
                "access_scope": (
                    "linked_record"
                    if "linked_record" in access_scopes
                    else (
                        context.get("access_scope")
                        or document.access_scope
                        or "company"
                    )
                ),
            }
            document.sudo().with_context(
                usl_documents_policy_write=True,
            ).write(policy)
            for target in context.get("related_records") or []:
                document.sudo().with_context(
                    usl_documents_linked_by_id=actor.id,
                    usl_documents_defer_access_sync=True,
                ).link_to_record(
                    target["model"],
                    int(target["id"]),
                    version_id=target.get("version_id"),
                )
            document._recompute_linked_record_access(sync_permissions=True)
        return True

    def _register_trashed_archive_context(self, context, *, submitted_by=None):
        """Keep source Trash intent while retaining the Odoo business links."""
        for document in self:
            actor = submitted_by or self.env.user
            for target in context.get("related_records") or []:
                record = self.env[target["model"]].with_user(actor).browse(
                    int(target["id"]),
                ).exists()
                if not record:
                    raise UserError(_("A related business record no longer exists."))
                record.check_access("read")
                document.sudo().with_context(
                    usl_documents_linked_by_id=actor.id,
                    usl_documents_defer_access_sync=True,
                    usl_documents_allow_trashed_link=True,
                ).link_to_record(
                    target["model"],
                    int(target["id"]),
                    version_id=target.get("version_id"),
                )
            document.sudo().with_context(
                usl_documents_cache_write=True,
            ).write(
                {
                    "review_state": "needs_attention",
                    "last_error": _(
                        "This file is still attached to an Odoo record, but its "
                        "matching archive document is in Trash. Restore it to apply "
                        "the current classification and archive access.",
                    ),
                },
            )
            document._recompute_linked_record_access(sync_permissions=False)
        return True


class IrAttachment(models.Model):
    _inherit = "ir.attachment"

    _usl_documents_unsupported_archive_suffixes = frozenset({".ics", ".xml", ".zip"})
    _usl_documents_inline_image_name = re.compile(
        r"^dbfamilycid\d+\.(?:gif|jpe?g|png|webp)$",
        re.IGNORECASE,
    )

    @api.model_create_multi
    def create(self, values_list):
        attachments = super().create(values_list)
        attachments._queue_usl_documents_archive()
        return attachments

    def _usl_documents_archive_eligibility(self):
        self.ensure_one()
        if self.type != "binary" or not self.res_model or not self.res_id:
            return False, "not_a_stored_business_file"
        if self.res_field:
            return False, "binary_or_image_field"
        if self.res_model not in self.env["usl.document.link"]._allowed_models():
            return False, "unsupported_business_model"
        if not self.file_size or not self.checksum:
            return False, "empty_or_unreadable"
        name = str(self.name or "").strip()
        normalized_name = name.casefold()
        suffix = f".{normalized_name.rsplit('.', 1)[-1]}" if "." in normalized_name else ""
        if suffix in self._usl_documents_unsupported_archive_suffixes:
            # Paperless intentionally supports document formats it can consume.
            # Keep other authoritative evidence on the native business record and
            # classify it explicitly instead of feeding a permanent retry loop.
            return False, "unsupported_archive_format"
        if self._usl_documents_inline_image_name.fullmatch(name):
            return False, "inline_message_image"
        if str(self.mimetype or "").casefold().startswith("image/") and self.file_size <= 512:
            # Real evidence cannot be meaningfully inspected at this size. These
            # are mail-client tracking pixels and disposable placeholder images.
            return False, "inline_or_placeholder_image"
        record = self.env[self.res_model].browse(self.res_id).exists()
        if not record:
            return False, "missing_business_record"
        policy = record._document_archive_policy(self)
        if not policy.get("archive", True):
            return False, policy.get("reason") or "record_policy"
        return True, False

    def _queue_usl_documents_archive(self):
        if self.env.context.get("usl_documents_skip_attachment_queue"):
            return self.env["usl.document.operation"]
        queued = self.env["usl.document.operation"]
        for attachment in self:
            eligible, _reason = attachment._usl_documents_archive_eligibility()
            if eligible:
                queued |= self.env["usl.document.operation"].queue_attachment(
                    attachment,
                )
        return queued

    def _post_add_create(self, **kwargs):
        result = super()._post_add_create(**kwargs)
        self._queue_usl_documents_archive()
        return result

    def write(self, values):
        archive_target_changed = bool(
            {"raw", "res_model", "res_id", "res_field"}.intersection(
                values,
            ),
        )
        result = super().write(values)
        if archive_target_changed:
            self._queue_usl_documents_archive()
        return result


class MailThread(models.AbstractModel):
    _inherit = "mail.thread"

    def _message_post_after_hook(self, message, msg_values):
        result = super()._message_post_after_hook(message, msg_values)
        attachment_ids = [
            command[1]
            for command in (msg_values.get("attachment_ids") or [])
            if command and command[0] == Command.LINK
        ]
        attachments = self.env["ir.attachment"].browse(attachment_ids).exists()
        body = str(msg_values.get("body") or "")
        inline_ids = {
            int(value)
            for value in re.findall(r"/(?:web/image|web/content)/(\d+)", body)
        }
        inline = attachments.filtered(lambda attachment: attachment.id in inline_ids)
        if inline:
            self.env["usl.document.operation"].sudo().search(
                [
                    ("source_attachment_id", "in", inline.ids),
                    ("state", "=", "pending"),
                ],
            ).unlink()
        (attachments - inline)._queue_usl_documents_archive()
        return result


class MailFollowers(models.Model):
    _inherit = "mail.followers"

    @api.model_create_multi
    def create(self, values_list):
        followers = super().create(values_list)
        followers._refresh_linked_document_access()
        return followers

    def unlink(self):
        targets = [(item.res_model, item.res_id) for item in self]
        result = super().unlink()
        self._refresh_linked_document_access(targets=targets)
        return result

    def _refresh_linked_document_access(self, *, targets=None):
        targets = targets or [(item.res_model, item.res_id) for item in self]
        supported = self.env["usl.document.link"]._allowed_models()
        for model_name, record_id in set(targets):
            if model_name not in supported or model_name not in self.env:
                continue
            record = self.env[model_name].sudo().browse(record_id).exists()
            if record and hasattr(record, "_document_refresh_linked_access"):
                record._document_refresh_linked_access()
        return True


class UslDocumentOperation(models.Model):
    _inherit = "usl.document.operation"

    source_attachment_id = fields.Many2one(
        "ir.attachment", readonly=True, index=True, ondelete="set null",
    )
    source_attachment_checksum = fields.Char(readonly=True, index=True)
    metadata_hash = fields.Char(readonly=True, index=True)
    context_json = fields.Json(readonly=True)
    accounting_evidence = fields.Boolean(readonly=True)
    access_scope = fields.Selection(
        [("company", "Company policy"), ("linked_record", "Linked record access")],
        readonly=True,
        default="linked_record",
    )
    next_attempt_at = fields.Datetime(readonly=True, index=True)
    attempt_count = fields.Integer(readonly=True)
    review_reason = fields.Selection(
        [("paperless_trash", "Matching archive document is in Trash")],
        readonly=True,
        index=True,
    )

    _source_version_unique = models.Constraint(
        "UNIQUE(source_attachment_id, source_attachment_checksum)",
        "This attachment version is already queued for archival.",
    )

    def write(self, values):
        result = super().write(values)
        if values.get("state") == "archived":
            for operation in self.filtered("source_attachment_id"):
                superseded = self.sudo().search(
                    [
                        ("id", "<", operation.id),
                        (
                            "source_attachment_id",
                            "=",
                            operation.source_attachment_id.id,
                        ),
                        ("state", "in", ("failed", "duplicate")),
                        ("acknowledged", "=", False),
                    ],
                )
                if superseded:
                    superseded.sudo().write(
                        {
                            "acknowledged": True,
                            "acknowledged_at": fields.Datetime.now(),
                        },
                    )
        return result

    @api.model
    def queue_attachment(self, attachment, *, source="odoo_attachment"):
        attachment.ensure_one()
        eligible, _reason = attachment._usl_documents_archive_eligibility()
        if not eligible:
            return self.browse()
        existing = self.sudo().search(
            [
                ("source_attachment_id", "=", attachment.id),
                ("source_attachment_checksum", "=", attachment.checksum),
            ],
            limit=1,
        )
        if existing:
            if source != "odoo_attachment" and existing.source == "odoo_attachment":
                existing.sudo().write({"source": source})
            if existing.state == "failed":
                existing.sudo().write(
                    {
                        "state": "pending",
                        "next_attempt_at": False,
                        "error_message": False,
                        "review_reason": False,
                        "acknowledged": False,
                        "acknowledged_at": False,
                    },
                )
            return existing
        record = self.env[attachment.res_model].browse(attachment.res_id).exists()
        context = record._document_archive_context(attachment)
        metadata_hash = self.env["usl.document"]._archive_metadata_hash(context)
        previous = self.sudo().search(
            [
                ("source_attachment_id", "=", attachment.id),
                ("state", "=", "archived"),
                ("document_id", "!=", False),
            ],
            order="id desc",
            limit=1,
        )
        return self.sudo().create(
            {
                "name": attachment.name or _("Untitled attachment"),
                "state": "pending",
                "checksum": attachment.checksum,
                "mime_type": attachment.mimetype,
                "company_id": context["company_id"],
                "confidentiality": context.get("confidentiality") or "internal",
                "accounting_evidence": bool(context.get("accounting_evidence")),
                "access_scope": context.get("access_scope") or "linked_record",
                "res_model": attachment.res_model,
                "res_id": attachment.res_id,
                "source": source,
                "source_attachment_id": attachment.id,
                "source_attachment_checksum": attachment.checksum,
                "metadata_hash": metadata_hash,
                "context_json": context,
                "target_document_id": previous.document_id.id or False,
                "user_id": attachment.create_uid.id or self.env.user.id,
            },
        )

    def _process_native_attachment(self):
        self.ensure_one()
        attachment = self.source_attachment_id.exists()
        if not attachment:
            self.sudo().write(
                {"state": "failed", "error_message": _("The Odoo file was removed.")},
            )
            return False
        if attachment.checksum != self.source_attachment_checksum:
            self.sudo().write(
                {
                    "state": "failed",
                    "error_message": _("The Odoo file changed before it was archived."),
                },
            )
            attachment._queue_usl_documents_archive()
            return False
        try:
            content = bytes(attachment.raw)
            content_base64 = base64.b64encode(content).decode()
            if self.target_document_id:
                source_record = self.env[attachment.res_model].browse(
                    attachment.res_id,
                ).exists()
                if not source_record:
                    self.sudo().write(
                        {
                            "state": "failed",
                            "error_message": _(
                                "The Odoo record was removed before archival.",
                            ),
                        },
                    )
                    return False
                raw_context = source_record._document_archive_context(attachment)
                metadata_hash = self.env["usl.document"]._archive_metadata_hash(
                    raw_context,
                )
                archive_context = self.env["usl.document"]._prepare_archive_context(
                    source_record,
                    attachment,
                )
                task_id = self.env["usl.document"]._paperless().update_version(
                    self.target_document_id.paperless_id,
                    content,
                    attachment.name,
                    attachment.mimetype,
                    version_label=attachment.name,
                )
                self.sudo().write(
                    {
                        "state": "processing",
                        "checksum": attachment.checksum,
                        "metadata_hash": metadata_hash,
                        "paperless_task_id": task_id,
                        "context_json": archive_context,
                        "attempt_count": self.attempt_count + 1,
                        "next_attempt_at": False,
                        "error_message": False,
                        "review_reason": False,
                    },
                )
                return True
            checksum = hashlib.sha256(content).hexdigest()
            source_record = self.env[attachment.res_model].browse(
                attachment.res_id,
            ).exists()
            if not source_record:
                self.sudo().write(
                    {
                        "state": "failed",
                        "attempt_count": self.attempt_count + 1,
                        "next_attempt_at": False,
                        "review_reason": False,
                        "error_message": _(
                            "The Odoo record was removed before archival.",
                        ),
                    },
                )
                return False
            raw_context = source_record._document_archive_context(attachment)
            metadata_hash = self.env["usl.document"]._archive_metadata_hash(
                raw_context,
            )
            trashed, _matching_version = self.env[
                "usl.document"
            ]._find_archive_fingerprint(
                checksum,
                metadata_hash,
                company=self.company_id,
                availability_state="trashed",
            )
            if trashed:
                archive_context = self.env["usl.document"]._prepare_archive_context(
                    source_record,
                    attachment,
                )
                trashed._register_trashed_archive_context(
                    archive_context,
                    submitted_by=self.user_id,
                )
                self.sudo().write(
                    {
                        "state": "failed",
                        "checksum": checksum,
                        "metadata_hash": metadata_hash,
                        "document_id": trashed.id,
                        "context_json": archive_context,
                        "attempt_count": self.attempt_count + 1,
                        "next_attempt_at": False,
                        "review_reason": "paperless_trash",
                        "error_message": _(
                            "The matching archive document is in Trash. Restore it "
                            "to complete archival; the native Odoo file remains "
                            "available.",
                        ),
                    },
                )
                return False
            self.env["usl.document"].sudo().with_context(
                usl_documents_operation_id=self.id,
            ).upload_from_odoo(
                attachment.name,
                content_base64,
                attachment.mimetype,
                res_model=attachment.res_model,
                res_id=attachment.res_id,
                company_id=self.company_id.id,
                source=self.source or "odoo_attachment",
            )
        except PaperlessUnavailable as error:
            delay = min(60, 2 ** max(0, self.attempt_count))
            self.sudo().write(
                {
                    "state": "pending",
                    "attempt_count": self.attempt_count + 1,
                    "next_attempt_at": fields.Datetime.now() + timedelta(minutes=delay),
                    "error_message": str(error),
                    "review_reason": False,
                },
            )
            return False
        except PaperlessError as error:
            self.sudo().write(
                {
                    "state": "failed",
                    "attempt_count": self.attempt_count + 1,
                    "error_message": str(error),
                    "review_reason": False,
                },
            )
            return False
        except UserError as error:
            self.sudo().write(
                {
                    "state": "failed",
                    "attempt_count": self.attempt_count + 1,
                    "next_attempt_at": False,
                    "review_reason": False,
                    "error_message": str(error),
                },
            )
            return False
        return True

    @api.model
    def cron_process_attachment_queue(self):
        self._cron_queue_existing_attachments()
        now = fields.Datetime.now()
        operations = self.sudo().search(
            [
                ("state", "=", "pending"),
                ("source_attachment_id", "!=", False),
                "|",
                ("next_attempt_at", "=", False),
                ("next_attempt_at", "<=", now),
            ],
            order="id",
            limit=20,
        )
        for operation in operations:
            operation._process_native_attachment()
        return len(operations)

    @api.model
    def _cron_queue_existing_attachments(self):
        parameters = self.env["ir.config_parameter"].sudo()
        if parameters.get_str(
            "usl_documents.attachment_backfill_state", "pending",
        ) == "complete":
            return False
        cursor = parameters.get_int(
            "usl_documents.attachment_backfill_cursor", 0,
        )
        result = self.queue_existing_attachments(after_id=cursor, limit=200)
        parameters.set_int(
            "usl_documents.attachment_backfill_cursor", result["last_id"],
        )
        if result["complete"]:
            parameters.set_str(
                "usl_documents.attachment_backfill_state", "complete",
            )
        return result

    @api.model
    def queue_existing_attachments(self, *, after_id=0, limit=200):
        attachments = self.env["ir.attachment"].sudo().search(
            [
                ("id", ">", int(after_id)),
                ("type", "=", "binary"),
                ("res_field", "=", False),
                ("res_model", "in", sorted(self.env["usl.document.link"]._allowed_models())),
                ("res_id", ">", 0),
            ],
            order="id",
            limit=min(max(int(limit), 1), 1000),
        )
        queued = self.browse()
        for attachment in attachments:
            queued |= attachment._queue_usl_documents_archive()
        return {
            "scanned": len(attachments),
            "queued": len(queued),
            "last_id": attachments[-1].id if attachments else int(after_id),
            "complete": len(attachments) < min(max(int(limit), 1), 1000),
        }


class ProjectCollaborator(models.Model):
    _inherit = "project.collaborator"

    @api.model_create_multi
    def create(self, values_list):
        collaborators = super().create(values_list)
        collaborators.mapped("project_id")._document_refresh_linked_access()
        return collaborators

    def write(self, values):
        projects = self.mapped("project_id")
        result = super().write(values)
        if {"project_id", "partner_id", "limited_access"}.intersection(values):
            (projects | self.mapped("project_id"))._document_refresh_linked_access()
        return result

    def unlink(self):
        projects = self.mapped("project_id")
        result = super().unlink()
        projects._document_refresh_linked_access()
        return result
