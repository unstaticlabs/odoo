from odoo import api, models


class ResUsers(models.Model):
    _inherit = "res.users"

    @api.model
    def _usl_pocketid_profile_definitions(self):
        definitions = super()._usl_pocketid_profile_definitions()
        definitions["accountant_reviewer"] = {
            "classification": "active",
            "active": True,
            "groups": (
                "base.group_user",
                "rebuild_account_migration.group_rebuild_accountant_reviewer",
            ),
            "pocketid": True,
        }
        return definitions
