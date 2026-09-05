from odoo import api, models
from odoo.fields import Domain


def _feedback_task_relation_domain(env, model_field):
    task_ids = env["project.task"].sudo().search(
        [("project_id.usl_feedback_project", "=", True)],
    ).ids
    return Domain(model_field, "=", "project.task") & Domain("res_id", "in", task_ids)


class MailMessage(models.Model):
    _inherit = "mail.message"

    @api.model
    def _access_domain(self, operation):
        domain = super()._access_domain(operation)
        if not self.env.su and self.env.user.has_group(
            "usl_feedback.group_feedback_agent",
        ):
            domain &= _feedback_task_relation_domain(self.env, "model")
        return domain


class IrAttachment(models.Model):
    _inherit = "ir.attachment"

    @api.model
    def _access_domain(self, operation):
        domain = super()._access_domain(operation)
        if not self.env.su and self.env.user.has_group(
            "usl_feedback.group_feedback_agent",
        ):
            domain &= _feedback_task_relation_domain(self.env, "res_model")
        return domain


class MailActivity(models.Model):
    _inherit = "mail.activity"

    @api.model
    def _access_domain(self, operation):
        domain = super()._access_domain(operation)
        if not self.env.su and self.env.user.has_group(
            "usl_feedback.group_feedback_agent",
        ):
            domain &= _feedback_task_relation_domain(self.env, "res_model")
        return domain
