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

    name = fields.Char(required=True, readonly=True, tracking=True)
    paperless_id = fields.Integer(
        string="Paperless ID", required=True, index=True, readonly=True, copy=False
    )
    paperless_created = fields.Datetime(readonly=True)
    paperless_modified = fields.Datetime(readonly=True)
    document_date = fields.Date(index=True, readonly=True, tracking=True)
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
    version_ids = fields.One2many(
        "usl.document.version", "document_id", string="File versions", readonly=True
    )
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
        mapping = self.env["usl.paperless.user.mapping"].sudo().search(
            [
                ("user_id", "=", self.env.user.id),
                ("active", "=", True),
                ("sync_state", "=", "synchronized"),
            ],
            limit=1,
        )
        for document in self:
            document.paperless_url = (
                client.paperless_url(document.paperless_id)
                if (
                    client.public_url
                    and document.paperless_id
                    and mapping
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
        cache_fields = {
            "name",
            "paperless_id",
            "paperless_created",
            "paperless_modified",
            "document_date",
            "availability_state",
            "original_filename",
            "mime_type",
            "checksum",
            "archive_checksum",
            "correspondent_name",
            "document_type_name",
            "tag_names",
            "custom_fields_json",
            "version_data_json",
            "current_version_label",
            "source",
            "submitted_by_id",
            "submitted_at",
            "permission_sync_state",
            "permission_sync_error",
            "last_error",
        }
        if cache_fields.intersection(values) and not self.env.context.get(
            "usl_documents_cache_write"
        ):
            raise AccessError(
                _("Paperless cache and diagnostic fields cannot be edited manually.")
            )
        if (
            policy_fields.intersection(values)
            and not self.env.context.get("usl_documents_policy_write")
            and not self.env.user.has_group("usl_documents.group_documents_manager")
        ):
            raise AccessError(
                _("Only Documents administrators may change archive access policy.")
            )
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

    @api.model
    def _version_values(self, payload, *, current_id=None):
        version_id = payload.get("id") or payload.get("version")
        return {
            "paperless_version_id": str(version_id),
            "label": payload.get("version_label")
            or (
                _("Received original")
                if payload.get("is_root")
                else _("Version %s") % version_id
            ),
            "created_at": self._paperless_datetime(
                payload.get("created") or payload.get("added")
            ),
            "original_filename": payload.get("original_file_name")
            or payload.get("original_filename"),
            "mime_type": payload.get("mime_type"),
            "checksum": payload.get("checksum"),
            "archive_checksum": payload.get("archive_checksum"),
            "page_count": payload.get("page_count") or 0,
            "is_current": str(version_id) == str(current_id),
            "is_received_original": bool(payload.get("is_root")),
        }

    def _synchronize_versions(self, versions):
        """Refresh the relational version cache without changing Paperless files."""
        self.ensure_one()
        version_model = self.env["usl.document.version"].sudo()
        normalized = [item for item in (versions or []) if isinstance(item, dict)]
        # Paperless API v10 returns versions newest first. `is_root` identifies
        # the initially received file, not the currently active version.
        current_id = normalized[0].get("id") if normalized else None
        seen = set()
        for payload in normalized:
            version_id = str(payload.get("id") or payload.get("version"))
            if not version_id or version_id == "None":
                continue
            seen.add(version_id)
            version = version_model.search(
                [
                    ("document_id", "=", self.id),
                    ("paperless_version_id", "=", version_id),
                ],
                limit=1,
            )
            values = self._version_values(payload, current_id=current_id)
            if version:
                version.write(values)
            else:
                version_model.create({"document_id": self.id, **values})
        if seen:
            version_model.search(
                [
                    ("document_id", "=", self.id),
                    ("paperless_version_id", "not in", list(seen)),
                ]
            ).unlink()

    def _require_manager(self):
        if not self.env.user.has_group("usl_documents.group_documents_manager"):
            raise AccessError(_("Only Documents administrators may perform this action."))

    @api.model
    def diagnostics(self):
        self._require_manager()
        values = {
            "configured": self._paperless().configured,
            "last_sync": self.env["ir.config_parameter"].sudo().get_str(
                "usl_documents.last_sync"
            ),
            "sync_status": self.env["ir.config_parameter"].sudo().get_str(
                "usl_documents.sync_status", "unknown"
            ),
            "sync_error": self.env["ir.config_parameter"].sudo().get_str(
                "usl_documents.sync_error"
            ),
            "sync_cursor_page": self.env["ir.config_parameter"].sudo().get_str(
                "usl_documents.sync_cursor_page"
            ),
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
    def _paperless_values(
        self, payload, *, source="paperless", metadata_catalog=None
    ):
        metadata_catalog = metadata_catalog or {}

        def metadata_name(section, value, fallback=False):
            try:
                key = int(value)
            except (TypeError, ValueError):
                key = value
            return metadata_catalog.get(section, {}).get(key, fallback)

        tags = payload.get("tags") or []
        if tags and isinstance(tags[0], dict):
            tag_names = ", ".join(item.get("name", "") for item in tags)
        else:
            tag_names = ", ".join(
                metadata_name("tags", item, str(item)) for item in tags
            )
        correspondent = payload.get("correspondent")
        document_type = payload.get("document_type")
        versions = payload.get("versions") or []
        current_version = (
            versions[0] if versions and isinstance(versions[0], dict) else {}
        )
        return {
            "name": payload.get("title") or _("Untitled document"),
            "paperless_id": int(payload["id"]),
            "paperless_created": self._paperless_datetime(payload.get("added")),
            "paperless_modified": self._paperless_datetime(payload.get("modified")),
            "document_date": payload.get("created"),
            "original_filename": current_version.get("original_file_name")
            or current_version.get("original_filename")
            or payload.get("original_file_name")
            or payload.get("original_filename"),
            "mime_type": payload.get("mime_type"),
            # Paperless API v10 exposes the current file first. The cache's
            # document checksum follows that current version, while every
            # historical checksum (including the received original) is kept
            # on usl.document.version.
            "checksum": current_version.get("checksum") or payload.get("checksum"),
            "archive_checksum": payload.get("archive_checksum"),
            "correspondent_name": (
                correspondent.get("name")
                if isinstance(correspondent, dict)
                else metadata_name("correspondents", correspondent)
            ),
            "document_type_name": (
                document_type.get("name")
                if isinstance(document_type, dict)
                else metadata_name("document_types", document_type)
            ),
            "tag_names": tag_names,
            "custom_fields_json": json.dumps(
                payload.get("custom_fields") or [], sort_keys=True
            ),
            "version_data_json": json.dumps(versions, sort_keys=True),
            "current_version_label": current_version.get("version_label"),
            "availability_state": "available",
            "source": source,
            "last_error": False,
        }

    @api.model
    def sync_from_paperless(self, *, full=False, limit_pages=None):
        self._require_manager()
        client = self._paperless()
        params = self.env["ir.config_parameter"].sudo()
        # Keep microseconds in the Paperless checkpoint. Odoo's database
        # datetime representation is second-granular; truncating here can omit
        # a document completed later in the same second as the sync starts.
        now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        cursor_mode = params.get_str("usl_documents.sync_mode")
        resuming = bool(
            not full
            and cursor_mode == "incremental"
            and params.get_str("usl_documents.sync_cursor_page")
        )
        page = (
            int(params.get_str("usl_documents.sync_cursor_page") or 1)
            if resuming
            else 1
        )
        modified_after = (
            params.get_str("usl_documents.sync_modified_after")
            if resuming
            else (None if full else params.get_str("usl_documents.last_sync"))
        )
        checkpoint = (
            params.get_str("usl_documents.sync_checkpoint") if resuming else now
        )
        params.set_str("usl_documents.sync_status", "running")
        params.set_str("usl_documents.sync_error", "")
        params.set_str("usl_documents.sync_mode", "full" if full else "incremental")
        params.set_str("usl_documents.sync_checkpoint", checkpoint)
        params.set_str("usl_documents.sync_modified_after", modified_after or "")

        seen = set()
        touched = self.browse()
        pages_processed = 0
        complete = False
        metadata_catalog = None
        try:
            client.compatibility()
            while True:
                payload = client.list_documents(
                    page=page,
                    page_size=100,
                    modified_after=modified_after,
                    modified_before=checkpoint,
                )
                results = payload.get("results", [])
                if metadata_catalog is None and any(
                    isinstance(item.get("correspondent"), int)
                    or isinstance(item.get("document_type"), int)
                    or any(isinstance(tag, int) for tag in (item.get("tags") or []))
                    for item in results
                ):
                    metadata_catalog = client.metadata_catalog()
                for item in results:
                    paperless_id = int(item["id"])
                    seen.add(paperless_id)
                    document = self.sudo().search(
                        [("paperless_id", "=", paperless_id)], limit=1
                    )
                    values = self._paperless_values(
                        item, metadata_catalog=metadata_catalog
                    )
                    if document:
                        # Odoo-origin provenance is authoritative and must survive
                        # refreshes of the Paperless metadata cache.
                        values.pop("source", None)
                        document.with_context(usl_documents_cache_write=True).write(values)
                    else:
                        document = self.sudo().create(values)
                    document._synchronize_versions(item.get("versions") or [])
                    touched |= document
                pages_processed += 1
                if not payload.get("next"):
                    complete = True
                    break
                page += 1
                if limit_pages and pages_processed >= int(limit_pages):
                    params.set_str("usl_documents.sync_cursor_page", str(page))
                    break

            if full and complete:
                self.sudo().search(
                    [
                        ("paperless_id", "not in", list(seen)),
                        ("availability_state", "=", "available"),
                    ]
                ).with_context(usl_documents_cache_write=True).write(
                    {
                        "availability_state": "missing",
                        "last_error": _(
                            "Document was not returned by a full Paperless reconciliation."
                        ),
                    }
                )
            if touched:
                touched.filtered(
                    lambda item: item.permission_sync_state != "synchronized"
                ).with_user(self.env.ref("base.user_admin")).action_sync_permissions()
            if complete:
                params.set_str("usl_documents.last_sync", checkpoint)
                params.set_str("usl_documents.sync_cursor_page", "")
                params.set_str("usl_documents.sync_checkpoint", "")
                params.set_str("usl_documents.sync_modified_after", "")
                params.set_str("usl_documents.sync_mode", "")
                params.set_str("usl_documents.sync_status", "healthy")
                params.set_str("usl_documents.last_sync_error", "")
            return {
                "synchronized": len(seen),
                "pages": pages_processed,
                "complete": complete,
                "next_page": page if not complete else None,
                "checkpoint": checkpoint,
            }
        except PaperlessError as error:
            params.set_str("usl_documents.sync_status", "failed")
            params.set_str("usl_documents.sync_error", str(error))
            params.set_str("usl_documents.last_sync_error", now)
            if not full:
                params.set_str("usl_documents.sync_cursor_page", str(page))
            raise

    @api.model
    def cron_sync_from_paperless(self):
        manager = self.env.ref("base.user_admin")
        try:
            return self.with_user(manager).sync_from_paperless(limit_pages=20)
        except PaperlessError:
            _logger.exception("Paperless incremental synchronization failed")
            return False

    @api.model
    def _paperless_search_ids(self, query):
        """Collect a complete bounded search result before applying Odoo rules.

        Paperless is authoritative for text search, while Odoo is authoritative
        for visibility. Returning only Paperless's first page would silently hide
        authorized matches and make Odoo pagination incorrect.
        """
        maximum = self.env["ir.config_parameter"].sudo().get_int(
            "usl_documents.max_search_results", 10000
        )
        ids = []
        page = 1
        truncated = False
        while True:
            payload = self._paperless().search(query, page=page, page_size=100)
            for item in payload.get("results", []):
                ids.append(int(item["id"]))
                if len(ids) >= maximum:
                    truncated = bool(payload.get("next")) or len(ids) < payload.get(
                        "count", len(ids)
                    )
                    return ids, truncated
            if not payload.get("next"):
                break
            page += 1
        return ids, truncated

    @api.model
    def _workspace_document_values(self, item):
        return {
            "id": item.id,
            "name": item.name,
            "paperless_id": item.paperless_id,
            "date": item.document_date,
            "ingested_at": item.paperless_created,
            "company": item.company_id.display_name,
            "company_id": item.company_id.id,
            "confidentiality": item.confidentiality,
            "review_state": item.review_state,
            "availability_state": item.availability_state,
            "permission_sync_state": item.permission_sync_state,
            "correspondent": item.correspondent_name,
            "document_type": item.document_type_name,
            "tags": item.tag_names,
            "filename": item.original_filename,
            "mime_type": item.mime_type,
            "source": item.source,
            "checksum": item.checksum,
            "current_version": item.current_version_label,
            "version_count": len(item.version_ids),
            "link_count": item.link_count,
            "paperless_url": item.paperless_url,
        }

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
        confidentiality=None,
        review_state=None,
        linked_model=None,
        linked_id=None,
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
        if confidentiality:
            if confidentiality not in dict(CONFIDENTIALITIES):
                raise ValidationError(_("Invalid confidentiality filter."))
            domain.append(("confidentiality", "=", confidentiality))
        if review_state:
            if review_state not in ("needs_attention", "classified", "reviewed"):
                raise ValidationError(_("Invalid review-state filter."))
            domain.append(("review_state", "=", review_state))
        if linked_model or linked_id:
            if (
                linked_model not in self.env["usl.document.link"]._allowed_models()
                or not linked_id
            ):
                raise ValidationError(_("Invalid linked-record filter."))
            domain.extend(
                [
                    ("link_ids.res_model", "=", linked_model),
                    ("link_ids.res_id", "=", int(linked_id)),
                    ("link_ids.active", "=", True),
                ]
            )
        truncated = False
        if query:
            try:
                ids, truncated = self._paperless_search_ids(query)
                domain.append(("paperless_id", "in", ids))
            except PaperlessError as error:
                return {
                    "documents": [],
                    "count": 0,
                    "degraded": True,
                    "error": str(error),
                }
        order = {
            "recent": "document_date desc, id desc",
            "ingested": "paperless_created desc, id desc",
            "date": "document_date desc, id desc",
            "title": "name asc, id asc",
        }.get(sort, "paperless_created desc, id desc")
        count = self.search_count(domain)
        documents = self.search(
            domain, order=order, offset=(page - 1) * page_size, limit=page_size
        )
        accessible_documents = self.search([])
        link_facets = []
        seen_links = set()
        for link in accessible_documents.mapped("link_ids").filtered("active"):
            key = f"{link.res_model}:{link.res_id}"
            if key in seen_links:
                continue
            seen_links.add(key)
            model_label = self.env["ir.model"]._get(link.res_model).name
            link_facets.append(
                {
                    "key": key,
                    "model": link.res_model,
                    "res_id": link.res_id,
                    "label": f"{model_label} — {link.record_name}",
                }
            )
            if len(link_facets) >= 200:
                break
        return {
            "documents": [self._workspace_document_values(item) for item in documents],
            "count": count,
            "page": page,
            "page_size": page_size,
            "degraded": False,
            "truncated": truncated,
            "companies": [
                {"id": company.id, "name": company.display_name}
                for company in self.env.user.company_ids
            ],
            "document_types": sorted(
                set(self.search([]).mapped("document_type_name")) - {False}
            ),
            "link_facets": sorted(link_facets, key=lambda item: item["label"]),
            "operations": [
                {
                    "id": operation.id,
                    "name": operation.name,
                    "state": operation.state,
                    "error": operation.error_message,
                    "document_id": operation.document_id.id,
                    "created_at": operation.create_date,
                }
                for operation in self.env["usl.document.operation"].search(
                    [], order="create_date desc, id desc", limit=10
                )
            ],
        }

    @api.model
    def document_detail(self, document_id):
        document = self.browse(int(document_id)).exists()
        if not document:
            raise ValidationError(_("The archived document no longer exists."))
        document.check_access("read")
        values = self._workspace_document_values(document)
        values.update(
            {
                "archive_checksum": document.archive_checksum,
                "submitted_by": document.submitted_by_id.display_name,
                "submitted_at": document.submitted_at,
                "paperless_created": document.paperless_created,
                "paperless_modified": document.paperless_modified,
                "custom_fields": json.loads(document.custom_fields_json or "[]"),
                "can_manage": self.env.user.has_group(
                    "usl_documents.group_documents_manager"
                ),
                "versions": [
                    {
                        "id": version.id,
                        "paperless_version_id": version.paperless_version_id,
                        "label": version.label,
                        "created_at": version.created_at,
                        "filename": version.original_filename,
                        "mime_type": version.mime_type,
                        "checksum": version.checksum,
                        "archive_checksum": version.archive_checksum,
                        "page_count": version.page_count,
                        "is_current": version.is_current,
                        "is_received_original": version.is_received_original,
                        "submitted_by": version.submitted_by_id.display_name,
                        "submitted_at": version.submitted_at,
                        "source": version.source,
                        "preview_url": (
                            f"/usl_documents/{document.id}/preview"
                            f"?version={version.paperless_version_id}"
                        ),
                        "original_url": (
                            f"/usl_documents/{document.id}/download"
                            f"?original=1&version={version.paperless_version_id}"
                        ),
                        "archive_url": (
                            f"/usl_documents/{document.id}/download"
                            f"?original=0&version={version.paperless_version_id}"
                        ),
                    }
                    for version in document.version_ids.sorted(
                        key=lambda item: (item.is_current, item.created_at or fields.Datetime.now()),
                        reverse=True,
                    )
                ],
                "links": [
                    {
                        "id": link.id,
                        "record_name": link.record_name,
                        "model": link.res_model,
                        "model_label": self.env["ir.model"]._get(link.res_model).name,
                        "res_id": link.res_id,
                        "company": link.company_id.display_name,
                        "linked_by": link.linked_by_id.display_name,
                        "linked_at": link.linked_at,
                        "version_id": link.version_id,
                    }
                    for link in document.link_ids.filtered("active")
                ],
            }
        )
        return values

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
        remote = {}
        page = 1
        while True:
            payload = self._paperless().list_documents(page=page, page_size=100)
            for item in payload.get("results", []):
                remote[int(item["id"])] = item
            if not payload.get("next"):
                break
            page += 1
        mirrored_ids = set(documents.mapped("paperless_id"))
        remote_ids = set(remote)
        checksum_mismatches = []
        for document in documents.filtered("checksum"):
            payload = remote.get(document.paperless_id)
            if not payload:
                continue
            remote_checksums = {
                payload.get("checksum"),
                *[
                    version.get("checksum")
                    for version in (payload.get("versions") or [])
                    if isinstance(version, dict)
                ],
            }
            if document.checksum not in remote_checksums:
                checksum_mismatches.append(
                    {
                        "paperless_id": document.paperless_id,
                        "odoo_checksum": document.checksum,
                        "paperless_checksums": sorted(
                            value for value in remote_checksums if value
                        ),
                    }
                )
        params = self.env["ir.config_parameter"].sudo()
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
            "relationship_counts_by_model": {
                model: len(links.filtered(lambda item, m=model: item.res_model == m))
                for model in sorted(set(links.mapped("res_model")))
            },
            "version_count": len(documents.mapped("version_ids")),
            "missing_document_ids": sorted(mirrored_ids - remote_ids),
            "unmirrored_paperless_ids": sorted(remote_ids - mirrored_ids),
            "orphaned_relationship_ids": orphaned_links,
            "checksum_mismatches": checksum_mismatches,
            "permission_sync_failures": documents.filtered(
                lambda item: item.permission_sync_state == "failed"
            ).mapped("paperless_id"),
            "representative_checksums": [
                {
                    "paperless_id": document.paperless_id,
                    "checksum": document.checksum,
                    "versions": [
                        {
                            "paperless_version_id": version.paperless_version_id,
                            "checksum": version.checksum,
                            "archive_checksum": version.archive_checksum,
                        }
                        for version in document.version_ids
                    ],
                }
                for document in documents.filtered("checksum")[:20]
            ],
            "last_successful_sync": params.get_str("usl_documents.last_sync", ""),
            "sync_status": params.get_str("usl_documents.sync_status", "unknown"),
            "backup_completion_status": params.get_str(
                "usl_documents.backup_completion_status", "not_recorded"
            ),
            "last_restore_test": params.get_str(
                "usl_documents.last_restore_test", "not_recorded"
            ),
            "integrity_ok": not (
                mirrored_ids - remote_ids
                or remote_ids - mirrored_ids
                or orphaned_links
                or checksum_mismatches
                or documents.filtered(
                    lambda item: item.permission_sync_state == "failed"
                )
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
        source_record = False
        if res_model or res_id:
            if (
                res_model not in self.env["usl.document.link"]._allowed_models()
                or not res_id
            ):
                raise ValidationError(_("Invalid source record for archive ingestion."))
            source_record = self.env[res_model].browse(int(res_id)).exists()
            if not source_record:
                raise ValidationError(_("The source Odoo record no longer exists."))
            source_record.check_access("read")
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
                _("The upload company must match the source record's legal company.")
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

    def upload_new_version(
        self, filename, content_base64, content_type, version_label=None
    ):
        self.ensure_one()
        self._require_manager()
        self.check_access("read")
        if self.availability_state != "available":
            raise UserError(
                _("A replacement cannot be added while the root document is unavailable.")
            )
        try:
            content = base64.b64decode(content_base64, validate=True)
        except (ValueError, TypeError) as error:
            raise ValidationError(_("The replacement file is not valid base64.")) from error
        maximum = self.env["ir.config_parameter"].sudo().get_int(
            "usl_documents.max_upload_bytes", 50 * 1024 * 1024
        )
        if not content or len(content) > maximum:
            raise ValidationError(
                _("The file is empty or exceeds the %(size)s MB upload limit.")
                % {"size": maximum // (1024 * 1024)}
            )
        checksum = hashlib.sha256(content).hexdigest()
        if checksum in ({self.checksum} | set(self.version_ids.mapped("checksum"))):
            return {
                "state": "duplicate",
                "document_id": self.id,
                "message": _(
                    "That exact file already belongs to this Paperless document."
                ),
            }
        operation = self.env["usl.document.operation"].create(
            {
                "name": filename,
                "state": "uploading",
                "checksum": checksum,
                "mime_type": content_type,
                "company_id": self.company_id.id,
                "source": self.source
                if self.source in dict(
                    self.env["usl.document.operation"]._fields["source"].selection
                )
                else "odoo_upload",
                "target_document_id": self.id,
            }
        )
        try:
            task_id = self._paperless().update_version(
                self.paperless_id,
                content,
                filename,
                content_type,
                version_label=version_label or filename,
            )
            operation.write({"state": "processing", "paperless_task_id": task_id})
        except PaperlessError as error:
            operation.write({"state": "failed", "error_message": str(error)})
            raise
        return {
            "state": "processing",
            "operation_id": operation.id,
            "task_id": task_id,
            "message": _(
                "Paperless accepted the replacement as a new immutable file version."
            ),
        }

    def unlink_from_record(self, res_model, res_id):
        self.ensure_one()
        links = self.env["usl.document.link"].search(
            [
                ("document_id", "=", self.id),
                ("res_model", "=", res_model),
                ("res_id", "=", int(res_id)),
                ("active", "=", True),
            ]
        )
        if not links:
            return False
        links.unlink()
        return True

    def action_open_paperless(self):
        self.ensure_one()
        self.check_access("read")
        mapping = self.env["usl.paperless.user.mapping"].sudo().search(
            [
                ("user_id", "=", self.env.user.id),
                ("active", "=", True),
                ("sync_state", "=", "synchronized"),
            ],
            limit=1,
        )
        if self.permission_sync_state != "synchronized" or not mapping:
            raise UserError(
                _(
                    "Open in Paperless is blocked until your individual archive "
                    "identity and this document's permissions are synchronized."
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
                document.with_context(
                    skip_permission_invalidation=True,
                    usl_documents_cache_write=True,
                ).write({
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
                document.with_context(
                    skip_permission_invalidation=True,
                    usl_documents_cache_write=True,
                ).write({
                    "permission_sync_state": "failed",
                    "permission_sync_error": str(error),
                    "availability_state": "permission_error",
                })
            else:
                document.with_context(
                    skip_permission_invalidation=True,
                    usl_documents_cache_write=True,
                ).write({
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

    def action_download_archive(self):
        self.ensure_one()
        self.check_access("read")
        return {
            "type": "ir.actions.act_url",
            "url": f"/usl_documents/{self.id}/download?original=0",
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
        self._require_manager()
        self.write({"review_state": "reviewed"})


class UslDocumentVersion(models.Model):
    _name = "usl.document.version"
    _description = "Paperless Document File Version"
    _order = "is_current desc, created_at desc, id desc"

    document_id = fields.Many2one(
        "usl.document", required=True, index=True, ondelete="cascade", readonly=True
    )
    paperless_version_id = fields.Char(required=True, index=True, readonly=True)
    label = fields.Char(required=True, readonly=True)
    created_at = fields.Datetime(readonly=True)
    original_filename = fields.Char(readonly=True)
    mime_type = fields.Char(readonly=True)
    checksum = fields.Char(index=True, readonly=True)
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
                existing.sudo().write({"active": True})
            return existing
        if not document.company_id:
            document.with_context(usl_documents_policy_write=True).write(
                {
                    "company_id": company.id,
                    "review_state": "classified",
                }
            )
        link = self.sudo().create(
            {
                "document_id": document.id,
                "res_model": res_model,
                "res_id": res_id,
                "record_name": record.display_name,
                "company_id": company.id,
                "linked_by_id": self.env.user.id,
            }
        )
        if document.permission_sync_state != "synchronized":
            document.with_user(self.env.ref("base.user_admin")).action_sync_permissions()
        return link

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
        for link in self:
            record = (
                self.env[link.res_model].browse(link.res_id).exists()
                if link.res_model in self.env
                else False
            )
            if record:
                record.check_access("read")
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
                client = self.env["usl.document"]._paperless()
                payload = client.get_document(paperless_id)
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
                    [("paperless_id", "=", paperless_id)], limit=1
                )
                if document:
                    values.pop("source", None)
                    # A replacement operation carries the checksum of the new
                    # version. The cache follows Paperless's current version;
                    # every historical checksum, including the received
                    # original, remains on usl.document.version.
                    if not operation.target_document_id:
                        values["checksum"] = operation.checksum
                    document.with_context(usl_documents_cache_write=True).write(values)
                else:
                    values.update(
                        {
                            "company_id": operation.company_id.id,
                            "confidentiality": "internal",
                            "review_state": "classified",
                            "submitted_by_id": operation.user_id.id,
                            "submitted_at": operation.create_date,
                            "checksum": operation.checksum,
                        }
                    )
                    document = document_cache.create(values)
                document._synchronize_versions(payload.get("versions") or [])
                current_version = document.version_ids.filtered("is_current")
                if current_version:
                    current_version.sudo().write(
                        {
                            "submitted_by_id": operation.user_id.id,
                            "submitted_at": operation.create_date,
                            "source": operation.source,
                        }
                    )
                if operation.res_model and operation.res_id:
                    document.with_user(operation.user_id).link_to_record(
                        operation.res_model, operation.res_id
                    )
                if document.permission_sync_state != "synchronized":
                    document.with_user(
                        self.env.ref("base.user_admin")
                    ).action_sync_permissions()
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
        for mapping in self:
            try:
                payload = self.env["usl.document"]._paperless().get_user(
                    mapping.paperless_user_id
                )
                remote_username = payload.get("username")
                if remote_username != mapping.paperless_username:
                    raise ValidationError(
                        _(
                            "Paperless user %(id)s is %(actual)s, not %(expected)s."
                        )
                        % {
                            "id": mapping.paperless_user_id,
                            "actual": remote_username or _("unnamed"),
                            "expected": mapping.paperless_username,
                        }
                    )
            except (PaperlessError, ValidationError) as error:
                mapping.write(
                    {
                        "sync_state": "failed",
                        "last_error": str(error),
                    }
                )
                raise
            mapping.write(
                {
                    "sync_state": "synchronized",
                    "last_verified_at": fields.Datetime.now(),
                    "last_error": False,
                }
            )
        return True
