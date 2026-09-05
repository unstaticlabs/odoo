import json
import re
from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, ValidationError

from .paperless_client import PaperlessError

_WHITESPACE = re.compile(r"\s+")
_MCP_MAX_QUERY_LENGTH = 2048
_MCP_MAX_RESULTS = 25
_MCP_MAX_OFFSET = 49
_MCP_MAX_CONTENT_LENGTH = 8000
_MCP_MAX_CONTENT_OFFSET = 1_000_000
_MCP_EXCERPT_LENGTH = 500
_MCP_SEARCH_WINDOW = 50


class UslDocumentMcp(models.Model):
    _inherit = "usl.document"

    @api.model
    def _mcp_visible_document(self, document_id):
        self.check_access("read")
        try:
            document_id = int(document_id)
        except (TypeError, ValueError) as error:
            raise AccessError(_("The document is unavailable.")) from error
        document = self.search([("id", "=", document_id)], limit=1)
        if not document:
            raise AccessError(_("The document is unavailable."))
        return document

    @api.model
    def _mcp_candidate_documents(
        self,
        *,
        saved_view=None,
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
        background_mode="include",
    ):
        self.check_access("read")
        if background_mode not in ("include", "exclude", "only"):
            raise ValidationError(_("Unsupported archive visibility filter."))
        domain = list(saved_view.document_domain()) if saved_view else []
        domain.append(
            (
                "availability_state",
                "not in",
                ("trashed", "permanently_deleted"),
            ),
        )
        if background_mode == "exclude":
            domain.append(("is_prominent", "=", True))
        elif background_mode == "only":
            domain.append(("is_prominent", "=", False))
        if company_id:
            domain.append(("company_id", "=", int(company_id)))
        normalized_tag_ids = sorted({int(tag_id) for tag_id in tag_ids or []})
        if len(normalized_tag_ids) > 100 or any(tag_id <= 0 for tag_id in normalized_tag_ids):
            raise ValidationError(_("Invalid document tag filter."))
        if normalized_tag_ids:
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
            if source not in dict(self._fields["source"].selection):
                raise ValidationError(_("Invalid document source filter."))
            domain.append(("source", "=", source))
        if confidentiality:
            if confidentiality not in dict(self._fields["confidentiality"].selection):
                raise ValidationError(_("Invalid confidentiality filter."))
            domain.append(("confidentiality", "=", confidentiality))
        if review_state:
            if review_state not in dict(self._fields["review_state"].selection):
                raise ValidationError(_("Invalid review-state filter."))
            domain.append(("review_state", "=", review_state))
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
            domain.append(
                ("linked_record_ref", "=", f"{linked_model}:{int(linked_id)}"),
            )
        elif linked_state:
            if linked_state not in ("linked", "unlinked"):
                raise ValidationError(_("Invalid linked-document filter."))
            domain.append(
                ("has_linked_record", "=", linked_state == "linked"),
            )
        return self.search(
            domain,
            order="document_date desc, archive_added_at desc, id desc",
        )

    @api.model
    def _mcp_saved_view(self, saved_view_id):
        self.check_access("read")
        if not saved_view_id:
            return self.env["usl.document.smart.view"]
        try:
            saved_view_id = int(saved_view_id)
        except (TypeError, ValueError) as error:
            raise AccessError(_("The saved view is unavailable.")) from error
        saved_view = (
            self.env["usl.document.smart.view"]
            .accessible_views()
            .filtered(lambda item: item.id == saved_view_id)[:1]
        )
        if not saved_view:
            raise AccessError(_("The saved view is unavailable."))
        return saved_view

    @api.model
    def _mcp_saved_view_filters(self, saved_view):
        if not saved_view or saved_view.system_rule != "saved":
            return {}
        try:
            filters = json.loads(saved_view.filter_json or "{}")
        except (TypeError, ValueError) as error:
            raise ValidationError(_("Invalid saved view filters.")) from error
        if not isinstance(filters, dict):
            raise ValidationError(_("Invalid saved view filters."))
        return filters

    @api.model
    def _mcp_saved_view_values(self, saved_view):
        workspace = saved_view.workspace_values()
        return {
            "id": saved_view.id,
            "key": saved_view.key or f"view:{saved_view.id}",
            "name": saved_view.name,
            "scope": saved_view.scope,
            "system_rule": saved_view.system_rule,
            "archive_native": saved_view.archive_native,
            "needs_attention": saved_view.paperless_sync_state == "failed",
            "filters": workspace.get("filters") or {},
            "tags": [
                {"id": item.id, "name": item.name}
                for item in saved_view.tag_ids.filtered("active")
            ],
            "correspondents": [
                {"id": item.id, "name": item.name}
                for item in saved_view.correspondent_ids.filtered("active")
            ],
            "document_types": [
                {"id": item.id, "name": item.name}
                for item in saved_view.document_type_ids.filtered("active")
            ],
            "quick_filters": [
                {
                    "id": item["id"],
                    "key": item["key"],
                    "name": item["name"],
                    "kind": item["kind"],
                }
                for item in workspace.get("quick_filters") or []
            ],
        }

    @api.model
    def _mcp_binary_documents(self, documents):
        accessible = self.browse()
        for document in documents.filtered(
            lambda item: (
                item.availability_state == "available"
                and item.permission_sync_state == "synchronized"
            ),
        ):
            try:
                if document._check_archive_binary_access():
                    accessible |= document
            except AccessError:
                continue
        return accessible

    @staticmethod
    def _mcp_excerpt(value):
        normalized = _WHITESPACE.sub(" ", str(value or "")).strip()
        if len(normalized) <= _MCP_EXCERPT_LENGTH:
            return normalized
        return normalized[: _MCP_EXCERPT_LENGTH - 1].rstrip() + "…"

    @api.model
    def _mcp_document_values(self, document, *, excerpt="", provenance=None):
        active_links = document._accessible_active_links()
        binary_available = False
        try:
            binary_available = bool(document._check_archive_binary_access())
        except AccessError:
            pass
        current_version = document.version_ids.filtered("is_current")[:1]
        available_variants = (
            ["original"]
            + (["archive"] if current_version.archive_checksum else [])
            if binary_available and current_version
            else []
        )
        return {
            "id": document.id,
            "name": document.name,
            "document_date": (
                fields.Date.to_string(document.document_date)
                if document.document_date
                else False
            ),
            "archive_added_at": (
                fields.Datetime.to_string(document.archive_added_at)
                if document.archive_added_at
                else False
            ),
            "company": (
                {
                    "id": document.company_id.id,
                    "name": document.company_id.display_name,
                }
                if document.company_id
                else False
            ),
            "confidentiality": document.confidentiality,
            "review_state": document.review_state,
            "availability_state": document.availability_state,
            "correspondent": (
                {
                    "id": document.correspondent_id.id,
                    "name": document.correspondent_id.name,
                }
                if document.correspondent_id
                else False
            ),
            "document_type": (
                {
                    "id": document.document_type_id.id,
                    "name": document.document_type_id.name,
                }
                if document.document_type_id
                else False
            ),
            "tags": [
                {"id": tag.id, "name": tag.name}
                for tag in document.tag_ids.filtered("active")
            ],
            "filename": document.original_filename,
            "mime_type": document.mime_type,
            "source": document.source,
            "intake_role": document.intake_role,
            "current_version": document.current_version_label,
            "version_count": len(document.version_ids),
            "link_count": len(active_links),
            "web_path": f"/odoo/usl.document/{document.id}",
            "binary_available": binary_available,
            "available_variants": available_variants,
            "materialization_required": binary_available,
            "excerpt": self._mcp_excerpt(excerpt),
            "provenance": provenance or [],
        }

    @api.model
    def _mcp_lexical_hits(self, query, document_ids, *, window):
        scope = sorted({int(document_id) for document_id in document_ids})
        if not scope:
            return [], {}, False
        scope_set = set(scope)
        payload = self._paperless().scoped_search(
            query,
            document_ids=scope,
            limit=window,
            fields="all",
            include_excerpt=True,
        )
        results = [
            item
            for item in payload.get("results") or []
            if int(item["id"]) in scope_set
        ]
        ids = [int(item["id"]) for item in results]
        return ids, {int(item["id"]): item for item in results}, bool(
            payload.get("truncated"),
        )

    @api.model
    def mcp_search(
        self,
        query="",
        *,
        mode="hybrid",
        limit=10,
        offset=0,
        saved_view_id=None,
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
        background_mode="include",
    ):
        saved_view = self._mcp_saved_view(saved_view_id)
        saved_filters = self._mcp_saved_view_filters(saved_view)

        def saved_default(value, key):
            return value if value not in (None, "", []) else saved_filters.get(key)

        query = str(query or saved_filters.get("query") or "").strip()
        limit = int(limit)
        offset = int(offset)
        if (not query and not saved_view) or len(query) > _MCP_MAX_QUERY_LENGTH:
            raise ValidationError(_("Invalid document search query."))
        if mode not in ("hybrid", "exact", "semantic"):
            raise ValidationError(_("Unsupported archive search mode."))
        if not 1 <= limit <= _MCP_MAX_RESULTS or not 0 <= offset <= _MCP_MAX_OFFSET:
            raise ValidationError(_("Invalid document search pagination."))
        if offset + limit > _MCP_SEARCH_WINDOW:
            raise ValidationError(_("Invalid document search pagination."))

        linked_record = saved_filters.get("linked_record")
        if linked_record and not (linked_model or linked_id):
            try:
                linked_model, linked_id = linked_record.split(":", 1)
            except (AttributeError, ValueError) as error:
                raise ValidationError(_("Invalid linked-record filter.")) from error
        candidates = self._mcp_candidate_documents(
            saved_view=saved_view,
            company_id=saved_default(company_id, "company_id"),
            tag_ids=saved_default(tag_ids, "tag_ids"),
            correspondent_id=saved_default(correspondent_id, "correspondent_id"),
            document_type_id=saved_default(document_type_id, "document_type_id"),
            date_from=saved_default(date_from, "date_from"),
            date_to=saved_default(date_to, "date_to"),
            added_from=saved_default(added_from, "added_from"),
            added_to=saved_default(added_to, "added_to"),
            source=saved_default(source, "source"),
            confidentiality=saved_default(confidentiality, "confidentiality"),
            review_state=saved_default(review_state, "review_state"),
            linked_state=saved_default(linked_state, "linked_state"),
            linked_model=linked_model,
            linked_id=linked_id,
            background_mode=background_mode,
        )
        if saved_view and saved_view.system_rule == "archive_search" and not query:
            candidates = self.browse()
        saved_view_values = (
            self._mcp_saved_view_values(saved_view) if saved_view else False
        )
        if not query:
            page = candidates[offset : offset + limit]
            return {
                "results": [
                    self._mcp_document_values(
                        document,
                        provenance=[{"source": "odoo_saved_view"}],
                    )
                    for document in page
                ],
                "count": len(page),
                "offset": offset,
                "limit": limit,
                "has_more": len(candidates) > offset + len(page),
                "truncated": False,
                "warnings": [],
                "mode": "browse",
                "query": "",
                "saved_view": saved_view_values,
            }
        binary_candidates = self._mcp_binary_documents(candidates)
        binary_scope = binary_candidates.mapped("paperless_id")
        candidate_paperless_ids = set(candidates.mapped("paperless_id"))
        window = offset + limit
        lexical_ids = []
        lexical_hits = {}
        truncated = False
        warnings = []
        local_ids = []
        if mode != "semantic":
            lexical_ids, lexical_hits, truncated = self._mcp_lexical_hits(
                query,
                binary_scope,
                window=max(window, min(_MCP_SEARCH_WINDOW, limit * 2)),
            )
            local_ids = [
                document_id
                for document_id in self._accessible_local_text_ids(
                    query,
                    documents=candidates,
                )
                if document_id in candidate_paperless_ids
            ]
            for document_id in local_ids:
                if document_id not in lexical_ids:
                    lexical_ids.append(document_id)
            if truncated:
                warnings.append(
                    {
                        "code": "lexical_truncated",
                        "message": _("Exact archive search reached its bounded result window."),
                    },
                )

        semantic_ids = []
        semantic_hits = {}
        if mode != "exact":
            try:
                payload = (
                    self._paperless().semantic_search(
                        query,
                        document_ids=binary_scope,
                        limit=max(window, limit),
                    )
                    if binary_scope
                    else {"results": [], "warnings": []}
                )
                warnings.extend(payload.get("warnings") or [])
                allowed = set(binary_scope)
                for item in payload.get("results") or []:
                    document_id = int(item["id"])
                    if document_id not in allowed:
                        continue
                    semantic_ids.append(document_id)
                    semantic_hits[document_id] = item
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
            ranked_ids = lexical_ids
        elif mode == "semantic":
            ranked_ids = semantic_ids
        else:
            ranked_ids = self._fuse_search_rankings(
                lexical_ids,
                semantic_ids,
            )
        documents_by_paperless = {
            document.paperless_id: document for document in candidates
        }
        page_ids = [
            document_id
            for document_id in ranked_ids
            if document_id in documents_by_paperless
        ][offset : offset + limit]
        results = []
        lexical_rank = {item: rank for rank, item in enumerate(lexical_ids, start=1)}
        semantic_rank = {item: rank for rank, item in enumerate(semantic_ids, start=1)}
        local_set = set(local_ids)
        for document_id in page_ids:
            provenance = []
            if document_id in lexical_rank:
                provenance.append(
                    {
                        "source": (
                            "odoo_metadata"
                            if document_id in local_set and document_id not in lexical_hits
                            else "paperless_lexical"
                        ),
                        "rank": lexical_rank[document_id],
                    },
                )
            if document_id in semantic_rank:
                semantic_hit = semantic_hits[document_id]
                provenance.append(
                    {
                        "source": "paperless_semantic",
                        "rank": semantic_rank[document_id],
                        "similarity": float(semantic_hit.get("similarity") or 0),
                    },
                )
            lexical_hit = lexical_hits.get(document_id) or {}
            semantic_hit = semantic_hits.get(document_id) or {}
            results.append(
                self._mcp_document_values(
                    documents_by_paperless[document_id],
                    excerpt=lexical_hit.get("excerpt") or semantic_hit.get("excerpt"),
                    provenance=provenance,
                ),
            )
        return {
            "results": results,
            "count": len(results),
            "offset": offset,
            "limit": limit,
            "has_more": len(ranked_ids) > offset + len(results),
            "truncated": truncated,
            "warnings": warnings,
            "mode": mode,
            "query": query,
            "saved_view": saved_view_values,
        }

    @api.model
    def mcp_get(self, document_id):
        document = self._mcp_visible_document(document_id)
        return self._mcp_document_values(document)

    @api.model
    def mcp_get_content(self, document_id, *, offset=0, limit=4000):
        document = self._mcp_visible_document(document_id)
        try:
            archive_available = document._check_archive_binary_access()
        except AccessError as error:
            raise AccessError(_("The document is unavailable.")) from error
        if not archive_available:
            raise AccessError(_("The document is unavailable."))
        offset = int(offset)
        limit = int(limit)
        if (
            not 0 <= offset <= _MCP_MAX_CONTENT_OFFSET
            or not 1 <= limit <= _MCP_MAX_CONTENT_LENGTH
        ):
            raise ValidationError(_("Invalid document content pagination."))
        payload = document._paperless().get_document(document.paperless_id)
        content = str(payload.get("content") or "")
        page = content[offset : offset + limit]
        return {
            "document_id": document.id,
            "content": page,
            "offset": offset,
            "limit": limit,
            "next_offset": offset + len(page) if offset + len(page) < len(content) else False,
            "has_more": offset + len(page) < len(content),
            "total_characters": len(content),
        }

    @api.model
    def mcp_find_similar(
        self,
        document_id,
        *,
        limit=10,
        saved_view_id=None,
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
        background_mode="include",
    ):
        source_document = self._mcp_visible_document(document_id)
        try:
            archive_available = source_document._check_archive_binary_access()
        except AccessError as error:
            raise AccessError(_("The document is unavailable.")) from error
        if not archive_available:
            raise AccessError(_("The document is unavailable."))
        limit = int(limit)
        if not 1 <= limit <= _MCP_MAX_RESULTS:
            raise ValidationError(_("Invalid document search pagination."))
        saved_view = self._mcp_saved_view(saved_view_id)
        saved_filters = self._mcp_saved_view_filters(saved_view)

        def saved_default(value, key):
            return value if value not in (None, "", []) else saved_filters.get(key)

        linked_record = saved_filters.get("linked_record")
        if linked_record and not (linked_model or linked_id):
            try:
                linked_model, linked_id = linked_record.split(":", 1)
            except (AttributeError, ValueError) as error:
                raise ValidationError(_("Invalid linked-record filter.")) from error
        candidates = self._mcp_binary_documents(
            self._mcp_candidate_documents(
                saved_view=saved_view,
                company_id=saved_default(company_id, "company_id"),
                tag_ids=saved_default(tag_ids, "tag_ids"),
                correspondent_id=saved_default(
                    correspondent_id,
                    "correspondent_id",
                ),
                document_type_id=saved_default(
                    document_type_id,
                    "document_type_id",
                ),
                date_from=saved_default(date_from, "date_from"),
                date_to=saved_default(date_to, "date_to"),
                added_from=saved_default(added_from, "added_from"),
                added_to=saved_default(added_to, "added_to"),
                source=saved_default(source, "source"),
                confidentiality=saved_default(
                    confidentiality,
                    "confidentiality",
                ),
                review_state=saved_default(review_state, "review_state"),
                linked_state=saved_default(linked_state, "linked_state"),
                linked_model=linked_model,
                linked_id=linked_id,
                background_mode=background_mode,
            ),
        ).filtered(lambda item: item.id != source_document.id)
        requested_scope = sorted(
            set(candidates.mapped("paperless_id")) | {source_document.paperless_id},
        )
        payload = source_document._paperless().semantic_search_by_document(
            source_document.paperless_id,
            document_ids=requested_scope,
            limit=limit,
        )
        candidates_by_paperless = {
            document.paperless_id: document for document in candidates
        }
        results = []
        for item in payload.get("results") or []:
            paperless_id = int(item["id"])
            document = candidates_by_paperless.get(paperless_id)
            if not document or document.id == source_document.id:
                continue
            results.append(
                self._mcp_document_values(
                    document,
                    excerpt=item.get("excerpt"),
                    provenance=[
                        {
                            "source": "paperless_similar",
                            "rank": len(results) + 1,
                            "similarity": float(item.get("similarity") or 0),
                        },
                    ],
                ),
            )
            if len(results) >= limit:
                break
        return {
            "source_document_id": source_document.id,
            "results": results,
            "count": len(results),
            "warnings": payload.get("warnings") or [],
            "saved_view": (
                self._mcp_saved_view_values(saved_view) if saved_view else False
            ),
        }

    @api.model
    def mcp_get_versions(self, document_id):
        document = self._mcp_visible_document(document_id)
        binary_available = bool(self._mcp_binary_documents(document))
        versions = document.version_ids.sorted(
            key=lambda item: (item.is_current, item.created_at or fields.Datetime.now()),
            reverse=True,
        )
        return {
            "document_id": document.id,
            "versions": [
                {
                    "id": version.id,
                    "version_id": version.paperless_version_id,
                    "label": version.label,
                    "created_at": (
                        fields.Datetime.to_string(version.created_at)
                        if version.created_at
                        else False
                    ),
                    "filename": version.original_filename,
                    "mime_type": version.mime_type,
                    "page_count": version.page_count,
                    "is_current": version.is_current,
                    "is_received_original": version.is_received_original,
                    "source": version.source,
                    "binary_available": binary_available,
                    "available_variants": (
                        ["original"] + (["archive"] if version.archive_checksum else [])
                        if binary_available
                        else []
                    ),
                    "materialization_required": binary_available,
                }
                for version in versions
            ],
        }

    @api.model
    def mcp_list_saved_views(self, *, query="", scope="all", limit=100, offset=0):
        self.check_access("read")
        limit = int(limit)
        offset = int(offset)
        if not 1 <= limit <= 100 or not 0 <= offset <= 1000:
            raise ValidationError(_("Invalid saved view pagination."))
        if scope not in ("all", "shared", "personal"):
            raise ValidationError(_("Invalid saved view scope."))
        views = self.env["usl.document.smart.view"].accessible_views()
        if scope != "all":
            views = views.filtered(lambda item: item.scope == scope)
        normalized_query = str(query or "").strip().casefold()[:200]
        if normalized_query:
            views = views.filtered(
                lambda item: normalized_query in item.name.casefold(),
            )
        views = views.sorted(
            key=lambda item: (item.scope, item.sequence, item.name.casefold(), item.id),
        )
        page = views[offset : offset + limit]
        return {
            "results": [self._mcp_saved_view_values(item) for item in page],
            "offset": offset,
            "limit": limit,
            "has_more": len(views) > offset + len(page),
        }

    @api.model
    def _mcp_catalog(self, model_name, *, query="", limit=100, offset=0):
        limit = int(limit)
        offset = int(offset)
        if not 1 <= limit <= 100 or not 0 <= offset <= 1000:
            raise ValidationError(_("Invalid catalog pagination."))
        catalog = self.env[model_name]
        catalog.check_access("read")
        domain = [("active", "=", True)]
        if query:
            domain.append(("name", "ilike", str(query)[:200]))
        records = catalog.search(domain, order="name, id", limit=limit + 1, offset=offset)
        return {
            "results": [
                {"id": item.id, "name": item.name}
                for item in records[:limit]
            ],
            "offset": offset,
            "limit": limit,
            "has_more": len(records) > limit,
        }

    @api.model
    def mcp_list_tags(self, *, query="", limit=100, offset=0):
        return self._mcp_catalog(
            "usl.paperless.tag",
            query=query,
            limit=limit,
            offset=offset,
        )

    @api.model
    def mcp_list_correspondents(self, *, query="", limit=100, offset=0):
        return self._mcp_catalog(
            "usl.paperless.correspondent",
            query=query,
            limit=limit,
            offset=offset,
        )

    @api.model
    def mcp_list_types(self, *, query="", limit=100, offset=0):
        return self._mcp_catalog(
            "usl.paperless.document.type",
            query=query,
            limit=limit,
            offset=offset,
        )

    @api.model
    def mcp_get_links(self, document_id):
        document = self._mcp_visible_document(document_id)
        return {
            "document_id": document.id,
            "links": [
                {
                    "id": link.id,
                    "name": link.record_name,
                    "model": link.res_model,
                    "record_id": link.res_id,
                    "company": link.company_id.display_name,
                    "document_role": link.document_role,
                    "linked_at": fields.Datetime.to_string(link.linked_at),
                    "version_id": link.version_id or False,
                }
                for link in document._accessible_active_links()
            ],
        }
