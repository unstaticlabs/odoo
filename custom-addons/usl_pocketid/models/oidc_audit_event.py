from odoo import api, fields, models
from odoo.exceptions import UserError


class OidcAuditEvent(models.Model):
    _name = "usl.oidc.audit.event"
    _description = "USL OIDC Audit Event"
    _order = "occurred_at desc, id desc"

    occurred_at = fields.Datetime(
        required=True,
        default=fields.Datetime.now,
        readonly=True,
    )
    event_type = fields.Selection(
        [
            ("login_success", "Login succeeded"),
            ("login_denied", "Login denied"),
            ("identity_linked", "Identity linked"),
            ("identity_relinked", "Identity relinked"),
            ("identity_disabled", "Identity disabled"),
            ("configuration", "Configuration changed"),
        ],
        required=True,
        readonly=True,
    )
    reason_code = fields.Char(required=True, readonly=True)
    provider_id = fields.Many2one(
        "auth.oauth.provider",
        ondelete="set null",
        readonly=True,
    )
    identity_id = fields.Many2one(
        "usl.oidc.identity",
        ondelete="set null",
        readonly=True,
    )
    user_id = fields.Many2one("res.users", ondelete="set null", readonly=True)
    subject_fingerprint = fields.Char(readonly=True)

    @api.model
    def _record(self, *, event_type, reason_code, **values):
        return self.sudo().create(
            {
                "event_type": event_type,
                "reason_code": reason_code,
                **values,
            },
        )

    @api.ondelete(at_uninstall=False)
    def _prevent_audit_deletion(self):
        raise UserError(self.env._("OIDC audit events cannot be deleted."))

