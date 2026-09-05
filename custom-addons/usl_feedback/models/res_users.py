from odoo import _, api, models
from odoo.exceptions import ValidationError


class ResUsers(models.Model):
    _inherit = "res.users"

    @api.constrains("group_ids")
    def _check_feedback_agent_identity(self):
        agent_group = self.env.ref("usl_feedback.group_feedback_agent", raise_if_not_found=False)
        internal_group = self.env.ref("base.group_user", raise_if_not_found=False)
        maintainer_group = self.env.ref(
            "usl_feedback.group_feedback_maintainer", raise_if_not_found=False,
        )
        if not agent_group:
            return
        for user in self:
            if agent_group not in user.all_group_ids:
                continue
            if internal_group and internal_group in user.all_group_ids:
                raise ValidationError(_("The Feedback Agent must be a non-human external identity."))
            if maintainer_group and maintainer_group in user.all_group_ids:
                raise ValidationError(_("The Feedback Agent cannot also be a feedback maintainer."))
