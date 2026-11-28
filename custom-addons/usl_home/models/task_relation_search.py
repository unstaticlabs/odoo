"""One-hop description evidence over existing, current native relations."""

from odoo import api, fields, models
from odoo.fields import Domain
from odoo.exceptions import UserError

from odoo.addons.usl_project.models.content_index import Projection
from odoo.addons.usl_project.models.content_search import company_domain

from .task_message_search import _query_text, _source_excerpt


class ProjectTask(models.Model):
    _inherit = "project.task"

    usl_search_sources = fields.Json(help=(
        "Readable chatter and immediate parent/project description evidence for the explicit search_source_query context."
    ))

    def _usl_description_relations(self):
        for relation, model, projection in (
            ("parent_id", "project.task", Projection("usl_task_search", "task_id", ("description",))),
            ("project_id", "project.project", Projection("usl_project_search", "project_id", ("description",), translated=True)),
        ):
            target = self.env[model]
            if (self.has_field_access(self._fields[relation], "read") and target.has_access("read")
                    and target.has_field_access(target._fields["description"], "read")):
                yield relation, target, projection

    @api.model
    def _usl_relation_content_domain(self, query):
        clauses = []
        for relation, target, projection in self._usl_description_relations():
            source = target._search(company_domain(target) & target._usl_indexed_projection_domain(projection, query))
            clauses.append(Domain(relation, "any", source))
        return Domain.OR(clauses)

    @api.model
    def _search_usl_search_text(self, operator, value):
        base = super()._search_usl_search_text(operator, value)
        # Preserve native scalar/domain searches; the Home/context API bounds
        # belong to their entry points, not to this existing field searcher.
        text = str(value or "").strip()
        if not text or not self.has_access("read"):
            return base
        related = self._usl_relation_content_domain(text)
        return company_domain(self) & (base & ~related if operator.startswith("not ") else base | related)

    def _usl_matching_relation_sources(self, query):
        text = _query_text(query, self.env)
        if not text or not self.has_access("read"):
            return {}
        ids = [record.id for record in self if type(record.id) is int]
        if not ids:
            return {}
        tasks = self.search(company_domain(self) & Domain("id", "in", ids))
        # Preserve native own-text/activity results before relation explanations.
        direct = tasks._usl_own_text_matches(text)
        tasks -= direct
        result = {}
        for relation, target, projection in self._usl_description_relations():
            remaining = tasks.filtered(lambda task: task.id not in result)
            ids = remaining.mapped(relation).ids
            if not ids:
                continue
            sources = target.search(company_domain(target) & Domain("id", "in", ids)
                & target._usl_indexed_projection_domain(projection, text), order="id")
            can_read_name = target.has_field_access(target._fields["name"], "read")
            sources.fetch(["description", "name"] if can_read_name else ["description"])
            by_id = {}
            for source in sources:
                name = source.display_name if can_read_name else ""
                if relation == "parent_id":
                    label = self.env._("Via parent task: %(name)s", name=name) if name else self.env._("Via parent task")
                else:
                    label = self.env._("Via project: %(name)s", name=name) if name else self.env._("Via project")
                by_id[source.id] = {
                    "kind": "relation", "relation": relation, "model": target._name, "id": source.id,
                    "field": "description", "label": label,
                    "excerpt": _source_excerpt(self.env, source.description, text),
                    "url": "/odoo/%s/%s" % (target._name, source.id),
                    "open_label": self.env._("Open parent task") if relation == "parent_id" else self.env._("Open project"),
                }
            for task in remaining:
                source = by_id.get(task[relation].id)
                if source:
                    result[task.id] = dict(source)

        return result

    def _usl_matching_search_sources(self, query):
        messages = self._usl_matching_message_sources(query)
        relations = (self - self.browse(list(messages)))._usl_matching_relation_sources(query)
        return relations | messages

    @api.depends("company_id", "active", "parent_id", "parent_id.description", "parent_id.name", "parent_id.active",
        "parent_id.company_id", "project_id", "project_id.description", "project_id.name", "project_id.active",
        "project_id.company_id", "name", "description", "message_ids", "message_ids.body", "message_ids.subject",
        "message_ids.model", "message_ids.res_id", "message_ids.message_type", "message_ids.subtype_id")
    @api.depends_context("uid", "lang", "company", "allowed_company_ids", "search_source_query")
    def _compute_usl_search_sources(self):
        sources = self._usl_matching_search_sources(self.env.context.get("search_source_query"))
        for task in self:
            source = sources.get(task.id)
            task.usl_search_sources = [source] if source else []


class HomeRelationSearch(models.AbstractModel):
    _inherit = "usl.home.service"

    def _get_search_relation_action(self, task, relation, source_id, query):
        error = self.env._("This search source is no longer available. Refresh the search and try again.")
        if task._name != "project.task" or relation not in ("parent_id", "project_id") or type(source_id) is not int:
            raise UserError(error)
        text = _query_text(query, self.env)
        specification = next(((target, projection) for edge, target, projection
            in task._usl_description_relations() if edge == relation), None)
        if not text or not specification or task[relation].id != source_id:
            raise UserError(error)
        target, projection = specification
        source = target.search(company_domain(target) & Domain("id", "=", source_id)
            & target._usl_indexed_projection_domain(projection, text), limit=1, order="id")
        if not source:
            raise UserError(error)
        return {
            "type": "ir.actions.act_window",
            "name": source.display_name if target.has_field_access(target._fields["name"], "read") else self.env._("Search source"),
            "res_model": target._name, "res_id": source.id,
            "views": [(False, "form")], "target": "current",
            "context": {key: value for key, value in self.env.context.items()
                if key in {"lang", "tz", "allowed_company_ids"}},
        }
