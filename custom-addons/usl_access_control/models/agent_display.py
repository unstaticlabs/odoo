from odoo import models


class ResUsers(models.Model):
    _inherit = "res.users"

    def _store_main_user_fields(self, res):
        super()._store_main_user_fields(res)
        res.attr(
            "usl_agent_display",
            lambda user: user.usl_identity_classification == "agent",
            internal=True,
        )
