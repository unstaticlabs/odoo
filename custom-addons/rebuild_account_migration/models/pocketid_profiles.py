from odoo import api, models


class ResUsers(models.Model):
    _inherit = "res.users"

    @api.model
    def _usl_pocketid_profile_definitions(self):
        definitions = super()._usl_pocketid_profile_definitions()
        existing_groups = tuple(
            definitions.get("accountant_reviewer", {}).get("groups") or (),
        )
        definitions["accountant_reviewer"] = {
            "classification": "active",
            "active": True,
            "groups": tuple(
                dict.fromkeys(
                    existing_groups
                    + (
                        "base.group_user",
                        "rebuild_account_migration.group_rebuild_accountant_reviewer",
                    ),
                ),
            ),
            "pocketid": True,
        }
        return definitions
