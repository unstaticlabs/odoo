import base64
import hashlib
import json
import logging
from datetime import datetime, timezone

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError

from .paperless_client import (
    PaperlessClient,
    PaperlessError,
)

_logger = logging.getLogger(__name__)


CONFIDENTIALITIES = [
    ("internal", "Internal"),
    ("accounting", "Accounting evidence"),
    ("hr", "HR restricted"),
    ("private", "Creator private"),
]


class UslDocument(models.Model):
    _name = "usl.document"
    _description = "Archived Document"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "document_date desc, paperless_created desc, id desc"

    name = fields.Char(required=True, tracking=True)
    paperless_id = fields.Integer(
        string="Paperless ID", required=True, index=True, readonly=True, copy=False
    )
    paperless_created = fields.Datetime(readonly=True)
    paperless_modified = fields.Datetime(readonly=True)
    document_date = fields.Date(index=True, tracking=True)
    company_id = fields.Many2one(
        "res.company", index=True, tracking=True, ondelete="restrict"
    )
    confidentiality = fields.Selection(
        CONFIDENTIALITIES,
        required=True,
        default="internal",
        index=True,
        tracking=True,
    )
    accounting_evidence = fields.Boolean(index=True, tracking=True)
    review_state = fields.Selection(
        [
            ("needs_attention", "Needs attention"),
            ("classified", "Classified"),
            ("reviewed", "Reviewed"),
        ],
        required=True,
        default="needs_attention",
        index=True,
        tracking=True,
    )
    availability_state = fields.Selection(
        [
            ("available", "Available"),
            ("processing", "Processing"),
            ("missing", "Missing from Paperless"),
            ("trashed", "Trashed in Paperless"),
            ("permission_error", "Permission synchronization failed"),
            ("failed", "Processing failed"),
        ],
        required=True,
        default="available",
        index=True,
        tracking=True,
    )
    original_filename = fields.Char(readonly=True)
    mime_type = fields.Char(readonly=True)
    checksum = fields.Char(index=True, readonly=True)
    archive_checksum = fields.Char(readonly=True)
    correspondent_name = fields.Char(index=True, readonly=True)
    document_type_name = fields.Char(index=True, readonly=True)
    tag_names = fields.Char(readonly=True)
    custom_fields_json = fields.Text(readonly=True)
    version_data_json = fields.Text(readonly=True)
    current_version_label = fields.Char(readonly=True)
    source = fields.Selection(
        [
            ("odoo_upload", "Uploaded from Odoo"),
            ("odoo_attachment", "Archived Odoo attachment"),
            ("odoo_generated", "Odoo-generated authoritative output"),
            ("paperless", "External Paperless ingestion"),
        ],
        required=True,
        default="paperless",
        readonly=True,
    )
    submitted_by_id = fields.Many2one("res.users", readonly=True)
    submitted_at = fields.Datetime(readonly=True)
    permission_sync_state = fields.Selection(
        [("pending", "Pending"), ("synchronized", "Synchronized"), ("failed", "Failed")],
        required=True,
        default="pending",
        tracking=True,
    )
    permission_sync_error = fields.Text(readonly=True)
    link_ids = fields.One2many("usl.document.link", "document_id")
    link_count = fields.Integer(compute="_compute_link_count")
    paperless_url = fields.Char(compute="_compute_paperless_url")
    last_error = fields.Text(readonly=True)

    _paperless_id_unique = models.Constraint(
        "UNIQUE(paperless_id)", "A Paperless document may only be mirrored once."
    )

    @api.depends("link_ids")
    def _compute_link_count(self):
        for document in self:
            document.link_count = len(document.link_ids)

    @api.depends("paperless_id", "permission_sync_state")
    def _compute_paperless_url(self):
        client = self._paperless()
        for document in self:
            document.paperless_url = (
                client.paperless_url(document.paperless_id)
                if (
                    client.public_url
                    and document.paperless_id
                    and document.permission_sync_state == "synchronized"
                )
                else False
            )

    @api.constrains("company_id", "review_state")
    def _check_classified_company(self):
        for document in self:
            if document.review_state != "needs_attention" and not document.company_id:
                raise ValidationError(
                    _("A classified document must belong to a legal company.")
                )

    def _paperless(self):
        return PaperlessClient(self.env)

    def write(self, values):
        policy_fields = {"company_id", "confidentiality", "accounting_evidence"}
        if (
            policy_fields.intersection(values)
            and not self.env.context.get("skip_permission_invalidation")
        ):
            values = {
                **values,
                "permission_sync_state": "pending",
                "permission_sync_error": False,
            }
        return super().write(values)

    def _require_manager(self):
        if not self.env.user.has_group("usl_documents.group_documents_manager"):
            raise AccessError(_("Only Documents administrators may perform this action."))

    @api.model
    def diagnostics(self):
        self._require_manager()
        values = {
            "configured": self._paperless().configured,
            "last_sync": self.env["ir.config_parameter"]
            .sudo()
            .get_str("usl_documents.last_sync"),
            "cached_documents": self.search_count([]),
            "missing_documents": self.search_count(
                [("availability_state", "in", ("missing", "trashed"))]
            ),
            "permission_failures": self.search_count(
                [("permission_sync_state", "=", "failed")]
            ),
        }
        if values["configured"]:
            try:
                values.update(self._paperless().compatibility())
            except PaperlessError as error:
                values.update({"ok": False, "error": str(error)})
        return values

    @api.model
    def _paperless_datetime(self, value):
        if not value:
            return False
        if isinstance(value, datetime):
            parsed = value
        else:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo:
            parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
        return fields.Datetime.to_string(parsed)

    @api.model
    def _paperless_values(self, payload, *, source="paperless"):
        tags = payload.get("tags") or []
        if tags and isinstance(tags[0], dict):
            tag_names = ", ".join(item.get("name", "") for item in tags)
        else:
            tag_names = ", ".join(str(item) for item in tags)
        correspondent = payload.get("correspondent")
        document_type = payload.get("document_type")
        versions = payload.get("versions") or []
        latest_version = (
            versions[-1] if versions and isinstance(versions[-1], dict) else {}
        )
        return {
            "name": payload.get("title") or _("Untitled document"),
            "paperless_id": int(payload["id"]),
            "paperless_created": self._paperless_datetime(payload.get("added")),
            "paperless_modified": self._paperless_datetime(payload.get("modified")),
            "document_date": payload.get("created"),
            "original_filename": payload.get("original_file_name")
            or payload.get("original_filename"),
            "mime_type": payload.get("mime_type"),
            "checksum": payload.get("checksum") or latest_version.get("checksum"),
            "archive_checksum": payload.get("archive_checksum"),
            "correspondent_name": (
                correspondent.get("name") if isinstance(correspondent, dict) else False
            ),
            "document_type_name": (
                document_type.get("name") if isinstance(document_type, dict) else False
            ),
            "tag_names": tag_names,
            "custom_fields_json": json.dumps(
                payload.get("custom_fields") or [], sort_keys=True
            ),
            "version_data_json": json.dumps(versions, sort_keys=True),
            "current_version_label": (
                latest_version.get("version_label")
            ),
            "availability_state": "available",
            "source": source,
            "last_error": False,
        }

    @api.model
    def sync_from_paperless(self, *, full=False, limit_pages=None):
        self._require_manager()
        client = self._paperless()
        client.compatibility()
        params = self.env["ir.config_parameter"].sudo()
        modified_after = None if full else params.get_str("usl_documents.last_sync")
        seen = set()
        page = 1
        while True:
            payload = client.list_documents(
                page=page, page_size=100, modified_after=modified_after
            )
            for item in payload.get("results", []):
                paperless_id = int(item["id"])
                seen.add(paperless_id)
                document = self.sudo().search(
                    [("paperless_id", "=", paperless_id)], limit=1
                )
                values = self._paperless_values(item)
                if document:
                    document.write(values)
                else:
                    self.sudo().create(values)
            if not payload.get("next"):
                break
            page += 1
            if limit_pages and page > limit_pages:
                break
        if full:
            self.sudo().search(
                [("paperless_id", "not in", list(seen)), ("availability_state", "=", "available")]
            ).write({
                "availability_state": "missing",
                "last_error": _("Document was not returned by a full Paperless reconciliation."),
            })
        params.set_str(
            "usl_documents.last_sync",
            fields.Datetime.to_string(fields.Datetime.now()),
        )
        return {"synchronized": len(seen), "pages": page}

    @api.model
    def cron_sync_from_paperless(self):
        manager = self.env.ref("base.user_admin")
        try:
            return self.with_user(manager).sync_from_paperless(limit_pages=20)
        except PaperlessError:
            _logger.exception("Paperless incremental synchronization failed")
            return False

    @api.model
    def workspace_data(
        self,
        *,
        query="",
        workspace="recent",
        page=1,
        page_size=24,
        company_id=None,
        document_type=None,
        sort="recent",
    ):
        page = max(1, int(page))
        page_size = min(100, max(1, int(page_size)))
        domain = []
        if workspace == "attention":
            domain.append(("review_state", "=", "needs_attention"))
        elif workspace == "accounting":
            domain.append(("accounting_evidence", "=", True))
        elif workspace == "contracts":
            domain.append(("document_type_name", "ilike", "contract"))
        elif workspace == "banking":
            domain.extend([
                "|",
                ("document_type_name", "ilike", "bank"),
                ("tag_names", "ilike", "bank"),
            ])
        elif workspace == "tax":
            domain.extend([
                "|",
                ("document_type_name", "ilike", "tax"),
                ("tag_names", "ilike", "tax"),
            ])
        elif workspace == "hr":
            domain.append(("confidentiality", "=", "hr"))
        if company_id:
            domain.append(("company_id", "=", int(company_id)))
        if document_type:
            domain.append(("document_type_name", "=", document_type))
        if query:
            try:
                result = self._paperless().search(query, page=1, page_size=100)
                ids = [int(item["id"]) for item in result.get("results", [])]
                domain.append(("paperless_id", "in", ids))
            except PaperlessError as error:
                return {
                    "documents": [],
                    "count": 0,
                    "degraded": True,
                    "error": str(error),
                }
        order = {
            "recent": "paperless_created desc, id desc",
            "date": "document_date desc, id desc",
            "title": "name asc, id asc",
        }.get(sort, "paperless_created desc, id desc")
        count = self.search_count(domain)
        documents = self.search(
            domain, order=order, offset=(page - 1) * page_size, limit=page_size
        )
        return {
            "documents": [
                {
                    "id": item.id,
                    "name": item.name,
                    "paperless_id": item.paperless_id,
                    "date": item.document_date,
                    "company": item.company_id.display_name,
                    "confidentiality": item.confidentiality,
                    "review_state": item.review_state,
                    "availability_state": item.availability_state,
                    "correspondent": item.correspondent_name,
                    "document_type": item.document_type_name,
                    "tags": item.tag_names,
                    "filename": item.original_filename,
                    "mime_type": item.mime_type,
                    "link_count": item.link_count,
                    "paperless_url": item.paperless_url,
                }
                for item in documents
            ],
            "count": count,
            "page": page,
            "page_size": page_size,
            "degraded": False,
            "companies": [
                {"id": company.id, "name": company.display_name}
                for company in self.env.user.company_ids
            ],
            "document_types": sorted(
                set(self.search([]).mapped("document_type_name")) - {False}
            ),
        }

    @api.model
    def integrity_manifest(self, backup_id=None):
        """Return a portable cross-system backup/reconciliation manifest."""
        self._require_manager()
        documents = self.search([])
        links = self.env["usl.document.link"].search([("active", "=", True)])
        orphaned_links = []
        for link in links:
            record = (
                self.env[link.res_model].browse(link.res_id).exists()
                if link.res_model in self.env
                else False
            )
            if not record:
                orphaned_links.append(link.id)
        compatibility = self._paperless().compatibility()
        return {
            "schema": "usl-documents-integrity-v1",
            "backup_id": backup_id or fields.Datetime.now().strftime("%Y%m%dT%H%M%SZ"),
            "generated_at": fields.Datetime.to_string(fields.Datetime.now()),
            "odoo_version": self.env["ir.module.module"].search(
                [("name", "=", "base")], limit=1
            ).latest_version,
            "paperless_version": compatibility["server_version"],
            "paperless_api_version": compatibility["api_version"],
            "paperless_document_count": compatibility["document_count"],
            "odoo_document_count": len(documents),
            "relationship_count": len(links),
            "missing_document_ids": documents.filtered(
                lambda item: item.availability_state == "missing"
            ).mapped("paperless_id"),
            "orphaned_relationship_ids": orphaned_links,
            "representative_checksums": [
                {
                    "paperless_id": document.paperless_id,
                    "checksum": document.checksum,
                }
                for document in documents.filtered("checksum")[:20]
            ],
            "last_successful_sync": self.env["ir.config_parameter"].sudo().get_str(
                "usl_documents.last_sync", ""
            ),
        }

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
    ):
        if not filename or not content_base64:
            raise ValidationError(_("Choose a non-empty file."))
        if confidentiality not in dict(CONFIDENTIALITIES):
            raise ValidationError(_("Invalid confidentiality policy."))
        try:
            content = base64.b64decode(content_base64, validate=True)
        except (ValueError, TypeError) as error:
            raise ValidationError(_("The uploaded file is not valid base64.")) from error
        maximum = self.env["ir.config_parameter"].sudo().get_int(
            "usl_documents.max_upload_bytes", 50 * 1024 * 1024
        )
        if not content or len(content) > maximum:
            raise ValidationError(
                _("The file is empty or exceeds the %(size)s MB upload limit.")
                % {"size": maximum // (1024 * 1024)}
            )
        company = (
            self.env["res.company"].browse(int(company_id)).exists()
            if company_id
            else self.env.company
        )
        if company not in self.env.user.company_ids:
            raise AccessError(_("You cannot archive a document for this company."))
        checksum = hashlib.sha256(content).hexdigest()
        existing = self.search([("checksum", "=", checksum)], limit=1)
        if existing:
            if res_model and res_id:
                existing.link_to_record(res_model, int(res_id))
            return {
                "state": "duplicate",
                "document_id": existing.id,
                "message": _("Identical content already exists; the archive item was reused."),
            }
        remote_candidates = self._paperless().search(
            "", page=1, page_size=2, filters={"checksum": checksum}
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
        if remote_matches:
            remote_id = int(remote_matches[0]["id"])
            mirrored = self.search([("paperless_id", "=", remote_id)], limit=1)
            if mirrored:
                if res_model and res_id:
                    mirrored.link_to_record(res_model, int(res_id))
                return {
                    "state": "duplicate",
                    "document_id": mirrored.id,
                    "message": _(
                        "Identical archive content was found and its existing item was reused."
                    ),
                }
            operation = self.env["usl.document.operation"].create({
                "name": filename,
                "state": "duplicate",
                "checksum": checksum,
                "mime_type": content_type,
                "company_id": company.id,
                "res_model": res_model,
                "res_id": int(res_id) if res_id else 0,
                "source": source,
                "error_message": _(
                    "Identical content exists outside your authorized Odoo archive view. "
                    "A Documents administrator must classify it before reuse."
                ),
            })
            return {
                "state": "duplicate",
                "operation_id": operation.id,
                "message": operation.error_message,
            }
        operation = self.env["usl.document.operation"].create({
            "name": filename,
            "state": "uploading",
            "checksum": checksum,
            "mime_type": content_type,
            "company_id": company.id,
            "res_model": res_model,
            "res_id": int(res_id) if res_id else 0,
            "source": source,
        })
        try:
            task_id = self._paperless().upload_multipart(
                content, filename, content_type, title=filename
            )
            operation.write({"state": "processing", "paperless_task_id": task_id})
        except PaperlessError as error:
            operation.write({"state": "failed", "error_message": str(error)})
            raise
        return {
            "state": "processing",
            "operation_id": operation.id,
            "task_id": task_id,
            "message": _("Paperless accepted the file and is processing it."),
        }

    def link_to_record(self, res_model, res_id):
        self.ensure_one()
        return self.env["usl.document.link"].create_for_record(
            self, res_model, int(res_id)
        )

    def action_open_paperless(self):
        self.ensure_one()
        self.check_access("read")
        if self.permission_sync_state != "synchronized":
            raise UserError(
                _(
                    "Open in Paperless is blocked until individual archive "
                    "permissions are synchronized."
                )
            )
        return {
            "type": "ir.actions.act_url",
            "url": self._paperless().paperless_url(self.paperless_id),
            "target": "new",
        }

    def action_sync_permissions(self):
        self._require_manager()
        mappings = self.env["usl.paperless.user.mapping"].search([
            ("active", "=", True),
            ("sync_state", "=", "synchronized"),
        ])
        for document in self:
            view_users = []
            change_users = []
            for mapping in mappings:
                try:
                    document.with_user(mapping.user_id).check_access("read")
                except AccessError:
                    continue
                view_users.append(mapping.paperless_user_id)
                if mapping.user_id.has_group(
                    "usl_documents.group_documents_manager"
                ):
                    change_users.append(mapping.paperless_user_id)
            if not view_users:
                document.with_context(skip_permission_invalidation=True).write({
                    "permission_sync_state": "failed",
                    "permission_sync_error": _(
                        "No synchronized individual Paperless identity is authorized."
                    ),
                    "availability_state": "permission_error",
                })
                continue
            try:
                document._paperless().set_document_permissions(
                    document.paperless_id,
                    view_users=sorted(view_users),
                    change_users=sorted(change_users),
                )
            except PaperlessError as error:
                document.with_context(skip_permission_invalidation=True).write({
                    "permission_sync_state": "failed",
                    "permission_sync_error": str(error),
                    "availability_state": "permission_error",
                })
            else:
                document.with_context(skip_permission_invalidation=True).write({
                    "permission_sync_state": "synchronized",
                    "permission_sync_error": False,
                    "availability_state": (
                        "available"
                        if document.availability_state == "permission_error"
                        else document.availability_state
                    ),
                })
        return True

    def action_preview(self):
        self.ensure_one()
        self.check_access("read")
        return {
            "type": "ir.actions.act_url",
            "url": f"/usl_documents/{self.id}/preview",
            "target": "new",
        }

    def action_download_original(self):
        self.ensure_one()
        self.check_access("read")
        return {
            "type": "ir.actions.act_url",
            "url": f"/usl_documents/{self.id}/download?original=1",
            "target": "self",
        }

    def action_open_links(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Linked Odoo records"),
            "res_model": "usl.document.link",
            "view_mode": "list,form",
            "domain": [("document_id", "=", self.id)],
            "context": {"default_document_id": self.id},
        }

    def action_mark_reviewed(self):
        self.write({"review_state": "reviewed"})


class UslDocumentLink(models.Model):
    _name = "usl.document.link"
    _description = "Archived Document Business Relationship"
    _inherit = ["mail.thread"]
    _order = "create_date desc, id desc"

    document_id = fields.Many2one(
        "usl.document", required=True, index=True, ondelete="restrict", tracking=True
    )
    res_model = fields.Char(required=True, index=True, readonly=True)
    res_id = fields.Integer(required=True, index=True, readonly=True)
    record_name = fields.Char(required=True, readonly=True)
    company_id = fields.Many2one(
        "res.company", required=True, index=True, ondelete="restrict", readonly=True
    )
    linked_by_id = fields.Many2one(
        "res.users", required=True, readonly=True, default=lambda self: self.env.user
    )
    linked_at = fields.Datetime(
        required=True, readonly=True, default=fields.Datetime.now
    )
    version_id = fields.Char(
        help="Paperless version that supports this business record, when legally relevant."
    )
    active = fields.Boolean(default=True, tracking=True)

    _record_link_unique = models.Constraint(
        "UNIQUE(document_id, res_model, res_id)",
        "This archived document is already linked to that Odoo record.",
    )

    @api.model
    def _allowed_models(self):
        return {
            "account.move",
            "hr.expense",
            "res.partner",
            "res.company",
            "project.project",
            "project.task",
            "hr.employee",
        }

    @api.model
    def create_for_record(self, document, res_model, res_id):
        if res_model not in self._allowed_models():
            raise ValidationError(_("This Odoo model cannot receive archived documents."))
        record = self.env[res_model].browse(res_id).exists()
        if not record:
            raise ValidationError(_("The target Odoo record no longer exists."))
        record.check_access("read")
        company = getattr(record, "company_id", False) or self.env.company
        if res_model == "res.company":
            company = record
        if company not in self.env.user.company_ids:
            raise AccessError(_("You cannot link records from this company."))
        if document.company_id and document.company_id != company:
            raise ValidationError(
                _("The document and Odoo record must belong to the same legal company.")
            )
        existing = self.search([
            ("document_id", "=", document.id),
            ("res_model", "=", res_model),
            ("res_id", "=", res_id),
        ], limit=1)
        if existing:
            if not existing.active:
                existing.active = True
            return existing
        if not document.company_id:
            document.write({
                "company_id": company.id,
                "review_state": "classified",
            })
        return self.create({
            "document_id": document.id,
            "res_model": res_model,
            "res_id": res_id,
            "record_name": record.display_name,
            "company_id": company.id,
        })

    def action_open_record(self):
        self.ensure_one()
        record = self.env[self.res_model].browse(self.res_id).exists()
        if not record:
            raise UserError(_("The linked Odoo record no longer exists."))
        record.check_access("read")
        return {
            "type": "ir.actions.act_window",
            "name": record.display_name,
            "res_model": self.res_model,
            "res_id": self.res_id,
            "view_mode": "form",
            "views": [(False, "form")],
        }

    def unlink(self):
        # Relationships can be removed; the archived original is deliberately untouched.
        return super().unlink()


class UslDocumentOperation(models.Model):
    _name = "usl.document.operation"
    _description = "Document Ingestion Operation"
    _order = "create_date desc, id desc"

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
    user_id = fields.Many2one(
        "res.users", required=True, readonly=True, default=lambda self: self.env.user
    )
    paperless_task_id = fields.Char(index=True, readonly=True)
    document_id = fields.Many2one("usl.document", readonly=True, ondelete="restrict")
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
    error_message = fields.Text(readonly=True)
    retry_count = fields.Integer(readonly=True)

    def poll(self):
        for operation in self.filtered(
            lambda item: item.state == "processing" and item.paperless_task_id
        ):
            try:
                task = self.env["usl.document"]._paperless().task(
                    operation.paperless_task_id
                )
            except PaperlessError as error:
                operation.write({"error_message": str(error)})
                continue
            if not task:
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
                if not paperless_id:
                    continue
                payload = self.env["usl.document"]._paperless().get_document(
                    paperless_id
                )
                values = self.env["usl.document"]._paperless_values(
                    payload, source=operation.source
                )
                values.update({
                    "company_id": operation.company_id.id,
                    "confidentiality": "internal",
                    "review_state": "classified",
                    "submitted_by_id": operation.user_id.id,
                    "submitted_at": operation.create_date,
                    "checksum": operation.checksum,
                })
                document_cache = self.env["usl.document"].sudo()
                document = document_cache.search(
                    [("paperless_id", "=", paperless_id)], limit=1
                )
                if document:
                    document.write(values)
                else:
                    document = document_cache.create(values)
                if operation.res_model and operation.res_id:
                    document.with_user(operation.user_id).link_to_record(
                        operation.res_model, operation.res_id
                    )
                operation.write({
                    "state": "archived",
                    "document_id": document.id,
                    "error_message": False,
                })
            elif status in ("failure", "failed"):
                result_data = task.get("result_data")
                operation.write({
                    "state": "failed",
                    "error_message": (
                        result_data.get("message")
                        if isinstance(result_data, dict)
                        else result_data
                    )
                    or task.get("result")
                    or task.get("message")
                    or _("Paperless processing failed."),
                })
        return {
            operation.id: {
                "state": operation.state,
                "document_id": operation.document_id.id,
                "error": operation.error_message,
            }
            for operation in self
        }

    @api.model
    def cron_poll_operations(self):
        operations = self.search([("state", "=", "processing")], limit=100)
        return operations.poll()


class UslPaperlessUserMapping(models.Model):
    _name = "usl.paperless.user.mapping"
    _description = "Odoo to Paperless Individual Identity"
    _order = "user_id"

    user_id = fields.Many2one(
        "res.users", required=True, index=True, ondelete="cascade"
    )
    paperless_user_id = fields.Integer(required=True, index=True)
    paperless_username = fields.Char(required=True)
    sync_state = fields.Selection(
        [
            ("pending", "Pending verification"),
            ("synchronized", "Verified"),
            ("failed", "Failed"),
        ],
        required=True,
        default="pending",
    )
    last_verified_at = fields.Datetime(readonly=True)
    last_error = fields.Text(readonly=True)
    active = fields.Boolean(default=True)

    _odoo_user_unique = models.Constraint(
        "UNIQUE(user_id)", "An Odoo user may have only one Paperless identity."
    )
    _paperless_user_unique = models.Constraint(
        "UNIQUE(paperless_user_id)",
        "A Paperless identity may be mapped to only one Odoo user.",
    )

    def action_mark_verified(self):
        if not self.env.user.has_group(
            "usl_documents.group_documents_manager"
        ):
            raise AccessError(_("Only Documents administrators verify identities."))
        self.write({
            "sync_state": "synchronized",
            "last_verified_at": fields.Datetime.now(),
            "last_error": False,
        })
