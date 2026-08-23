from odoo import models

from ..policy import is_sso_only


class PortalWizardUser(models.TransientModel):
    _inherit = "portal.wizard.user"

    def _usl_prepare_pocketid_portal_user(self):
        self.ensure_one()
        user = self.user_id.sudo()
        if user and is_sso_only(self.env):
            user.write(
                {
                    "usl_identity_classification": "portal",
                    "usl_pocketid_access": True,
                    "usl_pocketid_email_link": True,
                },
            )
        return user

    def _send_email(self):
        if not is_sso_only(self.env):
            return super()._send_email()
        self.ensure_one()
        user = self._usl_prepare_pocketid_portal_user()
        # The native portal wizard prepares a password-reset token before it
        # calls this hook.  SSO-only onboarding must not leave that alternative
        # credential path usable, even though the custom message does not show
        # the token.
        user.partner_id.sudo().write(
            {
                "signup_token": False,
                "signup_type": False,
                "signup_expiration": False,
            },
        )
        template = self.env.ref("usl_pocketid.portal_sso_invitation")
        template.with_context(
            lang=user.lang,
            welcome_message=self.wizard_id.welcome_message,
        ).send_mail(
            user.id,
            force_send=True,
            email_layout_xmlid="mail.mail_notification_layout",
        )
        return True
