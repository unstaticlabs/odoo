from odoo import api, fields, models
from odoo.fields import Domain
from odoo.exceptions import ValidationError

from .content_search import company_domain, readable_content_domain, task_content_execution


class ProjectTask(models.Model):
    _inherit = "project.task"

    usl_search_text = fields.Char(
        string="Task content", store=False, readonly=True,
        search="_search_usl_search_text",
    )

    @api.model
    @api.private
    @api.readonly
    def search_fetch(self, domain, field_names=None, offset=0, limit=None, order=None):
        with task_content_execution(self, domain):
            return super().search_fetch(domain, field_names, offset=offset, limit=limit, order=order)

    @api.model
    @api.readonly
    def search_count(self, domain, limit=None):
        with task_content_execution(self, domain):
            return super().search_count(domain, limit=limit)

    @api.model
    def _read_group(self, domain, groupby=(), aggregates=(), having=(), offset=0, limit=None, order=None):
        with task_content_execution(self, domain):
            return super()._read_group(domain, groupby, aggregates, having, offset, limit, order)

    @api.model
    def _read_grouping_sets(self, domain, grouping_sets, aggregates=(), order=None):
        with task_content_execution(self, domain):
            return super()._read_grouping_sets(domain, grouping_sets, aggregates, order)

    @api.model
    def _usl_content_search_domain(self, value):
        text = str(value or "").strip()
        domain = readable_content_domain(self, text)
        if not text:
            return domain
        Activity = self.env["mail.activity"]
        if Activity.has_access("read"):
            activities = Activity._search(
                Domain("res_model", "=", self._name) & readable_content_domain(Activity, text),
            )
            # Keep native activity ACLs/rules in the subquery. A bounded task
            # page must not materialize every matching activity in Python.
            domain |= Domain("id", "in", activities.select("res_id"))
        return company_domain(self) & domain

    @api.model
    def _search_usl_search_text(self, operator, value):
        if operator not in {"ilike", "not ilike"}:
            raise ValidationError(self.env._("Use a text search for task content."))
        domain = self._usl_content_search_domain(value)
        return company_domain(self) & ~domain if operator.startswith("not ") else domain
