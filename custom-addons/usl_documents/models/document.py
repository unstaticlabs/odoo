import base64
import hashlib
import json
import logging
import os
from datetime import UTC, datetime, timedelta

from odoo import Command, _, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.fields import Domain

from .paperless_client import (
    PaperlessClient,
    PaperlessError,
    PaperlessNotFound,
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
    _order = "document_date desc, archive_added_at desc, id desc"

    name = fields.Char(required=True, readonly=True, tracking=True)
    all_text = fields.Char(
        string="Search everywhere",
        compute="_compute_search_helpers",
        search="_search_all_text",
        help=(
            "Search OCR content, title, Paperless metadata, additional details, "
            "and accessible linked Odoo records."
        ),
    )
    archive_text = fields.Char(
        string="Document content",
        compute="_compute_search_helpers",
        search="_search_archive_text",
        help="Search title, OCR text, archive metadata, and Paperless identity.",
    )
    custom_field_text = fields.Char(
        string="Additional details",
        compute="_compute_search_helpers",
        search="_search_custom_field_text",
        help="Search across synchronized Paperless custom-field values.",
    )
    paperless_id = fields.Integer(
        string="Paperless ID", required=True, index=True, readonly=True, copy=False,
    )
    paperless_created = fields.Datetime(readonly=True)
    paperless_modified = fields.Datetime(readonly=True)
    archive_added_at = fields.Datetime(
        string="Added",
        compute="_compute_archive_added_at",
        store=True,
        index=True,
        help=(
            "When the enterprise first received the document. Odoo submissions "
            "use their attributed submission time; Paperless-native documents use "
            "the archive ingestion time."
        ),
    )
    document_date = fields.Date(index=True, readonly=True, tracking=True)
    company_id = fields.Many2one(
        "res.company", index=True, tracking=True, ondelete="restrict",
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
            ("permanently_deleted", "Permanently deleted"),
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
    correspondent_id = fields.Many2one(
        "usl.paperless.correspondent",
        string="Correspondent",
        index=True,
        readonly=True,
        ondelete="set null",
    )
    document_type_id = fields.Many2one(
        "usl.paperless.document.type",
        string="Document type",
        index=True,
        readonly=True,
        ondelete="set null",
    )
    tag_ids = fields.Many2many(
        "usl.paperless.tag",
        "usl_document_tag_rel",
        "document_id",
        "tag_id",
        string="Tags",
        readonly=True,
    )
    tag_sort_key = fields.Char(
        compute="_compute_tag_sort_key",
        store=True,
        index=True,
        readonly=True,
    )
    status_sort_key = fields.Char(
        compute="_compute_status_sort_key",
        store=True,
        index=True,
        readonly=True,
    )
    mapped_contact_id = fields.Many2one(
        "res.partner",
        string="Mapped Contact",
        compute="_compute_mapped_contact",
        search="_search_mapped_contact",
    )
    has_linked_record = fields.Boolean(
        string="Linked to Odoo",
        compute="_compute_search_helpers",
        search="_search_has_linked_record",
    )
    linked_record_ref = fields.Char(
        string="Linked record",
        compute="_compute_search_helpers",
        search="_search_linked_record_ref",
    )
    linked_employee_id = fields.Many2one(
        "hr.employee",
        string="Employee",
        compute="_compute_linked_employee",
        search="_search_linked_employee",
    )
    # Fallback names keep a document intelligible if its catalog relation is
    # temporarily unavailable during reconciliation. Normal UI and filters use
    # the stable relational metadata above.
    correspondent_name = fields.Char(index=True, readonly=True)
    document_type_name = fields.Char(index=True, readonly=True)
    custom_fields_json = fields.Text(readonly=True)
    current_version_label = fields.Char(readonly=True)
    version_ids = fields.One2many(
        "usl.document.version", "document_id", string="File versions", readonly=True,
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
    permission_checked_at = fields.Datetime(readonly=True)
    trashed_at = fields.Datetime(readonly=True, tracking=True)
    trashed_by_id = fields.Many2one(
        "res.users",
        string="Moved to Trash by",
        readonly=True,
        tracking=True,
    )
    trashed_by_label = fields.Char(
        string="Trash source",
        readonly=True,
        help=(
            "The Odoo user is recorded for actions made in Odoo. Paperless 3.0's "
            "Trash API does not identify the user for actions made directly there."
        ),
    )
    retention_until = fields.Datetime(readonly=True)
    retention_hold = fields.Boolean(
        string="Retention hold",
        tracking=True,
        help="Blocks permanent deletion regardless of the Trash retention date.",
    )
    deletion_approved_by_id = fields.Many2one("res.users", readonly=True)
    deletion_approved_at = fields.Datetime(readonly=True)
    deletion_reason = fields.Text()
    permanently_deleted_at = fields.Datetime(readonly=True)
    link_ids = fields.One2many("usl.document.link", "document_id")
    link_count = fields.Integer(compute="_compute_link_count")
    paperless_url = fields.Char(compute="_compute_paperless_url")
    last_error = fields.Text(readonly=True)

    @api.depends("submitted_at", "paperless_created")
    def _compute_archive_added_at(self):
        for document in self:
            document.archive_added_at = (
                document.submitted_at or document.paperless_created
            )

    _paperless_id_unique = models.Constraint(
        "UNIQUE(paperless_id)", "A Paperless document may only be mirrored once.",
    )

    @api.model_create_multi
    def create(self, values_list):
        if not self.env.su:
            raise AccessError(
                _("Archived document cache records can only be created by synchronization."),
            )
        return super().create(values_list)

    @api.depends("link_ids")
    def _compute_link_count(self):
        for document in self:
            document.link_count = len(document._accessible_active_links())

    def _accessible_active_links(self):
        """Return links whose target record is readable by the current user."""
        self.ensure_one()
        self.check_access("read")
        visible = self.env["usl.document.link"].sudo().browse()
        for link in self.sudo().link_ids.filtered("active"):
            if link.res_model not in self.env:
                continue
            record = self.env[link.res_model].browse(link.res_id).exists()
            if not record:
                continue
            try:
                record.check_access("read")
            except AccessError:
                continue
            visible |= link
        return visible

    @api.depends("correspondent_id", "correspondent_id.partner_id")
    def _compute_mapped_contact(self):
        for document in self:
            document.mapped_contact_id = (
                document.correspondent_id.partner_visible_id
                if document.correspondent_id
                else False
            )

    @api.model
    def _search_mapped_contact(self, operator, value):
        if operator not in ("=", "!=", "in", "not in"):
            raise ValidationError(_("Unsupported mapped Contact filter."))
        values = value if operator in ("in", "not in") else [value]
        requested = [int(item) for item in values if item]
        visible = self.env["res.partner"].search([("id", "in", requested)])
        if operator in ("=", "in") and requested and not visible:
            return [("id", "=", 0)]
        normalized = visible.ids if operator in ("in", "not in") else (
            visible.id if visible else False
        )
        return [("correspondent_id.partner_id", operator, normalized)]

    def _compute_search_helpers(self):
        for document in self:
            document.all_text = False
            document.archive_text = False
            document.custom_field_text = False
            document.has_linked_record = bool(document._accessible_active_links())
            document.linked_record_ref = False

    @api.depends("tag_ids", "tag_ids.name")
    def _compute_tag_sort_key(self):
        for document in self:
            document.tag_sort_key = " · ".join(
                sorted(document.tag_ids.mapped("name"), key=str.casefold),
            )

    @api.depends("availability_state", "review_state")
    def _compute_status_sort_key(self):
        for document in self:
            document.status_sort_key = (
                f"{document.availability_state or ''}|{document.review_state or ''}"
            )

    @api.depends("link_ids.active", "link_ids.res_model", "link_ids.res_id")
    def _compute_linked_employee(self):
        for document in self:
            employee_link = document._accessible_active_links().filtered(
                lambda link: link.active and link.res_model == "hr.employee",
            )[:1]
            document.linked_employee_id = (
                self.env["hr.employee"].browse(employee_link.res_id).exists()
                if employee_link
                else False
            )

    @api.model
    def _search_archive_text(self, operator, value):
        if operator not in ("=", "!=", "like", "not like", "ilike", "not ilike"):
            raise ValidationError(_("Unsupported document-content search operator."))
        if not value:
            return []
        ids, _truncated = self._paperless_search_ids(str(value))
        negative = operator in ("!=", "not like", "not ilike")
        return [("paperless_id", "not in" if negative else "in", ids)]

    @api.model
    def _accessible_local_text_ids(self, value):
        """Supplement Paperless full text with Odoo-owned, authorized labels."""
        needle = str(value or "").strip().casefold()
        if not needle:
            return []
        matching = set()
        documents = self.search([])
        for document in documents:
            searchable = [
                document.name,
                document.original_filename,
                document.company_id.display_name,
                document.correspondent_id.name,
                document.document_type_id.name,
                document.tag_sort_key,
            ]
            searchable.extend(
                link.record_name
                for link in document._accessible_active_links()
            )
            if any(needle in (item or "").casefold() for item in searchable):
                matching.add(document.paperless_id)
        return sorted(matching)

    @api.model
    def _all_text_search_ids(self, value):
        ids, truncated = self._paperless_search_ids(
            str(value),
            full_text=True,
        )
        seen = set(ids)
        for document_id in (
            self._custom_field_search_ids(value)
            + self._accessible_local_text_ids(value)
        ):
            if document_id not in seen:
                ids.append(document_id)
                seen.add(document_id)
        return ids, truncated

    @api.model
    def _search_all_text(self, operator, value):
        if operator not in ("=", "!=", "like", "not like", "ilike", "not ilike"):
            raise ValidationError(_("Unsupported broad document search operator."))
        if not value:
            return []
        ids, _truncated = self._all_text_search_ids(value)
        negative = operator in ("!=", "not like", "not ilike")
        return [("paperless_id", "not in" if negative else "in", ids)]

    @api.model
    def _custom_field_search_ids(self, value):
        definitions = json.loads(
            self.env["ir.config_parameter"].sudo().get_str(
                "usl_documents.paperless_custom_fields",
                "[]",
            ),
        )
        matching_ids = set()
        for definition in definitions[:100]:
            data_type = definition.get("data_type") or "string"
            parsed_value = value
            operator = "icontains"
            try:
                if data_type == "integer":
                    parsed_value = int(value)
                    operator = "exact"
                elif data_type in ("float", "monetary"):
                    parsed_value = float(value)
                    operator = "exact"
                elif data_type == "boolean":
                    normalized = str(value).strip().lower()
                    if normalized not in ("1", "0", "true", "false", "yes", "no"):
                        continue
                    parsed_value = normalized in ("1", "true", "yes")
                    operator = "exact"
                elif data_type in ("date", "select", "documentlink"):
                    operator = "exact"
            except (TypeError, ValueError):
                continue
            ids, _truncated = self._paperless_search_ids(
                "",
                filters={
                    "custom_field_query": json.dumps(
                        [definition["name"], operator, parsed_value],
                    ),
                },
            )
            matching_ids.update(ids)
        return sorted(matching_ids)

    @api.model
    def _search_custom_field_text(self, operator, value):
        if operator not in ("=", "!=", "like", "not like", "ilike", "not ilike"):
            raise ValidationError(_("Unsupported additional-detail search operator."))
        if not value:
            return []
        ids = self._custom_field_search_ids(value)
        negative = operator in ("!=", "not like", "not ilike")
        return [("paperless_id", "not in" if negative else "in", ids)]

    @api.model
    def _resolve_remote_search_domain(self, domain, resolved_ids=None):
        """Resolve each Paperless text condition once before Odoo paginates.

        ``search_count`` and ``search`` both expand custom search fields.  If
        the raw domain reached both calls, one user search caused two archive
        requests and could even observe different results between count and
        page retrieval.
        """

        def resolve(condition):
            if condition.field_expr not in (
                "all_text",
                "archive_text",
                "custom_field_text",
            ):
                return condition
            if not condition.value:
                return Domain.TRUE
            if condition.operator not in (
                "=",
                "!=",
                "like",
                "not like",
                "ilike",
                "not ilike",
            ):
                raise ValidationError(
                    _("Unsupported document-content search operator."),
                )
            cache_key = (condition.field_expr, str(condition.value))
            if resolved_ids and cache_key in resolved_ids:
                ids = resolved_ids[cache_key]
            elif condition.field_expr == "all_text":
                ids, _truncated = self._all_text_search_ids(
                    str(condition.value),
                )
            elif condition.field_expr == "archive_text":
                ids, _truncated = self._paperless_search_ids(
                    str(condition.value),
                )
            else:
                ids = self._custom_field_search_ids(condition.value)
            negative = condition.operator in ("!=", "not like", "not ilike")
            return Domain(
                "paperless_id",
                "not in" if negative else "in",
                ids,
            )

        return domain.map_conditions(resolve)

    @api.model
    def _search_has_linked_record(self, operator, value):
        if operator not in ("=", "!="):
            raise ValidationError(_("Linked status supports equals and not equals."))
        wanted = bool(value)
        if operator == "!=":
            wanted = not wanted
        accessible_document_ids = self.search([]).ids
        visible_linked_ids = set()
        links = self.env["usl.document.link"].sudo().search(
            [
                ("document_id", "in", accessible_document_ids),
                ("active", "=", True),
            ],
        )
        for model_name in links.mapped("res_model"):
            if model_name not in self.env:
                continue
            target_model = self.env[model_name]
            target_ids = links.filtered(
                lambda link: link.res_model == model_name,
            ).mapped("res_id")
            try:
                target_model.check_access("read")
                visible_target_ids = set(
                    target_model.search([("id", "in", target_ids)]).ids,
                )
            except AccessError:
                continue
            visible_linked_ids.update(
                links.filtered(
                    lambda link: (
                        link.res_model == model_name
                        and link.res_id in visible_target_ids
                    ),
                ).mapped("document_id").ids,
            )
        if not visible_linked_ids:
            return Domain.FALSE if wanted else Domain.TRUE
        return Domain(
            "id",
            "in" if wanted else "not in",
            sorted(visible_linked_ids),
        )

    @api.model
    def _search_linked_record_ref(self, operator, value):
        if operator not in ("=", "!=") or not isinstance(value, str):
            raise ValidationError(_("Choose a valid linked Odoo record."))
        try:
            model_name, record_id = value.rsplit(":", 1)
            record_id = int(record_id)
        except (TypeError, ValueError) as error:
            raise ValidationError(_("Choose a valid linked Odoo record.")) from error
        if model_name not in self.env["usl.document.link"]._allowed_models():
            raise ValidationError(_("That type of Odoo record cannot carry documents."))
        record = self.env[model_name].browse(record_id).exists()
        if not record:
            raise ValidationError(_("Choose a valid linked Odoo record."))
        record.check_access("read")
        link_ids = self.env["usl.document.link"].sudo().search(
            [
                ("active", "=", True),
                ("res_model", "=", model_name),
                ("res_id", "=", record_id),
            ],
        ).mapped("document_id").ids
        return [("id", "not in" if operator == "!=" else "in", link_ids)]

    @api.model
    def _search_linked_employee(self, operator, value):
        if operator not in ("=", "!=", "in", "not in"):
            raise ValidationError(_("Unsupported employee filter."))
        employee_ids = (
            [int(item) for item in value]
            if operator in ("in", "not in")
            else [int(value)]
        )
        employee_model = self.env["hr.employee"]
        employee_model.check_access("read")
        visible_employees = employee_model.search(
            [("id", "in", employee_ids)],
        )
        document_ids = self.env["usl.document.link"].sudo().search(
            [
                ("active", "=", True),
                ("res_model", "=", "hr.employee"),
                ("res_id", "in", visible_employees.ids),
            ],
        ).mapped("document_id").ids
        return [
            (
                "id",
                "not in" if operator in ("!=", "not in") else "in",
                document_ids,
            ),
        ]

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
                    and mapping._identity_is_safe()
                    and document.permission_sync_state == "synchronized"
                )
                else False
            )

    @api.constrains("company_id", "review_state")
    def _check_classified_company(self):
        for document in self:
            if document.review_state != "needs_attention" and not document.company_id:
                raise ValidationError(
                    _("A classified document must belong to a legal company."),
                )

    def _paperless(self):
        return PaperlessClient(self.env)

    def _check_archive_binary_access(self):
        """Authorize access to any file-derived Paperless response.

        Odoo record rules decide whether the user may know about the document.
        A live document's binary remains fail-closed until its equivalent
        Paperless object permissions have been confirmed. Non-live documents
        deliberately remain indistinguishable from missing files at the HTTP
        boundary.
        """
        self.ensure_one()
        self.check_access("read")
        if self.availability_state not in ("available", "permission_error"):
            return False
        if self.permission_sync_state != "synchronized":
            raise AccessError(
                _(
                    "The file is blocked until an administrator synchronizes "
                    "its archive permissions.",
                ),
            )
        return self.availability_state == "available"

    def write(self, values):
        policy_fields = {
            "company_id",
            "confidentiality",
            "accounting_evidence",
            "retention_hold",
            "deletion_reason",
        }
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
            "correspondent_id",
            "document_type_id",
            "tag_ids",
            "correspondent_name",
            "document_type_name",
            "custom_fields_json",
            "current_version_label",
            "source",
            "submitted_by_id",
            "submitted_at",
            "permission_sync_state",
            "permission_sync_error",
            "permission_checked_at",
            "trashed_at",
            "trashed_by_id",
            "trashed_by_label",
            "retention_until",
            "deletion_approved_by_id",
            "deletion_approved_at",
            "permanently_deleted_at",
            "last_error",
        }
        cache_write = (
            self.env.context.get("usl_documents_cache_write")
            and self.env.su
        )
        policy_write = (
            self.env.context.get("usl_documents_policy_write")
            and self.env.su
        )
        skip_permission_invalidation = (
            self.env.context.get("skip_permission_invalidation")
            and self.env.su
        )
        if cache_fields.intersection(values) and not cache_write:
            raise AccessError(
                _("Paperless cache and diagnostic fields cannot be edited manually."),
            )
        if (
            policy_fields.intersection(values)
            and not policy_write
            and not self.env.user.has_group("usl_documents.group_documents_manager")
        ):
            raise AccessError(
                _("Only Documents administrators may change archive access policy."),
            )
        if (
            policy_fields.intersection(values)
            and not skip_permission_invalidation
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
                payload.get("created") or payload.get("added"),
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
                ],
            ).unlink()
            # Relationships created before version pinning was introduced have
            # no reliable "current at link time" value. Pin those legacy links
            # to the immutable received original during reconciliation rather
            # than allowing a later replacement to silently redefine evidence.
            received_original = version_model.search(
                [
                    ("document_id", "=", self.id),
                    ("is_received_original", "=", True),
                ],
                limit=1,
            )
            if received_original:
                self.sudo().link_ids.filtered(
                    lambda link: not link.version_id,
                ).write(
                    {"version_id": received_original.paperless_version_id},
                )

    def _require_manager(self):
        if not self.env.user.has_group("usl_documents.group_documents_manager"):
            raise AccessError(_("Only Documents administrators may perform this action."))

    @api.model
    def diagnostics(self):
        self._require_manager()
        values = {
            "configured": self._paperless().configured,
            "last_sync": self.env["ir.config_parameter"].sudo().get_str(
                "usl_documents.last_sync",
            ),
            "sync_status": self.env["ir.config_parameter"].sudo().get_str(
                "usl_documents.sync_status", "unknown",
            ),
            "sync_error": self.env["ir.config_parameter"].sudo().get_str(
                "usl_documents.sync_error",
            ),
            "sync_cursor_page": self.env["ir.config_parameter"].sudo().get_str(
                "usl_documents.sync_cursor_page",
            ),
            "cached_documents": self.search_count([]),
            "missing_documents": self.search_count(
                [("availability_state", "=", "missing")],
            ),
            "trashed_documents": self.search_count(
                [("availability_state", "=", "trashed")],
            ),
            "permission_failures": self.search_count(
                [("permission_sync_state", "=", "failed")],
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
            parsed = parsed.astimezone(UTC).replace(tzinfo=None)
        return fields.Datetime.to_string(parsed)

    @api.model
    def _paperless_values(
        self, payload, *, source="paperless", metadata_catalog=None,
    ):
        metadata_catalog = metadata_catalog or {}

        def metadata_name(section, value, fallback=False):
            try:
                key = int(value)
            except (TypeError, ValueError):
                key = value
            return metadata_catalog.get(section, {}).get(key, fallback)

        def metadata_record(model_name, value):
            remote_id = value.get("id") if isinstance(value, dict) else value
            try:
                remote_id = int(remote_id)
            except (TypeError, ValueError):
                return self.env[model_name]
            record = self.env[model_name].sudo().search(
                [("paperless_id", "=", remote_id)], limit=1,
            )
            if not record and isinstance(value, dict):
                record = (
                    self.env[model_name]
                    .sudo()
                    .with_context(usl_documents_cache_write=True)
                    .create(self.env[model_name]._cache_values(value))
                )
            return record

        tags = payload.get("tags") or []
        tag_records = self.env["usl.paperless.tag"]
        for tag in tags:
            tag_records |= metadata_record("usl.paperless.tag", tag)
        correspondent = payload.get("correspondent")
        document_type = payload.get("document_type")
        correspondent_record = metadata_record(
            "usl.paperless.correspondent", correspondent,
        )
        document_type_record = metadata_record(
            "usl.paperless.document.type", document_type,
        )
        correspondent_name = (
            correspondent_record.name
            or (
                correspondent.get("name")
                if isinstance(correspondent, dict)
                else metadata_name("correspondents", correspondent)
            )
        )
        document_type_name = (
            document_type_record.name
            or (
                document_type.get("name")
                if isinstance(document_type, dict)
                else metadata_name("document_types", document_type)
            )
        )
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
            "correspondent_id": correspondent_record.id or False,
            "document_type_id": document_type_record.id or False,
            "tag_ids": [Command.set(tag_records.ids)],
            "correspondent_name": correspondent_name,
            "document_type_name": document_type_name,
            "custom_fields_json": json.dumps(
                payload.get("custom_fields") or [], sort_keys=True,
            ),
            "current_version_label": current_version.get("version_label"),
            "availability_state": "available",
            "source": source,
            "last_error": False,
        }

    @api.model
    def _sync_metadata_catalogs(self, client):
        """Refresh supported Paperless catalogs and bind the curated views."""
        for model_name in (
            "usl.paperless.tag",
            "usl.paperless.correspondent",
            "usl.paperless.document.type",
        ):
            self.env[model_name].synchronize_catalog(client=client)
        custom_fields = [
            {
                "id": int(item["id"]),
                "name": item["name"],
                "data_type": item["data_type"],
                "extra_data": item.get("extra_data"),
            }
            for item in client.list_custom_fields()
            if not (item.get("name") or "").startswith("Legacy Odoo ")
        ]
        self.env["ir.config_parameter"].sudo().set_str(
            "usl_documents.paperless_custom_fields",
            json.dumps(custom_fields, sort_keys=True),
        )

        tag_model = self.env["usl.paperless.tag"].sudo()
        default_tags = {
            "contracts": ("Contracts & legal", "#8c6bb1"),
            "banking": ("Banking", "#2b8cbe"),
            "tax": ("Tax & reporting", "#31a354"),
        }
        created = False
        for _key, (name, color) in default_tags.items():
            if not tag_model.search([("name", "=ilike", name)], limit=1):
                client.create_metadata(
                    "tags",
                    {
                        **tag_model._paperless_payload({
                            "name": name,
                            "color": color,
                            "matching_algorithm": 6,
                            "match": "",
                            "is_insensitive": True,
                        }),
                        "owner": None,
                    },
                )
                created = True
        if created:
            tag_model.synchronize_catalog(client=client)
        smart_views = self.env["usl.document.smart.view"].sudo()
        for key, (name, _color) in default_tags.items():
            view = smart_views.search([("key", "=", key)], limit=1)
            tag = tag_model.search([("name", "=ilike", name)], limit=1)
            if view and tag and (
                tag not in view.tag_ids or not view.archive_native
            ):
                view.with_context(usl_documents_view_setup=True).write(
                    {
                        "tag_ids": [Command.link(tag.id)],
                        "archive_native": True,
                    },
                )
        smart_views.synchronize_archive_views(client=client)

    @api.model
    def sync_from_paperless(self, *, full=False, limit_pages=None):
        self._require_manager()
        client = self._paperless()
        params = self.env["ir.config_parameter"].sudo()
        # Keep microseconds in the Paperless checkpoint. Odoo's database
        # datetime representation is second-granular; truncating here can omit
        # a document completed later in the same second as the sync starts.
        now = datetime.now(UTC).isoformat().replace("+00:00", "Z")
        cursor_mode = params.get_str("usl_documents.sync_mode")
        resuming = bool(
            not full
            and cursor_mode == "incremental"
            and params.get_str("usl_documents.sync_cursor_page"),
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
        trashed_ids = set()
        touched = self.browse()
        pages_processed = 0
        complete = False
        metadata_catalog = None
        try:
            client.compatibility()
            self._sync_metadata_catalogs(client)
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
                        [("paperless_id", "=", paperless_id)], limit=1,
                    )
                    values = self._paperless_values(
                        item, metadata_catalog=metadata_catalog,
                    )
                    # A document returned by the active endpoint has left
                    # Paperless Trash. Clear the previous deletion event even
                    # when it was restored directly in Paperless; otherwise a
                    # later Trash event could be falsely attributed to the
                    # Odoo user who performed the earlier one.
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

            if complete:
                retention_days = max(
                    0,
                    params.get_int(
                        "usl_documents.paperless_trash_retention_days",
                        30,
                    ),
                )
                for item in client.list_trashed_documents():
                    paperless_id = int(item["id"])
                    trashed_ids.add(paperless_id)
                    document = self.sudo().search(
                        [("paperless_id", "=", paperless_id)], limit=1,
                    )
                    values = self._paperless_values(
                        item, metadata_catalog=metadata_catalog,
                    )
                    values["availability_state"] = "trashed"
                    values["last_error"] = False
                    values.update(
                        {
                            "permission_sync_state": "pending",
                            "permission_sync_error": False,
                            "permission_checked_at": False,
                        },
                    )
                    trashed_at = self._paperless_datetime(item.get("deleted_at"))
                    values["trashed_at"] = trashed_at
                    if (
                        not document
                        or (
                            not document.trashed_by_id
                            and not document.trashed_by_label
                        )
                    ):
                        values["trashed_by_label"] = _(
                            "Moved in Paperless (user not provided by its API)",
                        )
                    values["retention_until"] = (
                        fields.Datetime.to_datetime(trashed_at)
                        + timedelta(days=retention_days)
                        if trashed_at
                        else False
                    )
                    if document and (
                        document.accounting_evidence
                        or document.confidentiality == "hr"
                    ):
                        values["retention_hold"] = True
                    if document:
                        values.pop("source", None)
                        document.with_context(
                            usl_documents_cache_write=True,
                        ).write(values)
                    else:
                        document = self.sudo().create(values)
                        document.with_context(
                            usl_documents_cache_write=True,
                        ).write({"availability_state": "trashed"})
                    document._synchronize_versions(item.get("versions") or [])
                    touched |= document

            if full and complete:
                omitted_available = self.sudo().search(
                    [
                        ("paperless_id", "not in", list(seen | trashed_ids)),
                        ("availability_state", "=", "available"),
                    ],
                )
                confirmed_missing = omitted_available
                # Paperless's list/search index is eventually consistent just
                # after consumption. An Odoo-origin document has already been
                # confirmed by its asynchronous task and direct document API;
                # do not downgrade it to missing (and thereby defeat local
                # checksum reuse) solely because one full-list response lags.
                # A direct supported-API lookup distinguishes that race from a
                # genuinely removed archive object.
                for document in omitted_available.filtered(
                    lambda item: item.source != "paperless",
                ):
                    try:
                        item = client.get_document(document.paperless_id)
                    except PaperlessNotFound:
                        continue
                    confirmed_missing -= document
                    seen.add(document.paperless_id)
                    if metadata_catalog is None and (
                        isinstance(item.get("correspondent"), int)
                        or isinstance(item.get("document_type"), int)
                        or any(
                            isinstance(tag, int)
                            for tag in (item.get("tags") or [])
                        )
                    ):
                        metadata_catalog = client.metadata_catalog()
                    values = self._paperless_values(
                        item,
                        metadata_catalog=metadata_catalog,
                    )
                    values.pop("source", None)
                    document.with_context(
                        usl_documents_cache_write=True,
                    ).write(values)
                    document._synchronize_versions(item.get("versions") or [])
                    touched |= document
                confirmed_missing.with_context(
                    usl_documents_cache_write=True,
                ).write(
                    {
                        "availability_state": "missing",
                        "last_error": _(
                            "Document was not returned by a full Paperless reconciliation.",
                        ),
                        "permission_sync_state": "pending",
                        "permission_sync_error": False,
                        "permission_checked_at": False,
                    },
                )
                self.sudo().search(
                    [
                        ("paperless_id", "not in", list(seen | trashed_ids)),
                        ("availability_state", "=", "trashed"),
                    ],
                ).with_context(usl_documents_cache_write=True).write(
                    {
                        "availability_state": "permanently_deleted",
                        "permanently_deleted_at": fields.Datetime.now(),
                        "last_error": _(
                            "Paperless no longer returns this previously trashed "
                            "archive item. Its Odoo tombstone and audit history "
                            "were retained.",
                        ),
                        "permission_sync_state": "pending",
                        "permission_sync_error": False,
                        "permission_checked_at": False,
                    },
                )
            if touched:
                touched.filtered(
                    lambda item: (
                        item.availability_state != "trashed"
                        and item.permission_sync_state != "synchronized"
                    ),
                ).with_user(self.env.ref("base.user_root")).action_sync_permissions()
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
                "trashed": len(trashed_ids),
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
        self._require_manager()
        manager = self.env.ref("base.user_admin")
        try:
            return self.with_user(manager).sync_from_paperless(limit_pages=20)
        except PaperlessError:
            _logger.exception("Paperless incremental synchronization failed")
            return False

    @api.model
    def _paperless_search_ids(self, query, filters=None, *, full_text=False):
        """Collect a complete bounded search result before applying Odoo rules.

        Paperless is authoritative for text search, while Odoo is authoritative
        for visibility. Returning only Paperless's first page would silently hide
        authorized matches and make Odoo pagination incorrect.
        """
        maximum = self.env["ir.config_parameter"].sudo().get_int(
            "usl_documents.max_search_results", 10000,
        )
        ids = []
        page = 1
        truncated = False
        while True:
            payload = self._paperless().search(
                query,
                page=page,
                page_size=100,
                filters=filters,
                full_text=full_text,
            )
            for item in payload.get("results", []):
                ids.append(int(item["id"]))
                if len(ids) >= maximum:
                    truncated = bool(payload.get("next")) or len(ids) < payload.get(
                        "count", len(ids),
                    )
                    return ids, truncated
            if not payload.get("next"):
                break
            page += 1
        return ids, truncated

    @api.model
    def _workspace_correspondent_values(self, correspondent):
        """Return archive metadata without exposing an inaccessible Contact."""
        partner = self.env["res.partner"]
        mapped_partner = correspondent.sudo().partner_id
        if mapped_partner:
            partner = self.env["res.partner"].search(
                [("id", "=", mapped_partner.id)],
                limit=1,
            )
        return {
            "id": correspondent.id,
            "name": partner.display_name if partner else correspondent.name,
            "archive_name": correspondent.name,
            "partner_id": partner.id,
        }

    @api.model
    def _broad_search_terms(self, domain):
        """Return positive Search-everywhere terms from a serialized domain."""
        terms = []

        def visit(node):
            if not isinstance(node, (list, tuple)):
                return
            if (
                len(node) >= 3
                and node[0] == "all_text"
                and node[1] in ("=", "like", "ilike")
                and node[2]
            ):
                terms.append(str(node[2]))
                return
            for child in node:
                visit(child)

        visit(domain or [])
        return terms

    @api.model
    def _workspace_order(self, order_by, legacy_sort):
        allowed = {
            "name",
            "document_date",
            "archive_added_at",
            "correspondent_id",
            "document_type_id",
            "company_id",
            "tag_sort_key",
            "status_sort_key",
            "review_state",
            "availability_state",
        }
        normalized = []
        if order_by:
            if not isinstance(order_by, list) or len(order_by) > 3:
                raise ValidationError(_("Invalid document ordering."))
            for term in order_by:
                if not isinstance(term, dict) or term.get("name") not in allowed:
                    raise ValidationError(_("Unsupported document ordering field."))
                normalized.append(
                    (
                        term["name"],
                        bool(term.get("asc", True)),
                    ),
                )
        if not normalized:
            return {
                "recent": "document_date desc, id desc",
                "ingested": "archive_added_at desc, id desc",
                "date": "document_date desc, id desc",
                "title": "name asc, id asc",
            }.get(legacy_sort, "archive_added_at desc, id desc")
        clauses = [
            f"{field_name} {'asc' if ascending else 'desc'}"
            for field_name, ascending in normalized
        ]
        clauses.append(f"id {'asc' if normalized[-1][1] else 'desc'}")
        return ", ".join(clauses)

    @api.model
    def _workspace_document_values(self, item):
        active_links = item._accessible_active_links()
        employee_link = active_links.filtered(
            lambda link: link.res_model == "hr.employee",
        )[:1]
        employee = (
            self.env["hr.employee"].search(
                [("id", "=", employee_link.res_id)],
                limit=1,
            )
            if employee_link
            else self.env["hr.employee"]
        )
        correspondent = self._workspace_correspondent_values(
            item.correspondent_id,
        )
        return {
            "id": item.id,
            "name": item.name,
            "paperless_id": item.paperless_id,
            "date": item.document_date,
            "ingested_at": item.archive_added_at,
            "company": item.company_id.display_name,
            "company_id": item.company_id.id,
            "confidentiality": item.confidentiality,
            "review_state": item.review_state,
            "availability_state": item.availability_state,
            "access_error": (
                _(
                    "The file is blocked until an administrator synchronizes "
                    "its archive permissions.",
                )
                if (
                    item.availability_state in ("available", "permission_error")
                    and item.permission_sync_state != "synchronized"
                )
                else False
            ),
            "correspondent": correspondent["name"] or item.correspondent_name,
            "correspondent_id": item.correspondent_id.id,
            "correspondent_archive_name": (
                item.correspondent_id.name or item.correspondent_name
            ),
            "correspondent_partner_id": correspondent["partner_id"],
            "document_type": (
                item.document_type_id.name or item.document_type_name
            ),
            "document_type_id": item.document_type_id.id,
            "tags": [
                {
                    "id": tag.id,
                    "name": tag.name,
                    "color": tag.color,
                    "text_color": tag.text_color,
                }
                for tag in item.tag_ids.filtered("active")
            ],
            "filename": item.original_filename,
            "mime_type": item.mime_type,
            "source": item.source,
            "current_version": item.current_version_label,
            "version_count": len(item.version_ids),
            "link_count": item.link_count,
            "primary_link": (
                {
                    "name": active_links[0].record_name,
                    "model": active_links[0].res_model,
                }
                if active_links
                else False
            ),
            "linked_employee": (
                {"id": employee.id, "name": employee.display_name}
                if employee
                else False
            ),
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
        tag_ids=None,
        correspondent_id=None,
        document_type_id=None,
        date_from=None,
        date_to=None,
        added_from=None,
        added_to=None,
        source=None,
        confidentiality=None,
        review_state=None,
        linked_state=None,
        linked_model=None,
        linked_id=None,
        mapped_partner_id=None,
        paperless_id=None,
        custom_field_id=None,
        custom_field_value=None,
        search_domain=None,
        shortcut_tag_ids=None,
        group_by=None,
        sort="recent",
        order_by=None,
    ):
        page = max(1, int(page))
        page_size = min(100, max(1, int(page_size)))
        smart_views = self.env["usl.document.smart.view"].accessible_views()
        selected_view = smart_views.filtered(
            lambda item: (item.key or f"view:{item.id}") == workspace,
        )[:1]
        if not selected_view:
            selected_view = smart_views.filtered(lambda item: item.key == "recent")[:1]
        domain = list(selected_view.document_domain()) if selected_view else []
        if search_domain:
            if not isinstance(search_domain, list):
                raise ValidationError(_("Invalid search filters."))
        broad_terms = self._broad_search_terms(search_domain)
        resolved_ids = {}
        relevance_paperless_ids = []
        truncated = False
        for term in broad_terms:
            ids, term_truncated = self._all_text_search_ids(term)
            resolved_ids["all_text", term] = ids
            truncated = truncated or term_truncated
            for paperless_document_id in ids:
                if paperless_document_id not in relevance_paperless_ids:
                    relevance_paperless_ids.append(paperless_document_id)
        try:
            native_domain = self._resolve_remote_search_domain(
                Domain(search_domain or []),
                resolved_ids=resolved_ids,
            )
        except PaperlessError as error:
            return {
                "documents": [],
                "count": 0,
                "degraded": True,
                "error": str(error),
            }
        if shortcut_tag_ids:
            domain.append(
                ("tag_ids", "in", [int(tag_id) for tag_id in shortcut_tag_ids]),
            )
        if selected_view and selected_view.system_rule == "saved":
            saved = json.loads(selected_view.filter_json or "{}")
            query = query or saved.get("query", "")
            company_id = company_id or saved.get("company_id")
            tag_ids = tag_ids or saved.get("tag_ids")
            correspondent_id = correspondent_id or saved.get("correspondent_id")
            document_type_id = document_type_id or saved.get("document_type_id")
            date_from = date_from or saved.get("date_from")
            date_to = date_to or saved.get("date_to")
            added_from = added_from or saved.get("added_from")
            added_to = added_to or saved.get("added_to")
            source = source or saved.get("source")
            confidentiality = confidentiality or saved.get("confidentiality")
            review_state = review_state or saved.get("review_state")
            linked_state = linked_state or saved.get("linked_state")
            linked_record = saved.get("linked_record")
            if linked_record and not (linked_model or linked_id):
                linked_model, linked_id = linked_record.split(":", 1)
            sort = saved.get("sort") or sort
        if company_id:
            domain.append(("company_id", "=", int(company_id)))
        if paperless_id:
            domain.append(("paperless_id", "=", int(paperless_id)))
        if tag_ids:
            normalized_tag_ids = [int(tag_id) for tag_id in tag_ids]
            domain.append(("tag_ids", "in", normalized_tag_ids))
        if correspondent_id:
            domain.append(("correspondent_id", "=", int(correspondent_id)))
        if document_type_id:
            domain.append(("document_type_id", "=", int(document_type_id)))
        for value, operator, field_name in (
            (date_from, ">=", "document_date"),
            (date_to, "<=", "document_date"),
            (added_from, ">=", "archive_added_at"),
            (added_to, "<", "archive_added_at"),
        ):
            if value:
                try:
                    parsed = fields.Date.to_date(value)
                except (TypeError, ValueError) as error:
                    raise ValidationError(_("Invalid date filter.")) from error
                if value == added_to and field_name == "archive_added_at":
                    parsed += timedelta(days=1)
                domain.append((field_name, operator, parsed))
        if source:
            valid_sources = dict(self._fields["source"].selection)
            if source not in valid_sources:
                raise ValidationError(_("Invalid document source filter."))
            domain.append(("source", "=", source))
        if confidentiality:
            if confidentiality not in dict(CONFIDENTIALITIES):
                raise ValidationError(_("Invalid confidentiality filter."))
            domain.append(("confidentiality", "=", confidentiality))
        if review_state:
            if review_state not in ("needs_attention", "classified", "reviewed"):
                raise ValidationError(_("Invalid review-state filter."))
            domain.append(("review_state", "=", review_state))
        mapped_partner = self.env["res.partner"]
        if mapped_partner_id:
            mapped_partner = self.env["res.partner"].browse(
                int(mapped_partner_id),
            ).exists()
            if not mapped_partner:
                raise ValidationError(_("Invalid Contact filter."))
            mapped_partner.check_access("read")
        if linked_model or linked_id:
            if (
                linked_model not in self.env["usl.document.link"]._allowed_models()
                or not linked_id
            ):
                raise ValidationError(_("Invalid linked-record filter."))
            linked_record = self.env[linked_model].browse(int(linked_id)).exists()
            if not linked_record:
                raise ValidationError(_("The linked Odoo record no longer exists."))
            linked_record.check_access("read")
            link_domain = [
                ("link_ids.res_model", "=", linked_model),
                ("link_ids.res_id", "=", int(linked_id)),
                ("link_ids.active", "=", True),
            ]
            if mapped_partner:
                domain.extend(
                    [
                        "|",
                        ("correspondent_id.partner_id", "=", mapped_partner.id),
                        "&",
                        "&",
                        *link_domain,
                    ],
                )
            else:
                domain.extend(link_domain)
        elif mapped_partner:
            domain.append(("correspondent_id.partner_id", "=", mapped_partner.id))
        elif linked_state:
            if linked_state not in ("linked", "unlinked"):
                raise ValidationError(_("Invalid linked-document filter."))
            domain.append(
                ("link_ids", "!=" if linked_state == "linked" else "=", False),
            )
        if not (
            (selected_view
            and selected_view.system_rule == "trash")
            or (linked_model and linked_id)
            or mapped_partner
        ):
            domain.append(
                ("availability_state", "not in", ("trashed", "permanently_deleted")),
            )
        custom_fields = json.loads(
            self.env["ir.config_parameter"].sudo().get_str(
                "usl_documents.paperless_custom_fields",
                "[]",
            ),
        )
        paperless_filters = {}
        if custom_field_id or custom_field_value:
            custom_field = next(
                (
                    item
                    for item in custom_fields
                    if int(item["id"]) == int(custom_field_id or 0)
                ),
                None,
            )
            if not custom_field or custom_field_value in (None, ""):
                raise ValidationError(_("Choose a custom field and a value."))
            data_type = custom_field["data_type"]
            value = custom_field_value
            operator = "icontains"
            if data_type in ("integer", "float"):
                try:
                    value = float(value) if data_type == "float" else int(value)
                except (TypeError, ValueError) as error:
                    raise ValidationError(_("Enter a valid number.")) from error
                operator = "exact"
            elif data_type == "boolean":
                value = str(value).lower() in ("1", "true", "yes")
                operator = "exact"
            elif data_type in ("date", "select", "documentlink"):
                operator = "exact"
            paperless_filters["custom_field_query"] = json.dumps(
                [custom_field["name"], operator, value],
            )
        if query or paperless_filters:
            try:
                ids, query_truncated = self._paperless_search_ids(
                    query,
                    filters=paperless_filters or None,
                )
                truncated = truncated or query_truncated
                domain.append(("paperless_id", "in", ids))
            except PaperlessError as error:
                return {
                    "documents": [],
                    "count": 0,
                    "degraded": True,
                    "error": str(error),
                }
        domain = Domain.AND([Domain(domain), native_domain])
        order = self._workspace_order(order_by, sort)
        try:
            if relevance_paperless_ids and not order_by and not query:
                matching = self.search(domain)
                by_paperless_id = {
                    document.paperless_id: document.id
                    for document in matching
                }
                ordered_ids = [
                    by_paperless_id[paperless_id]
                    for paperless_id in relevance_paperless_ids
                    if paperless_id in by_paperless_id
                ]
                ordered_id_set = set(ordered_ids)
                ordered_ids.extend(
                    document.id
                    for document in matching.sorted(
                        key=lambda item: (
                            item.document_date or fields.Date.from_string("1970-01-01"),
                            item.id,
                        ),
                        reverse=True,
                    )
                    if document.id not in ordered_id_set
                )
                count = len(ordered_ids)
                page_ids = ordered_ids[
                    (page - 1) * page_size : page * page_size
                ]
                documents = self.browse(page_ids)
            else:
                count = self.search_count(domain)
                documents = self.search(
                    domain,
                    order=order,
                    offset=(page - 1) * page_size,
                    limit=page_size,
                )
        except PaperlessError as error:
            return {
                "documents": [],
                "count": 0,
                "degraded": True,
                "error": str(error),
            }
        accessible_documents = self.search([])
        link_facets = []
        seen_links = set()
        for document in accessible_documents:
            for link in document._accessible_active_links():
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
                    },
                )
                if len(link_facets) >= 200:
                    break
            if len(link_facets) >= 200:
                break
        return {
            "documents": [self._workspace_document_values(item) for item in documents],
            "count": count,
            "page": page,
            "page_size": page_size,
            "selected_workspace": (
                selected_view.key or f"view:{selected_view.id}"
                if selected_view
                else workspace
            ),
            "degraded": False,
            "truncated": truncated,
            "companies": [
                {"id": company.id, "name": company.display_name}
                for company in self.env.companies
            ],
            "tags": [
                {
                    "id": tag.id,
                    "name": tag.name,
                    "color": tag.color,
                    "text_color": tag.text_color,
                    "parent_id": tag.parent_id.id,
                    "is_inbox_tag": tag.is_inbox_tag,
                    "document_count": tag.accessible_document_count,
                }
                for tag in self.env["usl.paperless.tag"].search(
                    [("active", "=", True)],
                )
            ],
            "correspondents": [
                self._workspace_correspondent_values(item)
                for item in self.env["usl.paperless.correspondent"].search(
                    [("active", "=", True)],
                )
            ],
            "document_types": [
                {"id": item.id, "name": item.name}
                for item in self.env["usl.paperless.document.type"].search(
                    [("active", "=", True)],
                )
            ],
            "custom_fields": custom_fields,
            "smart_views": [view.workspace_values() for view in smart_views],
            "link_facets": sorted(link_facets, key=lambda item: item["label"]),
            "can_upload": self.env.user.has_group(
                "usl_documents.group_documents_user",
            ),
            "active_operation": self.env[
                "usl.document.operation"
            ].current_workspace_operation(),
            "failed_operations": (
                self.env["usl.document.operation"].workspace_failures()
                if selected_view and selected_view.system_rule == "attention"
                else []
            ),
        }

    @api.model
    def document_detail(self, document_id, check_archive=False):
        document = self.browse(int(document_id)).exists()
        if not document:
            raise ValidationError(_("The archived document no longer exists."))
        document.check_access("read")
        archive_available = True
        if check_archive:
            try:
                document._paperless().compatibility()
            except PaperlessError:
                archive_available = False
        try:
            document.check_access("write")
            can_write = True
        except AccessError:
            can_write = False
        can_manage = self.env.user.has_group(
            "usl_documents.group_documents_manager",
        )
        accessible_links = document._accessible_active_links()
        all_active_links = (
            document.sudo().link_ids.filtered("active")
            if can_manage
            else accessible_links
        )
        review_blocker = False
        if document.availability_state != "available":
            review_blocker = _(
                "Restore or repair the archived document before completing the review.",
            )
        elif document.permission_sync_state != "synchronized":
            review_blocker = _(
                "Resolve archive access before completing the review.",
            )
        elif not document.company_id:
            review_blocker = _(
                "Choose the legal company before completing the review.",
            )
        values = self._workspace_document_values(document)
        custom_field_catalog = {
            int(item["id"]): item
            for item in json.loads(
                self.env["ir.config_parameter"].sudo().get_str(
                    "usl_documents.paperless_custom_fields",
                    "[]",
                ),
            )
            if not (item.get("name") or "").startswith("Legacy Odoo ")
        }
        custom_field_values = []
        for item in json.loads(document.custom_fields_json or "[]"):
            field_id = int(item.get("field") or 0)
            definition = custom_field_catalog.get(field_id)
            if not definition:
                continue
            custom_field_values.append(
                {
                    "id": field_id,
                    "name": definition.get("name"),
                    "data_type": definition.get("data_type") or "string",
                    "value": item.get("value"),
                },
            )
        values.update(
            {
                "checksum": document.checksum,
                "archive_checksum": document.archive_checksum,
                "submitted_by": document.submitted_by_id.display_name,
                "submitted_at": document.submitted_at,
                "paperless_created": document.paperless_created,
                "paperless_modified": document.paperless_modified,
                "permission_checked_at": document.permission_checked_at,
                "permission_sync_error": (
                    document.permission_sync_error
                    if document.permission_sync_state == "failed"
                    else False
                ),
                "trashed_at": document.trashed_at,
                "trashed_by": (
                    document.trashed_by_id.display_name
                    or document.trashed_by_label
                    or _("Not reported")
                ),
                "retention_until": document.retention_until,
                "retention_hold": document.retention_hold,
                "archive_available": archive_available,
                "custom_fields": custom_field_values,
                "can_edit": can_write and document.availability_state == "available",
                "can_change_company": (
                    can_manage
                    and can_write
                    and document.availability_state == "available"
                ),
                "can_change_links": can_write,
                "can_restore": (
                    can_write and document.availability_state == "trashed"
                ),
                "can_trash": (
                    can_write and document.availability_state == "available"
                ),
                "can_manage": can_manage,
                "can_mark_reviewed": bool(
                    can_manage
                    and can_write
                    and document.review_state != "reviewed"
                    and not review_blocker,
                ),
                "review_blocker": review_blocker,
                "permanent_delete_blocker": (
                    _(
                        "Remove the %(count)s active Odoo link(s) before permanent deletion.",
                        count=len(all_active_links),
                    )
                    if can_manage and all_active_links
                    else (
                        _("A retention hold prevents permanent deletion.")
                        if can_manage and document.retention_hold
                        else (
                            _("Retained until %s.") % document.retention_until
                            if (
                                can_manage
                                and
                                document.retention_until
                                and document.retention_until > fields.Datetime.now()
                            )
                            else False
                        )
                    )
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
                        "version_label": (
                            document.version_ids.filtered(
                                lambda version: (
                                    version.paperless_version_id == link.version_id
                                ),
                            )[:1].label
                            or _("Current file")
                        ),
                    }
                    for link in accessible_links
                ],
            },
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
        trashed_remote = {
            int(item["id"]): item
            for item in self._paperless().list_trashed_documents()
        }
        remote.update(trashed_remote)
        live_documents = documents.filtered(
            lambda item: item.availability_state != "permanently_deleted",
        )
        tombstones = documents - live_documents
        mirrored_ids = set(live_documents.mapped("paperless_id"))
        remote_ids = set(remote)
        checksum_mismatches = []
        for document in live_documents.filtered("checksum"):
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
                    },
                )
        params = self.env["ir.config_parameter"].sudo()
        return {
            "schema": "usl-documents-integrity-v1",
            "backup_id": backup_id or fields.Datetime.now().strftime("%Y%m%dT%H%M%SZ"),
            "generated_at": fields.Datetime.to_string(fields.Datetime.now()),
            "odoo_version": self.env["ir.module.module"].search(
                [("name", "=", "base")], limit=1,
            ).latest_version,
            "paperless_version": compatibility["server_version"],
            "paperless_api_version": compatibility["api_version"],
            "paperless_document_count": compatibility["document_count"],
            "paperless_trash_count": len(trashed_remote),
            "paperless_total_count": len(remote),
            "odoo_document_count": len(documents),
            "odoo_live_document_count": len(live_documents),
            "permanent_deletion_tombstone_count": len(tombstones),
            "permanently_deleted_paperless_ids": sorted(
                tombstones.mapped("paperless_id"),
            ),
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
            "permission_sync_failures": live_documents.filtered(
                lambda item: item.permission_sync_state == "failed",
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
                for document in live_documents.filtered("checksum")[:20]
            ],
            "last_successful_sync": params.get_str("usl_documents.last_sync", ""),
            "sync_status": params.get_str("usl_documents.sync_status", "unknown"),
            "backup_completion_status": params.get_str(
                "usl_documents.backup_completion_status", "not_recorded",
            ),
            "last_restore_test": params.get_str(
                "usl_documents.last_restore_test", "not_recorded",
            ),
            "integrity_ok": not (
                mirrored_ids - remote_ids
                or remote_ids - mirrored_ids
                or orphaned_links
                or checksum_mismatches
                or live_documents.filtered(
                    lambda item: item.permission_sync_state == "failed",
                )
            ),
        }

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
            payload["title"] = title
        if "document_date" in values:
            payload["created"] = values.get("document_date") or None
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
            payload[remote_field] = record.paperless_id if record else None
        if "tag_ids" in values:
            requested = {int(tag_id) for tag_id in values.get("tag_ids") or []}
            tags = self.env["usl.paperless.tag"].search(
                [("id", "in", list(requested)), ("active", "=", True)],
            )
            if set(tags.ids) != requested:
                raise ValidationError(_("One or more selected tags are unavailable."))
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
            "usl_documents.max_upload_bytes", 50 * 1024 * 1024,
        )
        if not content or len(content) > maximum:
            raise ValidationError(
                _("The file is empty or exceeds the %(size)s MB upload limit.")
                % {"size": maximum // (1024 * 1024)},
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
                _("The upload company must match the source record's legal company."),
            )
        if company not in self.env.user.company_ids:
            raise AccessError(_("You cannot archive a document for this company."))
        checksum = hashlib.sha256(content).hexdigest()
        retry_operation = self.env["usl.document.operation"].search(
            [
                ("checksum", "=", checksum),
                ("user_id", "=", self.env.user.id),
                ("state", "in", ("failed", "duplicate")),
                ("acknowledged", "=", False),
            ],
            order="create_date desc, id desc",
            limit=1,
        )
        existing = self.search(
            [
                ("availability_state", "=", "available"),
                "|",
                ("checksum", "=", checksum),
                ("version_ids.checksum", "=", checksum),
            ],
            limit=1,
        )
        if existing:
            retry_operation.acknowledge()
            if res_model and res_id:
                matching_version = existing.version_ids.filtered(
                    lambda version: version.checksum == checksum,
                )[:1]
                existing.link_to_record(
                    res_model,
                    int(res_id),
                    version_id=matching_version.paperless_version_id or None,
                )
            return {
                "state": "duplicate",
                "document_id": existing.id,
                "message": _(
                    "“%(document)s” already contains this exact file; the existing "
                    "archive document was reused.",
                )
                % {"document": existing.name},
            }
        trashed = self.search(
            [
                ("availability_state", "=", "trashed"),
                "|",
                ("checksum", "=", checksum),
                ("version_ids.checksum", "=", checksum),
            ],
            limit=1,
        )
        if trashed:
            raise UserError(
                _(
                    "Identical content is already in Trash. Restore that document "
                    "before linking or uploading it again.",
                ),
            )
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
        if remote_matches:
            remote_id = int(remote_matches[0]["id"])
            mirrored = self.search([("paperless_id", "=", remote_id)], limit=1)
            if mirrored:
                retry_operation.acknowledge()
                if res_model and res_id:
                    matching_version = mirrored.version_ids.filtered(
                        lambda version: version.checksum == checksum,
                    )[:1]
                    mirrored.link_to_record(
                        res_model,
                        int(res_id),
                        version_id=matching_version.paperless_version_id or None,
                    )
                return {
                    "state": "duplicate",
                    "document_id": mirrored.id,
                    "message": _(
                        "“%(document)s” already contains this exact file; the existing "
                        "archive document was reused.",
                    )
                    % {"document": mirrored.name},
                }
            operation = self.env["usl.document.operation"].sudo().create({
                "name": filename,
                "state": "duplicate",
                "checksum": checksum,
                "mime_type": content_type,
                "company_id": company.id,
                "confidentiality": confidentiality,
                "res_model": res_model,
                "res_id": int(res_id) if res_id else 0,
                "source": source,
                "error_message": _(
                    "Identical content exists outside your authorized Odoo archive view. "
                    "A Documents administrator must classify it before reuse.",
                ),
                "retry_of_id": retry_operation.id,
                "retry_count": (retry_operation.retry_count + 1)
                if retry_operation
                else 0,
            })
            return {
                "state": "duplicate",
                "operation_id": operation.id,
                "message": operation.error_message,
            }
        operation = self.env["usl.document.operation"].sudo().create({
            "name": filename,
            "state": "uploading",
            "checksum": checksum,
            "mime_type": content_type,
            "company_id": company.id,
            "confidentiality": confidentiality,
            "res_model": res_model,
            "res_id": int(res_id) if res_id else 0,
            "source": source,
            "retry_of_id": retry_operation.id,
            "retry_count": (retry_operation.retry_count + 1)
            if retry_operation
            else 0,
        })
        try:
            task_id = self._paperless().upload_multipart(
                content, filename, content_type, title=filename,
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

    def link_to_record(self, res_model, res_id, version_id=None):
        self.ensure_one()
        return self.env["usl.document.link"].create_for_record(
            self,
            res_model,
            int(res_id),
            version_id=version_id,
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
        record.check_access("read")
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
        links.unlink()
        return True

    def action_open_linked_record(self, link_id):
        self.ensure_one()
        self.check_access("read")
        link = self.sudo().link_ids.filtered(
            lambda item: item.id == int(link_id) and item.active,
        )
        if not link:
            raise ValidationError(_("That Odoo relationship no longer exists."))
        return link._record_action(user_env=self.env)

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
        if (
            self.permission_sync_state != "synchronized"
            or not mapping
            or not mapping._identity_is_safe()
        ):
            raise UserError(
                _(
                    "Open in Paperless is blocked until your individual archive "
                    "identity and this document's permissions are synchronized.",
                ),
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
        ]).filtered(lambda mapping: mapping._identity_is_safe())
        for document in self:
            if document.availability_state not in ("available", "permission_error"):
                # Paperless bulk permission edits only accept live documents.
                # Keep unavailable roots fail-closed and force a fresh check as
                # soon as reconciliation or an explicit restore makes them live.
                document.sudo().with_context(
                    skip_permission_invalidation=True,
                    usl_documents_cache_write=True,
                ).write({
                    "permission_sync_state": "pending",
                    "permission_sync_error": False,
                    "permission_checked_at": False,
                })
                continue
            view_users = []
            change_users = []
            for mapping in mappings:
                try:
                    document.with_user(mapping.user_id).check_access("read")
                except AccessError:
                    continue
                view_users.append(mapping.paperless_user_id)
                if mapping.user_id.has_group(
                    "usl_documents.group_documents_manager",
                ):
                    change_users.append(mapping.paperless_user_id)
            try:
                document._paperless().set_document_permissions(
                    document.paperless_id,
                    view_users=sorted(view_users),
                    change_users=sorted(change_users),
                )
            except PaperlessError as error:
                document.sudo().with_context(
                    skip_permission_invalidation=True,
                    usl_documents_cache_write=True,
                ).write({
                    "permission_sync_state": "failed",
                    "permission_sync_error": str(error),
                    "permission_checked_at": fields.Datetime.now(),
                    "availability_state": (
                        "permission_error"
                        if document.availability_state
                        in ("available", "permission_error")
                        else document.availability_state
                    ),
                })
            else:
                document.sudo().with_context(
                    skip_permission_invalidation=True,
                    usl_documents_cache_write=True,
                ).write({
                    "permission_sync_state": "synchronized",
                    "permission_sync_error": False,
                    "permission_checked_at": fields.Datetime.now(),
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
        self._require_manager()
        return {
            "type": "ir.actions.act_window",
            "name": _("Linked Odoo records"),
            "res_model": "usl.document.link",
            "view_mode": "list,form",
            "domain": [("document_id", "=", self.id)],
            "context": {"default_document_id": self.id},
        }

    def action_mark_reviewed(self):
        self.ensure_one()
        self.check_access("write")
        self._require_manager()
        if self.availability_state != "available":
            raise UserError(
                _(
                    "Restore or repair the archived document before completing "
                    "the review.",
                ),
            )
        if self.permission_sync_state != "synchronized":
            raise UserError(
                _("Resolve archive access before completing the review."),
            )
        if not self.company_id:
            raise ValidationError(
                _("Choose the legal company before completing the review."),
            )
        self.write({"review_state": "reviewed"})
        return self.document_detail(self.id)


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

    def action_restore_as_current(self):
        self.ensure_one()
        return self.document_id.restore_version(self.paperless_version_id)


class UslDocumentLink(models.Model):
    _name = "usl.document.link"
    _description = "Archived Document Business Relationship"
    _inherit = ["mail.thread"]
    _order = "create_date desc, id desc"

    document_id = fields.Many2one(
        "usl.document", required=True, index=True, ondelete="restrict", tracking=True,
    )
    res_model = fields.Char(required=True, index=True, readonly=True)
    res_id = fields.Integer(required=True, index=True, readonly=True)
    record_name = fields.Char(required=True, readonly=True)
    company_id = fields.Many2one(
        "res.company", required=True, index=True, ondelete="restrict", readonly=True,
    )
    linked_by_id = fields.Many2one(
        "res.users", required=True, readonly=True, default=lambda self: self.env.user,
    )
    linked_at = fields.Datetime(
        required=True, readonly=True, default=fields.Datetime.now,
    )
    version_id = fields.Char(
        help="Paperless version that supports this business record, when legally relevant.",
        readonly=True,
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
    def create_for_record(self, document, res_model, res_id, version_id=None):
        document.ensure_one()
        document.check_access("write")
        if document.availability_state != "available":
            raise UserError(
                _("Only an available archived document can receive a new Odoo link."),
            )
        if res_model not in self._allowed_models():
            raise ValidationError(_("This Odoo model cannot receive archived documents."))
        if version_id and not document.version_ids.filtered(
            lambda version: version.paperless_version_id == str(version_id),
        ):
            raise ValidationError(
                _("The selected file version does not belong to this document."),
            )
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
                _("The document and Odoo record must belong to the same legal company."),
            )
        existing = self.sudo().search([
            ("document_id", "=", document.id),
            ("res_model", "=", res_model),
            ("res_id", "=", res_id),
        ], limit=1)
        if existing:
            if not existing.active:
                existing.sudo().write({"active": True})
            if version_id and not existing.version_id:
                existing.sudo().write({"version_id": str(version_id)})
            return existing
        if not document.company_id:
            document.sudo().with_context(usl_documents_policy_write=True).write(
                {
                    "company_id": company.id,
                    "review_state": "classified",
                },
            )
        link = self.sudo().create(
            {
                "document_id": document.id,
                "res_model": res_model,
                "res_id": res_id,
                "record_name": record.display_name,
                "company_id": company.id,
                "linked_by_id": self.env.user.id,
                "version_id": (
                    str(version_id)
                    if version_id
                    else document.version_ids.filtered("is_current")[
                        :1
                    ].paperless_version_id
                    or False
                ),
            },
        )
        if document.permission_sync_state != "synchronized":
            document.with_user(self.env.ref("base.user_root")).action_sync_permissions()
        return link

    def action_open_record(self):
        self.ensure_one()
        return self._record_action()

    def _record_action(self, user_env=None):
        self.ensure_one()
        user_env = user_env or self.env
        record = user_env[self.res_model].browse(self.res_id).exists()
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
        if self.env.su:
            return super().unlink()
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
        return super().create(values_list)

    def write(self, values):
        if not self.env.su:
            raise AccessError(
                _("Ingestion state can only be changed by the archive workflow."),
            )
        return super().write(values)

    def _workspace_values(self):
        self.ensure_one()
        document = self.document_id or self.target_document_id
        return {
            "id": self.id,
            "name": self.name,
            "state": self.state,
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
            [("state", "in", ("uploading", "processing"))],
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

    def poll(self):
        for operation in self.filtered(
            lambda item: item.state == "processing" and item.paperless_task_id,
        ):
            try:
                task = self.env["usl.document"]._paperless().task(
                    operation.paperless_task_id,
                )
            except PaperlessError as error:
                operation.sudo().write({"error_message": str(error)})
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
                    [("paperless_id", "=", paperless_id)], limit=1,
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
                            "confidentiality": operation.confidentiality,
                            "review_state": "classified",
                            "submitted_by_id": operation.user_id.id,
                            "submitted_at": operation.create_date,
                            "checksum": operation.checksum,
                        },
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
                        },
                    )
                if operation.res_model and operation.res_id:
                    document.with_user(operation.user_id).link_to_record(
                        operation.res_model, operation.res_id,
                    )
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
                result_data = task.get("result_data")
                operation.sudo().write({
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
                "id": operation.id,
                "name": operation.name,
                "state": operation.state,
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
        operations = self.search([("state", "=", "processing")], limit=100)
        return operations.poll()


class UslPaperlessUserMapping(models.Model):
    _name = "usl.paperless.user.mapping"
    _description = "Odoo to Paperless Individual Identity"
    _order = "user_id"

    user_id = fields.Many2one(
        "res.users", required=True, index=True, ondelete="cascade",
    )
    paperless_user_id = fields.Integer(required=True, index=True)
    paperless_username = fields.Char(required=True)
    oidc_identity_id = fields.Many2one(
        "usl.oidc.identity",
        string="Pocket ID identity",
        ondelete="restrict",
        groups="base.group_system",
        help=(
            "Immutable Pocket ID identity used by this Odoo user. Paperless "
            "still keeps its own numeric user identity for object permissions."
        ),
    )
    oidc_subject_fingerprint = fields.Char(
        related="oidc_identity_id.subject_fingerprint",
        string="Pocket identity",
        readonly=True,
    )
    qa_local_identity = fields.Boolean(
        string="QA local login",
        groups="base.group_system",
        help=(
            "Explicit exception for the isolated QA environment's documented "
            "username/admin test accounts. It is ignored everywhere else."
        ),
    )
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
        "UNIQUE(user_id)", "An Odoo user may have only one Paperless identity.",
    )
    _paperless_user_unique = models.Constraint(
        "UNIQUE(paperless_user_id)",
        "A Paperless identity may be mapped to only one Odoo user.",
    )

    def _mapped_user_documents(self):
        self.ensure_one()
        visible = self.user_id._documents_visible_for_permission_sync()
        return self.env["usl.document"].browse(visible[self.user_id.id])

    @api.model
    def _pocket_provider(self):
        return self.env.ref(
            "usl_pocketid.provider_pocketid",
            raise_if_not_found=False,
        ).sudo()

    def _identity_error(self):
        self.ensure_one()
        provider = self._pocket_provider()
        if not provider or not provider.enabled:
            return False
        if (
            self.qa_local_identity
            and os.getenv("USL_DEPLOYMENT_ENV", "").strip() == "qa"
        ):
            return False
        identity = self.oidc_identity_id.sudo()
        if not identity:
            return _(
                "Link this user to their governed Pocket ID identity before "
                "granting direct Paperless access.",
            )
        if (
            not identity.active
            or identity.provider_id != provider
            or identity.issuer != provider.usl_oidc_issuer
            or identity.user_id != self.user_id
            or not self.user_id.active
            or not self.user_id.usl_pocketid_access
        ):
            return _(
                "The Pocket ID identity is disabled, mismatched, or no longer "
                "authorized for this Odoo user.",
            )
        return False

    def _identity_is_safe(self):
        self.ensure_one()
        return not self._identity_error()

    @api.model_create_multi
    def create(self, values_list):
        trusted_seed = (
            self.env.context.get("usl_documents_mapping_no_sync")
            and self.env.su
        )
        normalized = []
        for values in values_list:
            values = dict(values)
            if values.get("qa_local_identity") and (
                not trusted_seed
                or os.getenv("USL_DEPLOYMENT_ENV", "").strip() != "qa"
            ):
                raise AccessError(
                    _("Local Paperless identities are allowed only in isolated QA."),
                )
            if values.get("user_id") and not values.get("oidc_identity_id"):
                provider = self._pocket_provider()
                if provider and provider.enabled:
                    identities = self.env["usl.oidc.identity"].sudo().search(
                        [
                            ("user_id", "=", values["user_id"]),
                            ("issuer", "=", provider.usl_oidc_issuer),
                            ("active", "=", True),
                        ],
                        limit=2,
                    )
                    if len(identities) == 1:
                        values["oidc_identity_id"] = identities.id
            if not trusted_seed:
                values.update(
                    {
                        "sync_state": "pending",
                        "last_verified_at": False,
                        "last_error": False,
                    },
                )
            normalized.append(values)
        return super().create(normalized)

    def write(self, values):
        values = dict(values)
        trusted_seed = (
            self.env.context.get("usl_documents_mapping_no_sync")
            and self.env.su
        )
        verified_write = (
            self.env.context.get("usl_documents_mapping_verification")
            and self.env.su
        )
        if values.get("qa_local_identity") and (
            not trusted_seed
            or os.getenv("USL_DEPLOYMENT_ENV", "").strip() != "qa"
        ):
            raise AccessError(
                _("Local Paperless identities are allowed only in isolated QA."),
            )
        protected_fields = {"sync_state", "last_verified_at", "last_error"}
        if (
            protected_fields.intersection(values)
            and not (trusted_seed or verified_write)
        ):
            raise AccessError(
                _("Paperless verification state can only be changed by verification."),
            )
        identity_fields = {
            "user_id",
            "paperless_user_id",
            "paperless_username",
            "oidc_identity_id",
            "qa_local_identity",
        }
        if identity_fields.intersection(values) and not (trusted_seed or verified_write):
            values.update(
                {
                    "sync_state": "pending",
                    "last_verified_at": False,
                    "last_error": False,
                },
            )
        sync_fields = {
            "user_id",
            "paperless_user_id",
            "paperless_username",
            "oidc_identity_id",
            "qa_local_identity",
            "sync_state",
            "active",
        }
        effective_sync_fields = {
            field_name
            for field_name in sync_fields.intersection(values)
            if any(
                (
                    mapping[field_name].id
                    if mapping._fields[field_name].type == "many2one"
                    else mapping[field_name]
                )
                != values[field_name]
                for mapping in self
            )
        }
        if (
            not effective_sync_fields
            or trusted_seed
        ):
            return super().write(values)
        before_documents = {
            mapping.id: mapping._mapped_user_documents()
            for mapping in self
            if mapping.active and mapping.sync_state == "synchronized"
        }
        revoking = any(
            mapping.id in before_documents
            and (
                values.get("active", mapping.active) is False
                or values.get("sync_state", mapping.sync_state) != "synchronized"
                or "user_id" in effective_sync_fields
                or "paperless_user_id" in effective_sync_fields
                or "oidc_identity_id" in effective_sync_fields
                or "qa_local_identity" in effective_sync_fields
            )
            for mapping in self
        )
        result = super().write(values)
        documents = self.env["usl.document"].browse(
            list(
                set().union(
                    *(set(items.ids) for items in before_documents.values()),
                    *(
                        set(mapping._mapped_user_documents().ids)
                        for mapping in self
                        if mapping.active
                        and mapping.sync_state == "synchronized"
                    ),
                ),
            ),
        )
        if documents:
            documents.with_user(
                self.env.ref("base.user_root"),
            ).action_sync_permissions()
        if revoking and documents.filtered(
            lambda document: document.permission_sync_state == "failed",
        ):
            raise UserError(
                _(
                    "The identity change was not saved because Paperless could "
                    "not safely revoke existing document permissions.",
                ),
            )
        return result

    def unlink(self):
        documents = self.env["usl.document"].browse(
            list(
                set().union(
                    *(
                        set(mapping._mapped_user_documents().ids)
                        for mapping in self
                        if mapping.active
                        and mapping.sync_state == "synchronized"
                    ),
                )
                if self
                else set(),
            ),
        )
        result = super().unlink()
        if documents:
            documents.with_user(
                self.env.ref("base.user_root"),
            ).action_sync_permissions()
            if documents.filtered(
                lambda document: document.permission_sync_state == "failed",
            ):
                raise UserError(
                    _(
                        "The identity was not removed because Paperless could "
                        "not safely revoke existing document permissions.",
                    ),
                )
        return result

    def action_mark_verified(self):
        if not self.env.user.has_group(
            "usl_documents.group_documents_manager",
        ):
            raise AccessError(_("Only Documents administrators verify identities."))
        errors = []
        for mapping in self:
            identity_error = mapping._identity_error()
            if identity_error:
                mapping.sudo().with_context(
                    usl_documents_mapping_verification=True,
                ).write(
                    {
                        "sync_state": "failed",
                        "last_error": identity_error,
                    },
                )
                errors.append(
                    _("%(identity)s: %(error)s")
                    % {
                        "identity": mapping.display_name,
                        "error": identity_error,
                    },
                )
                continue
            try:
                payload = self.env["usl.document"]._paperless().get_user(
                    mapping.paperless_user_id,
                )
            except PaperlessError as error:
                message = str(error)
                mapping.sudo().with_context(
                    usl_documents_mapping_verification=True,
                ).write(
                    {
                        "sync_state": "failed",
                        "last_error": message,
                    },
                )
                errors.append(
                    _("%(identity)s: %(error)s")
                    % {"identity": mapping.display_name, "error": message},
                )
                continue
            remote_username = payload.get("username")
            if remote_username != mapping.paperless_username:
                message = (
                    _(
                        "Paperless user %(id)s is %(actual)s, not %(expected)s.",
                    )
                    % {
                        "id": mapping.paperless_user_id,
                        "actual": remote_username or _("unnamed"),
                        "expected": mapping.paperless_username,
                    },
                )
                mapping.sudo().with_context(
                    usl_documents_mapping_verification=True,
                ).write(
                    {
                        "sync_state": "failed",
                        "last_error": message,
                    },
                )
                errors.append(
                    _("%(identity)s: %(error)s")
                    % {"identity": mapping.display_name, "error": message},
                )
                continue
            mapping.sudo().with_context(
                usl_documents_mapping_verification=True,
            ).write(
                {
                    "sync_state": "synchronized",
                    "last_verified_at": fields.Datetime.now(),
                    "last_error": False,
                },
            )
        if errors:
            return {
                "type": "ir.actions.client",
                "tag": "display_notification",
                "params": {
                    "title": _("Identity verification needs attention"),
                    "message": "\n".join(errors),
                    "type": "danger",
                    "sticky": True,
                    "next": {"type": "ir.actions.client", "tag": "reload"},
                },
            }
        return True
