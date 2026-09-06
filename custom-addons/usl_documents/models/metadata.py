import json
import shlex

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, ValidationError

from .paperless_client import PaperlessClient, PaperlessError

MATCHING_ALGORITHMS = [
    ("0", "None"),
    ("1", "Any word"),
    ("2", "All words"),
    ("3", "Exact match"),
    ("4", "Regular expression"),
    ("5", "Fuzzy word"),
    ("6", "Learn automatically"),
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
    rule_lines = fields.Text(
        string="Matching terms",
        help=(
            "For Any word or All words, enter one word or phrase per line. "
            "Odoo writes the equivalent supported Paperless match expression."
        ),
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
    document_count = fields.Integer(
        readonly=True,
        groups="usl_documents.group_documents_manager",
    )
    accessible_document_count = fields.Integer(
        string="Documents",
        compute="_compute_accessible_document_count",
        help="Documents carrying this metadata that the current Odoo user may access.",
    )
    active = fields.Boolean(default=True)
    last_synced_at = fields.Datetime(readonly=True)
    last_error = fields.Text(readonly=True)

    _paperless_metadata_unique = models.Constraint(
        "UNIQUE(paperless_id)", "A Paperless metadata item may only be cached once.",
    )

    def _paperless(self):
        return PaperlessClient(self.env)

    def _require_manager(self):
        if not self.env.user.has_group("usl_documents.group_documents_manager"):
            raise AccessError(
                _("Only Documents administrators may reconcile archive catalogs."),
            )

    @api.model
    def _payload_fields(self):
        return {
            "name",
            "match",
            "rule_lines",
            "matching_algorithm",
            "is_insensitive",
        }

    @api.model
    def _local_write_fields(self):
        return {"active"}

    @api.model
    def _paperless_payload(self, values):
        values = dict(values)
        if "rule_lines" in values:
            algorithm = str(
                values.get("matching_algorithm")
                or (self[:1].matching_algorithm if self else "0")
                or "0",
            )
            values["match"] = self._compile_rule_lines(
                values.pop("rule_lines"),
                algorithm,
            )
        payload = {}
        for key in self._payload_fields():
            if key not in values:
                continue
            value = values[key]
            if key == "matching_algorithm":
                value = int(value or 0)
            elif key == "match":
                # Odoo represents an empty Char as ``False``. Paperless's
                # public serializers require a string, including when matching
                # is disabled or set to Automatic.
                value = value or ""
            payload[key] = value
        return payload

    @api.model
    def _compile_rule_lines(self, rule_lines, algorithm):
        lines = [
            line.strip()
            for line in (rule_lines or "").splitlines()
            if line.strip()
        ]
        if algorithm in ("1", "2"):
            expression = " ".join(
                json.dumps(line, ensure_ascii=False)
                if any(character.isspace() for character in line)
                else line
                for line in lines
            )
        else:
            expression = "\n".join(lines)
        if len(expression) > 256:
            raise ValidationError(
                _("Paperless matching expressions are limited to 256 characters."),
            )
        return expression

    @api.model
    def _rule_lines_from_match(self, match, algorithm):
        if not match:
            return False
        if str(algorithm or 0) not in ("1", "2"):
            return match
        try:
            return "\n".join(shlex.split(match))
        except ValueError:
            # Preserve an expression Paperless accepts even if it cannot be
            # losslessly presented as individual phrases.
            return match

    @api.model
    def _cache_values(self, payload):
        matching_algorithm = str(payload.get("matching_algorithm") or 0)
        return {
            "name": payload.get("name") or _("Unnamed"),
            "paperless_id": int(payload["id"]),
            "match": payload.get("match") or False,
            "rule_lines": self._rule_lines_from_match(
                payload.get("match"),
                matching_algorithm,
            ),
            "matching_algorithm": matching_algorithm,
            "is_insensitive": bool(payload.get("is_insensitive")),
            "document_count": int(payload.get("document_count") or 0),
            "active": True,
            "last_synced_at": fields.Datetime.now(),
            "last_error": False,
        }

    @api.model_create_multi
    def create(self, values_list):
        if (
            self.env.context.get("usl_documents_cache_write")
            and self.env.su
        ):
            return super().create(values_list)
        records = self.browse()
        for values in values_list:
            payload = self._paperless_payload(values)
            # Paperless's create serializers require a string.  Keep this
            # create-only: defaulting it during a name-only update would erase
            # an existing matching expression.
            payload.setdefault("match", "")
            # Tags, correspondents, and document types created from Odoo are
            # shared archive catalogs. Keep this create-only as well: an
            # Odoo-only Contact mapping must not produce an empty remote patch.
            payload.setdefault("owner", None)
            client = self._paperless()
            try:
                remote = client.create_metadata(self._paperless_kind, payload)
            except PaperlessError:
                # Paperless may have committed while the surrounding Odoo
                # transaction later rolled back. Adopt that shared stable
                # object on retry instead of creating a second catalog item.
                remote = next(
                    (
                        item
                        for item in client.list_metadata(self._paperless_kind)
                        if (item.get("name") or "").casefold()
                        == (payload.get("name") or "").casefold()
                        and not (
                            item.get("owner", {}).get("id")
                            if isinstance(item.get("owner"), dict)
                            else item.get("owner")
                        )
                    ),
                    None,
                )
                if not remote:
                    raise
            cache_values = self._cache_values(remote)
            cache_values.update(
                {
                    key: value
                    for key, value in values.items()
                    if key in self._local_write_fields()
                },
            )
            cached = self.sudo().with_context(active_test=False).search(
                [("paperless_id", "=", cache_values["paperless_id"])],
                limit=1,
            )
            if cached:
                cached.with_context(usl_documents_cache_write=True).write(
                    cache_values,
                )
            else:
                cached = super(
                    UslPaperlessMetadataMixin,
                    self.sudo().with_context(usl_documents_cache_write=True),
                ).create(cache_values)
            records |= cached.with_env(self.env)
        return records

    def write(self, values):
        if (
            self.env.context.get("usl_documents_cache_write")
            and self.env.su
        ):
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
            super(
                UslPaperlessMetadataMixin,
                record.sudo().with_context(usl_documents_cache_write=True),
            ).write(cache_values)
        return True

    def unlink(self):
        if (
            self.env.context.get("usl_documents_cache_write")
            and self.env.su
        ):
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
    def synchronize_catalog(self, client=None, payloads=None):
        self._require_manager()
        client = client or self._paperless()
        if payloads is None:
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
            # Paperless identifiers may be reused after a deliberate archive
            # reset.  Include inactive cache rows so the newly returned
            # metadata reactivates the stable Odoo record instead of violating
            # the unique Paperless identity constraint.
            record = self.sudo().with_context(active_test=False).search(
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
        self._require_manager()
        self.synchronize_catalog()
        return {"type": "ir.actions.client", "tag": "reload"}

    def _compute_accessible_document_count(self):
        counts = {record.id: 0 for record in self}
        documents = self.env["usl.document"].search(
            [
                (
                    "availability_state",
                    "not in",
                    ("trashed", "permanently_deleted"),
                ),
            ],
        )
        if self._paperless_kind == "tags":
            for document in documents:
                for tag_id in document.tag_ids.ids:
                    if tag_id in counts:
                        counts[tag_id] += 1
        else:
            field_name = {
                "correspondents": "correspondent_id",
                "document_types": "document_type_id",
            }[self._paperless_kind]
            for document in documents:
                metadata_id = document[field_name].id
                if metadata_id in counts:
                    counts[metadata_id] += 1
        for record in self:
            record.accessible_document_count = counts[record.id]

    def action_open_documents(self):
        """Open the native workspace with a removable native search facet."""
        self.ensure_one()
        field_by_kind = {
            "tags": "tag_ids",
            "correspondents": "correspondent_id",
            "document_types": "document_type_id",
        }
        field_name = field_by_kind[self._paperless_kind]
        action = self.env["ir.actions.actions"]._for_xml_id(
            "usl_documents.action_documents_workspace",
        )
        action["context"] = {
            **dict(self.env.context),
            f"search_default_{field_name}": self.ids,
        }
        action["params"] = {"initial_workspace": "all"}
        return action


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
        self._require_manager()
        client = client or self._paperless()
        payloads = client.list_metadata(self._paperless_kind)
        result = super().synchronize_catalog(client=client, payloads=payloads)
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
        groups="usl_documents.group_documents_manager",
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
        groups="usl_documents.group_documents_manager",
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
        protected = self.sudo()
        visible_ids = set(
            self.env["res.partner"].search(
                [("id", "in", protected.mapped("partner_id").ids)],
            ).ids,
        )
        for correspondent in self:
            protected_correspondent = correspondent.sudo()
            correspondent.partner_visible_id = (
                protected_correspondent.partner_id
                if protected_correspondent.partner_id.id in visible_ids
                else False
            )
            correspondent.partner_mapping_hidden = bool(
                protected_correspondent.partner_id
                and protected_correspondent.partner_id.id not in visible_ids,
            )

    def _inverse_partner_visible(self):
        for correspondent in self:
            if correspondent.partner_mapping_hidden:
                raise AccessError(
                    _(
                        "This correspondent is already mapped to a Contact outside "
                        "your accessible companies. Ask a Documents administrator "
                        "to review the mapping.",
                    ),
                )
            correspondent.partner_id = correspondent.partner_visible_id

    @api.model
    def _search_partner_visible(self, operator, value):
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
        return [("partner_id", operator, normalized)]

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
            self._check_visible_partner_value(values.get("rejected_partner_id"))
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
                    "to review the mapping.",
                ),
            )
        if "partner_visible_id" in values:
            values["partner_id"] = values.pop("partner_visible_id")
        if "partner_id" in values:
            self._check_visible_partner_value(values["partner_id"])
        if "rejected_partner_id" in values:
            self._check_visible_partner_value(values["rejected_partner_id"])
        return super().write(values)

    @api.depends("name", "partner_id", "rejected_partner_id")
    def _compute_suggested_partner(self):
        for correspondent in self:
            protected = correspondent.sudo()
            if protected.partner_id or not correspondent.name:
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
                if candidate and candidate != protected.rejected_partner_id
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

    @api.model
    def create_from_partner(self, partner_id):
        """Create or reuse a correspondent explicitly selected from Contacts."""
        partner = self.env["res.partner"].browse(int(partner_id or 0)).exists()
        if not partner:
            raise ValidationError(_("The selected Odoo Contact no longer exists."))
        partner.check_access("read")
        # The stored mapping is manager-only because it may point to a Contact
        # outside the caller's companies. Resolve it in a protected environment,
        # then return to the caller's environment and re-check normal read access.
        protected_correspondent = self.sudo().search(
            [("partner_visible_id", "=", partner.id), ("active", "=", True)],
            limit=1,
        )
        correspondent = self.browse(protected_correspondent.id).exists()
        if correspondent:
            correspondent.check_access("read")
        if not correspondent:
            protected_matches = self.sudo().search(
                [
                    ("name", "=ilike", partner.display_name),
                    ("partner_id", "=", False),
                    ("active", "=", True),
                ],
                limit=2,
            )
            exact_matches = self.browse(protected_matches.ids).exists()
            exact_matches.check_access("read")
            if len(exact_matches) == 1:
                correspondent = exact_matches
                correspondent.write({"partner_visible_id": partner.id})
            else:
                correspondent = self.create(
                    {
                        "name": partner.display_name,
                        "partner_visible_id": partner.id,
                        "matching_algorithm": "0",
                        "is_insensitive": True,
                    },
                )
        visible_partner = correspondent.partner_visible_id
        return {
            "id": correspondent.id,
            "name": (
                visible_partner.display_name
                if visible_partner
                else correspondent.name
            ),
            "archive_name": correspondent.name,
            "partner_id": visible_partner.id,
        }


class UslPaperlessDocumentType(models.Model):
    _name = "usl.paperless.document.type"
    _description = "Paperless Document Type"
    _inherit = "usl.paperless.metadata.mixin"

    _paperless_kind = "document_types"
