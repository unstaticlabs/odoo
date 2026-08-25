import re

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
_MCP_SCOPE_CHUNK = 500
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
        company_id=None,
        tag_ids=None,
        correspondent_id=None,
        document_type_id=None,
        date_from=None,
        date_to=None,
        background_mode="include",
    ):
        self.check_access("read")
        if background_mode not in ("include", "exclude", "only"):
            raise ValidationError(_("Unsupported archive visibility filter."))
        domain = [
            (
                "availability_state",
                "not in",
                ("trashed", "permanently_deleted"),
            ),
        ]
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
        for value, operator in ((date_from, ">="), (date_to, "<=")):
            if value:
                try:
                    parsed = fields.Date.to_date(value)
                except (TypeError, ValueError) as error:
                    raise ValidationError(_("Invalid date filter.")) from error
                domain.append(("document_date", operator, parsed))
        return self.search(domain, order="id")

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
            "preview_path": f"/usl_documents/{document.id}/preview",
            "download_path": f"/usl_documents/{document.id}/download?original=1",
            "paperless_url": document.paperless_url or False,
            "excerpt": self._mcp_excerpt(excerpt),
            "provenance": provenance or [],
        }

    @api.model
    def _mcp_lexical_hits(self, query, document_ids, *, window):
        scope = sorted({int(document_id) for document_id in document_ids})
        if not scope:
            return [], {}, False
        scope_set = set(scope)
        rankings = []
        hit_by_id = {}
        truncated = False
        for offset in range(0, len(scope), _MCP_SCOPE_CHUNK):
            filters = {
                "id__in": ",".join(
                    str(document_id)
                    for document_id in scope[offset : offset + _MCP_SCOPE_CHUNK]
                ),
            }
            payload = self._paperless().search(
                query,
                page=1,
                page_size=window,
                filters=filters,
                full_text=False,
            )
            results = payload.get("results") or []
            ranking = []
            for item in results:
                document_id = int(item["id"])
                if document_id not in scope_set:
                    continue
                ranking.append(document_id)
                hit_by_id.setdefault(document_id, item)
            rankings.append(ranking)
            truncated = truncated or bool(payload.get("next")) or int(
                payload.get("count") or len(results),
            ) > len(results)

        merged = []
        seen = set()
        for rank in range(max(map(len, rankings), default=0)):
            for ranking in rankings:
                if rank >= len(ranking) or ranking[rank] in seen:
                    continue
                merged.append(ranking[rank])
                seen.add(ranking[rank])
        return merged[:window], hit_by_id, truncated or len(merged) > window

    @api.model
    def mcp_search(
        self,
        query,
        *,
        mode="hybrid",
        limit=10,
        offset=0,
        company_id=None,
        tag_ids=None,
        correspondent_id=None,
        document_type_id=None,
        date_from=None,
        date_to=None,
        background_mode="include",
    ):
        query = str(query or "").strip()
        limit = int(limit)
        offset = int(offset)
        if not query or len(query) > _MCP_MAX_QUERY_LENGTH:
            raise ValidationError(_("Invalid document search query."))
        if mode not in ("hybrid", "exact", "semantic"):
            raise ValidationError(_("Unsupported archive search mode."))
        if not 1 <= limit <= _MCP_MAX_RESULTS or not 0 <= offset <= _MCP_MAX_OFFSET:
            raise ValidationError(_("Invalid document search pagination."))
        if offset + limit > _MCP_SEARCH_WINDOW:
            raise ValidationError(_("Invalid document search pagination."))

        candidates = self._mcp_candidate_documents(
            company_id=company_id,
            tag_ids=tag_ids,
            correspondent_id=correspondent_id,
            document_type_id=document_type_id,
            date_from=date_from,
            date_to=date_to,
            background_mode=background_mode,
        )
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
                for document_id in (
                    (
                        self._custom_field_search_ids(
                            query,
                            document_ids=binary_scope,
                        )
                        if binary_scope
                        else []
                    )
                    + self._accessible_local_text_ids(query)
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
                query,
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
                    excerpt=lexical_hit.get("content") or semantic_hit.get("excerpt"),
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
        company_id=None,
        tag_ids=None,
        correspondent_id=None,
        document_type_id=None,
        date_from=None,
        date_to=None,
        background_mode="include",
    ):
        source = self._mcp_visible_document(document_id)
        try:
            archive_available = source._check_archive_binary_access()
        except AccessError as error:
            raise AccessError(_("The document is unavailable.")) from error
        if not archive_available:
            raise AccessError(_("The document is unavailable."))
        limit = int(limit)
        if not 1 <= limit <= _MCP_MAX_RESULTS:
            raise ValidationError(_("Invalid document search pagination."))
        candidates = self._mcp_binary_documents(
            self._mcp_candidate_documents(
                company_id=company_id,
                tag_ids=tag_ids,
                correspondent_id=correspondent_id,
                document_type_id=document_type_id,
                date_from=date_from,
                date_to=date_to,
                background_mode=background_mode,
            ),
        ).filtered(lambda item: item.id != source.id)
        requested_scope = sorted(
            set(candidates.mapped("paperless_id")) | {source.paperless_id},
        )
        payload = source._paperless().semantic_search_by_document(
            source.paperless_id,
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
            if not document or document.id == source.id:
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
            "source_document_id": source.id,
            "results": results,
            "count": len(results),
            "warnings": payload.get("warnings") or [],
        }

    @api.model
    def mcp_get_versions(self, document_id):
        document = self._mcp_visible_document(document_id)
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
                    "preview_path": (
                        f"/usl_documents/{document.id}/preview"
                        f"?version={version.paperless_version_id}"
                    ),
                    "download_path": (
                        f"/usl_documents/{document.id}/download"
                        f"?original=1&version={version.paperless_version_id}"
                    ),
                }
                for version in versions
            ],
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
