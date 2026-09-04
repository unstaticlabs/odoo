from odoo import _, http
from odoo.http import request, route


class SenderAliasVerificationController(http.Controller):
    @route(
        "/usl/mail/sender/verify/<int:alias_id>/<string:token>",
        type="http",
        auth="public",
        methods=["GET"],
        csrf=False,
        sitemap=False,
    )
    def verify_sender_alias(self, alias_id, token, **_kwargs):
        alias = request.env["usl.mail.sender.alias"].sudo().browse(alias_id).exists()
        verified = bool(alias and alias._verify_token(token))
        return request.render(
            "usl_pocketid.sender_alias_verification_result",
            {
                "verified": verified,
                "email": alias.email_normalized if alias else False,
                "title": (
                    _("Email address verified")
                    if verified
                    else _("Verification link unavailable")
                ),
            },
        )
