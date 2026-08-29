import re

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, ValidationError

RELEASE_SHA_RE = re.compile(r"^[0-9a-f]{40}$")


class ProjectTask(models.Model):
    _inherit = "project.task"

    usl_feedback_reporter_id = fields.Many2one(
        "res.users",
        string="Reporter",
        readonly=True,
        copy=False,
        index=True,
        ondelete="restrict",
    )
    usl_feedback_category = fields.Selection(
        [
            ("bug", "Bug"),
            ("improvement", "Improvement"),
            ("question", "Question"),
            ("ux", "UX"),
        ],
        string="Feedback Category",
        readonly=True,
        copy=False,
        index=True,
    )
    usl_feedback_context_included = fields.Boolean(
        string="Page Context Shared",
        readonly=True,
        copy=False,
    )
    usl_feedback_source_action_id = fields.Many2one(
        "ir.actions.actions",
        string="Source Action",
        readonly=True,
        copy=False,
        ondelete="set null",
    )
    usl_feedback_source_model_id = fields.Many2one(
        "ir.model",
        string="Source Model",
        readonly=True,
        copy=False,
        ondelete="set null",
    )
    usl_feedback_source_res_id = fields.Integer(
        string="Source Record ID",
        readonly=True,
        copy=False,
    )
    usl_feedback_viewport_width = fields.Integer(
        string="Viewport Width",
        readonly=True,
        copy=False,
    )
    usl_feedback_viewport_height = fields.Integer(
        string="Viewport Height",
        readonly=True,
        copy=False,
    )
    usl_feedback_release_sha = fields.Char(
        string="Release SHA",
        readonly=True,
        copy=False,
        size=40,
        index=True,
    )

    def _usl_feedback_is_maintainer(self):
        return self.env.user.has_group("usl_feedback.group_feedback_maintainer")

    @api.constrains(
        "project_id",
        "company_id",
        "usl_feedback_reporter_id",
        "usl_feedback_category",
        "usl_feedback_context_included",
        "usl_feedback_source_action_id",
        "usl_feedback_source_model_id",
        "usl_feedback_source_res_id",
        "usl_feedback_viewport_width",
        "usl_feedback_viewport_height",
        "usl_feedback_release_sha",
    )
    def _check_usl_feedback_metadata(self):
        for task in self:
            is_feedback = bool(task.project_id.usl_feedback_project)
            has_reporter = bool(task.usl_feedback_reporter_id)
            if is_feedback != has_reporter:
                raise ValidationError(
                    _("Feedback tasks must stay in the governed feedback Project and have a reporter."),
                )
            if not is_feedback:
                continue
            if not task.usl_feedback_category:
                raise ValidationError(_("A feedback category is required."))
            if not RELEASE_SHA_RE.fullmatch(task.usl_feedback_release_sha or ""):
                raise ValidationError(_("Feedback must carry an exact 40-character release SHA."))
            if task.company_id not in task.usl_feedback_reporter_id.company_ids:
                raise ValidationError(_("The feedback company must be available to the reporter."))
            if task.usl_feedback_source_res_id and not task.usl_feedback_source_model_id:
                raise ValidationError(_("A source record requires a source model."))
            if not task.usl_feedback_context_included and any(
                (
                    task.usl_feedback_source_action_id,
                    task.usl_feedback_source_model_id,
                    task.usl_feedback_source_res_id,
                    task.usl_feedback_viewport_width,
                    task.usl_feedback_viewport_height,
                ),
            ):
                raise ValidationError(_("Page context cannot be retained after the reporter opts out."))
            for value in (
                task.usl_feedback_viewport_width,
                task.usl_feedback_viewport_height,
            ):
                if value and not 1 <= value <= 16384:
                    raise ValidationError(_("Viewport dimensions must be between 1 and 16384 pixels."))

    @api.model_create_multi
    def create(self, vals_list):
        if not self.env.su and not self._usl_feedback_is_maintainer():
            project_ids = {
                values.get("project_id")
                for values in vals_list
                if values.get("project_id")
            }
            protected_projects = self.env["project.project"].sudo().browse(project_ids).filtered(
                "usl_feedback_project",
            )
            if protected_projects or any(
                values.get("usl_feedback_reporter_id") for values in vals_list
            ):
                raise AccessError(
                    _("Use Send feedback to create product feedback."),
                )
        return super().create(vals_list)

    def write(self, values):
        if not self.env.su and not self._usl_feedback_is_maintainer():
            self.check_access("write")
            if self.sudo().filtered("usl_feedback_reporter_id") or values.get(
                "usl_feedback_reporter_id",
            ):
                raise AccessError(
                    _("Submitted feedback is read-only. Add details in the conversation instead."),
                )
            if project_id := values.get("project_id"):
                if self.env["project.project"].sudo().browse(project_id).usl_feedback_project:
                    raise AccessError(_("Use Send feedback to create product feedback."))
        return super().write(values)

    def unlink(self):
        if not self.env.su and not self._usl_feedback_is_maintainer():
            self.check_access("unlink")
            if self.sudo().filtered("usl_feedback_reporter_id"):
                raise AccessError(_("Submitted feedback cannot be deleted by its reporter."))
        return super().unlink()
