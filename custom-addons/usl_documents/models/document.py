import logging
from datetime import timedelta

from odoo import Command, _, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.fields import Domain

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
    def create(self, vals_list):
        if not self.env.su:
            raise AccessError(
                _("Archived document cache records can only be created by synchronization."),
            )
        return super().create(vals_list)

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
    def _search_custom_field_text(self, operator, value):
        if operator not in ("=", "!=", "like", "not like", "ilike", "not ilike"):
            raise ValidationError(_("Unsupported additional-detail search operator."))
        if not value:
            return []
        ids = self._custom_field_search_ids(value)
        negative = operator in ("!=", "not like", "not ilike")
        return [("paperless_id", "not in" if negative else "in", ids)]

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

    def write(self, vals):
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
        if cache_fields.intersection(vals) and not cache_write:
            raise AccessError(
                _("Paperless cache and diagnostic fields cannot be edited manually."),
            )
        if (
            policy_fields.intersection(vals)
            and not policy_write
            and not self.env.user.has_group("usl_documents.group_documents_manager")
        ):
            raise AccessError(
                _("Only Documents administrators may change archive access policy."),
            )
        if (
            policy_fields.intersection(vals)
            and not skip_permission_invalidation
        ):
            vals = {
                **vals,
                "permission_sync_state": "pending",
                "permission_sync_error": False,
            }
        return super().write(vals)

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
