from odoo import api, models


class ResUsers(models.Model):
    _inherit = "res.users"

    @api.model
    def _usl_pocketid_profile_definitions(self):
        definitions = super()._usl_pocketid_profile_definitions()
        sign_groups = {
            "administrator": "usl_sign.group_sign_admin",
            "break_glass": "usl_sign.group_sign_admin",
            "collaborator": "usl_sign.group_sign_user",
            "accountant_reviewer": "usl_sign.group_sign_evidence_reviewer",
        }
        for profile, group in sign_groups.items():
            if profile not in definitions:
                continue
            groups = tuple(definitions[profile]["groups"] or ())
            definitions[profile] = {
                **definitions[profile],
                "groups": tuple(dict.fromkeys((*groups, group))),
            }
        return definitions
