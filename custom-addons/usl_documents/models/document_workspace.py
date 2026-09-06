"""Workspace and detail payloads consumed by the Documents browser client."""

import json
import logging
from datetime import timedelta

from odoo import (
    _,
    api,
    fields,
    models,
)
from odoo.exceptions import AccessError, ValidationError
from odoo.fields import Domain

from .document import CONFIDENTIALITIES
from .paperless_client import PaperlessError

_logger = logging.getLogger(__name__)


class UslDocument(models.Model):
    _inherit = "usl.document"

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
        smart_views, selected_view = self._workspace_selected_view(workspace)
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
        search = self._workspace_broad_search(
            search_domain,
            search_mode,
            scope=authorized_scope,
            authorized_documents=authorized_documents,
            local_documents=accessible_documents,
        )
        try:
            native_domain = self._resolve_remote_search_domain(
                Domain(search_domain or []),
                resolved_ids=search["resolved_ids"],
                authorized_scope=authorized_scope,
                authorized_documents=authorized_documents,
                local_documents=accessible_documents,
            )
        except PaperlessError as error:
            return self._workspace_degraded(error)
        if shortcut_tag_ids:
            domain.append(
                ("tag_ids", "in", [int(tag_id) for tag_id in shortcut_tag_ids]),
            )
        filters = {
            "query": query,
            "company_id": company_id,
            "tag_ids": tag_ids,
            "correspondent_id": correspondent_id,
            "document_type_id": document_type_id,
            "date_from": date_from,
            "date_to": date_to,
            "added_from": added_from,
            "added_to": added_to,
            "source": source,
            "confidentiality": confidentiality,
            "review_state": review_state,
            "linked_state": linked_state,
            "linked_model": linked_model,
            "linked_id": linked_id,
            "mapped_partner_id": mapped_partner_id,
            "paperless_id": paperless_id,
            "custom_field_id": custom_field_id,
            "custom_field_value": custom_field_value,
        }
        if selected_view and selected_view.system_rule == "saved":
            filters, sort = self._workspace_saved_view_filters(
                selected_view, filters, sort,
            )
        query = filters["query"]
        archive_search_requested = bool(search_domain) or any(filters.values())
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
        domain.extend(self._workspace_filter_domain(filters, selected_view))
        custom_fields = json.loads(
            self.env["ir.config_parameter"].sudo().get_str(
                "usl_documents.paperless_custom_fields",
                "[]",
            ),
        )
        paperless_filters = self._workspace_custom_field_filters(
            custom_fields,
            filters["custom_field_id"],
            filters["custom_field_value"],
        )
        truncated = search["truncated"]
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
                return self._workspace_degraded(error)
        domain = Domain.AND([Domain(domain), native_domain])
        order = self._workspace_order(order_by, sort)
        try:
            documents, count, ordered_ids = self._workspace_page(
                domain,
                order,
                page,
                page_size,
                sort=sort,
                order_by=order_by,
                query=query,
                search=search,
            )
        except PaperlessError as error:
            return self._workspace_degraded(error)
        link_facets = []
        visible_links_by_document = None
        if include_workspace_metadata:
            link_facets, visible_links_by_document = self._workspace_link_facets(
                accessible_documents,
            )
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
            search["semantic_scores"],
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
            "warnings": search["warnings"],
            "search_mode": search_mode,
            "semantic_scores_loaded": search["semantic_scores_loaded"],
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
        result.update(
            self._workspace_catalog_values(custom_fields, smart_views, link_facets),
        )
        return result

    @api.model
    def _workspace_degraded(self, error):
        """Return the empty workspace payload used when Paperless is unavailable."""
        return {
            "documents": [],
            "count": 0,
            "degraded": True,
            "error": str(error),
        }

    @api.model
    def _workspace_selected_view(self, workspace):
        """Return the accessible smart views and the view the workspace key selects."""
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
        return smart_views, selected_view

    @api.model
    def _workspace_broad_search(
        self,
        search_domain,
        search_mode,
        *,
        scope,
        authorized_documents,
        local_documents,
    ):
        """Resolve the free-text terms of a search domain through hybrid search."""
        resolved_ids = {}
        relevance_paperless_ids = []
        semantic_scores = {}
        semantic_scores_loaded = False
        warnings = []
        truncated = False
        for field_name, term in self._broad_search_terms(search_domain):
            (
                ids,
                term_truncated,
                term_warnings,
                term_semantic_scores,
                term_semantic_scores_loaded,
            ) = self._hybrid_search_ids(
                term,
                mode="semantic" if field_name == "semantic_text" else search_mode,
                scope=scope,
                authorized_documents=authorized_documents,
                local_documents=local_documents,
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
            warnings.extend(term_warnings)
            for paperless_document_id in ids:
                if paperless_document_id not in relevance_paperless_ids:
                    relevance_paperless_ids.append(paperless_document_id)
        return {
            "resolved_ids": resolved_ids,
            "relevance_paperless_ids": relevance_paperless_ids,
            "semantic_scores": semantic_scores,
            "semantic_scores_loaded": semantic_scores_loaded,
            "warnings": warnings,
            "truncated": truncated,
        }

    @api.model
    def _workspace_saved_view_filters(self, selected_view, filters, sort):
        """Fill the filters a saved view stores when the request leaves them empty."""
        saved = json.loads(selected_view.filter_json or "{}")
        filters = dict(filters)
        filters["query"] = filters["query"] or saved.get("query", "")
        for key in (
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
        ):
            filters[key] = filters[key] or saved.get(key)
        linked_record = saved.get("linked_record")
        if linked_record and not (filters["linked_model"] or filters["linked_id"]):
            filters["linked_model"], filters["linked_id"] = linked_record.split(":", 1)
        return filters, saved.get("sort") or sort

    @api.model
    def _workspace_filter_domain(self, filters, selected_view):
        """Translate validated workspace filters into document domain terms."""
        domain = []
        if filters["company_id"]:
            domain.append(("company_id", "=", int(filters["company_id"])))
        if filters["paperless_id"]:
            domain.append(("paperless_id", "=", int(filters["paperless_id"])))
        if filters["tag_ids"]:
            normalized_tag_ids = [int(tag_id) for tag_id in filters["tag_ids"]]
            domain.append(("tag_ids", "in", normalized_tag_ids))
        if filters["correspondent_id"]:
            domain.append(("correspondent_id", "=", int(filters["correspondent_id"])))
        if filters["document_type_id"]:
            domain.append(("document_type_id", "=", int(filters["document_type_id"])))
        added_to = filters["added_to"]
        for value, operator, field_name in (
            (filters["date_from"], ">=", "document_date"),
            (filters["date_to"], "<=", "document_date"),
            (filters["added_from"], ">=", "archive_added_at"),
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
        source = filters["source"]
        if source:
            valid_sources = dict(self._fields["source"].selection)
            if source not in valid_sources:
                raise ValidationError(_("Invalid document source filter."))
            domain.append(("source", "=", source))
        confidentiality = filters["confidentiality"]
        if confidentiality:
            if confidentiality not in dict(CONFIDENTIALITIES):
                raise ValidationError(_("Invalid confidentiality filter."))
            domain.append(("confidentiality", "=", confidentiality))
        review_state = filters["review_state"]
        if review_state:
            if review_state not in ("needs_attention", "classified", "reviewed"):
                raise ValidationError(_("Invalid review-state filter."))
            domain.append(("review_state", "=", review_state))
        mapped_partner = self.env["res.partner"]
        if filters["mapped_partner_id"]:
            mapped_partner = self.env["res.partner"].browse(
                int(filters["mapped_partner_id"]),
            ).exists()
            if not mapped_partner:
                raise ValidationError(_("Invalid Contact filter."))
            mapped_partner.check_access("read")
        linked_model = filters["linked_model"]
        linked_id = filters["linked_id"]
        linked_state = filters["linked_state"]
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
        return domain

    @api.model
    def _workspace_custom_field_filters(
        self, custom_fields, custom_field_id, custom_field_value,
    ):
        """Return the Paperless custom-field query for a workspace filter."""
        if not (custom_field_id or custom_field_value):
            return {}
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
        return {
            "custom_field_query": json.dumps(
                [custom_field["name"], operator, value],
            ),
        }

    @api.model
    def _workspace_page(
        self, domain, order, page, page_size, *, sort, order_by, query, search,
    ):
        """Return the page documents, the total count and the relevance order."""
        relevance_paperless_ids = search["relevance_paperless_ids"]
        semantic_scores = search["semantic_scores"]
        ordered_ids = None
        if sort == "semantic" and search["semantic_scores_loaded"] and not order_by:
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
        else:
            count = self.search_count(domain)
            documents = self.search(
                domain,
                order=order,
                offset=(page - 1) * page_size,
                limit=page_size,
            )
            return documents, count, ordered_ids
        count = len(ordered_ids)
        page_ids = ordered_ids[(page - 1) * page_size : page * page_size]
        return self.browse(page_ids), count, ordered_ids

    @api.model
    def _workspace_link_facets(self, accessible_documents):
        """Return up to 200 linked-record facets and the visible links per document."""
        visible_links_by_document = (
            accessible_documents._accessible_active_links_by_document()
        )
        link_facets = []
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
        return link_facets, visible_links_by_document

    @api.model
    def _workspace_catalog_values(self, custom_fields, smart_views, link_facets):
        """Return the companies, catalogs, views and facets of the workspace."""
        return {
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
        }

    @api.model
    def document_detail(self, document_id, check_archive=False):
        document = self.browse(int(document_id)).exists()
        if not document:
            raise ValidationError(_("The archived document no longer exists."))
        document.check_access("read")
        archive_available = True
        paperless_suggestions = []
        if check_archive:
            try:
                document._paperless().compatibility()
            except PaperlessError:
                archive_available = False
            if archive_available:
                try:
                    paperless_suggestions = document._paperless_suggestion_values()
                except PaperlessError as error:
                    _logger.info(
                        "Paperless suggestions are unavailable for document %s: %s",
                        document.paperless_id,
                        error,
                    )
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
                "paperless_suggestions": paperless_suggestions,
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

    def _paperless_suggestion_values(self):
        """Return accessible classifier proposals without applying any metadata."""
        self.ensure_one()
        payload = self._paperless().get_document_suggestions(self.paperless_id) or {}
        suggestions = []
        definitions = (
            (
                "document_type",
                "document_type_id",
                "document_types",
                "usl.paperless.document.type",
                self.document_type_id,
            ),
            (
                "correspondent",
                "correspondent_id",
                "correspondents",
                "usl.paperless.correspondent",
                self.correspondent_id,
            ),
            ("tag", "tag_ids", "tags", "usl.paperless.tag", self.tag_ids),
        )
        for kind, field_name, payload_key, model_name, current in definitions:
            remote_ids = []
            for value in payload.get(payload_key) or []:
                try:
                    remote_ids.append(int(value))
                except (TypeError, ValueError):
                    continue
            records = self.env[model_name].search(
                [("paperless_id", "in", remote_ids), ("active", "=", True)],
            )
            records_by_remote_id = {record.paperless_id: record for record in records}
            current_ids = set(current.ids)
            for remote_id in remote_ids:
                record = records_by_remote_id.get(remote_id)
                if not record or record.id in current_ids:
                    continue
                suggestions.append(
                    {
                        "kind": kind,
                        "field": field_name,
                        "record_id": record.id,
                        "label": record.display_name,
                    },
                )
        for raw_date in payload.get("dates") or []:
            try:
                suggested_date = fields.Date.to_date(raw_date)
            except (TypeError, ValueError):
                continue
            if suggested_date and suggested_date != self.document_date:
                suggestions.append(
                    {
                        "kind": "date",
                        "field": "document_date",
                        "value": fields.Date.to_string(suggested_date),
                        "label": fields.Date.to_string(suggested_date),
                    },
                )
        return suggestions
