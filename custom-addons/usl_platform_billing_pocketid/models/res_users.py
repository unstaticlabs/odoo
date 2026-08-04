from odoo import api, models


class ResUsers(models.Model):
    _inherit = "res.users"

    @api.model
    def _usl_pocketid_profile_definitions(self):
        definitions = super()._usl_pocketid_profile_definitions()
        platform_manager = (
            "usl_platform_billing.group_platform_billing_manager"
        )
        for profile in ("administrator", "break_glass"):
            groups = tuple(definitions[profile]["groups"])
            definitions[profile] = {
                **definitions[profile],
                "groups": tuple(dict.fromkeys((*groups, platform_manager))),
            }
        return definitions
