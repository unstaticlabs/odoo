from odoo import _, api, models


class ResUsers(models.Model):
    _inherit = "res.users"

    @api.model
    def _usl_pocketid_profile_definitions(self):
        definitions = super()._usl_pocketid_profile_definitions()
        document_groups = {
            "administrator": ("usl_documents.group_documents_manager",),
            "collaborator": ("usl_documents.group_documents_user",),
        }
        for profile, groups in document_groups.items():
            if profile not in definitions:
                continue
            definitions[profile] = {
                **definitions[profile],
                "groups": tuple(
                    dict.fromkeys(
                        tuple(definitions[profile].get("groups") or ()) + groups,
                    ),
                ),
            }
        return definitions

    def _invalidate_unsafe_paperless_mappings(self):
        mappings = self.env["usl.paperless.user.mapping"].sudo().search(
            [
                ("user_id", "in", self.ids),
                ("active", "=", True),
                ("sync_state", "=", "synchronized"),
            ],
        ).filtered(lambda mapping: not mapping._identity_is_safe())
        if mappings:
            mappings.with_context(
                usl_documents_mapping_verification=True,
            ).write(
                {
                    "sync_state": "failed",
                    "last_verified_at": False,
                    "last_error": _(
                        "Pocket ID access changed. Verify the individual "
                        "Paperless identity again before restoring access.",
                    ),
                },
            )


class OidcIdentity(models.Model):
    _inherit = "usl.oidc.identity"

    def write(self, values):
        result = super().write(values)
        if {"active", "issuer", "subject", "provider_id", "user_id"}.intersection(
            values,
        ):
            self.mapped("user_id")._invalidate_unsafe_paperless_mappings()
        return result
