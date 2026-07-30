import json
from datetime import timedelta

from odoo import Command, _, api, fields, models
from odoo.exceptions import AccessError, ValidationError

from .paperless_client import PaperlessClient

MATCHING_ALGORITHMS = [
    ("0", "None"),
    ("1", "Any word"),
    ("2", "All words"),
    ("3", "Exact match"),
    ("4", "Regular expression"),
    ("5", "Fuzzy word"),
    ("6", "Automatic"),
]


class UslPaperlessMetadataMixin(models.AbstractModel):
    _name = "usl.paperless.metadata.mixin"
    _description = "Paperless Metadata Cache"
    _order = "name, paperless_id"

    _paperless_kind = None

    name = fields.Char(required=True, index=True)
    paperless_id = fields.Integer(
        string="Paperless ID", required=True, index=True, readonly=True, copy=False,
    )
    match = fields.Char(
        string="Words to look for",
        help="Text or pattern Paperless uses when automatically classifying documents.",
    )
    matching_algorithm = fields.Selection(
        MATCHING_ALGORITHMS,
        string="How documents match",
        default="0",
        required=True,
        help="Automatic learns from existing documents. The other options apply the matching pattern directly.",
    )
    is_insensitive = fields.Boolean(
        string="Ignore letter case",
        help="Match upper- and lower-case text in the same way.",
    )
    document_count = fields.Integer(readonly=True)
    active = fields.Boolean(default=True)
    last_synced_at = fields.Datetime(readonly=True)
    last_error = fields.Text(readonly=True)

    _paperless_metadata_unique = models.Constraint(
        "UNIQUE(paperless_id)", "A Paperless metadata item may only be cached once.",
    )

    def _paperless(self):
        return PaperlessClient(self.env)

    @api.model
    def _payload_fields(self):
        return {"name", "match", "matching_algorithm", "is_insensitive"}

    @api.model
    def _local_write_fields(self):
        return {"active"}

    @api.model
    def _paperless_payload(self, values):
        payload = {}
        for key in self._payload_fields():
            if key not in values:
                continue
            value = values[key]
            if key == "matching_algorithm":
                value = int(value or 0)
            payload[key] = value
        # Tags, correspondents, and document types are shared archive catalogs.
        # Paperless treats an object without an owner as visible to users who
        # have the corresponding global model permission. Document object
        # permissions remain independent and are synchronized separately.
        payload.setdefault("owner", None)
        return payload

    @api.model
    def _cache_values(self, payload):
        return {
            "name": payload.get("name") or _("Unnamed"),
            "paperless_id": int(payload["id"]),
            "match": payload.get("match") or False,
            "matching_algorithm": str(payload.get("matching_algorithm") or 0),
            "is_insensitive": bool(payload.get("is_insensitive")),
            "document_count": int(payload.get("document_count") or 0),
            "active": True,
            "last_synced_at": fields.Datetime.now(),
            "last_error": False,
        }

    @api.model_create_multi
    def create(self, values_list):
        if self.env.context.get("usl_documents_cache_write"):
            return super().create(values_list)
        records = self.browse()
        for values in values_list:
            remote = self._paperless().create_metadata(
                self._paperless_kind, self._paperless_payload(values),
            )
            cache_values = self._cache_values(remote)
            cache_values.update(
                {
                    key: value
                    for key, value in values.items()
                    if key in self._local_write_fields()
                },
            )
            records |= super(UslPaperlessMetadataMixin, self.with_context(
                usl_documents_cache_write=True,
            )).create(cache_values)
        return records

    def write(self, values):
        if self.env.context.get("usl_documents_cache_write"):
            return super().write(values)
        if "paperless_id" in values:
            raise AccessError(_("Paperless identities cannot be changed."))
        allowed = self._payload_fields() | self._local_write_fields()
        unsupported = set(values) - allowed
        if unsupported:
            raise AccessError(
                _("These archive metadata fields cannot be edited: %s")
                % ", ".join(sorted(unsupported)),
            )
        for record in self:
            remote_values = record._paperless_payload(values)
            cache_values = {
                key: value
                for key, value in values.items()
                if key in record._local_write_fields()
            }
            if remote_values:
                remote = record._paperless().update_metadata(
                    record._paperless_kind,
                    record.paperless_id,
                    remote_values,
                )
                cache_values.update(record._cache_values(remote))
            super(UslPaperlessMetadataMixin, record.with_context(
                usl_documents_cache_write=True,
            )).write(cache_values)
        return True

    def unlink(self):
        if self.env.context.get("usl_documents_cache_write"):
            return super().unlink()
        if not self.env.user.has_group("usl_documents.group_documents_manager"):
            raise AccessError(
                _("Only Documents administrators may delete shared archive metadata."),
            )
        for record in self:
            record._paperless().delete_metadata(
                record._paperless_kind, record.paperless_id,
            )
        return super().unlink()

    @api.model
    def synchronize_catalog(self, client=None):
        client = client or self._paperless()
        payloads = client.list_metadata(self._paperless_kind)
        seen = set()
        for payload in payloads:
            paperless_id = int(payload["id"])
            seen.add(paperless_id)
            owner = payload.get("owner")
            owner_id = (
                int(owner.get("id"))
                if isinstance(owner, dict) and owner.get("id")
                else int(owner or 0)
            )
            if client.owner_user_id and owner_id == client.owner_user_id:
                # Migrate catalogs created by earlier revisions from the
                # integration identity to Paperless's supported shared form.
                payload = client.update_metadata(
                    self._paperless_kind,
                    paperless_id,
                    {"owner": None},
                )
            record = self.sudo().search(
                [("paperless_id", "=", paperless_id)], limit=1,
            )
            values = self._cache_values(payload)
            if record:
                record.with_context(usl_documents_cache_write=True).write(values)
            else:
                self.sudo().with_context(usl_documents_cache_write=True).create(values)
        stale = self.sudo().search(
            [("paperless_id", "not in", list(seen)), ("active", "=", True)],
        )
        if stale:
            stale.with_context(usl_documents_cache_write=True).write({"active": False})
        return len(seen)

    @api.model
    def action_refresh_catalog(self):
        self.synchronize_catalog()
        return {"type": "ir.actions.client", "tag": "reload"}


class UslPaperlessTag(models.Model):
    _name = "usl.paperless.tag"
    _description = "Paperless Tag"
    _inherit = "usl.paperless.metadata.mixin"

    _paperless_kind = "tags"

    color = fields.Char(default="#a6cee3")
    text_color = fields.Char(readonly=True)
    is_inbox_tag = fields.Boolean(
        string="Add to new documents",
        help="Paperless automatically adds inbox tags to newly received documents.",
    )
    parent_id = fields.Many2one(
        "usl.paperless.tag", string="Parent tag", ondelete="restrict",
    )
    child_ids = fields.One2many("usl.paperless.tag", "parent_id")

    @api.model
    def _payload_fields(self):
        return super()._payload_fields() | {"color", "is_inbox_tag", "parent_id"}

    @api.model
    def _paperless_payload(self, values):
        payload = super()._paperless_payload(values)
        if "parent_id" in values:
            parent = self.browse(values["parent_id"]).exists()
            payload["parent"] = parent.paperless_id if parent else None
        return payload

    @api.model
    def _cache_values(self, payload):
        parent = self.search(
            [("paperless_id", "=", int(payload["parent"]))], limit=1,
        ) if payload.get("parent") else self.browse()
        return {
            **super()._cache_values(payload),
            "color": payload.get("color") or "#a6cee3",
            "text_color": payload.get("text_color") or "#000000",
            "is_inbox_tag": bool(payload.get("is_inbox_tag")),
            "parent_id": parent.id or False,
        }

    @api.model
    def synchronize_catalog(self, client=None):
        client = client or self._paperless()
        payloads = client.list_metadata(self._paperless_kind)
        result = super().synchronize_catalog(client=client)
        by_remote_id = {
            record.paperless_id: record
            for record in self.sudo().search(
                [("paperless_id", "in", [int(item["id"]) for item in payloads])],
            )
        }
        for payload in payloads:
            record = by_remote_id[int(payload["id"])]
            parent = by_remote_id.get(payload.get("parent"))
            parent_id = parent.id if parent else False
            if record.parent_id.id != parent_id:
                record.with_context(usl_documents_cache_write=True).write(
                    {"parent_id": parent_id},
                )
        return result


class UslPaperlessCorrespondent(models.Model):
    _name = "usl.paperless.correspondent"
    _description = "Paperless Correspondent"
    _inherit = "usl.paperless.metadata.mixin"

    _paperless_kind = "correspondents"

    partner_id = fields.Many2one(
        "res.partner",
        string="Mapped Contact",
        index=True,
        ondelete="set null",
        help=(
            "Optional business-identity mapping. It does not link documents or grant "
            "access. Paperless remains responsible for archive matching."
        ),
    )
    partner_visible_id = fields.Many2one(
        "res.partner",
        string="Odoo Contact",
        compute="_compute_partner_visible",
        inverse="_inverse_partner_visible",
        search="_search_partner_visible",
        help=(
            "The mapped Contact, when it is accessible in the current user's "
            "companies. Hidden mappings never grant or reveal Contact access."
        ),
    )
    partner_mapping_hidden = fields.Boolean(
        compute="_compute_partner_visible",
        help=(
            "The correspondent is already mapped to a Contact outside the current "
            "user's accessible companies."
        ),
    )
    rejected_partner_id = fields.Many2one(
        "res.partner",
        string="Rejected suggestion",
        readonly=True,
        copy=False,
        ondelete="set null",
    )
    suggested_partner_id = fields.Many2one(
        "res.partner",
        string="Suggested Contact",
        compute="_compute_suggested_partner",
    )

    @api.model
    def _local_write_fields(self):
        return super()._local_write_fields() | {
            "partner_id",
            "rejected_partner_id",
        }

    @api.depends("partner_id")
    @api.depends_context("uid", "allowed_company_ids")
    def _compute_partner_visible(self):
        visible_ids = set(
            self.env["res.partner"].search(
                [("id", "in", self.mapped("partner_id").ids)],
            ).ids,
        )
        for correspondent in self:
            correspondent.partner_visible_id = (
                correspondent.partner_id
                if correspondent.partner_id.id in visible_ids
                else False
            )
            correspondent.partner_mapping_hidden = bool(
                correspondent.partner_id
                and correspondent.partner_id.id not in visible_ids
            )

    def _inverse_partner_visible(self):
        for correspondent in self:
            if correspondent.partner_mapping_hidden:
                raise AccessError(
                    _(
                        "This correspondent is already mapped to a Contact outside "
                        "your accessible companies. Ask a Documents administrator "
                        "to review the mapping."
                    ),
                )
            correspondent.partner_id = correspondent.partner_visible_id

    @api.model
    def _search_partner_visible(self, operator, value):
        return [("partner_id", operator, value)]

    @api.model
    def _check_visible_partner_value(self, value):
        if not value:
            return
        partner = self.env["res.partner"].browse(int(value)).exists()
        if not partner:
            raise ValidationError(_("The selected Odoo Contact no longer exists."))
        partner.check_access("read")

    @api.model_create_multi
    def create(self, values_list):
        for values in values_list:
            self._check_visible_partner_value(
                values.get("partner_visible_id") or values.get("partner_id"),
            )
            if "partner_visible_id" in values:
                values["partner_id"] = values.pop("partner_visible_id")
        return super().create(values_list)

    def write(self, values):
        values = dict(values)
        mapping_requested = (
            "partner_visible_id" in values or "partner_id" in values
        )
        if mapping_requested and any(self.mapped("partner_mapping_hidden")):
            raise AccessError(
                _(
                    "This correspondent is already mapped to a Contact outside "
                    "your accessible companies. Ask a Documents administrator "
                    "to review the mapping."
                ),
            )
        if "partner_visible_id" in values:
            values["partner_id"] = values.pop("partner_visible_id")
        if "partner_id" in values:
            self._check_visible_partner_value(values["partner_id"])
        return super().write(values)

    @api.depends("name", "partner_id", "rejected_partner_id")
    def _compute_suggested_partner(self):
        for correspondent in self:
            if correspondent.partner_id or not correspondent.name:
                correspondent.suggested_partner_id = False
                continue
            candidate = self.env["res.partner"].search(
                [
                    ("active", "=", True),
                    ("name", "=ilike", correspondent.name),
                    "|",
                    ("company_id", "=", False),
                    ("company_id", "in", self.env.user.company_ids.ids),
                ],
                limit=1,
            )
            correspondent.suggested_partner_id = (
                candidate
                if candidate and candidate != correspondent.rejected_partner_id
                else False
            )

    def action_accept_suggested_partner(self):
        for correspondent in self:
            if correspondent.suggested_partner_id:
                correspondent.write(
                    {
                        "partner_id": correspondent.suggested_partner_id.id,
                        "rejected_partner_id": False,
                    },
                )
        return {"type": "ir.actions.client", "tag": "reload"}

    def action_reject_suggested_partner(self):
        for correspondent in self:
            if correspondent.suggested_partner_id:
                correspondent.write(
                    {"rejected_partner_id": correspondent.suggested_partner_id.id},
                )
        return {"type": "ir.actions.client", "tag": "reload"}

    def action_open_partner(self):
        self.ensure_one()
        if not self.partner_visible_id:
            raise ValidationError(_("Map an Odoo Contact first."))
        self.partner_visible_id.check_access("read")
        return {
            "type": "ir.actions.act_window",
            "name": self.partner_visible_id.display_name,
            "res_model": "res.partner",
            "res_id": self.partner_visible_id.id,
            "views": [(False, "form")],
            "target": "current",
        }

    @api.model
    def suggest_contacts(self, correspondent_id, limit=8):
        correspondent = self.browse(int(correspondent_id)).exists()
        if not correspondent:
            raise ValidationError(_("The correspondent no longer exists."))
        terms = [term for term in correspondent.name.split() if len(term) > 2]
        domain = [("active", "=", True)]
        if terms:
            domain.append(("name", "ilike", " ".join(terms)))
        partners = self.env["res.partner"].search(domain, limit=min(20, int(limit)))
        return [
            {
                "id": partner.id,
                "name": partner.display_name,
                "email": partner.email or "",
                "company": partner.company_id.display_name,
            }
            for partner in partners
        ]


class UslPaperlessDocumentType(models.Model):
    _name = "usl.paperless.document.type"
    _description = "Paperless Document Type"
    _inherit = "usl.paperless.metadata.mixin"

    _paperless_kind = "document_types"


class UslDocumentSmartView(models.Model):
    _name = "usl.document.smart.view"
    _description = "Documents Smart View"
    _order = "scope, sequence, name, id"

    name = fields.Char(required=True, translate=True)
    key = fields.Char(index=True, copy=False)
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
    system_rule = fields.Selection(
        [
            ("all", "All accessible documents"),
            ("attention", "Needs review"),
            ("recent", "Recently added"),
            ("accounting", "Accounting evidence"),
            ("hr", "HR restricted"),
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

    @api.model_create_multi
    def create(self, values_list):
        normalized = []
        for values in values_list:
            values = dict(values)
            if values.get("scope", "personal") == "shared":
                self._require_manager()
                values["user_id"] = False
            else:
                values["scope"] = "personal"
                values["user_id"] = self.env.user.id
                values["key"] = False
            normalized.append(values)
        records = super().create(normalized)
        if (
            not self.env.context.get("usl_documents_archive_view_sync")
            and not self.env.context.get("install_mode")
        ):
            records.filtered("archive_native")._push_to_paperless()
        return records

    def write(self, values):
        if self.filtered(lambda item: item.scope == "shared"):
            self._require_manager()
        if self.filtered(
            lambda item: item.scope == "personal" and item.user_id != self.env.user,
        ):
            raise AccessError(_("You may only edit your own saved views."))
        result = super().write(values)
        push_fields = {
            "name",
            "archive_native",
            "tag_ids",
            "document_type_ids",
            "correspondent_ids",
        }
        if (
            push_fields.intersection(values)
            and not self.env.context.get("usl_documents_archive_view_sync")
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
        if not self.env.context.get("usl_documents_archive_view_sync"):
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
                ("active", "=", True),
                "|",
                ("scope", "=", "shared"),
                ("user_id", "=", self.env.user.id),
            ],
        )

    def document_domain(self):
        self.ensure_one()
        if self.system_rule == "attention":
            domain = [("review_state", "=", "needs_attention")]
        elif self.system_rule == "recent":
            cutoff = fields.Datetime.now() - timedelta(days=max(1, self.days or 30))
            domain = [("paperless_created", ">=", cutoff)]
        elif self.system_rule == "accounting":
            domain = [("accounting_evidence", "=", True)]
        elif self.system_rule == "hr":
            domain = [("confidentiality", "=", "hr")]
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
        return {
            "id": self.id,
            "key": self.key or f"view:{self.id}",
            "name": self.name,
            "icon": self.icon or "fa-folder-o",
            "personal": self.scope == "personal",
            "filters": filters,
            "archive_native": self.archive_native,
            "needs_attention": self.paperless_sync_state == "failed",
        }

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
            record.with_context(usl_documents_archive_view_sync=True).write(
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
            if (
                view
                and view.scope == "shared"
                and view.archive_native
                and client.owner_user_id
                and owner_id == client.owner_user_id
            ):
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
