from odoo import models


class ProjectMilestone(models.Model):
    _inherit = "project.milestone"

    def action_view_tasks(self):
        action = super().action_view_tasks()
        action["name"] = self.display_name
        # Native action URLs persist active_id, so reload can rebuild the milestone
        # domain and defaults rather than relying on a transient button context.
        action["context"].update({
            "active_model": self._name,
            "active_id": self.id,
            "active_ids": self.ids,
        })
        return action
