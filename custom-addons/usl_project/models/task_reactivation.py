from odoo import api, fields, models
from odoo.exceptions import AccessError


RETURN_REASONS = "usl_return_reasons"
RETURN_ORDER = (
    "reopened",
    "unblocked",
    "subtasks_closed",
    "overdue",
    "due_today",
    "due_soon",
    "start_reached",
    "status_changed",
    "project_stage_changed",
)
RETURN_WRITE = object()


def check_return_write(records, vals_list):
    if records.env.context.get("usl_return_write") is RETURN_WRITE:
        return
    if records.env.context.get("default_usl_return_reasons") or any(
        RETURN_REASONS in vals for vals in vals_list
    ):
        raise AccessError(records.env._("Return reasons are recorded by the task reminders."))


class PersonalTaskStage(models.Model):
    _inherit = "project.task.stage.personal"

    usl_return_reasons = fields.Json(readonly=True, copy=False)

    @api.model_create_multi
    def create(self, vals_list):
        check_return_write(self, vals_list)
        return super().create(vals_list)

    def write(self, vals):
        check_return_write(self, [vals])
        tracked = self.filtered(RETURN_REASONS) if {"stage_id", "user_id", "task_id"} & vals.keys() else self.browse()
        before = {link.id: (link.stage_id, link.user_id, link.task_id) for link in tracked}
        result = super().write(vals)
        if self.env.context.get("usl_return_write") is not RETURN_WRITE:
            changed = tracked.filtered(
                lambda link: before[link.id] != (link.stage_id, link.user_id, link.task_id),
            )
            if changed:
                super(PersonalTaskStage, changed).write({RETURN_REASONS: False})
        return result
