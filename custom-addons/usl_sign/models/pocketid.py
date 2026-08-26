from odoo import _, api, models, modules


class ResUsers(models.Model):
    _inherit = "res.users"

    @api.model
    def _usl_sign_actionable_signer_domain(self):
        """Keep the dashboard and OCA pencil menu on one definition of "to sign"."""
        return [
            ("partner_id", "=", self.env.user.partner_id.id),
            ("state", "in", ["notified", "viewed", "authorized"]),
            ("request_id.state", "in", ["sent", "viewed", "partial"]),
            ("access_revoked", "=", False),
        ]

    @api.model
    def sign_oca_request_user_count(self):
        """Adapt the inherited Sign systray to Native Sign lifecycle states."""
        count = self.env["sign.oca.request.signer"].search_count(
            self._usl_sign_actionable_signer_domain(),
        )
        if not count:
            return []
        model = self.env["sign.oca.request.signer"]
        return [
            {
                "id": self.env["ir.model"]._get_id(model._name),
                "name": _("Documents to be Signed"),
                "model": model._name,
                "icon": modules.module.get_module_icon("usl_sign"),
                "total_records": count,
            },
        ]

    @api.model
    def action_open_usl_sign_requests(self):
        return {
            "type": "ir.actions.act_window",
            "name": _("Documents to be Signed"),
            "res_model": "sign.oca.request.signer",
            "views": [
                (self.env.ref("usl_sign.my_signature_list_usl").id, "list"),
                (self.env.ref("usl_sign.sign_request_signer_result_form").id, "form"),
            ],
            "search_view_id": self.env.ref("usl_sign.my_signature_search_usl").id,
            "domain": self._usl_sign_actionable_signer_domain(),
            "context": {},
        }

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
