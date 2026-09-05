from odoo import _, api, fields, models
from odoo.exceptions import AccessError


class ProjectProject(models.Model):
    _inherit = "project.project"

    usl_feedback_project = fields.Boolean(
        string="Product Feedback Project",
        default=False,
        copy=False,
        index=True,
        help="Identifies the governed shared Project used for product feedback.",
    )

    def _usl_feedback_check_project_operator(self, values=None):
        if self.env.su or self.env.user.has_group("usl_feedback.group_feedback_maintainer"):
            return
        if self.sudo().filtered("usl_feedback_project") or (values or {}).get(
            "usl_feedback_project",
        ):
            raise AccessError(_("Only Feedback Maintainers can change the feedback Project."))

    @api.model_create_multi
    def create(self, values_list):
        for values in values_list:
            self._usl_feedback_check_project_operator(values)
        return super().create(values_list)

    def write(self, values):
        self._usl_feedback_check_project_operator(values)
        return super().write(values)

    def unlink(self):
        self._usl_feedback_check_project_operator()
        return super().unlink()

    def action_view_tasks(self):
        if len(self) != 1 or not self.usl_feedback_project:
            return super().action_view_tasks()
        return self._feedback_board_action()

    def _feedback_board_action(self):
        self.ensure_one()
        maintainer = self.env.user.has_group("usl_feedback.group_feedback_maintainer")
        action_xmlid = (
            "usl_feedback.action_feedback_maintainer"
            if maintainer
            else "usl_feedback.action_feedback_collaborator"
        )
        action = self.env["ir.actions.actions"]._for_xml_id(action_xmlid)
        action.update(
            {
                "name": self.env._("Product Feedback"),
                "domain": [("project_id", "=", self.id)],
                "context": {
                    "default_project_id": self.id,
                    "project_kanban": True,
                    "create": maintainer,
                    "edit": maintainer,
                    "delete": maintainer,
                },
            },
        )
        return action

    @api.model
    def feedback_open_board(self):
        project = self.env.ref("usl_feedback.project_product_feedback")
        project.check_access("read")
        return project._feedback_board_action()
