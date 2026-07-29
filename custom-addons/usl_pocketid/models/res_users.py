from odoo import _, api, fields, models
from odoo.exceptions import AccessDenied, ValidationError

from ..exceptions import PocketIDAccessDenied


class ResUsers(models.Model):
    _inherit = "res.users"

    usl_pocketid_access = fields.Boolean(
        string="Pocket ID login enabled",
        copy=False,
        help="Allow an already governed Pocket ID identity to log in as this user.",
    )
    usl_pocketid_email_link = fields.Boolean(
        string="Pre-approve verified email link",
        copy=False,
        help=(
            "Allow first-login linking only when the verified Pocket ID email "
            "uniquely and exactly matches this active internal user."
        ),
    )
    usl_identity_classification = fields.Selection(
        [
            ("active", "Active Pocket ID-managed user"),
            ("historical", "Historical identity; login disabled"),
            ("portal", "Portal or external identity"),
            ("decision", "Obsolete or duplicate; decision required"),
            ("break_glass", "Local break-glass administrator"),
        ],
        string="Identity classification",
        copy=False,
    )
    usl_local_break_glass = fields.Boolean(
        string="Local break-glass administrator",
        copy=False,
        help="Emergency local administrator independent of Pocket ID.",
    )
    usl_oidc_identity_ids = fields.One2many(
        "usl.oidc.identity",
        "user_id",
        string="OIDC identities",
    )

    @api.constrains(
        "usl_pocketid_access",
        "usl_pocketid_email_link",
        "usl_local_break_glass",
        "share",
    )
    def _check_pocketid_user_policy(self):
        for user in self:
            if user.share and (
                user.usl_pocketid_access or user.usl_pocketid_email_link
            ):
                raise ValidationError(
                    _("Pocket ID internal SSO cannot be enabled for portal users."),
                )
            if user.usl_local_break_glass and (
                user.usl_pocketid_access or user.usl_pocketid_email_link
            ):
                raise ValidationError(
                    _("The break-glass administrator must remain independent of Pocket ID."),
                )

    @api.model
    def _usl_pocketid_resolve_user(self, provider, claims):
        issuer = claims["iss"]
        subject = claims["sub"]
        identities = self.env["usl.oidc.identity"].sudo().with_context(
            active_test=False,
        ).search(
            [
                ("issuer", "=", issuer),
                ("subject", "=", subject),
            ],
        )
        if len(identities) > 1:
            raise PocketIDAccessDenied("identity_conflict")
        identity = identities[:1]
        if not identity:
            identity = self._usl_pocketid_match_verified_email(
                provider,
                claims,
            )
        if not identity.active:
            raise PocketIDAccessDenied("identity_disabled")
        user = identity.user_id.sudo()
        if (
            not user.active
            or user.share
            or not user.usl_pocketid_access
            or user.usl_local_break_glass
        ):
            raise PocketIDAccessDenied("user_disabled")
        return user, identity

    @api.model
    def _usl_pocketid_match_verified_email(self, provider, claims):
        if not provider.usl_allow_unique_email_link:
            raise PocketIDAccessDenied("identity_unlinked")
        email = claims.get("email")
        if (
            claims.get("email_verified") is not True
            or not isinstance(email, str)
            or not email.strip()
            or len(email) > 320
        ):
            raise PocketIDAccessDenied("identity_unlinked")
        email = email.strip()
        candidates = self.sudo().search(
            [
                ("active", "=", True),
                ("share", "=", False),
                ("usl_pocketid_access", "=", True),
                ("usl_pocketid_email_link", "=", True),
                ("email", "=ilike", email),
            ],
        ).filtered(lambda user: (user.email or "").strip().casefold() == email.casefold())
        if len(candidates) != 1:
            reason = "identity_conflict" if candidates else "identity_unlinked"
            raise PocketIDAccessDenied(reason)
        user = candidates
        if user.usl_oidc_identity_ids.filtered(
            lambda identity: identity.issuer == provider.usl_oidc_issuer,
        ):
            raise PocketIDAccessDenied("identity_conflict")
        return self.env["usl.oidc.identity"].sudo().create(
            {
                "issuer": provider.usl_oidc_issuer,
                "subject": claims["sub"],
                "provider_id": provider.id,
                "user_id": user.id,
                "link_method": "verified_unique_email",
                "last_email": email,
                "last_display_name": claims.get("name"),
            },
        )

    @api.model
    def _usl_pocketid_login(self, provider, claims, access_token):
        user, identity = self._usl_pocketid_resolve_user(provider, claims)
        if (
            user.oauth_provider_id
            and (
                user.oauth_provider_id != provider
                or user.oauth_uid != claims["sub"]
            )
        ):
            raise PocketIDAccessDenied("identity_conflict")
        user.write(
            {
                "oauth_provider_id": provider.id,
                "oauth_uid": claims["sub"],
                "oauth_access_token": access_token,
            },
        )
        identity.sudo().write(
            {
                "last_login_at": fields.Datetime.now(),
                "last_email": claims.get("email"),
                "last_display_name": claims.get("name"),
            },
        )
        return self.env.cr.dbname, user.login, access_token, identity

    def _check_credentials(self, credential, env):
        self.ensure_one()
        if (
            credential.get("type") == "password"
            and self.usl_pocketid_access
            and not self.usl_local_break_glass
        ):
            raise AccessDenied()
        return super()._check_credentials(credential, env)

