from odoo import Command, _, api, fields, models
from odoo.exceptions import AccessError


def _references_record(commands, record_id):
    """Return whether M2M commands explicitly reference a protected record."""
    for command in commands or ():
        if not isinstance(command, (list, tuple)) or not command:
            continue
        operation = command[0]
        if operation in (Command.UPDATE, Command.DELETE, Command.UNLINK, Command.LINK):
            if len(command) > 1 and command[1] == record_id:
                return True
        elif operation == Command.SET and len(command) > 2 and record_id in command[2]:
            return True
    return False


class ProjectTaskType(models.Model):
    _inherit = "project.task.type"

    def _usl_feedback_check_metadata_operator(self, values=None):
        if self.env.su or self.env.user.has_group("usl_feedback.group_feedback_maintainer"):
            return
        feedback_project = self.env.ref("usl_feedback.project_product_feedback")
        protected = self.sudo().filtered(
            lambda stage: feedback_project in stage.project_ids,
        )
        if protected or _references_record((values or {}).get("project_ids"), feedback_project.id):
            raise AccessError(_("Only Feedback Maintainers can change feedback stages."))

    @api.model_create_multi
    def create(self, values_list):
        for values in values_list:
            self._usl_feedback_check_metadata_operator(values)
        return super().create(values_list)

    def write(self, values):
        self._usl_feedback_check_metadata_operator(values)
        return super().write(values)

    def unlink(self):
        self._usl_feedback_check_metadata_operator()
        return super().unlink()


class ProjectTags(models.Model):
    _inherit = "project.tags"

    usl_feedback_tag = fields.Boolean(
        string="Product Feedback Tag",
        default=False,
        copy=False,
        index=True,
        help="Identifies a governed category tag for the Product Feedback Project.",
    )

    def _usl_feedback_check_metadata_operator(self, values=None):
        if self.env.su or self.env.user.has_group("usl_feedback.group_feedback_maintainer"):
            return
        if self.sudo().filtered("usl_feedback_tag") or (values or {}).get("usl_feedback_tag"):
            raise AccessError(_("Only Feedback Maintainers can change feedback tags."))

    @api.model_create_multi
    def create(self, values_list):
        for values in values_list:
            self._usl_feedback_check_metadata_operator(values)
        return super().create(values_list)

    def write(self, values):
        self._usl_feedback_check_metadata_operator(values)
        return super().write(values)

    def unlink(self):
        self._usl_feedback_check_metadata_operator()
        return super().unlink()
