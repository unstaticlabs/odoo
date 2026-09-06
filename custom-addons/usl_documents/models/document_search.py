"""Authorized lexical, semantic and remote search resolution for documents."""

import logging
import math

from odoo import (
    _,
    api,
    models,
)
from odoo.exceptions import ValidationError
from odoo.fields import Domain

from .paperless_client import PaperlessError

_logger = logging.getLogger(__name__)


class UslDocument(models.Model):
    _inherit = "usl.document"

    @api.model
    def _accessible_role_document_ids(self, roles):
        """Resolve role visibility through the target record's native ACLs."""
        links = self.env["usl.document.link"].sudo().search(
            [
                ("document_id", "in", self._search([])),
                ("active", "=", True),
                ("document_role", "in", list(roles)),
            ],
        )
        return self._accessible_link_document_ids(links)

    @api.model
    def _accessible_project_document_ids(self):
        links = self.env["usl.document.link"].sudo().search(
            [
                ("document_id", "in", self._search([])),
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
