from odoo import api, models


class ResUsers(models.Model):
    _inherit = "res.users"

    @api.model
    def _usl_pocketid_profile_definitions(self):
        definitions = super()._usl_pocketid_profile_definitions()
        reviewer = definitions.get("accountant_reviewer")
        if reviewer:
            reviewer_groups = tuple(reviewer.get("groups") or ())
            definitions["accountant_reviewer"] = {
                **reviewer,
                "groups": tuple(
                    dict.fromkeys(
                        reviewer_groups
                        + ("usl_documents.group_documents_accountant",),
                    ),
                ),
            }
        return definitions
