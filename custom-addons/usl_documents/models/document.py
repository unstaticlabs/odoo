import base64
import hashlib
import json
import logging
import math
import os
from datetime import UTC, datetime, timedelta

from odoo import SUPERUSER_ID, Command, _, api, fields, models
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

ARCHIVE_MODES = [
    ("mandatory", "Mandatory retention"),
    ("automatic", "Automatic"),
    ("on_request", "Keep on request"),
    ("never", "Excluded"),
]

DOCUMENT_ROLES = [
    ("evidence", "Evidence"),
    ("library", "Library"),
    ("background", "Background"),
]

ATTACHMENT_ORIGINS = [
    ("documents_workspace", "Documents workspace"),
    ("direct_record", "Direct record upload"),
    ("chatter", "Chatter"),
    ("portal", "Portal"),
    ("generated_final", "Generated final output"),
    ("generated_transient", "Generated transient output"),
    ("external_paperless", "External Paperless intake"),
    ("migration", "Migration"),
    ("backfill", "Backfill"),
]

PERMISSION_SYNC_BATCH_SIZE = 100


class UslDocument(models.Model):
    _name = "usl.document"
    _description = "Archived Document"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "document_date desc, archive_added_at desc, id desc"

    name = fields.Char(required=True, readonly=True, tracking=True)
    all_text = fields.Char(
        string="Everywhere",
        compute="_compute_search_helpers",
        search="_search_all_text",
        help=(
            "Search OCR content, title, Paperless metadata, additional details, "
            "and accessible linked Odoo records."
        ),
    )
    semantic_text = fields.Char(
        string="Meaning (Semantic)",
        compute="_compute_search_helpers",
        search="_search_semantic_text",
        help="Search by meaning with the local BGE-M3 semantic index.",
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
    original_created_at = fields.Datetime(
        string="Original creation",
        readonly=True,
        index=True,
        help="Creation timestamp recorded by the system that supplied the document.",
    )
    original_modified_at = fields.Datetime(
        string="Original modification",
        readonly=True,
        index=True,
        help="Last modification timestamp recorded by the system that supplied the document.",
    )
    archive_added_at = fields.Datetime(
        string="Added",
        compute="_compute_archive_added_at",
        store=True,
        index=True,
        help=(
            "Original creation time supplied with imported documents. New uploads "
            "use their submission time."
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
    accounting_evidence = fields.Boolean(
        index=True,
        tracking=True,
        help=(
            "Mark this document as supporting evidence for bookkeeping, tax, "
            "or audit work. With Accounting evidence privacy, it becomes "
            "available read-only to Accounting Evidence Readers. The document "
            "is also easier to retrieve in accounting filters and is put on "
            "retention hold if Paperless reports it in Trash. Changing this "
            "setting resynchronizes archive permissions."
        ),
    )
    access_scope = fields.Selection(
        [
            ("company", "Company policy"),
            ("linked_record", "Linked record access"),
        ],
        required=True,
        default="company",
        index=True,
        tracking=True,
        help=(
            "Linked-record documents are visible only to Documents users who can "
            "read at least one active related Odoo record."
        ),
    )
    intake_role = fields.Selection(
        DOCUMENT_ROLES,
        required=True,
        default="background",
        index=True,
        readonly=True,
        help=(
            "Odoo presentation role captured when the Paperless root entered "
            "Documents. Business relationships carry their own role."
        ),
    )
    is_prominent = fields.Boolean(
        compute="_compute_is_prominent",
        search="_search_is_prominent",
        help=(
            "Prominent roots may appear in Home and My library. Background-only "
            "roots remain available from business context and archive search."
        ),
    )
    is_starred = fields.Boolean(
        string="Starred for me",
        compute="_compute_personal_workspace_state",
        search="_search_is_starred",
        help="Private Odoo preference; it does not change Paperless metadata.",
    )
    recently_opened = fields.Boolean(
        string="Recently opened by me",
        compute="_compute_personal_workspace_state",
        search="_search_recently_opened",
    )
    is_in_my_library = fields.Boolean(
        string="In my library",
        compute="_compute_is_in_my_library",
        search="_search_is_in_my_library",
        help=(
            "Documents uploads, accessible library relationships, and documents "
            "starred by the current user."
        ),
    )
    permitted_user_ids = fields.Many2many(
        "res.users",
        "usl_document_permitted_user_rel",
        "document_id",
        "user_id",
        string="Users allowed by linked records",
        readonly=True,
        copy=False,
    )
    review_state = fields.Selection(
        [
            ("needs_attention", "Needs attention"),
            ("classified", "Ready for review"),
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
        readonly=True,
        tracking=True,
        help=(
            "Updated automatically from Paperless processing, reconciliation, "
            "trash and restore operations, and permission checks. It cannot be "
            "changed manually."
        ),
    )
    original_filename = fields.Char(readonly=True)
    mime_type = fields.Char(readonly=True)
    checksum = fields.Char(index=True, readonly=True)
    metadata_hash = fields.Char(index=True, readonly=True, copy=False)
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
    version_count = fields.Integer(compute="_compute_file_presentation")
    has_distinct_archive_file = fields.Boolean(compute="_compute_file_presentation")
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

    @api.depends("version_ids", "checksum", "archive_checksum")
    def _compute_file_presentation(self):
        for document in self:
            document.version_count = len(document.version_ids)
            document.has_distinct_archive_file = bool(
                document.archive_checksum
                and document.checksum
                and document.archive_checksum != document.checksum,
            )

    @api.depends("original_created_at", "submitted_at", "paperless_created")
    def _compute_archive_added_at(self):
        for document in self:
            document.archive_added_at = (
                document.original_created_at
                or document.submitted_at
                or document.paperless_created
            )

    def _merge_original_timestamps(self, created_at=None, modified_at=None):
        """Preserve the earliest creation and latest source modification."""
        incoming_created = fields.Datetime.to_datetime(created_at)
        incoming_modified = fields.Datetime.to_datetime(modified_at) or incoming_created
        for document in self:
            values = {}
            if incoming_created and (
                not document.original_created_at
                or incoming_created < document.original_created_at
            ):
                values["original_created_at"] = incoming_created
            if incoming_modified and (
                not document.original_modified_at
                or incoming_modified > document.original_modified_at
            ):
                values["original_modified_at"] = incoming_modified
            if incoming_created and (
                not document.submitted_at or incoming_created < document.submitted_at
            ):
                values["submitted_at"] = incoming_created
            if values:
                document.sudo().with_context(
                    usl_documents_cache_write=True,
                ).write(values)
        return True

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
        visible_by_document = self._accessible_active_links_by_document()
        for document in self:
            document.link_count = len(visible_by_document[document.id])

    def _accessible_active_links_by_document(self):
        """Return readable active links grouped by document.

        One target search is issued per linked model rather than one access
        check per relationship.  The document check and every target search
        still run in the current user's environment.
        """
        visible_by_document = {
            document_id: self.env["usl.document.link"].sudo().browse()
            for document_id in self.ids
        }
        if not self:
            return visible_by_document

        self.check_access("read")
        links = self.env["usl.document.link"].sudo().search(
            [
                ("document_id", "in", self.ids),
                ("active", "=", True),
            ],
        )
        links_by_model = {}
        for link in links:
            if link.res_model in self.env:
                links_by_model.setdefault(link.res_model, []).append(link)

        for model_name, model_links in links_by_model.items():
            target_model = self.env[model_name]
            try:
                target_model.check_access("read")
                visible_target_ids = set(
                    target_model.with_context(active_test=False).search(
                        [("id", "in", [link.res_id for link in model_links])],
                    ).ids,
                )
            except AccessError:
                continue
            for link in model_links:
                if link.res_id in visible_target_ids:
                    visible_by_document[link.document_id.id] |= link
        return visible_by_document

    @api.depends("intake_role", "link_ids.active", "link_ids.document_role")
    @api.depends_context("uid", "allowed_company_ids")
    def _compute_is_prominent(self):
        visible_by_document = self._accessible_active_links_by_document()
        for document in self:
            accessible_roles = visible_by_document[document.id].mapped(
                "document_role",
            )
            document.is_prominent = (
                document.intake_role in {"evidence", "library"}
                or bool({"evidence", "library"}.intersection(accessible_roles))
            )

    @api.depends_context("uid")
    def _compute_personal_workspace_state(self):
        states = self.env["usl.document.user.state"].sudo().search(
            [
                ("user_id", "=", self.env.user.id),
                ("document_id", "in", self.ids),
            ],
        )
        by_document = {state.document_id.id: state for state in states}
        cutoff = fields.Datetime.now() - timedelta(days=30)
        for document in self:
            state = by_document.get(document.id)
            document.is_starred = bool(state and state.starred)
            document.recently_opened = bool(
                state and state.last_opened_at and state.last_opened_at >= cutoff,
            )

    @api.depends("intake_role", "link_ids.active", "link_ids.document_role")
    @api.depends_context("uid", "allowed_company_ids")
    def _compute_is_in_my_library(self):
        starred_document_ids = set(
            self.env["usl.document.user.state"].sudo().search(
                [
                    ("user_id", "=", self.env.user.id),
                    ("document_id", "in", self.ids),
                    ("starred", "=", True),
                ],
            ).mapped("document_id").ids,
        )
        visible_by_document = self._accessible_active_links_by_document()
        for document in self:
            accessible_roles = visible_by_document[document.id].mapped(
                "document_role",
            )
            document.is_in_my_library = (
                document.intake_role == "library"
                or "library" in accessible_roles
                or document.id in starred_document_ids
            )

    @api.model
    def _accessible_role_document_ids(self, roles):
        """Resolve role visibility through the target record's native ACLs."""
        accessible_ids = set(self.search([]).ids)
        links = self.env["usl.document.link"].sudo().search(
            [
                ("document_id", "in", list(accessible_ids)),
                ("active", "=", True),
                ("document_role", "in", list(roles)),
            ],
        )
        return self._accessible_link_document_ids(links)

    @api.model
    def _accessible_project_document_ids(self):
        accessible_ids = self.search([]).ids
        links = self.env["usl.document.link"].sudo().search(
            [
                ("document_id", "in", accessible_ids),
                ("active", "=", True),
                ("res_model", "in", ("project.project", "project.task")),
            ],
        )
        return self._accessible_link_document_ids(links)

    @api.model
    def _accessible_link_document_ids(self, links):
        """Return linked document IDs using one ACL-aware query per model."""
        visible_ids = set()
        for model_name in sorted(set(links.mapped("res_model"))):
            if model_name not in self.env:
                continue
            model_links = links.filtered(
                lambda link, name=model_name: link.res_model == name,
            )
            visible_target_ids = set(
                self.env[model_name]
                .browse(model_links.mapped("res_id"))
                .exists()
                ._filtered_access("read")
                .ids,
            )
            visible_ids.update(
                link.document_id.id
                for link in model_links
                if link.res_id in visible_target_ids
            )
        return visible_ids

    @api.model
    def _search_boolean_ids(self, operator, value, matching_ids):
        if operator not in ("=", "!=") or value not in (True, False):
            raise ValidationError(_("Unsupported personal Documents filter."))
        positive = (operator == "=" and value) or (operator == "!=" and not value)
        return [("id", "in" if positive else "not in", list(matching_ids))]

    @api.model
    def _search_is_prominent(self, operator, value):
        matching_ids = set(
            self.search([("intake_role", "in", ("evidence", "library"))]).ids,
        )
        matching_ids.update(
            self._accessible_role_document_ids({"evidence", "library"}),
        )
        return self._search_boolean_ids(operator, value, matching_ids)

    @api.model
    def _personal_state_document_ids(self, domain):
        return set(
            self.env["usl.document.user.state"].sudo().search(
                [("user_id", "=", self.env.user.id), *domain],
            ).mapped("document_id").ids,
        )

    @api.model
    def _search_is_starred(self, operator, value):
        return self._search_boolean_ids(
            operator,
            value,
            self._personal_state_document_ids([("starred", "=", True)]),
        )

    @api.model
    def _search_recently_opened(self, operator, value):
        return self._search_boolean_ids(
            operator,
            value,
            self._personal_state_document_ids(
                [
                    (
                        "last_opened_at",
                        ">=",
                        fields.Datetime.now() - timedelta(days=30),
                    ),
                ],
            ),
        )

    @api.model
    def _search_is_in_my_library(self, operator, value):
        matching_ids = set(self.search([("intake_role", "=", "library")]).ids)
        matching_ids.update(self._accessible_role_document_ids({"library"}))
        matching_ids.update(
            self._personal_state_document_ids([("starred", "=", True)]),
        )
        return self._search_boolean_ids(operator, value, matching_ids)

    def _accessible_active_links(self):
        """Return links whose target record is readable by the current user."""
        self.ensure_one()
        return self._accessible_active_links_by_document()[self.id]

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
        visible_by_document = self._accessible_active_links_by_document()
        for document in self:
            document.all_text = False
            document.semantic_text = False
            document.archive_text = False
            document.custom_field_text = False
            document.has_linked_record = bool(visible_by_document[document.id])
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
        visible_by_document = self._accessible_active_links_by_document()
        for document in self:
            employee_link = visible_by_document[document.id].filtered(
                lambda link: link.active and link.res_model == "hr.employee",
            )[:1]
            document.linked_employee_id = (
                self.env["hr.employee"].browse(employee_link.res_id)
                if employee_link
                else False
            )

    @api.model
    def _search_archive_text(self, operator, value):
        if operator not in ("=", "!=", "like", "not like", "ilike", "not ilike"):
            raise ValidationError(_("Unsupported document-content search operator."))
        if not value:
            return []
        ids, _truncated = self._permission_scoped_paperless_search_ids(
            str(value),
            self._authorized_paperless_scope(),
            fields="content",
        )
        negative = operator in ("!=", "not like", "not ilike")
        return [("paperless_id", "not in" if negative else "in", ids)]

    @api.model
    def _accessible_local_text_ids(self, value, *, documents=None):
        """Supplement Paperless full text with Odoo-owned, authorized labels."""
        text = str(value or "").strip()
        if not text:
            return []
        documents = documents if documents is not None else self.search([])
        if not documents:
            return []
        matching_document_ids = set(
            self.search(
                Domain("id", "in", documents.ids)
                & Domain.OR(
                    Domain(field_name, "ilike", text)
                    for field_name in (
                        "name",
                        "original_filename",
                        "company_id.name",
                        "correspondent_id.name",
                        "document_type_id.name",
                        "tag_sort_key",
                    )
                ),
            ).ids,
        )
        links = self.env["usl.document.link"].sudo().search(
            [
                ("document_id", "in", documents.ids),
                ("active", "=", True),
                ("record_name", "ilike", text),
            ],
        )
        matching_document_ids.update(self._accessible_link_document_ids(links))
        return sorted(
            document.paperless_id
            for document in documents
            if document.id in matching_document_ids and document.paperless_id
        )

    @api.model
    def _all_text_search_ids(self, value):
        ids, truncated, warnings = self._hybrid_search_ids(value)
        for warning in warnings:
            _logger.info(
                "Documents search degradation: %s",
                warning.get("code", "unknown"),
            )
        return ids, truncated

    @api.model
    def _lexical_all_text_search_ids(
        self,
        value,
        *,
        scope=None,
        authorized_documents=None,
        local_documents=None,
    ):
        scope = (
            self._authorized_paperless_scope(authorized_documents)
            if scope is None
            else scope
        )
        ids, truncated = self._permission_scoped_paperless_search_ids(
            str(value),
            scope,
            # Ordinary Documents search is user text, not Paperless's advanced
            # Tantivy query language. The simple text surface safely handles
            # apostrophes and other natural French/English punctuation.
            full_text=False,
            fields="all",
        )
        seen = set(ids)
        for document_id in self._accessible_local_text_ids(
            value,
            documents=local_documents,
        ):
            if document_id not in seen:
                ids.append(document_id)
                seen.add(document_id)
        return ids, truncated

    @api.model
    def _authorized_paperless_scope(self, documents=None):
        documents = documents if documents is not None else self.search(
            [
                (
                    "availability_state",
                    "not in",
                    ("trashed", "permanently_deleted"),
                ),
            ],
        )
        return sorted(
            {
                document.paperless_id
                for document in documents
                if document.paperless_id
            },
        )

    @api.model
    def _fuse_search_rankings(self, lexical_ids, semantic_ids):
        lexical_ids = list(dict.fromkeys(int(item) for item in lexical_ids))
        semantic_ids = list(dict.fromkeys(int(item) for item in semantic_ids))
        lexical_set = set(lexical_ids)
        return lexical_ids + [
            document_id
            for document_id in semantic_ids
            if document_id not in lexical_set
        ]

    @api.model
    def _hybrid_search_ids(
        self,
        value,
        *,
        mode="hybrid",
        scope=None,
        authorized_documents=None,
        local_documents=None,
        return_semantic_metadata=False,
    ):
        if mode not in ("hybrid", "exact", "semantic"):
            raise ValidationError(_("Unsupported archive search mode."))
        lexical_ids = []
        truncated = False
        warnings = []
        scope = (
            self._authorized_paperless_scope(authorized_documents)
            if scope is None
            else scope
        )
        if mode != "semantic":
            lexical_ids, truncated = self._lexical_all_text_search_ids(
                value,
                scope=scope,
                authorized_documents=authorized_documents,
                local_documents=local_documents,
            )
        semantic_ids = []
        semantic_scores = {}
        semantic_scores_loaded = False
        if mode != "exact":
            try:
                payload = self._paperless().semantic_search(
                    str(value),
                    document_ids=scope,
                    limit=200,
                )
                allowed = set(scope)
                semantic_scores_loaded = True
                for item in payload.get("results") or []:
                    paperless_id = int(item["id"])
                    if paperless_id not in allowed:
                        continue
                    semantic_ids.append(paperless_id)
                    try:
                        similarity = float(item.get("similarity"))
                    except (TypeError, ValueError):
                        continue
                    if math.isfinite(similarity):
                        semantic_scores[paperless_id] = min(
                            1.0,
                            max(0.0, similarity),
                        )
                warnings.extend(payload.get("warnings") or [])
            except PaperlessError:
                if mode == "semantic":
                    raise
                warnings.append(
                    {
                        "code": "semantic_unavailable",
                        "message": _(
                            "Meaning-based matching is temporarily unavailable; "
                            "exact archive search remains active.",
                        ),
                    },
                )
        if mode == "exact":
            result = (lexical_ids, truncated, warnings)
        elif mode == "semantic":
            result = (semantic_ids, False, warnings)
        else:
            result = (
                self._fuse_search_rankings(lexical_ids, semantic_ids),
                truncated,
                warnings,
            )
        if return_semantic_metadata:
            return (*result, semantic_scores, semantic_scores_loaded)
        return result

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
    def _search_semantic_text(self, operator, value):
        if operator not in ("=", "!=", "like", "not like", "ilike", "not ilike"):
            raise ValidationError(_("Unsupported semantic search operator."))
        if not value:
            return []
        ids, _truncated, _warnings = self._hybrid_search_ids(
            value,
            mode="semantic",
        )
        negative = operator in ("!=", "not like", "not ilike")
        return [("paperless_id", "not in" if negative else "in", ids)]

    @api.model
    def _custom_field_search_ids(self, value, *, document_ids=None):
        scope = (
            self._authorized_paperless_scope()
            if document_ids is None
            else document_ids
        )
        ids, _truncated = self._permission_scoped_paperless_search_ids(
            str(value),
            scope,
            fields="custom_fields",
        )
        return ids

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
    def _resolve_remote_search_domain(
        self,
        domain,
        resolved_ids=None,
        *,
        authorized_scope=None,
        authorized_documents=None,
        local_documents=None,
    ):
        """Resolve each Paperless text condition once before Odoo paginates.

        ``search_count`` and ``search`` both expand custom search fields.  If
        the raw domain reached both calls, one user search caused two archive
        requests and could even observe different results between count and
        page retrieval.
        """
        if authorized_scope is None:
            authorized_scope = self._authorized_paperless_scope(
                authorized_documents,
            )

        def resolve(condition):
            if condition.field_expr not in (
                "all_text",
                "semantic_text",
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
                ids, _truncated, _warnings = self._hybrid_search_ids(
                    str(condition.value),
                    scope=authorized_scope,
                    authorized_documents=authorized_documents,
                    local_documents=local_documents,
                )
            elif condition.field_expr == "semantic_text":
                ids, _truncated, _warnings = self._hybrid_search_ids(
                    str(condition.value),
                    mode="semantic",
                    scope=authorized_scope,
                    authorized_documents=authorized_documents,
                )
            elif condition.field_expr == "archive_text":
                ids, _truncated = self._permission_scoped_paperless_search_ids(
                    str(condition.value),
                    authorized_scope,
                    fields="content",
                )
            else:
                ids = self._custom_field_search_ids(
                    condition.value,
                    document_ids=authorized_scope,
                )
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
        accessible_documents = self.browse(accessible_document_ids)
        visible_by_document = (
            accessible_documents._accessible_active_links_by_document()
        )
        visible_linked_ids = {
            document_id
            for document_id, links in visible_by_document.items()
            if links
        }
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
                client.paperless_login_url(document.paperless_id)
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
        if (
            self.access_scope == "linked_record"
            and not self.env.user.has_group(
                "usl_documents.group_documents_manager",
            )
            and not self._accessible_active_links()
        ):
            raise AccessError(
                _("You no longer have access to this document's related record."),
            )
        if self.availability_state not in ("available", "permission_error"):
            return False
        if self.permission_sync_state == "pending":
            raise AccessError(
                _(
                    "Documents is still securing access to this file. Try again "
                    "in a moment.",
                ),
            )
        if self.permission_sync_state == "failed":
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
            "access_scope",
            "permitted_user_ids",
            "retention_hold",
            "deletion_reason",
            "intake_role",
        }
        cache_fields = {
            "name",
            "paperless_id",
            "paperless_created",
            "paperless_modified",
            "original_created_at",
            "original_modified_at",
            "document_date",
            "availability_state",
            "original_filename",
            "mime_type",
            "checksum",
            "metadata_hash",
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

    def _recompute_linked_record_access(self, *, sync_permissions=False):
        """Mirror native linked-record visibility into one searchable policy."""
        documents = self.sudo().filtered(
            lambda document: document.access_scope == "linked_record",
        )
        if not documents:
            return True
        groups = self.env.ref("usl_documents.group_documents_user")
        groups |= self.env.ref("usl_documents.group_documents_accountant")
        groups |= self.env.ref("usl_documents.group_documents_hr")
        candidates = self.env["res.users"].sudo().search(
            [
                ("active", "=", True),
                ("share", "=", False),
                ("group_ids", "in", groups.ids),
            ],
        )
        active_links = documents.mapped("link_ids").filtered("active")
        linked_ids_by_model = {}
        for link in active_links:
            if link.res_model in self.env:
                linked_ids_by_model.setdefault(link.res_model, set()).add(link.res_id)
        readable_ids = {}
        for user in candidates:
            for model_name, record_ids in linked_ids_by_model.items():
                try:
                    readable_ids[user.id, model_name] = set(
                        self.env[model_name]
                        .with_user(user)
                        .search([("id", "in", list(record_ids))])
                        .ids,
                    )
                except AccessError:
                    readable_ids[user.id, model_name] = set()
        changed_documents = self.browse()
        removed_by_document = {}
        for document in documents:
            before = set(document.permitted_user_ids.ids)
            permitted = self.env["res.users"].sudo().browse()
            document_links = document.link_ids.filtered("active")
            for user in candidates:
                if document.company_id and document.company_id not in user.company_ids:
                    continue
                for link in document_links:
                    if link.res_id not in readable_ids.get(
                        (user.id, link.res_model),
                        set(),
                    ):
                        continue
                    permitted |= user
                    break
            if not document_links and document.submitted_by_id in candidates:
                permitted |= document.submitted_by_id
            after = set(permitted.ids)
            if before != after:
                document.with_context(
                    usl_documents_policy_write=True,
                    skip_permission_invalidation=True,
                ).write({"permitted_user_ids": [Command.set(permitted.ids)]})
                changed_documents |= document
                removed_by_document[document.id] = before - after
        if sync_permissions:
            # A repeated policy application is a no-op. Only permissions whose
            # Odoo access changed, or whose last Paperless synchronization is
            # incomplete, need an external API call.
            pending = documents.filtered(
                lambda document: document.permission_sync_state != "synchronized",
            )
            live = (changed_documents | pending).filtered(
                lambda document: document.paperless_id
                and document.availability_state in ("available", "permission_error"),
            )
            has_verified_mapping = bool(
                self.env["usl.paperless.user.mapping"].sudo().search_count(
                    [("active", "=", True), ("sync_state", "=", "synchronized")],
                ),
            )
            if live and has_verified_mapping:
                live.with_user(self.env.ref("base.user_root")).action_sync_permissions()
                unsafe = live.filtered(
                    lambda document: removed_by_document.get(document.id)
                    and document.permission_sync_state == "failed",
                )
                if unsafe:
                    raise UserError(
                        _(
                            "Access was not changed because Paperless could not "
                            "safely revoke one or more document permissions.",
                        ),
                    )
        return True

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
        self,
        payload,
        *,
        source="paperless",
        metadata_catalog=None,
        metadata_records=None,
    ):
        metadata_catalog = metadata_catalog or {}
        metadata_records = metadata_records if metadata_records is not None else {}

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
            records_by_id = metadata_records.get(model_name)
            record = (
                records_by_id.get(remote_id, self.env[model_name])
                if records_by_id is not None
                else self.env[model_name].sudo().search(
                    [("paperless_id", "=", remote_id)], limit=1,
                )
            )
            if not record and isinstance(value, dict):
                record = (
                    self.env[model_name]
                    .sudo()
                    .with_context(usl_documents_cache_write=True)
                    .create(self.env[model_name]._cache_values(value))
                )
                if records_by_id is not None:
                    records_by_id[remote_id] = record
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
    def _paperless_metadata_records(self):
        """Prefetch synchronized catalogs once for a multi-document refresh."""
        return {
            model_name: {
                record.paperless_id: record
                for record in self.env[model_name].sudo().search([])
            }
            for model_name in (
                "usl.paperless.tag",
                "usl.paperless.correspondent",
                "usl.paperless.document.type",
            )
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
        self._configure_archive_automation(client)
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
    def _configure_archive_automation(self, client):
        """Enable Paperless learning when the catalog has usable examples.

        Explicit matching rules are user-owned and are never replaced.  Automatic
        learning is enabled only for shared, active metadata that is still
        unconfigured and already occurs on at least two documents.
        """
        configured = 0
        for model_name in (
            "usl.paperless.tag",
            "usl.paperless.correspondent",
            "usl.paperless.document.type",
        ):
            model = self.env[model_name].sudo()
            records = model.search(
                [
                    ("active", "=", True),
                    ("matching_algorithm", "=", "0"),
                    ("match", "in", (False, "")),
                    ("document_count", ">=", 2),
                ],
            )
            if model_name == "usl.paperless.tag":
                records = records.filtered(lambda record: not record.is_inbox_tag)
            for record in records:
                payload = client.update_metadata(
                    model._paperless_kind,
                    record.paperless_id,
                    {
                        "matching_algorithm": 6,
                        "match": "",
                        "is_insensitive": True,
                    },
                )
                record.with_context(usl_documents_cache_write=True).write(
                    model._cache_values(payload),
                )
                configured += 1
        return configured

    @api.model
    def reconcile_linked_classification(self, *, limit=1000):
        """Finish classification backed by authoritative Odoo context.

        A mandatory evidence relationship or a direct-record/final-output
        relationship already carries the owning workflow's reviewed business
        context.  Those documents do not need a duplicate Documents approval.
        Manual workspace links remain ready for an explicit human review.
        """
        candidates = self.sudo().search(
            [
                ("review_state", "in", ("needs_attention", "classified")),
                ("availability_state", "=", "available"),
                ("company_id", "!=", False),
                ("permission_sync_state", "=", "synchronized"),
                ("last_error", "=", False),
                ("link_ids.active", "=", True),
                "|",
                ("document_type_id", "!=", False),
                ("tag_ids", "!=", False),
            ],
            order="id",
            limit=max(0, int(limit or 0)) or None,
        )
        classified = self.browse()
        reviewed = self.browse()
        skipped = 0
        for document in candidates:
            links = document.link_ids.filtered("active")
            if not links or any(
                link.company_id != document.company_id
                or link.policy_reason in {
                    "legacy_relationship_backfill_pending",
                    "legacy_operation_backfill_pending",
                }
                or link.res_model not in self.env
                or not self.env[link.res_model].sudo().browse(link.res_id).exists()
                for link in links
            ):
                skipped += 1
                continue
            has_authoritative_context = any(
                (
                    link.archive_mode == "mandatory"
                    and link.policy_role == "evidence"
                )
                or link.attachment_origin in {"direct_record", "generated_final"}
                for link in links
            )
            if has_authoritative_context:
                reviewed |= document
            elif document.review_state == "needs_attention":
                classified |= document
        if classified:
            classified.with_context(usl_documents_cache_write=True).write(
                {"review_state": "classified"},
            )
        if reviewed:
            reviewed.with_context(usl_documents_cache_write=True).write(
                {"review_state": "reviewed"},
            )
        return {
            "considered": len(candidates),
            "classified": len(classified),
            "reviewed": len(reviewed),
            "skipped": skipped,
        }

    @api.model
    def cron_reconcile_linked_classification(self):
        return self.reconcile_linked_classification(limit=0)

    @api.model
    def sync_from_paperless(self, *, full=False, limit_pages=None, client=None):
        self._require_manager()
        client = client or self._paperless()
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
        metadata_records = None
        try:
            client.compatibility()
            self._sync_metadata_catalogs(client)
            metadata_records = self._paperless_metadata_records()
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
                documents_by_paperless_id = {
                    document.paperless_id: document
                    for document in self.sudo().search(
                        [
                            (
                                "paperless_id",
                                "in",
                                [int(item["id"]) for item in results],
                            ),
                        ],
                    )
                }
                for item in results:
                    paperless_id = int(item["id"])
                    seen.add(paperless_id)
                    document = documents_by_paperless_id.get(paperless_id)
                    values = self._paperless_values(
                        item,
                        metadata_catalog=metadata_catalog,
                        metadata_records=metadata_records,
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
                        document.with_context(
                            usl_documents_cache_write=True,
                            skip_permission_invalidation=True,
                        ).write(values)
                    else:
                        document = self.sudo().create(values)
                        documents_by_paperless_id[paperless_id] = document
                    if document.source == "paperless":
                        document._merge_original_timestamps(
                            document.paperless_created,
                            document.paperless_modified,
                        )
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
                trashed_items = list(client.list_trashed_documents())
                trashed_documents_by_paperless_id = {
                    document.paperless_id: document
                    for document in self.sudo().search(
                        [
                            (
                                "paperless_id",
                                "in",
                                [int(item["id"]) for item in trashed_items],
                            ),
                        ],
                    )
                }
                for item in trashed_items:
                    paperless_id = int(item["id"])
                    trashed_ids.add(paperless_id)
                    document = trashed_documents_by_paperless_id.get(paperless_id)
                    values = self._paperless_values(
                        item,
                        metadata_catalog=metadata_catalog,
                        metadata_records=metadata_records,
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
                        trashed_documents_by_paperless_id[paperless_id] = document
                        document.with_context(
                            usl_documents_cache_write=True,
                        ).write({"availability_state": "trashed"})
                    if document.source == "paperless":
                        document._merge_original_timestamps(
                            document.paperless_created,
                            document.paperless_modified,
                        )
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
                        metadata_records=metadata_records,
                    )
                    values.pop("source", None)
                    document.with_context(
                        usl_documents_cache_write=True,
                        skip_permission_invalidation=True,
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
        try:
            client = self._paperless()
            self.env[
                "usl.paperless.user.mapping"
            ]._reconcile_remote_identity_state(client=client)
            result = self.sync_from_paperless(limit_pages=20, client=client)
            if result.get("complete"):
                result["classification"] = self.reconcile_linked_classification(
                    limit=1000,
                )
            return result
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
    def _permission_scoped_paperless_search_ids(
        self,
        query,
        document_ids,
        filters=None,
        *,
        full_text=False,
        fields="all",
    ):
        """Search Paperless only inside the current Odoo-authorized roots."""
        scope = sorted({int(document_id) for document_id in document_ids})
        if not scope:
            return [], False
        maximum = self.env["ir.config_parameter"].sudo().get_int(
            "usl_documents.max_search_results", 10000,
        )
        scoped_filters = dict(filters or {})
        unsupported_filters = set(scoped_filters) - {"custom_field_query"}
        if full_text or unsupported_filters:
            raise ValidationError(_("Unsupported bounded archive search filter."))
        payload = self._paperless().scoped_search(
            str(query or ""),
            document_ids=scope,
            limit=maximum,
            fields=fields,
            custom_field_query=scoped_filters.get("custom_field_query"),
        )
        allowed = set(scope)
        ids = [
            int(item["id"])
            for item in payload.get("results") or []
            if int(item["id"]) in allowed
        ]
        return ids, bool(payload.get("truncated"))

    @api.model
    def _workspace_correspondent_values(self, correspondent):
        """Return archive metadata without exposing an inaccessible Contact."""
        return self._workspace_correspondent_values_by_id(correspondent).get(
            correspondent.id,
            {
                "id": False,
                "name": False,
                "archive_name": False,
                "partner_id": False,
            },
        )

    @api.model
    def _workspace_correspondent_values_by_id(self, correspondents):
        """Serialize correspondents with one ACL-aware Contact lookup."""
        correspondents = correspondents.exists()
        if not correspondents:
            return {}
        partner_id_by_correspondent = {
            correspondent.id: correspondent.partner_id.id
            for correspondent in correspondents.sudo()
        }
        partner_ids = {
            partner_id
            for partner_id in partner_id_by_correspondent.values()
            if partner_id
        }
        visible_partners = self.env["res.partner"].search(
            [("id", "in", list(partner_ids))],
        )
        visible_partner_by_id = {partner.id: partner for partner in visible_partners}
        values_by_id = {}
        for correspondent in correspondents:
            partner = visible_partner_by_id.get(
                partner_id_by_correspondent.get(correspondent.id),
            )
            values_by_id[correspondent.id] = {
                "id": correspondent.id,
                "name": partner.display_name if partner else correspondent.name,
                "archive_name": correspondent.name,
                "partner_id": partner.id if partner else False,
            }
        return values_by_id

    @api.model
    def _broad_search_terms(self, domain):
        """Return positive Search-everywhere terms from a serialized domain."""
        terms = []

        def visit(node):
            if not isinstance(node, (list, tuple)):
                return
            if (
                len(node) >= 3
                and node[0] in ("all_text", "semantic_text")
                and node[1] in ("=", "like", "ilike")
                and node[2]
            ):
                terms.append((node[0], str(node[2])))
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
    def _workspace_document_values(
        self,
        item,
        semantic_scores=None,
        *,
        active_links=None,
        correspondent=None,
        employee_by_id=None,
    ):
        semantic_similarity = (semantic_scores or {}).get(item.paperless_id)
        if active_links is None:
            active_links = item._accessible_active_links()
        employee_link = active_links.filtered(
            lambda link: link.res_model == "hr.employee",
        )[:1]
        if employee_by_id is None:
            employee = (
                self.env["hr.employee"].search(
                    [("id", "=", employee_link.res_id)],
                    limit=1,
                )
                if employee_link
                else self.env["hr.employee"]
            )
        else:
            employee = employee_by_id.get(employee_link.res_id)
        if correspondent is None:
            correspondent = self._workspace_correspondent_values(
                item.correspondent_id,
            )
        return {
            "id": item.id,
            "name": item.name,
            "paperless_id": item.paperless_id,
            "semantic_similarity": semantic_similarity,
            "semantic_match_percent": (
                round(semantic_similarity * 100)
                if semantic_similarity is not None
                else None
            ),
            "date": item.document_date,
            "ingested_at": item.archive_added_at,
            "company": item.company_id.display_name,
            "company_id": item.company_id.id,
            "confidentiality": item.confidentiality,
            "review_state": item.review_state,
            "availability_state": item.availability_state,
            "permission_sync_state": item.permission_sync_state,
            "access_pending": (
                _(
                    "Documents is securing access to this file. Preview and "
                    "download will become available automatically when the "
                    "check finishes.",
                )
                if (
                    item.availability_state in ("available", "permission_error")
                    and item.permission_sync_state == "pending"
                )
                else False
            ),
            "access_error": (
                _(
                    "Archive access could not be verified. A Documents "
                    "administrator can retry synchronization.",
                )
                if (
                    item.availability_state in ("available", "permission_error")
                    and item.permission_sync_state == "failed"
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
            "intake_role": item.intake_role,
            "is_prominent": item.is_prominent,
            "is_starred": item.is_starred,
            "is_in_my_library": item.is_in_my_library,
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
    def _workspace_documents_values(
        self,
        documents,
        semantic_scores=None,
        *,
        visible_links_by_document=None,
    ):
        """Serialize a result window without per-document security queries."""
        if not documents:
            return {}
        documents = documents.exists()
        if visible_links_by_document is None:
            visible_links_by_document = (
                documents._accessible_active_links_by_document()
            )
        correspondent_by_id = self._workspace_correspondent_values_by_id(
            documents.mapped("correspondent_id"),
        )
        employee_ids = {
            employee_link.res_id
            for document in documents
            if (
                employee_link := visible_links_by_document[document.id].filtered(
                    lambda link: link.res_model == "hr.employee",
                )[:1]
            )
        }
        employee_by_id = {
            employee.id: employee
            for employee in self.env["hr.employee"].search(
                [("id", "in", list(employee_ids))],
            )
        }
        return {
            document.id: self._workspace_document_values(
                document,
                semantic_scores,
                active_links=visible_links_by_document[document.id],
                correspondent=correspondent_by_id.get(document.correspondent_id.id),
                employee_by_id=employee_by_id,
            )
            for document in documents
        }

    @api.model
    def workspace_data(
        self,
        *,
        query="",
        workspace="home",
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
        search_mode="hybrid",
        background_mode="include",
        include_workspace_metadata=True,
    ):
        page = max(1, int(page))
        page_size = min(500, max(1, int(page_size)))
        if search_mode not in ("hybrid", "exact", "semantic"):
            raise ValidationError(_("Unsupported archive search mode."))
        if background_mode not in ("include", "exclude", "only"):
            raise ValidationError(_("Unsupported archive visibility filter."))
        if not isinstance(include_workspace_metadata, bool):
            raise ValidationError(_("Invalid workspace metadata option."))
        smart_views = self.env["usl.document.smart.view"].accessible_views()
        selected_view = smart_views.filtered(
            lambda item: (item.key or f"view:{item.id}") == workspace,
        )[:1]
        if not selected_view and workspace in {"all", "attention", "recent"}:
            # Preserve stable API and saved-session keys that predate the
            # reduced primary navigation. Record rules still scope every
            # result; these diagnostic/legacy views are simply not advertised.
            selected_view = self.env["usl.document.smart.view"].sudo().with_context(
                active_test=False,
            ).search(
                [("key", "=", workspace)],
                limit=1,
            )
        if not selected_view:
            selected_view = smart_views.filtered(lambda item: item.key == "home")[:1]
        domain = list(selected_view.document_domain()) if selected_view else []
        if search_domain:
            if not isinstance(search_domain, list):
                raise ValidationError(_("Invalid search filters."))
        accessible_documents = self.search([])
        authorized_documents = accessible_documents.filtered(
            lambda document: document.availability_state
            not in ("trashed", "permanently_deleted"),
        )
        authorized_scope = self._authorized_paperless_scope(authorized_documents)
        broad_terms = self._broad_search_terms(search_domain)
        resolved_ids = {}
        relevance_paperless_ids = []
        semantic_scores = {}
        semantic_scores_loaded = False
        search_warnings = []
        truncated = False
        for field_name, term in broad_terms:
            (
                ids,
                term_truncated,
                term_warnings,
                term_semantic_scores,
                term_semantic_scores_loaded,
            ) = self._hybrid_search_ids(
                term,
                mode="semantic" if field_name == "semantic_text" else search_mode,
                scope=authorized_scope,
                authorized_documents=authorized_documents,
                local_documents=accessible_documents,
                return_semantic_metadata=True,
            )
            semantic_scores_loaded = (
                semantic_scores_loaded or term_semantic_scores_loaded
            )
            for paperless_document_id, score in term_semantic_scores.items():
                semantic_scores[paperless_document_id] = max(
                    score,
                    semantic_scores.get(paperless_document_id, 0.0),
                )
            resolved_ids[field_name, term] = ids
            truncated = truncated or term_truncated
            search_warnings.extend(term_warnings)
            for paperless_document_id in ids:
                if paperless_document_id not in relevance_paperless_ids:
                    relevance_paperless_ids.append(paperless_document_id)
        try:
            native_domain = self._resolve_remote_search_domain(
                Domain(search_domain or []),
                resolved_ids=resolved_ids,
                authorized_scope=authorized_scope,
                authorized_documents=authorized_documents,
                local_documents=accessible_documents,
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
        archive_search_requested = any(
            (
                query,
                search_domain,
                company_id,
                tag_ids,
                correspondent_id,
                document_type_id,
                date_from,
                date_to,
                added_from,
                added_to,
                source,
                confidentiality,
                review_state,
                linked_state,
                linked_model,
                linked_id,
                mapped_partner_id,
                paperless_id,
                custom_field_id,
                custom_field_value,
            ),
        )
        if (
            selected_view
            and selected_view.system_rule == "archive_search"
            and not archive_search_requested
        ):
            domain.append(("id", "=", 0))
        if background_mode == "exclude":
            domain.append(("is_prominent", "=", True))
        elif background_mode == "only":
            domain.append(("is_prominent", "=", False))
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
                ids, query_truncated = self._permission_scoped_paperless_search_ids(
                    query,
                    authorized_scope,
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
        ordered_ids = None
        try:
            if sort == "semantic" and semantic_scores_loaded and not order_by:
                matching = self.search(domain)
                relevance_position = {
                    paperless_id: position
                    for position, paperless_id in enumerate(relevance_paperless_ids)
                }
                ordered_ids = [
                    document.id
                    for document in sorted(
                        matching,
                        key=lambda document: (
                            -semantic_scores.get(document.paperless_id, -1.0),
                            relevance_position.get(
                                document.paperless_id,
                                len(relevance_position),
                            ),
                            -document.id,
                        ),
                    )
                ]
                count = len(ordered_ids)
                page_ids = ordered_ids[
                    (page - 1) * page_size : page * page_size
                ]
                documents = self.browse(page_ids)
            elif relevance_paperless_ids and not order_by and not query:
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
        link_facets = []
        visible_links_by_document = None
        if include_workspace_metadata:
            visible_links_by_document = (
                accessible_documents._accessible_active_links_by_document()
            )
            seen_links = set()
            for document in accessible_documents:
                for link in visible_links_by_document[document.id]:
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
        result_window = self.browse()
        if archive_search_requested:
            result_window = (
                self.browse(ordered_ids[:500])
                if ordered_ids is not None
                else self.search(domain, order=order, limit=500)
            )
        serialized_documents = documents | result_window
        if visible_links_by_document is None:
            visible_links_by_document = (
                serialized_documents._accessible_active_links_by_document()
            )
        serialized_by_id = self._workspace_documents_values(
            serialized_documents,
            semantic_scores,
            visible_links_by_document=visible_links_by_document,
        )
        result = {
            "documents": [serialized_by_id[item.id] for item in documents],
            "result_window": [serialized_by_id[item.id] for item in result_window],
            "result_window_offset": 0,
            "result_window_complete": bool(
                archive_search_requested and count <= len(result_window),
            ),
            "count": count,
            "page": page,
            "page_size": page_size,
            "selected_workspace": (
                selected_view.key or f"view:{selected_view.id}"
                if selected_view
                else workspace
            ),
            "degraded": False,
            "warnings": search_warnings,
            "search_mode": search_mode,
            "semantic_scores_loaded": semantic_scores_loaded,
            "background_mode": background_mode,
            "truncated": truncated,
            "metadata_included": include_workspace_metadata,
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
        if not include_workspace_metadata:
            return result
        result.update({
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
        })
        return result

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
                "original_created_at": document.original_created_at,
                "original_modified_at": document.original_modified_at,
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
                        "document_role": link.document_role,
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

    def action_set_starred(self, starred):
        self.ensure_one()
        self.check_access("read")
        starred = bool(starred)
        states = self.env["usl.document.user.state"].sudo()
        state = states.search(
            [
                ("document_id", "=", self.id),
                ("user_id", "=", self.env.user.id),
            ],
            limit=1,
        )
        if state:
            state.write({"starred": starred})
        elif starred:
            states.create(
                {
                    "document_id": self.id,
                    "user_id": self.env.user.id,
                    "starred": True,
                },
            )
        return {"document_id": self.id, "is_starred": starred}

    def action_mark_opened(self):
        self.ensure_one()
        self.check_access("read")
        states = self.env["usl.document.user.state"].sudo()
        state = states.search(
            [
                ("document_id", "=", self.id),
                ("user_id", "=", self.env.user.id),
            ],
            limit=1,
        )
        values = {"last_opened_at": fields.Datetime.now()}
        if state:
            state.write(values)
        else:
            states.create(
                {
                    "document_id": self.id,
                    "user_id": self.env.user.id,
                    **values,
                },
            )
        return True

    def _presentation_role_target(self, *, promote, res_model=None, res_id=None):
        self.ensure_one()
        links = self._accessible_active_links()
        if res_model or res_id:
            if not res_model or not res_id:
                raise ValidationError(_("Choose one complete Odoo relationship."))
            links = links.filtered(
                lambda link: (
                    link.res_model == res_model and link.res_id == int(res_id)
                ),
            )
            if not links:
                raise AccessError(_("That Odoo relationship is not accessible."))
            return "link", links[:1]
        desired_role = "background" if promote else "library"
        candidates = links.filtered(lambda link: link.document_role == desired_role)
        if len(candidates) == 1:
            return "link", candidates
        if self.intake_role in {"background", "library"}:
            return "intake", self
        if candidates:
            raise UserError(
                _("Open the linked Odoo record to choose which relationship to change."),
            )
        return "intake", self

    def action_set_library_visibility(
        self,
        promote,
        res_model=None,
        res_id=None,
    ):
        """Change Odoo presentation only; never touch the archived binary."""
        self.ensure_one()
        self.check_access("write")
        if self.availability_state != "available":
            raise UserError(_("Only an available document can change library visibility."))
        promote = bool(promote)
        target_kind, target = self._presentation_role_target(
            promote=promote,
            res_model=res_model,
            res_id=res_id,
        )
        role = "library" if promote else "background"
        current_role = target.document_role if target_kind == "link" else self.intake_role
        if current_role == "evidence":
            raise UserError(_("Required evidence cannot be removed from Documents Home."))
        if current_role != role:
            if target_kind == "link":
                target.sudo().with_context(
                    usl_documents_link_policy_write=True,
                ).write({"document_role": role})
                relationship = _("the link to %(record)s", record=target.record_name)
            else:
                self.sudo().with_context(
                    usl_documents_policy_write=True,
                    skip_permission_invalidation=True,
                ).write({"intake_role": role})
                relationship = _("the archive intake relationship")
            self.message_post(
                body=(
                    _(
                        "%(user)s added %(relationship)s to My library. "
                        "The archived file and its versions were unchanged.",
                        user=self.env.user.display_name,
                        relationship=relationship,
                    )
                    if promote
                    else _(
                        "%(user)s removed %(relationship)s from My library. "
                        "The business link, archived file, and versions were kept.",
                        user=self.env.user.display_name,
                        relationship=relationship,
                    )
                ),
            )
        return self.document_detail(self.id)

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
        operation = self.env["usl.document.operation"]
        operation_id = self.env.context.get("usl_documents_operation_id")
        if operation_id and self.env.su:
            operation = operation.sudo().browse(int(operation_id)).exists()
            if not operation or operation.state != "pending":
                raise ValidationError(_("The attachment archive request is no longer pending."))
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
        confidentiality = archive_context.get("confidentiality") or confidentiality
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
        checksum = hashlib.sha256(content).hexdigest()
        metadata_hash = (
            operation.metadata_hash
            if operation and operation.metadata_hash
            else self._archive_metadata_hash(archive_context)
        )
        retry_operation = self.env["usl.document.operation"].search(
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
            if matching_version:
                archive_context = {
                    **archive_context,
                    "related_records": [
                        {
                            **target,
                            "version_id": matching_version.paperless_version_id,
                        }
                        for target in archive_context.get("related_records") or []
                    ],
                }
            existing._apply_archive_context(
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
                        "document_id": existing.id,
                        "context_json": archive_context,
                        "error_message": False,
                        "next_attempt_at": False,
                    },
                )
            return {
                "state": "duplicate",
                "document_id": existing.id,
                "message": _(
                    "“%(document)s” already contains this exact file and "
                    "classification; the existing archive document was reused.",
                )
                % {"document": existing.name},
            }
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
        if mirrored_match:
            mirrored = mirrored_match
            if mirrored_version:
                archive_context = {
                    **archive_context,
                    "related_records": [
                        {
                            **target,
                            "version_id": mirrored_version.paperless_version_id,
                        }
                        for target in archive_context.get("related_records") or []
                    ],
                }
            retry_operation.acknowledge()
            mirrored._apply_archive_context(
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
                        "document_id": mirrored.id,
                        "context_json": archive_context,
                        "error_message": False,
                        "next_attempt_at": False,
                    },
                )
            return {
                "state": "duplicate",
                "document_id": mirrored.id,
                "message": _(
                    "“%(document)s” already contains this exact file and "
                    "classification; the existing archive document was reused.",
                )
                % {"document": mirrored.name},
            }
        if unknown_remote_matches:
            operation_values = {
                "name": filename,
                "state": "duplicate",
                "checksum": checksum,
                "metadata_hash": metadata_hash,
                "mime_type": content_type,
                "company_id": company.id,
                "confidentiality": confidentiality,
                "res_model": res_model,
                "res_id": int(res_id) if res_id else 0,
                "source": source,
                "accounting_evidence": bool(
                    archive_context.get("accounting_evidence"),
                ),
                "access_scope": archive_context.get("access_scope") or "linked_record",
                "archive_mode": archive_context.get("archive_mode") or "automatic",
                "document_role": archive_context.get("document_role") or "library",
                "attachment_origin": (
                    archive_context.get("attachment_origin")
                    or "documents_workspace"
                ),
                "policy_reason": (
                    archive_context.get("policy_reason")
                    or "generic_documents_upload"
                ),
                "context_json": archive_context,
                "original_created_at": original_created_at,
                "original_modified_at": original_modified_at,
                "error_message": _(
                    "Identical content exists outside your authorized Odoo archive "
                    "view, but its classification fingerprint cannot be verified. "
                    "A Documents administrator must classify it before reuse.",
                ),
                "retry_of_id": retry_operation.id,
                "retry_count": (retry_operation.retry_count + 1)
                if retry_operation
                else 0,
            }
            if operation:
                operation.sudo().write(operation_values)
            else:
                operation = self.env["usl.document.operation"].sudo().create(
                    operation_values,
                )
            return {
                "state": "duplicate",
                "operation_id": operation.id,
                "message": operation.error_message,
            }
        operation_values = {
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
        if operation:
            operation.sudo().write(operation_values)
        else:
            operation = self.env["usl.document.operation"].sudo().create(
                operation_values,
            )
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
            ],
            limit=1,
        )
        if not mapping:
            raise UserError(
                _(
                    "Paperless access is not set up for your account. You can "
                    "still preview and download this document in Odoo. Ask a "
                    "Documents administrator if you need Paperless access.",
                ),
            )
        if (
            mapping.sync_state != "synchronized"
            or not mapping._identity_is_safe()
        ):
            raise UserError(
                _(
                    "Your Paperless access needs attention. You can still preview "
                    "and download this document in Odoo. Ask a Documents "
                    "administrator to review your access.",
                ),
            )
        if self.permission_sync_state != "synchronized":
            raise UserError(
                _(
                    "Paperless access for this document needs attention. Use the "
                    "Odoo preview for now, or ask a Documents administrator to "
                    "retry access synchronization.",
                ),
            )
        return {
            "type": "ir.actions.act_url",
            "url": self._paperless().paperless_login_url(self.paperless_id),
            "target": "new",
        }

    def action_sync_permissions(self):
        self._require_manager()
        mappings = self.env["usl.paperless.user.mapping"].search([
            ("active", "=", True),
            ("sync_state", "=", "synchronized"),
        ]).filtered(lambda mapping: mapping._identity_is_safe())
        visible_by_user = mappings.mapped(
            "user_id",
        )._documents_visible_for_permission_sync()
        mapping_permissions = [
            (
                mapping.paperless_user_id,
                visible_by_user.get(mapping.user_id.id, set()),
                mapping.user_id.has_group(
                    "usl_documents.group_documents_manager",
                ),
            )
            for mapping in mappings
        ]
        permission_batches = {}
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
            for paperless_user_id, visible_ids, may_change in mapping_permissions:
                if document.id not in visible_ids:
                    continue
                view_users.append(paperless_user_id)
                if may_change:
                    change_users.append(paperless_user_id)
            permission_key = (
                tuple(sorted(view_users)),
                tuple(sorted(change_users)),
            )
            permission_batches.setdefault(permission_key, self.browse())
            permission_batches[permission_key] |= document

        for (view_users, change_users), documents in permission_batches.items():
            for offset in range(0, len(documents), PERMISSION_SYNC_BATCH_SIZE):
                batch = documents[offset : offset + PERMISSION_SYNC_BATCH_SIZE]
                client = batch[0]._paperless()
                try:
                    if len(batch) == 1:
                        client.set_document_permissions(
                            batch.paperless_id,
                            view_users=list(view_users),
                            change_users=list(change_users),
                        )
                    else:
                        client.set_documents_permissions(
                            batch.mapped("paperless_id"),
                            view_users=list(view_users),
                            change_users=list(change_users),
                        )
                except PaperlessError as error:
                    batch.sudo().with_context(
                        skip_permission_invalidation=True,
                        usl_documents_cache_write=True,
                    ).write({
                        "permission_sync_state": "failed",
                        "permission_sync_error": str(error),
                        "permission_checked_at": fields.Datetime.now(),
                        "availability_state": "permission_error",
                    })
                else:
                    batch.sudo().with_context(
                        skip_permission_invalidation=True,
                        usl_documents_cache_write=True,
                    ).write({
                        "permission_sync_state": "synchronized",
                        "permission_sync_error": False,
                        "permission_checked_at": fields.Datetime.now(),
                        "availability_state": "available",
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
    metadata_hash = fields.Char(index=True, readonly=True, copy=False)
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
    original_created_at = fields.Datetime(readonly=True)
    original_modified_at = fields.Datetime(readonly=True)
    has_distinct_archive_file = fields.Boolean(
        compute="_compute_has_distinct_archive_file",
    )

    @api.depends("checksum", "archive_checksum")
    def _compute_has_distinct_archive_file(self):
        for version in self:
            version.has_distinct_archive_file = bool(
                version.archive_checksum
                and version.checksum
                and version.archive_checksum != version.checksum,
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


class UslDocumentUserState(models.Model):
    _name = "usl.document.user.state"
    _description = "Private Documents Workspace State"
    _order = "last_opened_at desc, id desc"

    document_id = fields.Many2one(
        "usl.document",
        required=True,
        index=True,
        ondelete="cascade",
    )
    user_id = fields.Many2one(
        "res.users",
        required=True,
        index=True,
        ondelete="cascade",
    )
    starred = fields.Boolean(index=True)
    last_opened_at = fields.Datetime(index=True)

    _document_user_unique = models.Constraint(
        "UNIQUE(document_id, user_id)",
        "A user may have only one private state per document.",
    )


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
    archive_mode = fields.Selection(
        ARCHIVE_MODES,
        required=True,
        default="automatic",
        readonly=True,
        index=True,
    )
    policy_role = fields.Selection(
        DOCUMENT_ROLES,
        required=True,
        default="library",
        readonly=True,
        index=True,
        help="Document role resolved by the business archive policy.",
    )
    document_role = fields.Selection(
        DOCUMENT_ROLES,
        required=True,
        default="library",
        tracking=True,
        index=True,
        help=(
            "Current Odoo presentation role. Promotion or demotion changes only "
            "this relationship and never uploads another Paperless file."
        ),
    )
    attachment_origin = fields.Selection(
        ATTACHMENT_ORIGINS,
        required=True,
        default="migration",
        readonly=True,
        index=True,
    )
    policy_reason = fields.Char(
        required=True,
        default="legacy_relationship_backfill_pending",
        readonly=True,
        index=True,
    )
    active = fields.Boolean(default=True, tracking=True)

    _record_link_unique = models.Constraint(
        "UNIQUE(document_id, res_model, res_id)",
        "This archived document is already linked to that Odoo record.",
    )
    _active_record_lookup_idx = models.Index(
        "(res_model, res_id, document_id) WHERE active IS TRUE",
    )

    @api.model_create_multi
    def create(self, values_list):
        protected = {
            "archive_mode",
            "policy_role",
            "document_role",
            "attachment_origin",
            "policy_reason",
        }
        if any(protected.intersection(values) for values in values_list) and not (
            self.env.su
            and self.env.context.get("usl_documents_link_policy_write")
        ):
            raise AccessError(
                _("Document relationship policy can only change through Documents."),
            )
        return super().create(values_list)

    @api.model
    def _allowed_models(self):
        return {
            "account.move",
            "account.payment",
            "hr.expense",
            "res.partner",
            "res.company",
            "project.project",
            "project.task",
            "hr.employee",
        }

    @api.model
    def create_for_record(
        self,
        document,
        res_model,
        res_id,
        version_id=None,
        *,
        archive_mode="automatic",
        policy_role="library",
        attachment_origin="documents_workspace",
        policy_reason="manual_documents_link",
    ):
        document.ensure_one()
        document.check_access("write")
        allow_trashed_link = (
            self.env.su
            and self.env.context.get("usl_documents_allow_trashed_link")
            and document.availability_state == "trashed"
        )
        if document.availability_state != "available" and not allow_trashed_link:
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
        company = (
            getattr(record, "company_id", False)
            or document.company_id
            or self.env.company
        )
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
            diagnostics = {}
            if not existing.active:
                diagnostics["active"] = True
            if version_id and not existing.version_id:
                diagnostics["version_id"] = str(version_id)
            if existing.policy_reason == "legacy_relationship_backfill_pending":
                diagnostics.update(
                    {
                        "archive_mode": archive_mode,
                        "policy_role": policy_role,
                        "document_role": policy_role,
                        "attachment_origin": attachment_origin,
                        "policy_reason": policy_reason,
                    },
                )
            if diagnostics:
                existing.sudo().with_context(
                    usl_documents_link_policy_write=True,
                ).write(diagnostics)
            if not self.env.context.get("usl_documents_defer_access_sync"):
                document._recompute_linked_record_access(sync_permissions=True)
                document.reconcile_linked_classification(limit=1000)
            return existing
        if not document.company_id:
            document.sudo().with_context(usl_documents_policy_write=True).write(
                {
                    "company_id": company.id,
                    "review_state": "classified",
                },
            )
        record_name = (record.display_name or "").strip()
        if not record_name:
            record_name = (
                document.original_filename or document.name or ""
            ).strip()
        if not record_name:
            model = self.env["ir.model"]._get(res_model)
            record_name = _(
                "%(model)s #%(record_id)s",
                model=model.name or record._description,
                record_id=record.id,
            )
        link = self.sudo().with_context(
            usl_documents_link_policy_write=True,
        ).create(
            {
                "document_id": document.id,
                "res_model": res_model,
                "res_id": res_id,
                "record_name": record_name,
                "company_id": company.id,
                "linked_by_id": (
                    int(self.env.context.get("usl_documents_linked_by_id"))
                    if self.env.su
                    and self.env.context.get("usl_documents_linked_by_id")
                    else self.env.user.id
                ),
                "version_id": (
                    str(version_id)
                    if version_id
                    else document.version_ids.filtered("is_current")[
                        :1
                    ].paperless_version_id
                    or False
                ),
                "archive_mode": archive_mode,
                "policy_role": policy_role,
                "document_role": policy_role,
                "attachment_origin": attachment_origin,
                "policy_reason": policy_reason,
            },
        )
        if not self.env.context.get("usl_documents_defer_access_sync"):
            document._recompute_linked_record_access(sync_permissions=True)
            document.reconcile_linked_classification(limit=1000)
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
        documents = self.mapped("document_id")
        result = super().unlink()
        documents._recompute_linked_record_access(sync_permissions=True)
        return result

    def write(self, values):
        protected = {
            "archive_mode",
            "policy_role",
            "document_role",
            "attachment_origin",
            "policy_reason",
        }
        if protected.intersection(values) and not (
            self.env.su
            and self.env.context.get("usl_documents_link_policy_write")
        ):
            raise AccessError(
                _("Document relationship policy can only change through Documents."),
            )
        documents = self.mapped("document_id") if "active" in values else self.env[
            "usl.document"
        ]
        result = super().write(values)
        if documents:
            documents._recompute_linked_record_access(sync_permissions=True)
        return result


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
            <= (now or fields.Datetime.now()) - timedelta(minutes=timeout_minutes)
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

    def _remote_identity_error(self, payload):
        self.ensure_one()
        if not payload:
            return _(
                "Paperless user %(id)s no longer exists or is not visible.",
                id=self.paperless_user_id,
            )
        remote_username = payload.get("username")
        if remote_username != self.paperless_username:
            return _(
                "Paperless user %(id)s is %(actual)s, not %(expected)s.",
                id=self.paperless_user_id,
                actual=remote_username or _("unnamed"),
                expected=self.paperless_username,
            )
        if payload.get("is_active") is not True:
            return _(
                "Paperless user %(id)s (%(username)s) is inactive. Run the "
                "governed Paperless user reconciliation before verifying it again.",
                id=self.paperless_user_id,
                username=self.paperless_username,
            )
        return False

    @api.model
    def _reconcile_remote_identity_state(self, *, client=None):
        """Fail closed when a previously verified Paperless user drifts."""
        mappings = self.sudo().search(
            [("active", "=", True), ("sync_state", "=", "synchronized")],
        )
        if not mappings:
            return 0
        client = client or self.env["usl.document"]._paperless()
        remote_users = {
            int(payload["id"]): payload
            for payload in client.list_users()
            if isinstance(payload, dict) and payload.get("id") is not None
        }
        failures = 0
        for mapping in mappings:
            message = mapping._remote_identity_error(
                remote_users.get(mapping.paperless_user_id),
            )
            if not message:
                continue
            mapping.with_context(
                usl_documents_mapping_verification=True,
            ).write(
                {
                    "sync_state": "failed",
                    "last_error": message,
                },
            )
            failures += 1
        return failures

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
            message = mapping._remote_identity_error(payload)
            if message:
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
