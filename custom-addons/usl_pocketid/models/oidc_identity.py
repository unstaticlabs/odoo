import hashlib

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


def identity_fingerprint(issuer, subject):
    value = f"{issuer}\0{subject}".encode()
    return hashlib.sha256(value).hexdigest()[:16]


class OidcIdentity(models.Model):
    _name = "usl.oidc.identity"
    _description = "USL OIDC Identity Link"
    _inherit = ["mail.thread"]
    _order = "active desc, user_id, id"
    _rec_name = "subject"

    issuer = fields.Char(required=True, index=True, tracking=True)
    subject = fields.Char(required=True, index=True, tracking=True)
    provider_id = fields.Many2one(
        "auth.oauth.provider",
        required=True,
        ondelete="restrict",
        tracking=True,
    )
    user_id = fields.Many2one(
        "res.users",
        required=True,
        ondelete="restrict",
        tracking=True,
    )
    active = fields.Boolean(default=True, tracking=True)
    link_method = fields.Selection(
        [
            ("explicit", "Explicit administrator link"),
            ("verified_unique_email", "Pre-approved verified unique email"),
        ],
        required=True,
        default="explicit",
        tracking=True,
    )
    linked_by_id = fields.Many2one(
        "res.users",
        required=True,
        default=lambda self: self.env.user,
        ondelete="restrict",
        readonly=True,
    )
    linked_at = fields.Datetime(
        required=True,
        default=fields.Datetime.now,
        readonly=True,
    )
    last_login_at = fields.Datetime(readonly=True)
    last_email = fields.Char(readonly=True)
    last_display_name = fields.Char(readonly=True)
    subject_fingerprint = fields.Char(
        compute="_compute_subject_fingerprint",
        store=True,
    )

    _unique_issuer_subject = models.Constraint(
        "UNIQUE(issuer, subject)",
        "This issuer and subject are already linked.",
    )
    _unique_issuer_user = models.Constraint(
        "UNIQUE(issuer, user_id)",
        "This Odoo user already has an identity for this issuer.",
    )

    @api.depends("issuer", "subject")
    def _compute_subject_fingerprint(self):
        for identity in self:
            identity.subject_fingerprint = (
                identity_fingerprint(identity.issuer, identity.subject)
                if identity.issuer and identity.subject
                else False
            )

    @api.constrains("provider_id", "issuer")
    def _check_provider_issuer(self):
        for identity in self:
            if (
                not identity.provider_id.usl_pocketid
                or identity.issuer != identity.provider_id.usl_oidc_issuer
            ):
                raise ValidationError(
                    _("The identity issuer must exactly match the Pocket ID provider."),
                )

    @api.constrains("user_id", "active")
    def _check_user_type(self):
        for identity in self:
            if identity.active and identity.user_id.share:
                raise ValidationError(
                    _("Pocket ID internal SSO cannot be linked to a portal user."),
                )

    def _sync_oauth_binding(self):
        for identity in self.filtered("active"):
            user = identity.user_id.sudo()
            if (
                user.oauth_provider_id
                and (
                    user.oauth_provider_id != identity.provider_id
                    or user.oauth_uid != identity.subject
                )
            ):
                raise ValidationError(
                    _("The Odoo user already has a conflicting OAuth identity."),
                )
            user.write(
                {
                    "oauth_provider_id": identity.provider_id.id,
                    "oauth_uid": identity.subject,
                },
            )

    @api.model_create_multi
    def create(self, vals_list):
        for values in vals_list:
            provider = self.env["auth.oauth.provider"].browse(
                values.get("provider_id"),
            )
            if provider and not values.get("issuer"):
                values["issuer"] = provider.usl_oidc_issuer
        identities = super().create(vals_list)
        identities._sync_oauth_binding()
        for identity in identities:
            self.env["usl.oidc.audit.event"]._record(
                event_type="identity_linked",
                reason_code=identity.link_method,
                provider_id=identity.provider_id.id,
                identity_id=identity.id,
                user_id=identity.user_id.id,
                subject_fingerprint=identity.subject_fingerprint,
            )
        return identities

    def write(self, values):
        previous = {
            identity.id: (
                identity.user_id,
                identity.provider_id,
                identity.subject,
                identity.active,
            )
            for identity in self
        }
        result = super().write(values)
        for identity in self:
            old_user, old_provider, old_subject, old_active = previous[identity.id]
            changed_link = (
                old_user != identity.user_id
                or old_provider != identity.provider_id
                or old_subject != identity.subject
            )
            if changed_link or (old_active and not identity.active):
                if (
                    old_user.oauth_provider_id == old_provider
                    and old_user.oauth_uid == old_subject
                ):
                    old_user.sudo().write(
                        {
                            "oauth_provider_id": False,
                            "oauth_uid": False,
                            "oauth_access_token": False,
                        },
                    )
            if identity.active:
                identity._sync_oauth_binding()
            if changed_link:
                event_type = "identity_relinked"
                reason_code = "explicit_relink"
            elif old_active and not identity.active:
                event_type = "identity_disabled"
                reason_code = "explicit_disable"
            else:
                continue
            self.env["usl.oidc.audit.event"]._record(
                event_type=event_type,
                reason_code=reason_code,
                provider_id=identity.provider_id.id,
                identity_id=identity.id,
                user_id=identity.user_id.id,
                subject_fingerprint=identity.subject_fingerprint,
            )
        return result

    @api.ondelete(at_uninstall=False)
    def _prevent_identity_deletion(self):
        raise ValidationError(
            _("OIDC identity links must be disabled or relinked, not deleted."),
        )

