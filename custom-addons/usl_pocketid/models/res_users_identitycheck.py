from odoo import _, api, fields, models
from odoo.exceptions import AccessDenied, UserError

from ..policy import is_sso_only


class ResUsersIdentitycheck(models.TransientModel):
    _inherit = "res.users.identitycheck"

    auth_method = fields.Selection(
        selection_add=[("usl_pocketid", "Pocket ID")],
        ondelete={"usl_pocketid": "set default"},
    )

    @api.model
    def _get_default_auth_method(self):
        user = self.env.user
        if is_sso_only(self.env) and user.usl_pocketid_access:
            return "usl_pocketid"
        return super()._get_default_auth_method()

    def _check_identity(self):
        if self.auth_method != "usl_pocketid":
            return super()._check_identity()
        try:
            self.create_uid._check_credentials(
                {"type": "usl_pocketid"},
                {"interactive": True},
            )
        except AccessDenied:
            raise UserError(
                _("Pocket ID confirmation expired. Confirm your identity again."),
            ) from None
