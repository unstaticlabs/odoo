import json
from datetime import timedelta

from odoo import _, api, fields, models
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
        string="Matching pattern",
        help="Text or pattern Paperless uses when automatically classifying documents.",
    )
    matching_algorithm = fields.Selection(
        MATCHING_ALGORITHMS,
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
    def _paperless_payload(self, values):
        payload = {}
        for key in self._payload_fields():
            if key not in values:
                continue
            value = values[key]
            if key == "matching_algorithm":
                value = int(value or 0)
            payload[key] = value
        if self._paperless().owner_user_id:
            payload.setdefault("owner", self._paperless().owner_user_id)
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
            records |= super(UslPaperlessMetadataMixin, self.with_context(
                usl_documents_cache_write=True,
            )).create(self._cache_values(remote))
        return records

    def write(self, values):
        if self.env.context.get("usl_documents_cache_write"):
            return super().write(values)
        if "paperless_id" in values:
            raise AccessError(_("Paperless identities cannot be changed."))
        for record in self:
            remote = record._paperless().update_metadata(
                record._paperless_kind,
                record.paperless_id,
                record._paperless_payload(values),
            )
            super(UslPaperlessMetadataMixin, record.with_context(
                usl_documents_cache_write=True,
            )).write(record._cache_values(remote))
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

    _smart_view_key_unique = models.Constraint(
        "UNIQUE(key)", "A shared Documents view key must be unique.",
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
        return super().create(normalized)

    def write(self, values):
        if self.filtered(lambda item: item.scope == "shared"):
            self._require_manager()
        if self.filtered(
            lambda item: item.scope == "personal" and item.user_id != self.env.user,
        ):
            raise AccessError(_("You may only edit your own saved views."))
        return super().write(values)

    def unlink(self):
        if self.filtered(lambda item: item.scope == "shared"):
            self._require_manager()
        if self.filtered(
            lambda item: item.scope == "personal" and item.user_id != self.env.user,
        ):
            raise AccessError(_("You may only remove your own saved views."))
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
        elif self.system_rule == "metadata":
            alternatives = []
            if self.tag_ids:
                alternatives.append([("tag_ids", "in", self.tag_ids.ids)])
            if self.document_type_ids:
                alternatives.append(
                    [("document_type_id", "in", self.document_type_ids.ids)],
                )
            if self.correspondent_ids:
                alternatives.append(
                    [("correspondent_id", "in", self.correspondent_ids.ids)],
                )
            domain = (
                ["|"] * (len(alternatives) - 1)
                + [alternative[0] for alternative in alternatives]
                if alternatives
                else [("id", "=", 0)]
            )
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
        }

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
