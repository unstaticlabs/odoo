from odoo import models


class ResUsers(models.Model):
    _inherit = "res.users"

    def _notify_security_setting_update(
        self,
        subject,
        content,
        mail_values=None,
        **kwargs,
    ):
        """Suppress only governed migration-time identity notifications.

        Accounting creates the canonical manager and reviewer before the
        permanent identity product is installed. Their login and temporary
        credential changes must not contact addresses restored from the
        source. The normal product notification path remains unchanged.
        """
        if self.env.context.get("usl_governed_identity_provisioning"):
            return self.env["mail.mail"]
        return super()._notify_security_setting_update(
            subject,
            content,
            mail_values=mail_values,
            **kwargs,
        )
