from odoo import api, fields, models
from odoo.fields import Domain
from odoo.exceptions import ValidationError

from .content_search import company_domain, readable_content_domain


class ProjectTask(models.Model):
    _inherit = "project.task"

    usl_search_text = fields.Char(
        string="Task content", store=False, readonly=True,
        search="_search_usl_search_text",
    )

    @api.model
    def _usl_content_search_domain(self, value):
        text = str(value or "").strip()
        domain = readable_content_domain(self, text)
        if not text:
            return domain
        Activity = self.env["mail.activity"]
        if Activity.has_access("read"):
            activities = Activity.search(
                Domain("res_model", "=", self._name) & readable_content_domain(Activity, text),
            )
            domain |= Domain("id", "in", activities.mapped("res_id"))
        return company_domain(self) & domain

    @api.model
    def _search_usl_search_text(self, operator, value):
        if operator not in {"ilike", "not ilike"}:
            raise ValidationError(self.env._("Use a text search for task content."))
        domain = self._usl_content_search_domain(value)
        return company_domain(self) & ~domain if operator.startswith("not ") else domain
