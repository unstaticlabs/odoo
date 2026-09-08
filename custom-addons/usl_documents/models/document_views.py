"""Saved quick filters and smart views of the Documents workspace."""

import ast
import json
import uuid
from datetime import timedelta

from dateutil.relativedelta import relativedelta

from odoo import (
    Command,
    _,
    api,
    fields,
    models,
)
from odoo.exceptions import AccessError, ValidationError
from odoo.fields import Domain
from odoo.tools.safe_eval import safe_eval

from .paperless_client import PaperlessClient


class UslDocumentQuickFilter(models.Model):
    _name = "usl.document.quick.filter"
    _description = "Documents One-click Shortcut"
    _order = "sequence, name, id"

    _GROUP_BY_SELECTION = [
        ("company_id", "Company"),
        ("correspondent_id", "Correspondent"),
        ("document_type_id", "Document type"),
        ("linked_employee_id", "Employee"),
        ("confidentiality", "Privacy"),
        ("review_state", "Review status"),
        ("document_date:month", "Document month"),
        ("archive_added_at:month", "Added month"),
    ]
    _SORT_BY_SELECTION = [
        ("name", "Document"),
        ("document_date", "Document date"),
        ("archive_added_at", "Added date"),
        ("correspondent_id", "Correspondent"),
        ("document_type_id", "Document type"),
        ("company_id", "Company"),
        ("tag_sort_key", "Tags"),
        ("status_sort_key", "Status"),
    ]
    _SORT_DIRECTION_SELECTION = [
        ("asc", "Ascending"),
        ("desc", "Descending"),
    ]
    _EDITOR_FIELDS = {
        "filter_domain",
        "group_by_1",
        "group_by_2",
        "group_by_3",
        "sort_by_1",
        "sort_direction_1",
        "sort_by_2",
        "sort_direction_2",
        "sort_by_3",
        "sort_direction_3",
    }

    name = fields.Char(required=True, translate=True)
    key = fields.Char(
        required=True,
        index=True,
        readonly=True,
        copy=False,
        default=lambda self: f"shortcut_{uuid.uuid4().hex}",
    )
    icon = fields.Char(default="fa-filter")
    sequence = fields.Integer(default=10)
    ir_filter_id = fields.Many2one(
        "ir.filters",
        string="Saved search",
        ondelete="cascade",
        copy=False,
        help=(
            "Native Odoo search definition containing the shortcut domain, "
            "grouping, and ordering."
        ),
    )
    smart_view_ids = fields.Many2many(
        "usl.document.smart.view",
        "usl_document_smart_view_quick_filter_rel",
        "filter_id",
        "view_id",
        string="Available in Smart Views",
        help=(
            "Smart Views that show this optional shortcut below the native "
            "Odoo search bar."
        ),
    )
    active = fields.Boolean(default=True)
    filter_domain = fields.Text(
        string="Filters",
        compute="_compute_filter_editor",
        inverse="_inverse_filter_editor",
        help="The native Odoo filter conditions applied by this shortcut.",
    )
    group_by_1 = fields.Selection(
        _GROUP_BY_SELECTION,
        string="First grouping",
        compute="_compute_filter_editor",
        inverse="_inverse_filter_editor",
    )
    group_by_2 = fields.Selection(
        _GROUP_BY_SELECTION,
        string="Second grouping",
        compute="_compute_filter_editor",
        inverse="_inverse_filter_editor",
    )
    group_by_3 = fields.Selection(
        _GROUP_BY_SELECTION,
        string="Third grouping",
        compute="_compute_filter_editor",
        inverse="_inverse_filter_editor",
    )
    sort_by_1 = fields.Selection(
        _SORT_BY_SELECTION,
        string="Sort first by",
        compute="_compute_filter_editor",
        inverse="_inverse_filter_editor",
    )
    sort_direction_1 = fields.Selection(
        _SORT_DIRECTION_SELECTION,
        string="First direction",
        compute="_compute_filter_editor",
        inverse="_inverse_filter_editor",
    )
    sort_by_2 = fields.Selection(
        _SORT_BY_SELECTION,
        string="Sort second by",
        compute="_compute_filter_editor",
        inverse="_inverse_filter_editor",
    )
    sort_direction_2 = fields.Selection(
        _SORT_DIRECTION_SELECTION,
        string="Second direction",
        compute="_compute_filter_editor",
        inverse="_inverse_filter_editor",
    )
    sort_by_3 = fields.Selection(
        _SORT_BY_SELECTION,
        string="Sort third by",
        compute="_compute_filter_editor",
        inverse="_inverse_filter_editor",
    )
    sort_direction_3 = fields.Selection(
        _SORT_DIRECTION_SELECTION,
        string="Third direction",
        compute="_compute_filter_editor",
        inverse="_inverse_filter_editor",
    )

    _quick_filter_key_unique = models.Constraint(
        "UNIQUE(key)", "A Documents shortcut key must be unique.",
    )

    def _require_manager(self):
        if not self.env.user.has_group("usl_documents.group_documents_manager"):
            raise AccessError(
                _("Only Documents administrators may configure shared shortcuts."),
            )

    @api.depends(
        "ir_filter_id.domain",
        "ir_filter_id.context",
        "ir_filter_id.sort",
    )
    def _compute_filter_editor(self):
        for shortcut in self:
            groups = shortcut._filter_group_by()
            ordering = shortcut._filter_order_by()
            shortcut.filter_domain = (
                shortcut.ir_filter_id.domain if shortcut.ir_filter_id else "[]"
            )
            for position in range(3):
                group = groups[position] if position < len(groups) else False
                order = ordering[position] if position < len(ordering) else {}
                shortcut[f"group_by_{position + 1}"] = group
                shortcut[f"sort_by_{position + 1}"] = order.get("name", False)
                shortcut[f"sort_direction_{position + 1}"] = (
                    "asc" if order.get("asc", True) else "desc"
                ) if order else "asc"

    def _inverse_filter_editor(self):
        """Editor values are persisted atomically by create/write overrides."""

    @api.model
    def _default_ir_filter_values(self, name):
        return {
            "name": name,
            "model_id": "usl.document",
            "action_id": self.env.ref(
                "usl_documents.action_documents_workspace",
            ).id,
            "domain": "[]",
            "context": "{}",
            "sort": "[]",
            "user_ids": [Command.clear()],
            "is_default": False,
        }

    def _editor_ir_filter_values(self, editor_values):
        self.ensure_one()
        current_context = self._filter_context()
        current_groups = self._filter_group_by()
        current_ordering = self._filter_order_by()

        domain = editor_values.get(
            "filter_domain",
            self.ir_filter_id.domain if self.ir_filter_id else "[]",
        )
        groups = []
        ordering = []
        allowed_groups = dict(self._GROUP_BY_SELECTION)
        allowed_ordering = dict(self._SORT_BY_SELECTION)
        for position in range(3):
            group_key = f"group_by_{position + 1}"
            group = editor_values.get(
                group_key,
                current_groups[position] if position < len(current_groups) else False,
            )
            if group:
                if group not in allowed_groups:
                    raise ValidationError(_("Unsupported document grouping."))
                groups.append(group)

            sort_key = f"sort_by_{position + 1}"
            direction_key = f"sort_direction_{position + 1}"
            current_order = (
                current_ordering[position]
                if position < len(current_ordering)
                else {}
            )
            sort_field = editor_values.get(
                sort_key,
                current_order.get("name", False),
            )
            direction = editor_values.get(
                direction_key,
                "asc" if current_order.get("asc", True) else "desc",
            )
            if sort_field:
                if sort_field not in allowed_ordering:
                    raise ValidationError(_("Unsupported document ordering field."))
                if direction not in dict(self._SORT_DIRECTION_SELECTION):
                    raise ValidationError(_("Unsupported document ordering direction."))
                ordering.append(
                    f"{sort_field}{' desc' if direction == 'desc' else ''}",
                )

        context = dict(current_context)
        if groups:
            context["group_by"] = groups
        else:
            context.pop("group_by", None)
        return self._validated_ir_filter_values(
            {
                "domain": domain or "[]",
                "context": context,
                "sort": ordering,
            },
        )

    @api.model_create_multi
    def create(self, vals_list):
        self._require_manager()
        normalized = []
        pending_editor_values = []
        for values in vals_list:
            values = dict(values)
            editor_values = {
                key: values.pop(key)
                for key in self._EDITOR_FIELDS
                if key in values
            }
            values.setdefault("key", f"shortcut_{uuid.uuid4().hex}")
            if not values.get("ir_filter_id"):
                native_values = self._default_ir_filter_values(values.get("name"))
                ir_filter = self.env["ir.filters"].create(native_values)
                values["ir_filter_id"] = ir_filter.id
            normalized.append(values)
            pending_editor_values.append(editor_values)
        shortcuts = super().create(normalized)
        for shortcut, editor_values in zip(shortcuts, pending_editor_values):
            if editor_values:
                shortcut.ir_filter_id.write(
                    shortcut._editor_ir_filter_values(editor_values),
                )
        return shortcuts

    def write(self, vals):
        self._require_manager()
        vals = dict(vals)
        editor_values = {
            key: vals.pop(key)
            for key in self._EDITOR_FIELDS
            if key in vals
        }
        result = super().write(vals)
        for shortcut in self:
            native_values = {}
            if editor_values:
                native_values.update(
                    shortcut._editor_ir_filter_values(editor_values),
                )
            if "name" in vals:
                native_values["name"] = shortcut.name
            if native_values:
                shortcut.ir_filter_id.write(native_values)
        return result

    def unlink(self):
        native_filters = self.mapped("ir_filter_id")
        result = super().unlink()
        native_filters.exists().unlink()
        return result

    def _filter_domain(self):
        self.ensure_one()
        if not self.ir_filter_id:
            return []
        try:
            domain = safe_eval(
                self.ir_filter_id.domain or "[]",
                {
                    "uid": self.env.uid,
                    "context_today": lambda: fields.Date.context_today(self),
                    "relativedelta": relativedelta,
                },
            )
        except Exception as error:
            raise ValidationError(
                _("The shortcut contains an invalid domain."),
            ) from error
        if not isinstance(domain, list):
            raise ValidationError(_("The shortcut contains an invalid domain."))
        Domain(domain)
        return domain

    def _filter_context(self):
        self.ensure_one()
        if not self.ir_filter_id:
            return {}
        try:
            context = ast.literal_eval(self.ir_filter_id.context or "{}")
        except (SyntaxError, ValueError) as error:
            raise ValidationError(_("The shortcut contains an invalid context.")) from error
        if not isinstance(context, dict):
            raise ValidationError(_("The shortcut contains an invalid context."))
        return context

    def _filter_group_by(self):
        self.ensure_one()
        group_by = self._filter_context().get("group_by")
        if isinstance(group_by, str):
            return [group_by]
        if isinstance(group_by, list):
            return [item for item in group_by if isinstance(item, str)]
        return []

    def _filter_order_by(self):
        self.ensure_one()
        if not self.ir_filter_id:
            return []
        try:
            values = json.loads(self.ir_filter_id.sort or "[]")
        except json.JSONDecodeError as error:
            raise ValidationError(_("The shortcut contains invalid ordering.")) from error
        result = []
        for value in values:
            if not isinstance(value, str):
                continue
            field_name, *direction = value.split()
            result.append(
                {
                    "name": field_name,
                    "asc": not direction or direction[0].lower() != "desc",
                },
            )
        return result

    @api.model
    def _validated_ir_filter_values(self, values):
        if not isinstance(values, dict):
            raise ValidationError(_("Invalid saved-search definition."))
        allowed = {"domain", "context", "sort"}
        if set(values) - allowed:
            raise ValidationError(_("Unsupported saved-search value."))
        domain = values.get("domain") or "[]"
        context = values.get("context") or "{}"
        sort = values.get("sort") or "[]"
        try:
            parsed_domain = (
                domain
                if isinstance(domain, list)
                else safe_eval(
                    domain,
                    {
                        "uid": self.env.uid,
                        "context_today": lambda: fields.Date.context_today(self),
                        "relativedelta": relativedelta,
                    },
                )
            )
            parsed_context = (
                context
                if isinstance(context, dict)
                else ast.literal_eval(context)
            )
            parsed_sort = sort if isinstance(sort, list) else json.loads(sort)
        except (SyntaxError, ValueError, json.JSONDecodeError) as error:
            raise ValidationError(_("Invalid saved-search definition.")) from error
        if (
            not isinstance(parsed_domain, list)
            or not isinstance(parsed_context, dict)
            or not isinstance(parsed_sort, list)
        ):
            raise ValidationError(_("Invalid saved-search definition."))
        Domain(parsed_domain)
        return {
            "domain": domain if isinstance(domain, str) else repr(parsed_domain),
            "context": repr(parsed_context),
            "sort": json.dumps(parsed_sort),
        }

    @api.model
    def save_from_search(
        self,
        name,
        search_values,
        *,
        shortcut_id=None,
        icon="fa-filter",
        sequence=10,
        smart_view_ids=None,
    ):
        self._require_manager()
        name = (name or "").strip()
        if not name:
            raise ValidationError(_("Give the shortcut a name."))
        ir_values = self._validated_ir_filter_values(search_values)
        action = self.env.ref("usl_documents.action_documents_workspace")
        ir_values.update(
            {
                "name": name,
                "model_id": "usl.document",
                "action_id": action.id,
                "user_ids": [Command.clear()],
                "is_default": False,
            },
        )
        shortcut = self.browse(int(shortcut_id or 0)).exists()
        if shortcut:
            shortcut.check_access("write")
            if shortcut.ir_filter_id:
                shortcut.ir_filter_id.write(ir_values)
            else:
                shortcut.ir_filter_id = self.env["ir.filters"].create(ir_values)
            shortcut.write(
                {
                    "name": name,
                    "icon": icon or "fa-filter",
                    "sequence": int(sequence or 10),
                    "smart_view_ids": [
                        Command.set(
                            self.env["usl.document.smart.view"].browse(
                                [int(item) for item in (smart_view_ids or [])],
                            ).filtered(lambda view: view.scope == "shared").ids,
                        ),
                    ],
                },
            )
        else:
            ir_filter = self.env["ir.filters"].create(ir_values)
            shortcut = self.create(
                {
                    "name": name,
                    "ir_filter_id": ir_filter.id,
                    "icon": icon or "fa-filter",
                    "sequence": int(sequence or 10),
                    "smart_view_ids": [
                        Command.set(
                            self.env["usl.document.smart.view"].browse(
                                [int(item) for item in (smart_view_ids or [])],
                            ).filtered(lambda view: view.scope == "shared").ids,
                        ),
                    ],
                },
            )
        return shortcut.workspace_values()

    @api.model
    def builder_values(self, shortcut_id=None):
        self._require_manager()
        shortcut = self.browse(int(shortcut_id or 0)).exists()
        return {
            "shortcut": (
                {
                    "id": shortcut.id,
                    "name": shortcut.name,
                    "icon": shortcut.icon,
                    "sequence": shortcut.sequence,
                    "smart_view_ids": shortcut.smart_view_ids.ids,
                    "domain": shortcut._filter_domain(),
                    "group_by": shortcut._filter_group_by(),
                    "order_by": shortcut._filter_order_by(),
                }
                if shortcut
                else False
            ),
            "smart_views": [
                {"id": view.id, "name": view.name}
                for view in self.env["usl.document.smart.view"].search(
                    [("scope", "=", "shared"), ("active", "=", True)],
                )
            ],
        }

    def workspace_values(self):
        self.ensure_one()
        return {
            "id": self.id,
            "key": self.key,
            "name": self.name,
            "icon": self.icon or "fa-filter",
            "kind": (
                "group"
                if self._filter_group_by() and not self._filter_domain()
                else "filter"
            ),
            "domain": self._filter_domain(),
            "group_by": self._filter_group_by(),
            "order_by": self._filter_order_by(),
        }


class UslDocumentSmartView(models.Model):
    _name = "usl.document.smart.view"
    _description = "Documents Smart View"
    _order = "scope, sequence, name, id"

    name = fields.Char(required=True, translate=True)
    key = fields.Char(
        index=True,
        copy=False,
        default=lambda self: f"view_{uuid.uuid4().hex}",
    )
    icon = fields.Char(default="fa-folder-o")
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)
    scope = fields.Selection(
        [("shared", "Shared"), ("personal", "Personal")],
        required=True,
        default="personal",
        index=True,
    )
    user_id = fields.Many2one("res.users", index=True, ondelete="cascade")
    group_ids = fields.Many2many(
        "res.groups",
        "usl_document_smart_view_group_rel",
        "view_id",
        "group_id",
        string="Visible to groups",
        help="Leave empty for every Documents user.",
    )
    system_rule = fields.Selection(
        [
            ("all", "All accessible documents"),
            ("home", "Home"),
            ("library", "My library"),
            ("attention", "Needs attention"),
            ("recent", "Recently added"),
            ("accounting", "Accounting evidence"),
            ("projects", "Projects"),
            ("hr", "HR restricted"),
            ("inbox", "Inbox / To classify"),
            ("archive_search", "Archive search"),
            ("trash", "Trash"),
            ("metadata", "Paperless metadata"),
            ("saved", "Saved filters"),
        ],
        required=True,
        default="metadata",
    )
    days = fields.Integer(default=30)
    tag_ids = fields.Many2many(
        "usl.paperless.tag",
        "usl_document_smart_view_tag_rel",
        "view_id",
        "tag_id",
        string="Tags",
    )
    document_type_ids = fields.Many2many(
        "usl.paperless.document.type",
        "usl_document_smart_view_type_rel",
        "view_id",
        "type_id",
        string="Document types",
    )
    correspondent_ids = fields.Many2many(
        "usl.paperless.correspondent",
        "usl_document_smart_view_correspondent_rel",
        "view_id",
        "correspondent_id",
        string="Correspondents",
    )
    quick_filter_ids = fields.Many2many(
        "usl.document.quick.filter",
        "usl_document_smart_view_quick_filter_rel",
        "view_id",
        "filter_id",
        string="One-click shortcuts",
        help=(
            "Useful filters shown below the search bar for this view. "
            "They compose with normal search facets and tag shortcuts."
        ),
    )
    filter_json = fields.Text(readonly=True)
    archive_native = fields.Boolean(
        string="Available in Paperless",
        help=(
            "Synchronize this shared view with Paperless. Odoo-only company, "
            "confidentiality, and business-record filters remain enforced only in Odoo."
        ),
    )
    paperless_id = fields.Integer(
        string="Paperless Saved View ID", readonly=True, copy=False, index=True,
    )
    paperless_filter_json = fields.Text(readonly=True, copy=False)
    paperless_sync_state = fields.Selection(
        [
            ("pending", "Pending"),
            ("synchronized", "Synchronized"),
            ("failed", "Needs attention"),
        ],
        default="pending",
        required=True,
        readonly=True,
        copy=False,
    )
    paperless_sync_error = fields.Text(readonly=True, copy=False)
    paperless_synced_at = fields.Datetime(readonly=True, copy=False)

    _smart_view_key_unique = models.Constraint(
        "UNIQUE(key)", "A shared Documents view key must be unique.",
    )
    _smart_view_paperless_unique = models.Constraint(
        "UNIQUE(paperless_id)",
        "A Paperless saved view may only be mapped once.",
    )

    @api.model
    def _paperless_cache_fields(self):
        return {
            "paperless_id",
            "paperless_filter_json",
            "paperless_sync_state",
            "paperless_sync_error",
            "paperless_synced_at",
        }

    @api.model_create_multi
    def create(self, vals_list):
        normalized = []
        for values in vals_list:
            values = dict(values)
            cache_write = (
                self.env.context.get("usl_documents_archive_view_sync")
                and self.env.su
            )
            if self._paperless_cache_fields().intersection(values) and not cache_write:
                raise AccessError(
                    _("Paperless synchronization fields cannot be edited manually."),
                )
            scope = (
                values.get("scope")
                or self.env.context.get("default_scope")
                or "personal"
            )
            if scope == "shared":
                self._require_manager()
                values["scope"] = "shared"
                values["user_id"] = False
                values.setdefault("key", f"view_{uuid.uuid4().hex}")
            else:
                values["scope"] = "personal"
                values["user_id"] = self.env.user.id
                values["key"] = False
                values["archive_native"] = False
            normalized.append(values)
        records = super().create(normalized)
        internal_sync = (
            self.env.context.get("usl_documents_archive_view_sync")
            and self.env.su
        )
        if not internal_sync and not self.env.context.get("install_mode"):
            records.filtered("archive_native")._push_to_paperless()
        return records

    def write(self, vals):
        cache_write = (
            self.env.context.get("usl_documents_archive_view_sync")
            and self.env.su
        )
        if self._paperless_cache_fields().intersection(vals) and not cache_write:
            raise AccessError(
                _("Paperless synchronization fields cannot be edited manually."),
            )
        if self.filtered(lambda item: item.scope == "shared"):
            self._require_manager()
        if self.filtered(
            lambda item: item.scope == "personal" and item.user_id != self.env.user,
        ):
            raise AccessError(_("You may only edit your own saved views."))
        if vals.get("archive_native") and self.filtered(
            lambda item: item.scope == "personal",
        ):
            raise AccessError(_("Personal saved searches stay private to Odoo."))
        result = super().write(vals)
        push_fields = {
            "name",
            "archive_native",
            "tag_ids",
            "document_type_ids",
            "correspondent_ids",
        }
        if (
            push_fields.intersection(vals)
            and not cache_write
            and not self.env.context.get("install_mode")
        ):
            self.filtered("archive_native")._push_to_paperless()
        return result

    def unlink(self):
        if self.filtered(lambda item: item.scope == "shared"):
            self._require_manager()
        if self.filtered(
            lambda item: item.scope == "personal" and item.user_id != self.env.user,
        ):
            raise AccessError(_("You may only remove your own saved views."))
        internal_sync = (
            self.env.context.get("usl_documents_archive_view_sync")
            and self.env.su
        )
        if not internal_sync:
            for record in self.filtered(
                lambda item: item.archive_native and item.paperless_id,
            ):
                PaperlessClient(self.env).delete_saved_view(record.paperless_id)
        return super().unlink()

    def _require_manager(self):
        if not self.env.user.has_group("usl_documents.group_documents_manager"):
            raise AccessError(
                _("Only Documents administrators may change shared smart views."),
            )

    @api.model
    def accessible_views(self):
        return self.search(
            [
                "&",
                "&",
                ("active", "=", True),
                "|",
                ("scope", "=", "shared"),
                ("user_id", "=", self.env.user.id),
                "|",
                ("group_ids", "=", False),
                ("group_ids", "in", self.env.user.group_ids.ids),
            ],
        )

    def document_domain(self):
        self.ensure_one()
        if self.system_rule == "home":
            cutoff = fields.Datetime.now() - timedelta(days=max(1, self.days or 30))
            domain = [
                "&",
                ("is_prominent", "=", True),
                "|",
                "|",
                "|",
                ("is_starred", "=", True),
                ("recently_opened", "=", True),
                ("review_state", "=", "needs_attention"),
                ("archive_added_at", ">=", cutoff),
            ]
        elif self.system_rule == "library":
            domain = [("is_in_my_library", "=", True)]
        elif self.system_rule == "attention":
            domain = [("review_state", "=", "needs_attention")]
        elif self.system_rule == "recent":
            cutoff = fields.Datetime.now() - timedelta(days=max(1, self.days or 30))
            domain = [("archive_added_at", ">=", cutoff)]
        elif self.system_rule == "accounting":
            domain = [("accounting_evidence", "=", True)]
        elif self.system_rule == "projects":
            domain = [
                (
                    "id",
                    "in",
                    list(
                        self.env["usl.document"]._accessible_project_document_ids(),
                    ),
                ),
            ]
        elif self.system_rule == "hr":
            domain = [("confidentiality", "=", "hr")]
        elif self.system_rule == "inbox":
            domain = [
                ("source", "=", "paperless"),
                ("review_state", "=", "needs_attention"),
                ("intake_role", "=", "background"),
            ]
        elif self.system_rule == "archive_search":
            domain = []
        elif self.system_rule == "saved":
            domain = []
            if self.tag_ids:
                domain.append(("tag_ids", "in", self.tag_ids.ids))
            if self.document_type_ids:
                domain.append(("document_type_id", "in", self.document_type_ids.ids))
            if self.correspondent_ids:
                domain.append(
                    ("correspondent_id", "in", self.correspondent_ids.ids),
                )
        elif self.system_rule == "metadata":
            domain = []
            if self.tag_ids:
                domain.append(("tag_ids", "in", self.tag_ids.ids))
            if self.document_type_ids:
                domain.append(("document_type_id", "in", self.document_type_ids.ids))
            if self.correspondent_ids:
                domain.append(
                    ("correspondent_id", "in", self.correspondent_ids.ids),
                )
            if not domain:
                domain = [("id", "=", 0)]
        elif self.system_rule == "trash":
            domain = [("availability_state", "=", "trashed")]
        else:
            domain = []
        return domain

    def workspace_values(self):
        self.ensure_one()
        filters = json.loads(self.filter_json or "{}")
        quick_filters = self.quick_filter_ids.filtered("active")
        if not quick_filters:
            defaults = {
                "home": ["needs_review", "last_30_days"],
                "library": ["my_uploads", "group_document_type"],
                "attention": ["needs_review", "unlinked"],
                "recent": ["last_30_days", "group_document_month"],
                "accounting": ["group_correspondent", "group_document_type"],
                "projects": ["group_document_month", "group_document_type"],
                "metadata": ["group_correspondent", "group_document_type"],
                "hr": ["group_employee", "group_document_month"],
                "inbox": ["needs_review", "unlinked"],
                "archive_search": ["group_company", "group_document_type"],
                "trash": ["group_document_type", "group_correspondent"],
                "all": ["my_uploads", "unlinked", "group_company"],
            }
            quick_filters = self.env["usl.document.quick.filter"].search(
                [("key", "in", defaults.get(self.system_rule, []))],
            )
        favorite_filter = {
            "id": 0,
            "key": "starred",
            "name": _("Starred documents"),
            "icon": "fa-star",
            "kind": "filter",
            "domain": [["is_starred", "=", True]],
            "group_by": [],
            "order_by": [],
        }
        return {
            "id": self.id,
            "key": self.key or f"view:{self.id}",
            "name": self.name,
            "icon": self.icon or "fa-folder-o",
            "personal": self.scope == "personal",
            "filters": filters,
            "archive_native": self.archive_native,
            "needs_attention": self.paperless_sync_state == "failed",
            "quick_filters": [
                favorite_filter,
                *[
                    item.workspace_values()
                    for item in quick_filters.filtered(
                        lambda shortcut: shortcut.key != "starred",
                    ).sorted("sequence")
                ],
            ],
        }

    def action_open_documents(self):
        """Open Documents with this Smart View selected."""
        self.ensure_one()
        self.check_access("read")
        action = self.env["ir.actions.actions"]._for_xml_id(
            "usl_documents.action_documents_workspace",
        )
        action["params"] = {
            "initial_workspace": self.key or f"view:{self.id}",
        }
        return action

    def _paperless_filter_rules(self):
        self.ensure_one()
        rules = [
            {"rule_type": 6, "value": str(tag.paperless_id)}
            for tag in self.tag_ids
        ]
        rules.extend(
            {"rule_type": 4, "value": str(document_type.paperless_id)}
            for document_type in self.document_type_ids
        )
        rules.extend(
            {"rule_type": 3, "value": str(correspondent.paperless_id)}
            for correspondent in self.correspondent_ids
        )
        saved = json.loads(self.filter_json or "{}")
        if saved.get("query"):
            rules.append({"rule_type": 20, "value": saved["query"]})
        return rules

    def _paperless_payload(self):
        self.ensure_one()
        return {
            "name": self.name,
            "sort_field": "created",
            "sort_reverse": True,
            "filter_rules": self._paperless_filter_rules(),
            # Shared Odoo views are shared Paperless Saved Views. Personal
            # Paperless and personal Odoo views retain their individual owner.
            "owner": None,
        }

    def _push_to_paperless(self, client=None):
        client = client or PaperlessClient(self.env)
        remote_views = None

        def remote_owner_id(remote):
            owner = remote.get("owner")
            return int(owner.get("id")) if isinstance(owner, dict) else int(owner or 0)

        for record in self:
            if record.scope != "shared" or not record.archive_native:
                continue
            payload = record._paperless_payload()
            paperless_id = record.paperless_id
            if not paperless_id:
                # A remote create may have committed while the surrounding Odoo
                # transaction later rolled back. Adopt that stable object on retry
                # instead of producing a duplicate or leaving synchronization stuck.
                remote_views = (
                    client.list_saved_views()
                    if remote_views is None
                    else remote_views
                )
                existing = next(
                    (
                        remote
                        for remote in remote_views
                        if remote.get("name") == record.name
                        and remote_owner_id(remote) == 0
                    ),
                    None,
                )
                paperless_id = int(existing["id"]) if existing else False
            if paperless_id:
                remote = client.update_saved_view(paperless_id, payload)
            else:
                remote = client.create_saved_view(payload)
                if remote_views is not None:
                    remote_views.append(remote)
            record.sudo().with_context(usl_documents_archive_view_sync=True).write(
                {
                    "paperless_id": int(remote["id"]),
                    "paperless_filter_json": json.dumps(
                        remote.get("filter_rules") or [], sort_keys=True,
                    ),
                    "paperless_sync_state": "synchronized",
                    "paperless_sync_error": False,
                    "paperless_synced_at": fields.Datetime.now(),
                },
            )
        return True

    @api.model
    def _remote_cache_values(self, payload):
        tag_ids = []
        document_type_ids = []
        correspondent_ids = []
        saved_filters = {}
        unsupported = []
        models_by_rule = {
            6: ("usl.paperless.tag", tag_ids),
            4: ("usl.paperless.document.type", document_type_ids),
            3: ("usl.paperless.correspondent", correspondent_ids),
        }
        for rule in payload.get("filter_rules") or []:
            rule_type = int(rule.get("rule_type") or 0)
            if rule_type == 20:
                saved_filters["query"] = rule.get("value") or ""
                continue
            target = models_by_rule.get(rule_type)
            if not target:
                unsupported.append(str(rule_type))
                continue
            try:
                paperless_id = int(rule.get("value"))
            except (TypeError, ValueError):
                unsupported.append(str(rule_type))
                continue
            record = self.env[target[0]].sudo().search(
                [("paperless_id", "=", paperless_id)], limit=1,
            )
            if record:
                target[1].append(record.id)
            else:
                unsupported.append(str(rule_type))
        return {
            "name": payload.get("name") or _("Unnamed saved view"),
            "archive_native": True,
            "paperless_id": int(payload["id"]),
            "paperless_filter_json": json.dumps(
                payload.get("filter_rules") or [], sort_keys=True,
            ),
            "system_rule": "saved" if saved_filters else "metadata",
            "filter_json": json.dumps(saved_filters, sort_keys=True),
            "tag_ids": [Command.set(tag_ids)],
            "document_type_ids": [Command.set(document_type_ids)],
            "correspondent_ids": [Command.set(correspondent_ids)],
            "paperless_sync_state": "failed" if unsupported else "synchronized",
            "paperless_sync_error": (
                _("Paperless uses unsupported saved-view rules: %s")
                % ", ".join(sorted(set(unsupported)))
                if unsupported
                else False
            ),
            "paperless_synced_at": fields.Datetime.now(),
        }

    @api.model
    def synchronize_archive_views(self, client=None):
        """Synchronize shared Paperless-compatible views by stable identity."""
        self._require_manager()
        client = client or PaperlessClient(self.env)
        local_views = self.sudo().search(
            [("scope", "=", "shared"), ("archive_native", "=", True)],
        )
        local_views.filtered(lambda view: not view.paperless_id)._push_to_paperless(
            client=client,
        )
        remote_views = client.list_saved_views()
        seen = set()
        for payload in remote_views:
            paperless_id = int(payload["id"])
            seen.add(paperless_id)
            view = self.sudo().search(
                [("paperless_id", "=", paperless_id)], limit=1,
            )
            owner = payload.get("owner")
            owner_id = (
                int(owner.get("id"))
                if isinstance(owner, dict) and owner.get("id")
                else int(owner or 0)
            )
            migratable_service_view = bool(
                view
                and view.scope == "shared"
                and view.archive_native
                and client.owner_user_id
                and owner_id == client.owner_user_id,
            )
            if owner_id and not migratable_service_view:
                if view and view.scope == "shared" and view.archive_native:
                    view.with_context(
                        usl_documents_archive_view_sync=True,
                    ).write(
                        {
                            "paperless_sync_state": "failed",
                            "paperless_sync_error": _(
                                "The mapped Paperless Saved View is private. "
                                "Make it shared in Paperless or remap this view.",
                            ),
                            "paperless_synced_at": fields.Datetime.now(),
                        },
                    )
                continue
            if migratable_service_view:
                # Earlier revisions owned synchronized views with the service
                # account, making them disappear from ordinary Paperless users.
                payload = client.update_saved_view(
                    paperless_id,
                    {"owner": None},
                )
            values = self._remote_cache_values(payload)
            if view:
                view.with_context(usl_documents_archive_view_sync=True).write(values)
            elif not values["paperless_sync_error"]:
                self.sudo().with_context(
                    usl_documents_archive_view_sync=True,
                ).create(
                    {
                        **values,
                        "scope": "shared",
                        "key": f"paperless:{paperless_id}",
                        "icon": "fa-folder-open-o",
                        "sequence": 75,
                    },
                )
        missing = local_views.filtered(
            lambda view: view.paperless_id and view.paperless_id not in seen,
        )
        if missing:
            missing.with_context(usl_documents_archive_view_sync=True).write(
                {
                    "paperless_sync_state": "failed",
                    "paperless_sync_error": _(
                        "The mapped Paperless saved view no longer exists.",
                    ),
                },
            )
        return len(remote_views)

    @api.model
    def save_personal_view(self, name, filters):
        if not (name or "").strip():
            raise ValidationError(_("Give this saved view a name."))
        allowed = {
            "query",
            "company_id",
            "tag_ids",
            "correspondent_id",
            "document_type_id",
            "date_from",
            "date_to",
            "added_from",
            "added_to",
            "source",
            "confidentiality",
            "review_state",
            "linked_state",
            "linked_record",
            "sort",
        }
        sanitized = {key: value for key, value in (filters or {}).items() if key in allowed}
        view = self.create(
            {
                "name": name.strip(),
                "scope": "personal",
                "system_rule": "saved",
                "icon": "fa-bookmark-o",
                "filter_json": json.dumps(sanitized, sort_keys=True),
            },
        )
        return view.workspace_values()
