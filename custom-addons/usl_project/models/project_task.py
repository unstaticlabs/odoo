from odoo import _, api, fields, models


class ProjectTask(models.Model):
    _inherit = "project.task"

    planned_date_begin = fields.Datetime(
        string="Planned Start",
        index=True,
        copy=False,
        tracking=True,
        help=(
            "The date and time when work is planned to start. Together with "
            "the deadline, it defines the task's planned range."
        ),
    )
    usl_dependency_date_warning = fields.Char(
        string="Dependency Schedule Warning",
        compute="_compute_usl_dependency_date_warning",
    )
    @api.depends(
        "planned_date_begin",
        "depend_on_ids.date_deadline",
        "depend_on_ids.state",
    )
    def _compute_usl_dependency_date_warning(self):
        closed_states = {"1_done", "1_canceled"}
        for task in self:
            overlapping = task.depend_on_ids.filtered(
                lambda blocker: (
                    blocker.state not in closed_states
                    and blocker.date_deadline
                    and task.planned_date_begin
                    and blocker.date_deadline > task.planned_date_begin
                ),
            )
            task.usl_dependency_date_warning = (
                _(
                    "This task is planned to start before blocking task(s) "
                    "finish: %s",
                )
                % ", ".join(overlapping.mapped("name")[:3])
                if overlapping
                else False
            )
