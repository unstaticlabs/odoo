from odoo import _, api, fields, models


class ProjectProject(models.Model):
    _name = "project.project"
    _inherit = ["project.project", "rebuild.source.trace.mixin"]

    usl_source_task_properties_definition = fields.Json(
        string="Source Task Properties Definition",
        copy=False,
        groups="project.group_project_manager",
        help=(
            "Exact Odoo Online definition retained for audit when Community "
            "drops Enterprise-only property metadata."
        ),
    )


class ProjectProjectStage(models.Model):
    _name = "project.project.stage"
    _inherit = ["project.project.stage", "rebuild.source.trace.mixin"]


class ProjectTask(models.Model):
    _name = "project.task"
    _inherit = ["project.task", "rebuild.source.trace.mixin"]

    planned_date_begin = fields.Datetime(
        string="Planned Start",
        index=True,
        copy=False,
        tracking=True,
        help=(
            "Planned start restored from Odoo Online. Together with the "
            "deadline it preserves the former Gantt planning range."
        ),
    )
    usl_source_task_properties = fields.Json(
        string="Source Task Properties",
        copy=False,
        groups="project.group_project_manager",
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


class ProjectTaskType(models.Model):
    _name = "project.task.type"
    _inherit = ["project.task.type", "rebuild.source.trace.mixin"]


class ProjectTags(models.Model):
    _name = "project.tags"
    _inherit = ["project.tags", "rebuild.source.trace.mixin"]


class ProjectMilestone(models.Model):
    _name = "project.milestone"
    _inherit = ["project.milestone", "rebuild.source.trace.mixin"]


class ProjectTaskRecurrence(models.Model):
    _name = "project.task.recurrence"
    _inherit = ["project.task.recurrence", "rebuild.source.trace.mixin"]


class ProjectUpdate(models.Model):
    _name = "project.update"
    _inherit = ["project.update", "rebuild.source.trace.mixin"]


class MailMessage(models.Model):
    _name = "mail.message"
    _inherit = ["mail.message", "rebuild.source.trace.mixin"]


class MailActivity(models.Model):
    _name = "mail.activity"
    _inherit = ["mail.activity", "rebuild.source.trace.mixin"]


class MailActivityType(models.Model):
    _name = "mail.activity.type"
    _inherit = ["mail.activity.type", "rebuild.source.trace.mixin"]


class MailTrackingValue(models.Model):
    _name = "mail.tracking.value"
    _inherit = ["mail.tracking.value", "rebuild.source.trace.mixin"]


class MailAlias(models.Model):
    _name = "mail.alias"
    _inherit = ["mail.alias", "rebuild.source.trace.mixin"]


class MailFollowers(models.Model):
    _name = "mail.followers"
    _inherit = ["mail.followers", "rebuild.source.trace.mixin"]
