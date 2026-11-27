"""Private, rebuildable task title and description projection."""

from odoo import api, models

from .content_index import ContentIndexMixin, Projection


INDEXED_FIELDS = ("name", "description")
_PROJECTION = Projection("usl_task_search", "task_id", INDEXED_FIELDS)


class ProjectTask(models.Model, ContentIndexMixin):
    _inherit = "project.task"

    @api.private
    def init(self):
        super().init()
        self._usl_initialize_projection(_PROJECTION)

    def _usl_queue_task_content(self, *, all_tasks=False):
        self._usl_queue_projection(_PROJECTION, all_records=all_tasks)

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        records._usl_queue_task_content()
        return records

    def write(self, vals):
        # Decide before native code runs: for a portal user it moves the
        # description into a separate sudo write that never reaches this
        # override, so the keys left in vals afterwards are not the edit.
        touched = any(name in vals for name in INDEXED_FIELDS)
        result = super().write(vals)
        if touched:
            self._usl_queue_task_content()
        return result

    @api.model
    def _cron_usl_index_task_content(self):
        self._usl_refresh_projection(_PROJECTION)

    @api.model
    def _usl_indexed_task_content_domain(self, text):
        return self._usl_indexed_projection_domain(_PROJECTION, text)
