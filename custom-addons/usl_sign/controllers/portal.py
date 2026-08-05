from werkzeug.exceptions import NotFound
from werkzeug.utils import redirect

from odoo import http
from odoo.exceptions import AccessError
from odoo.http import request


class SignPortalController(http.Controller):
    def _secure_signer(self, signer_id, access_token):
        signer = request.env["sign.oca.request.signer"].sudo().browse(signer_id).exists()
        if not signer:
            raise NotFound()
        try:
            signer._check_secure_access(access_token)
        except AccessError as error:
            raise NotFound() from error
        return signer

    @http.route(
        "/sign/document/<int:signer_id>/<string:access_token>",
        type="http",
        auth="public",
        methods=["GET"],
        website=True,
        sitemap=False,
    )
    def sign_document(self, signer_id, access_token):
        signer = self._secure_signer(signer_id, access_token)
        signer._mark_viewed()
        if signer.request_id.requested_assurance == "qualified":
            return redirect(signer.provider_signature_link, code=303)
        return request.render(
            "usl_sign.portal_sign_provider",
            {
                "signer": signer,
                "signature_link": signer.provider_signature_link,
                "status_url": (
                    f"/sign/document/{signer.id}/{access_token}/provider-status"
                ),
                "sandbox": signer.request_id.provider_environment == "sandbox",
            },
        )

    @http.route(
        "/sign/document/<int:signer_id>/<string:access_token>/provider-status",
        type="json",
        auth="public",
        methods=["POST"],
        csrf=False,
    )
    def provider_status(self, signer_id, access_token, event=None):
        signer = self._secure_signer(signer_id, access_token)
        if event in {"success", "signature.done", "declined", "error"}:
            signer.request_id._provider_reconcile()
        return {
            "state": signer.request_id.state,
            "signer_state": signer.state,
        }
