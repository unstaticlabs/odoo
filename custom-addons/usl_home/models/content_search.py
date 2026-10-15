"""Read-only live business search for Home and the native Projects search bar."""

import re

from odoo import api, models
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.fields import Domain
from odoo.tools import html2plaintext

from odoo.addons.usl_project.models.content_search import (
    _CREDENTIAL_FIELD, company_domain, readable_content_domain,
)

# Business destinations, not a registry scan of configuration or credentials.
SEARCH_MODELS = (
    ("tasks", "project.task", "Tasks"),
    ("projects", "project.project", "Projects"),
    ("documents", "usl.document", "Documents"),
    ("accounting", "account.move", "Invoices and entries"),
    ("accounting_lines", "account.move.line", "Accounting lines"),
    ("expenses", "hr.expense", "Expenses"),
    ("contacts", "res.partner", "Contacts"),
    ("sales", "sale.order", "Sales orders"),
    ("purchases", "purchase.order", "Purchase orders"),
    ("opportunities", "crm.lead", "Opportunities"),
    ("bank_transactions", "account.bank.statement.line", "Bank transactions"),
    ("products", "product.template", "Products"),
    ("transfers", "stock.picking", "Inventory transfers"),
)


def _excerpt(value, query):
    text = " ".join(html2plaintext(str(value or "")).split())
    position = text.casefold().find(query.casefold())
    start = max(0, position - 65) if position >= 0 else 0
    return ("…" if start else "") + text[start:start + 240] + ("…" if len(text) > start + 240 else "")


class HomeContentSearch(models.AbstractModel):
    _inherit = "usl.home.service"

    @api.model
    def _search_catalog(self):
        self._ensure_internal()
        # Evaluate/validate the caller's selected company context before queries.
        self.env.companies
        return {
            key: (model, self.env._(label))
            for key, model, label in SEARCH_MODELS
            if self._model_is_readable(model)
        }

    @api.model
    def _search_arguments(self, query, limit, offset):
        if not isinstance(query, str) or len(query) > 2048:
            raise ValidationError(self.env._("Enter a search of up to 2,048 characters."))
        if type(limit) is not int or not 1 <= limit <= 25 or type(offset) is not int or offset < 0:
            raise ValidationError(self.env._("The search page is not valid."))
        return query.strip()

    def _search_preview(self, record, query):
        """Read only authorized own text, not hidden related owner values."""
        values = []
        metadata = record.fields_get(attributes=["type"])
        for name, definition in metadata.items():
            field = record._fields[name]
            if definition["type"] not in {"char", "text", "html"} or field.related or not field.store:
                continue
            if name in {"display_name", "access_url", "access_warning", "usl_search_text"} or _CREDENTIAL_FIELD.search(name):
                continue
            if record.has_field_access(field, "read"):
                value = record.with_context(prefetch_fields=False)[name]
                if value:
                    values.append(str(value))
        for name, field in record._fields.items():
            if field.type != "properties" or not record.has_field_access(field, "read"):
                continue
            for item in record.with_context(prefetch_fields=False).read([name])[0][name] or []:
                label = re.sub(r"\W+", "_", str(item.get("string", "")).casefold())
                if item.get("type") in {"char", "text", "html"} and not _CREDENTIAL_FIELD.search(label):
                    values.append(str(item.get("value") or ""))
        if record._name == "project.task":
            Activity = self.env["mail.activity"]
            if Activity.has_access("read"):
                activities = Activity.search(
                    Domain("res_model", "=", "project.task") & Domain("res_id", "=", record.id)
                    & readable_content_domain(Activity, query), limit=1,
                )
                for activity in activities:
                    for name in ("summary", "note"):
                        if Activity.has_field_access(Activity._fields[name], "read"):
                            values.append(activity[name] or "")
        matching = next((value for value in values if query.casefold() in html2plaintext(value).casefold()), "")
        return _excerpt(matching, query)

    def _search_context_label(self, record):
        labels = []
        if record._name == "project.task" and record.has_field_access(record._fields["project_id"], "read"):
            project = record.project_id._filtered_access("read")
            if project:
                labels.append(project.display_name)
        if "company_id" in record._fields and record.has_field_access(record._fields["company_id"], "read"):
            company = record.company_id
            if company and company.id in self.env.companies.ids:
                labels.append(company.display_name)
        return " · ".join(labels)

    @api.model
    def search_records(self, query, categories=None, limit=5, offset=0, exclude_categories=None):
        catalog = self._search_catalog()
        query = self._search_arguments(query, limit, offset)
        requested = list(catalog) if categories is None else categories
        if not isinstance(requested, list) or any(not isinstance(key, str) or key not in dict((k, m) for k, m, _l in SEARCH_MODELS) for key in requested):
            raise ValidationError(self.env._("Choose a supported search category."))
        excluded = [] if exclude_categories is None else exclude_categories
        if not isinstance(excluded, list) or any(not isinstance(key, str) or key not in {item[0] for item in SEARCH_MODELS} for key in excluded):
            raise ValidationError(self.env._("Choose a supported search category."))
        requested = [key for key in requested if key not in excluded]
        groups = []
        if not query:
            return {"groups": groups}
        for key in dict.fromkeys(requested):
            if key not in catalog or key == "documents":
                continue
            model_name, label = catalog[key]
            model = self.env[model_name].with_context(active_test=False)
            domain = Domain("usl_search_text", "ilike", query) if model_name == "project.task" else readable_content_domain(model, query)
            records = model.search(domain, limit=limit + 1, offset=offset, order="id desc")
            rows = []
            for record in records[:limit]:
                rows.append({
                    "model": model_name, "id": record.id,
                    "title": record.display_name,
                    "excerpt": self._search_preview(record, query),
                    "context_label": self._search_context_label(record),
                })
            groups.append({"key": key, "label": label, "records": rows, "has_more": len(records) > limit, "offset": offset})
        return {"groups": groups}

    @api.model
    def search_documents(self, query, limit=5, offset=0):
        catalog = self._search_catalog()
        query = self._search_arguments(query, limit, offset)
        group = {"key": "documents", "label": self.env._("Documents"), "records": [], "has_more": False, "offset": offset}
        if "documents" not in catalog or not query:
            return {"group": group}
        if offset >= 50:
            group["continue_in_documents"] = True
            return {"group": group}
        try:
            result = self.env["usl.document"].mcp_search(query, mode="hybrid", limit=min(limit, 50 - offset), offset=offset)
        except UserError:
            group["unavailable"] = True
            return {"group": group}
        group["records"] = [{"model": "usl.document", "id": row["id"], "title": row["name"], "excerpt": row.get("excerpt", ""), "context_label": (row.get("company") or {}).get("name", "")} for row in result["results"]]
        group["has_more"] = bool(result.get("has_more")) and offset + limit < 50
        group["continue_in_documents"] = bool(result.get("truncated")) or offset + limit >= 50
        group["warnings"] = result.get("warnings", [])
        return {"group": group}

    @api.model
    def get_search_result_action(self, model_name, record_id):
        catalog = self._search_catalog()
        if model_name not in {model for model, _label in catalog.values()} or type(record_id) is not int:
            raise AccessError(self.env._("This search destination is not available."))
        model = self.env[model_name].with_context(active_test=False)
        record = model.search(company_domain(model) & Domain("id", "=", record_id), limit=1)
        if not record:
            raise UserError(self.env._("This record is no longer available in your selected companies."))
        return {
            "type": "ir.actions.act_window", "name": record.display_name,
            "res_model": model_name, "res_id": record.id,
            "views": [(False, "form")], "target": "current",
            "context": {key: value for key, value in self.env.context.items() if key in {"lang", "tz", "allowed_company_ids"}},
        }
