"""Native project/contact content domains backed by private own-field fragments."""

from odoo import api, fields, models
from odoo.exceptions import ValidationError

from odoo.addons.usl_project.models.content_index import ContentIndexMixin, Projection
from odoo.addons.usl_project.models.content_search import company_domain, readable_content_domain, task_content_execution


class ProjectProject(models.Model, ContentIndexMixin):
    _inherit = "project.project"

    usl_search_text = fields.Char(
        string="Project content", store=False, readonly=True,
        search="_search_usl_search_text",
    )
    _usl_projection = Projection("usl_project_search", "project_id", ('name', 'description'), translated=True)

    @api.private
    def init(self):
        super().init()
        self._usl_initialize_projection(self._usl_projection)

    def _usl_queue_content(self, *, all_records=False):
        self._usl_queue_projection(self._usl_projection, all_records=all_records)

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        records._usl_queue_content()
        return records

    def write(self, vals):
        result = super().write(vals)
        if any(name in vals for name in self._usl_projection.fields):
            self._usl_queue_content()
        return result

    @api.model
    def _cron_usl_index_content(self):
        self._usl_refresh_projection(self._usl_projection)

    @api.model
    def _search_usl_search_text(self, operator, value):
        if operator not in {"ilike", "not ilike"}:
            raise ValidationError(self.env._("Use a text search for project content."))
        text = str(value or "").strip()
        domain = readable_content_domain(self, text, exclude_fields=set(self._usl_projection.fields))
        domain |= self._usl_indexed_projection_domain(self._usl_projection, text)
        return company_domain(self) & (~domain if operator.startswith("not ") else domain)

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


class ResPartner(models.Model, ContentIndexMixin):
    _inherit = "res.partner"

    usl_search_text = fields.Char(
        string="Contact content", store=False, readonly=True,
        search="_search_usl_search_text",
    )
    _usl_projection = Projection("usl_contact_search", "partner_id", ('name', 'comment'), translated=False)

    @api.private
    def init(self):
        super().init()
        self._usl_initialize_projection(self._usl_projection)

    def _usl_queue_content(self, *, all_records=False):
        self._usl_queue_projection(self._usl_projection, all_records=all_records)

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        records._usl_queue_content()
        return records

    def write(self, vals):
        result = super().write(vals)
        if any(name in vals for name in self._usl_projection.fields):
            self._usl_queue_content()
        return result

    @api.model
    def _cron_usl_index_content(self):
        self._usl_refresh_projection(self._usl_projection)

    @api.model
    def _search_usl_search_text(self, operator, value):
        if operator not in {"ilike", "not ilike"}:
            raise ValidationError(self.env._("Use a text search for contact content."))
        text = str(value or "").strip()
        domain = readable_content_domain(self, text, exclude_fields=set(self._usl_projection.fields))
        domain |= self._usl_indexed_projection_domain(self._usl_projection, text)
        return company_domain(self) & (~domain if operator.startswith("not ") else domain)

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
