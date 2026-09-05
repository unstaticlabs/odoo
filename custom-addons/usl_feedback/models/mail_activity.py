from odoo import _, api, models
from odoo.exceptions import AccessError


class MailActivity(models.Model):
    _inherit = "mail.activity"

    @api.model
    def _usl_feedback_task_from_values(self, values):
        model_name = values.get("res_model")
        if not model_name and values.get("res_model_id"):
            model_name = self.env["ir.model"].sudo().browse(values["res_model_id"]).model
        if model_name != "project.task" or not values.get("res_id"):
            return self.env["project.task"]
        return self.env["project.task"].sudo().browse(values["res_id"]).exists().filtered(
            lambda task: task.project_id.usl_feedback_project,
        )

    def _usl_feedback_protected(self):
        return self.filtered(
            lambda activity: activity.res_model == "project.task"
            and self.env["project.task"].sudo().browse(activity.res_id).project_id.usl_feedback_project,
        )

    def _usl_feedback_check_activity_management(self):
        if (
            not self.env.su
            and not self.env.user.has_group("usl_feedback.group_feedback_maintainer")
            and self._usl_feedback_protected()
        ):
            raise AccessError(_("Only feedback maintainers can manage activities on feedback cards."))

    @api.model_create_multi
    def create(self, vals_list):
        if not self.env.su and not self.env.user.has_group(
            "usl_feedback.group_feedback_maintainer",
        ):
            if any(self._usl_feedback_task_from_values(values) for values in vals_list):
                raise AccessError(_("Only feedback maintainers can schedule feedback activities."))
        return super().create(vals_list)

    def write(self, vals):
        self._usl_feedback_check_activity_management()
        if self._usl_feedback_task_from_values(vals):
            if not self.env.su and not self.env.user.has_group(
                "usl_feedback.group_feedback_maintainer",
            ):
                raise AccessError(_("Only feedback maintainers can move activities onto feedback cards."))
        return super().write(vals)

    def unlink(self):
        self._usl_feedback_check_activity_management()
        return super().unlink()
