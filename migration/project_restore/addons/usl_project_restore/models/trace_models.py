from odoo import fields, models


class ProjectProject(models.Model):
    _name = "project.project"
    _inherit = ["project.project", "usl.accounting.restore.source.mixin"]

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
    _inherit = ["project.project.stage", "usl.accounting.restore.source.mixin"]


class ProjectTask(models.Model):
    _name = "project.task"
    _inherit = ["project.task", "usl.accounting.restore.source.mixin"]

    usl_source_task_properties = fields.Json(
        string="Source Task Properties",
        copy=False,
        groups="project.group_project_manager",
    )


class ProjectTaskType(models.Model):
    _name = "project.task.type"
    _inherit = ["project.task.type", "usl.accounting.restore.source.mixin"]


class ProjectTags(models.Model):
    _name = "project.tags"
    _inherit = ["project.tags", "usl.accounting.restore.source.mixin"]


class ProjectMilestone(models.Model):
    _name = "project.milestone"
    _inherit = ["project.milestone", "usl.accounting.restore.source.mixin"]


class ProjectTaskRecurrence(models.Model):
    _name = "project.task.recurrence"
    _inherit = ["project.task.recurrence", "usl.accounting.restore.source.mixin"]


class ProjectUpdate(models.Model):
    _name = "project.update"
    _inherit = ["project.update", "usl.accounting.restore.source.mixin"]


class MailMessage(models.Model):
    _name = "mail.message"
    _inherit = ["mail.message", "usl.accounting.restore.source.mixin"]


class MailActivity(models.Model):
    _name = "mail.activity"
    _inherit = ["mail.activity", "usl.accounting.restore.source.mixin"]


class MailActivityType(models.Model):
    _name = "mail.activity.type"
    _inherit = ["mail.activity.type", "usl.accounting.restore.source.mixin"]


class MailTrackingValue(models.Model):
    _name = "mail.tracking.value"
    _inherit = ["mail.tracking.value", "usl.accounting.restore.source.mixin"]


class MailAlias(models.Model):
    _name = "mail.alias"
    _inherit = ["mail.alias", "usl.accounting.restore.source.mixin"]


class MailFollowers(models.Model):
    _name = "mail.followers"
    _inherit = ["mail.followers", "usl.accounting.restore.source.mixin"]
