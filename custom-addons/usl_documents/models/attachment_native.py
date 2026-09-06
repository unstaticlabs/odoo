"""Native attachment, chatter, follower and collaborator extensions that feed the Documents archive."""

import re

from odoo import (
    SUPERUSER_ID,
    Command,
    _,
    api,
    fields,
    models,
)
from odoo.exceptions import AccessError, UserError, ValidationError

from .attachment_bridge import ATTACHMENT_LEDGER_STATES, ORIGIN_CAPTURE_TOKEN
from .document import ARCHIVE_MODES, ATTACHMENT_ORIGINS, DOCUMENT_ROLES


class IrAttachment(models.Model):
    _inherit = "ir.attachment"

    _usl_documents_unsupported_archive_suffixes = frozenset(
        {".htm", ".html", ".ics", ".xml", ".zip"},
    )
    _usl_documents_inline_image_name = re.compile(
        r"^dbfamilycid\d+\.(?:gif|jpe?g|png|webp)$",
        re.IGNORECASE,
    )
    usl_documents_origin = fields.Selection(
        ATTACHMENT_ORIGINS,
        string="Documents origin",
        readonly=True,
        copy=False,
        index=True,
    )
    usl_documents_archive_mode = fields.Selection(
        ARCHIVE_MODES,
        string="Documents archive mode",
        readonly=True,
        copy=False,
        index=True,
    )
    usl_documents_document_role = fields.Selection(
        DOCUMENT_ROLES,
        string="Documents role",
        readonly=True,
        copy=False,
        index=True,
    )
    usl_documents_policy_reason = fields.Char(
        string="Documents policy reason",
        readonly=True,
        copy=False,
        index=True,
    )
    usl_documents_ledger_state = fields.Selection(
        ATTACHMENT_LEDGER_STATES,
        string="Documents ledger state",
        readonly=True,
        copy=False,
        default="unresolved",
        index=True,
    )

    def _store_attachment_fields(self, res, **kwargs):
        """Tell the viewer whether it may persist a generated thumbnail.

        A user may be allowed to update the business record while the attachment
        itself is immutable.  Paid expenses deliberately use that distinction.
        The standard viewer otherwise infers attachment write access from the
        parent record and attempts a forbidden thumbnail write.
        """
        res.attr(
            "uslCanUpdateThumbnail",
            lambda attachment: attachment.sudo(False).has_access("write"),
        )
        return super()._store_attachment_fields(res, **kwargs)

    @api.model
    def _usl_documents_policy_fields(self):
        return {
            "usl_documents_origin",
            "usl_documents_archive_mode",
            "usl_documents_document_role",
            "usl_documents_policy_reason",
            "usl_documents_ledger_state",
        }

    @api.model
    def _usl_documents_trusted_origin(self):
        if self.env.context.get("usl_documents_origin_token") is not ORIGIN_CAPTURE_TOKEN:
            return False
        origin = self.env.context.get("usl_documents_attachment_origin")
        return origin if origin in dict(ATTACHMENT_ORIGINS) else False

    def _usl_documents_default_origin(self):
        self.ensure_one()
        return self._usl_documents_trusted_origin() or (
            "portal" if self.env.user.share else "direct_record"
        )

    @api.model_create_multi
    def create(self, values_list):
        if not self._usl_documents_trusted_origin():
            protected = self._usl_documents_policy_fields()
            values_list = [
                {
                    key: value
                    for key, value in values.items()
                    if key not in protected
                }
                for values in values_list
            ]
        attachments = super().create(values_list)
        attachments._queue_usl_documents_archive()
        return attachments

    def _usl_documents_technical_exclusion(self):
        self.ensure_one()
        if self.type != "binary" or not self.res_model or not self.res_id:
            return "not_a_stored_business_file"
        if self.res_field:
            return "binary_or_image_field"
        if self.res_model not in self.env["usl.document.link"]._allowed_models():
            return "unsupported_business_model"
        if not self.file_size or not self.checksum:
            return "empty_or_unreadable"
        name = str(self.name or "").strip()
        normalized_name = name.casefold()
        suffix = f".{normalized_name.rsplit('.', 1)[-1]}" if "." in normalized_name else ""
        if suffix in self._usl_documents_unsupported_archive_suffixes:
            # Paperless intentionally supports document formats it can consume.
            # Keep other authoritative evidence on the native business record and
            # classify it explicitly instead of feeding a permanent retry loop.
            return "unsupported_archive_format"
        if self._usl_documents_inline_image_name.fullmatch(name):
            return "inline_message_image"
        if (
            str(self.mimetype or "").casefold().startswith("image/")
            and self.file_size <= 4 * 1024
        ):
            # Real evidence cannot be meaningfully inspected at this size. These
            # are mail-client tracking pixels and disposable placeholder images.
            return "inline_or_placeholder_image"
        record = self.env[self.res_model].browse(self.res_id).exists()
        if not record:
            return "missing_business_record"
        return False

    def _usl_documents_capture_policy(
        self,
        *,
        origin=None,
        refresh=False,
        exclusion_reason=None,
    ):
        self.ensure_one()
        origin = origin or self.usl_documents_origin or self._usl_documents_default_origin()
        if origin not in dict(ATTACHMENT_ORIGINS):
            raise UserError(_("The attachment origin is not supported."))
        if refresh or self.usl_documents_origin != origin:
            self.sudo().with_context(
                usl_documents_attachment_policy_write=True,
            ).write({"usl_documents_origin": origin})

        exclusion_reason = exclusion_reason or self._usl_documents_technical_exclusion()
        if exclusion_reason:
            policy = {
                "archive_mode": "never",
                "document_role": "background",
                "policy_reason": exclusion_reason,
            }
        else:
            record = self.env[self.res_model].browse(self.res_id).exists()
            policy = record._document_archive_policy(self)
        archive_mode = policy.get("archive_mode")
        document_role = policy.get("document_role")
        policy_reason = str(policy.get("policy_reason") or "").strip()
        if (
            archive_mode not in dict(ARCHIVE_MODES)
            or document_role not in dict(DOCUMENT_ROLES)
            or not policy_reason
        ):
            raise UserError(_("The business record returned an incomplete archive policy."))
        ledger_state = {
            "mandatory": "pending",
            "automatic": "pending",
            "on_request": "native_only_on_request",
            "never": "explicitly_excluded",
        }[archive_mode]
        values = {
            "usl_documents_origin": origin,
            "usl_documents_archive_mode": archive_mode,
            "usl_documents_document_role": document_role,
            "usl_documents_policy_reason": policy_reason,
            "usl_documents_ledger_state": ledger_state,
        }
        if refresh or any(self[field] != value for field, value in values.items()):
            self.sudo().with_context(
                usl_documents_attachment_policy_write=True,
            ).write(values)
        if archive_mode == "never":
            obsolete_failures = self.env["usl.document.operation"].sudo().search(
                [
                    ("source_attachment_id", "=", self.id),
                    ("state", "in", ("failed", "duplicate")),
                    ("acknowledged", "=", False),
                ],
            )
            if obsolete_failures:
                obsolete_failures.write(
                    {
                        "acknowledged": True,
                        "acknowledged_at": fields.Datetime.now(),
                    },
                )
        return {
            **policy,
            "archive_mode": archive_mode,
            "document_role": document_role,
            "policy_reason": policy_reason,
            "attachment_origin": origin,
        }

    def _usl_documents_archive_eligibility(
        self,
        *,
        force_on_request=False,
        origin=None,
        refresh=False,
        exclusion_reason=None,
    ):
        self.ensure_one()
        policy = self._usl_documents_capture_policy(
            origin=origin,
            refresh=refresh,
            exclusion_reason=exclusion_reason,
        )
        if policy["archive_mode"] == "never":
            return False, policy["policy_reason"]
        if policy["archive_mode"] == "on_request" and not force_on_request:
            return False, "on_request"
        return True, False

    def _queue_usl_documents_archive(
        self,
        *,
        force_on_request=False,
        origin=None,
        refresh=False,
    ):
        if self.env.context.get("usl_documents_skip_attachment_queue"):
            return self.env["usl.document.operation"]
        queued = self.env["usl.document.operation"]
        for attachment in self:
            # Capture source audit timestamps before policy resolution writes its
            # archival ledger fields on the attachment.  Those internal writes
            # must never become the document's historical modification date.
            original_created_at = fields.Datetime.to_datetime(
                self.env.context.get("usl_documents_original_created_at"),
            ) or attachment.create_date
            original_modified_at = fields.Datetime.to_datetime(
                self.env.context.get("usl_documents_original_modified_at"),
            ) or attachment.write_date or original_created_at
            eligible, _reason = attachment._usl_documents_archive_eligibility(
                force_on_request=force_on_request,
                origin=origin,
                refresh=refresh,
            )
            if eligible:
                queued |= (
                    self.env["usl.document.operation"]
                    .with_context(
                        usl_documents_original_created_at=original_created_at,
                        usl_documents_original_modified_at=original_modified_at,
                    )
                    ._queue_attachment(
                        attachment,
                        source=(
                            "odoo_generated"
                            if attachment.usl_documents_origin == "generated_final"
                            else "odoo_attachment"
                        ),
                        force_on_request=force_on_request,
                    )
                )
        return queued

    def _post_add_create(self, **kwargs):
        result = super()._post_add_create(**kwargs)
        self._queue_usl_documents_archive()
        return result

    def write(self, values):
        protected = self._usl_documents_policy_fields().intersection(values)
        internal_policy_write = (
            self.env.su
            and self.env.context.get("usl_documents_attachment_policy_write")
        )
        if protected and not internal_policy_write:
            raise AccessError(
                _("Attachment archive policy can only change through Documents."),
            )
        archive_target_changed = bool(
            {"raw", "res_model", "res_id", "res_field"}.intersection(
                values,
            ),
        )
        policy_target_changed = bool(
            {"res_model", "res_id", "res_field"}.intersection(values),
        )
        result = super().write(values)
        if archive_target_changed and not internal_policy_write:
            self._queue_usl_documents_archive(refresh=policy_target_changed)
        return result

    def action_keep_in_documents(self):
        if not self.env.user.has_group("usl_documents.group_documents_user"):
            raise AccessError(_("Documents access is required to keep this file."))
        required_crons = (
            self.env.ref("usl_documents.ir_cron_usl_documents_attachment_queue"),
            self.env.ref("usl_documents.ir_cron_usl_documents_poll"),
        )
        if any(not cron.sudo().active for cron in required_crons):
            raise UserError(
                _(
                    "Documents archiving is paused. The original file is still "
                    "attached to this record; ask a Documents administrator to "
                    "resume archive processing.",
                ),
            )
        operations = self.env["usl.document.operation"]
        for attachment in self:
            attachment.check_access("read")
            operations |= attachment._queue_usl_documents_archive(
                force_on_request=True,
            )
        if not operations:
            raise UserError(_("This file cannot be kept in Documents."))
        return operations

    @api.model
    def get_keep_in_documents_states(self, attachment_ids):
        return {
            attachment_id: detail["state"]
            for attachment_id, detail in self.get_keep_in_documents_details(
                attachment_ids,
            ).items()
            if detail["state"] == "available"
        }

    @api.model
    def get_keep_in_documents_details(self, attachment_ids):
        if not self.env.user.has_group("usl_documents.group_documents_user"):
            return {}
        try:
            normalized_ids = list(
                dict.fromkeys(
                    int(item) for item in (attachment_ids or []) if int(item) > 0
                ),
            )[:200]
        except (TypeError, ValueError) as error:
            raise ValidationError(_("Invalid attachment selection.")) from error
        attachments = self.browse(normalized_ids).exists()
        visible_attachments = self.browse()
        for attachment in attachments:
            try:
                attachment.check_access("read")
            except AccessError:
                continue
            visible_attachments |= attachment
        operations = self.env["usl.document.operation"].sudo().search(
            [("source_attachment_id", "in", visible_attachments.ids)],
            order="id desc",
        )
        latest_by_attachment = {}
        for operation in operations:
            latest_by_attachment.setdefault(
                operation.source_attachment_id.id,
                operation,
            )
        details = {}
        for attachment in visible_attachments:
            operation = latest_by_attachment.get(attachment.id)
            if operation:
                document = operation.document_id or operation.target_document_id
                visible_document = self.env["usl.document"]
                if document:
                    visible_document = document.with_user(
                        self.env.user,
                    )._filtered_access("read")
                state = operation.state
                status_label = {
                    "pending": _("Queued for Documents"),
                    "uploading": _("Sending to Documents"),
                    "processing": _("Documents is indexing this file"),
                    "archived": _("Open in Documents"),
                    "duplicate": _("Review the matching document"),
                    "failed": _("Documents archiving needs review"),
                }[state]
                details[str(attachment.id)] = {
                    "state": state,
                    "status_label": status_label,
                    "operation_id": operation.id,
                    "document_id": visible_document.id or False,
                    "error": operation.error_message or False,
                }
                if visible_document and state in {"archived", "duplicate"}:
                    res_model = attachment.res_model or operation.res_model
                    res_id = attachment.res_id or operation.res_id
                    normalized_res_id = int(res_id or 0)
                    record = (
                        self.env[res_model].browse(normalized_res_id).exists()
                        if res_model in self.env and normalized_res_id
                        else False
                    )
                    can_remove = bool(
                        attachment.has_access("write")
                        and visible_document.has_access("write")
                        and record
                        and record.has_access("write"),
                    )
                    active_links = visible_document.sudo().link_ids.filtered("active")
                    current_links = active_links.filtered(
                        lambda link: (
                            link.res_model == res_model
                            and link.res_id == normalized_res_id
                        ),
                    )
                    details[str(attachment.id)].update(
                        {
                            "can_remove_from_record": can_remove,
                            "can_move_to_trash": bool(
                                can_remove
                                and visible_document.availability_state == "available"
                                and not (active_links - current_links),
                            ),
                        },
                    )
                continue
            if (
                attachment.usl_documents_archive_mode == "on_request"
                and attachment.usl_documents_ledger_state
                == "native_only_on_request"
            ):
                details[str(attachment.id)] = {
                    "state": "available",
                    "status_label": _("Keep in Documents"),
                    "operation_id": False,
                    "document_id": False,
                    "error": False,
                }
        return details

    def action_open_in_documents(self):
        self.ensure_one()
        self.check_access("read")
        operation = self._usl_documents_archived_operation()
        document = operation.document_id or operation.target_document_id
        if not document:
            raise UserError(_("This attachment is not yet available in Documents."))
        document = document.with_user(self.env.user)
        document.check_access("read")
        action = self.env.ref("usl_documents.action_documents_workspace").read()[0]
        record_name = self.res_name or self.name
        action["params"] = {
            "initial_document_id": document.id,
            "res_model": self.res_model,
            "res_id": self.res_id,
            "record_name": record_name,
            "linked_filter": True,
        }
        return action

    def _usl_documents_archived_operation(self):
        """Return the latest durable archive for this exact attachment."""
        self.ensure_one()
        return self.env["usl.document.operation"].sudo().search(
            [
                ("source_attachment_id", "=", self.id),
                ("state", "in", ("archived", "duplicate")),
                "|",
                ("document_id", "!=", False),
                ("target_document_id", "!=", False),
            ],
            order="id desc",
            limit=1,
        )

    def action_remove_archived_from_record(self, removal="unlink"):
        """Remove a record attachment without deleting its archived original."""
        self.ensure_one()
        if removal not in {"unlink", "trash"}:
            raise ValidationError(_("Choose a supported document removal action."))
        self.check_access("write")
        operation = self._usl_documents_archived_operation()
        document = operation.document_id or operation.target_document_id
        if not document:
            raise UserError(
                _(
                    "This file is not safely archived yet. Keep it in Documents "
                    "and wait for archiving to finish before removing it.",
                ),
            )
        document = document.with_user(self.env.user)
        document.check_access("write")
        res_model = self.res_model or operation.res_model
        res_id = self.res_id or operation.res_id
        if (
            res_model not in self.env["usl.document.link"]._allowed_models()
            or not res_id
        ):
            raise ValidationError(
                _("This attachment is not linked to a supported business record."),
            )
        record = self.env[res_model].browse(int(res_id)).exists()
        if not record:
            raise ValidationError(_("The linked Odoo record no longer exists."))
        record.check_access("write")

        active_links = document.sudo().link_ids.filtered("active")
        current_links = active_links.filtered(
            lambda link: link.res_model == res_model and link.res_id == int(res_id),
        )
        if removal == "trash" and active_links - current_links:
            raise UserError(
                _(
                    "This document still supports another Odoo record. Unlink it "
                    "from this record without moving the shared archive to Trash.",
                ),
            )
        if current_links:
            document.unlink_from_record(res_model, res_id)
        if removal == "trash":
            document.move_to_trash()

        message = self.env["mail.message"].sudo().search(
            [("attachment_ids", "in", self.ids)],
            limit=1,
        )
        attachment_name = self.name
        # Authorization was verified above. Scoped elevation only removes the
        # redundant Odoo copy and notifies open clients; it never edits the
        # system message that originally carried the attachment.
        self.with_user(SUPERUSER_ID)._delete_and_notify(message)
        if removal == "trash":
            return {
                "removed": True,
                "message": _(
                    "“%(attachment)s” was unlinked and moved to Documents Trash.",
                    attachment=attachment_name,
                ),
            }
        return {
            "removed": True,
            "message": _(
                "“%(attachment)s” was unlinked. The archived document remains in Documents.",
                attachment=attachment_name,
            ),
        }

    def action_keep_in_documents_from_ui(self):
        self.ensure_one()
        operation = self.action_keep_in_documents()
        return {
            "attachment_id": self.id,
            "operation_id": operation.id,
            "state": operation.state,
            "detail": self.get_keep_in_documents_details([self.id]).get(
                str(self.id),
            ),
            "message": _(
                "Archiving started. The original stays attached here; Documents "
                "will reuse an identical archived file when safe, or create one "
                "linked archive document.",
            ),
        }


class MailThread(models.AbstractModel):
    _inherit = "mail.thread"

    def message_post(self, **kwargs):
        if kwargs.get("attachments") and (
            self.env.context.get("usl_documents_origin_token")
            is not ORIGIN_CAPTURE_TOKEN
        ):
            origin = "portal" if self.env.user.share else "chatter"
            return super(
                MailThread,
                self.with_context(
                    usl_documents_origin_token=ORIGIN_CAPTURE_TOKEN,
                    usl_documents_attachment_origin=origin,
                ),
            ).message_post(**kwargs)
        return super().message_post(**kwargs)

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
            for attachment in inline:
                attachment._usl_documents_capture_policy(
                    origin=attachment.usl_documents_origin or "chatter",
                    refresh=True,
                    exclusion_reason="inline_message_image",
                )
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
