import secrets

from werkzeug.exceptions import NotFound

from odoo import http
from odoo.exceptions import AccessError
from odoo.http import request
from odoo.tools.misc import format_datetime

from .strong import _personal_certificate_subject
from odoo.addons.sign_oca.controllers.main import PortalSign
from odoo.addons.usl_sign.models.constants import SIGN_RESULT_SESSION_KEY, TRUST_LEVELS

STRONG_CSP = (
    # Odoo's OWL runtime compiles its registered QWeb templates with
    # `new Function`. Keep inline scripts nonce-only, but allow that required
    # compiler capability for the shared signing application.
    "default-src 'none'; script-src 'self' 'unsafe-eval'; style-src 'self'; font-src 'self'; "
    "connect-src 'self'; frame-src 'self'; worker-src 'self'; img-src 'self' data:; "
    "base-uri 'none'; object-src 'none'; form-action 'self'; frame-ancestors 'none'"
)


def _secure_strong_response(response, *, script_nonce=None):
    # Do not set COOP here: the signing page intentionally owns a cross-origin
    # Pocket ID popup, and a new browsing-context group severs that journey.
    content_security_policy = STRONG_CSP
    if script_nonce:
        content_security_policy = content_security_policy.replace(
            "script-src 'self'",
            f"script-src 'self' 'nonce-{script_nonce}'",
        )
    response.headers.update(
        {
            "Cache-Control": "no-store, max-age=0",
            "Content-Security-Policy": content_security_policy,
            "Permissions-Policy": "publickey-credentials-get=(), publickey-credentials-create=()",
            "Referrer-Policy": "no-referrer",
            "X-Content-Type-Options": "nosniff",
            "X-Frame-Options": "DENY",
        },
    )
    return response


class SignPortalController(PortalSign):
    @http.route(
        "/sign_oca/document/<int:signer_id>/<string:access_token>",
        type="http",
        auth="public",
        methods=["GET"],
        website=True,
        sitemap=False,
    )
    def reject_oca_plaintext_route(self, signer_id, access_token, **kwargs):
        del signer_id, access_token, kwargs
        return request.render("usl_sign.portal_sign_unavailable")

    def _check_signer_authentication(self, signer):
        method = signer.request_id.authentication_method
        if method not in {"portal", "pocket_id"}:
            return
        current_user = request.env.user
        if (
            current_user._is_public()
            or current_user.partner_id != signer.partner_id
        ):
            raise NotFound()
        if method == "pocket_id" and not current_user.oauth_provider_id.usl_pocketid:
            raise NotFound()

    def _secure_signer(self, signer_id, token, *, session=True):
        signer = request.env["sign.oca.request.signer"].sudo().browse(signer_id).exists()
        if not signer:
            raise NotFound()
        try:
            signer._check_token(token, session=session)
        except AccessError as error:
            raise NotFound() from error
        self._check_signer_authentication(signer)
        return signer

    @http.route(
        "/sign/document/<int:signer_id>/<string:access_token>",
        type="http",
        auth="public",
        methods=["GET"],
        website=True,
        sitemap=False,
    )
    def exchange_signing_link(self, signer_id, access_token):
        signer = request.env["sign.oca.request.signer"].sudo().browse(signer_id).exists()
        try:
            if not signer:
                raise NotFound()  # noqa: TRY301 - handled as an unavailable public link below
            self._check_signer_authentication(signer)
            exchange = signer._exchange_access_token(access_token)
        except (AccessError, NotFound):
            return request.render("usl_sign.portal_sign_unavailable")
        if isinstance(exchange, dict) and exchange.get("otp_required"):
            return request.redirect(
                f"/sign/otp/{signer.id}/{exchange['exchange_token']}", code=303,
            )
        return request.redirect(f"/sign/session/{signer.id}/{exchange}", code=303)

    @http.route(
        "/sign/otp/<int:signer_id>/<string:exchange_token>",
        type="http",
        auth="public",
        methods=["GET", "POST"],
        website=True,
        sitemap=False,
    )
    def verify_email_otp(self, signer_id, exchange_token, code=None, **kwargs):
        del kwargs
        signer = request.env["sign.oca.request.signer"].sudo().browse(signer_id).exists()
        if not signer or signer.request_id.authentication_method != "email_otp":
            return request.render("usl_sign.portal_sign_unavailable")
        error = False
        if request.httprequest.method == "POST":
            try:
                session_token = signer._verify_email_otp(exchange_token, code)
            except AccessError as verification_error:
                error = str(verification_error)
            else:
                return request.redirect(
                    f"/sign/session/{signer.id}/{session_token}", code=303,
                )
        return request.render(
            "usl_sign.portal_sign_email_otp",
            {
                "signer": signer,
                "exchange_token": exchange_token,
                "error": error,
            },
        )

    @http.route(
        "/sign/session/<int:signer_id>/<string:access_token>",
        type="http",
        auth="public",
        methods=["GET"],
        website=True,
        sitemap=False,
    )
    def signing_session(self, signer_id, access_token, review=None, **kwargs):
        del kwargs
        try:
            signer = self._secure_signer(signer_id, access_token)
        except NotFound:
            return request.render("usl_sign.portal_sign_unavailable")
        signer._mark_viewed()
        if not review:
            return request.render(
                "usl_sign.portal_sign_start",
                {
                    "signer": signer,
                    "access_token": access_token,
                    "requested_trust_display": dict(TRUST_LEVELS).get(
                        signer.request_id.requested_trust,
                    ),
                    "due_display": format_datetime(
                        request.env,
                        signer.request_id.expires_at,
                        dt_format="short",
                    )
                    if signer.request_id.expires_at
                    else False,
                },
            )
        if signer.request_id.requested_trust == "strong_personal":
            if not signer._active_enrollment():
                return _secure_strong_response(
                    request.render(
                        "usl_sign.strong_identity_required_page",
                        {"signer": signer},
                    ),
                )
            script_nonce = secrets.token_urlsafe(24)
            return _secure_strong_response(
                request.render(
                    "usl_sign.portal_sign_document",
                    {
                        "doc": signer.request_id,
                        "partner": signer.partner_id,
                        "signer": signer,
                        "access_token": access_token,
                        "strong_signing": True,
                        "script_nonce": script_nonce,
                        "certificate_subject": _personal_certificate_subject(signer),
                        "sign_oca_backend_info": {
                            "access_token": access_token,
                            "signer_id": signer.id,
                            "lang": signer.partner_id.lang,
                        },
                    },
                ),
                script_nonce=script_nonce,
            )
        return request.render(
            "usl_sign.portal_sign_document",
            {
                "doc": signer.request_id,
                "partner": signer.partner_id,
                "signer": signer,
                "access_token": access_token,
                "strong_signing": False,
                "script_nonce": False,
                "certificate_subject": False,
                "sign_oca_backend_info": {
                    "access_token": access_token,
                    "signer_id": signer.id,
                    "lang": signer.partner_id.lang,
                },
            },
        )

    @http.route(
        "/sign/user/<int:signer_id>",
        type="http",
        auth="user",
        methods=["GET"],
        website=True,
    )
    def authenticated_signing(self, signer_id):
        signer = request.env["sign.oca.request.signer"].browse(signer_id).exists()
        if (
            not signer
            or signer.partner_id != request.env.user.partner_id
        ):
            return request.not_found()
        token = signer._issue_access_token()
        return self.exchange_signing_link(signer.id, token)

    @http.route(
        ["/sign_oca/content/<int:signer_id>/<string:access_token>"],
        type="http",
        auth="public",
        website=True,
    )
    def get_sign_oca_content_access(self, signer_id, access_token):
        try:
            signer = self._secure_signer(signer_id, access_token)
        except NotFound:
            return request.not_found()
        stream = request.env["ir.binary"]._get_stream_from(
            signer.request_id,
            "data",
            filename=signer.request_id.filename,
            mimetype="application/pdf",
        )
        return stream.get_response()

    @http.route(
        ["/sign_oca/info/<int:signer_id>/<string:access_token>"],
        type="jsonrpc",
        auth="public",
        website=True,
    )
    def get_sign_oca_info_access(self, signer_id, access_token):
        signer = self._secure_signer(signer_id, access_token)
        return signer.get_info(access_token=access_token)

    @http.route(
        ["/sign_oca/sign/<int:signer_id>/<string:access_token>"],
        type="jsonrpc",
        auth="public",
        website=True,
    )
    def get_sign_oca_sign_access(
        self,
        signer_id,
        access_token,
        items,
        document_sha256,
        latitude=False,
        longitude=False,
        consent=False,
        location=None,
        browser_context=None,
    ):
        signer = self._secure_signer(signer_id, access_token)
        action = signer.action_sign(
            items,
            access_token=access_token,
            document_sha256=document_sha256,
            latitude=latitude,
            longitude=longitude,
            consent=consent,
            location=location,
            browser_context=browser_context,
        )
        request.session[SIGN_RESULT_SESSION_KEY] = {
            "status": "success",
            "company_id": signer.request_id.company_id.id,
            "request_name": signer.request_id.name,
            "request_id": signer.request_id.id,
        }
        return action

    @http.route(
        "/sign/decline/<int:signer_id>/<string:access_token>",
        type="jsonrpc",
        auth="public",
        website=True,
    )
    def decline(self, signer_id, access_token, reason):
        signer = self._secure_signer(signer_id, access_token)
        result = signer.action_decline(reason, access_token=access_token)
        request.session[SIGN_RESULT_SESSION_KEY] = {
            "status": "declined",
            "company_id": signer.request_id.company_id.id,
            "request_name": signer.request_id.name,
        }
        return result

    @http.route(
        "/sign/result/<string:status>",
        type="http",
        auth="public",
        website=True,
        sitemap=False,
    )
    def sign_result(self, status):
        summary = request.session.get(SIGN_RESULT_SESSION_KEY, {})
        if summary.get("status") != status:
            summary = {}
        company = request.env["res.company"].sudo().browse(
            summary.get("company_id"),
        ).exists()
        sign_request = request.env["sign.oca.request"].sudo().browse(
            summary.get("request_id"),
        ).exists()
        result_state = (
            sign_request._signing_result_state()
            if status == "success" and sign_request
            else {}
        )
        return request.render(
            "usl_sign.portal_sign_result",
            {
                "successful": status == "success",
                "declined": status == "declined",
                "result_company": company,
                "result_request_name": summary.get("request_name"),
                "has_pending_signers": result_state.get("has_pending_signers"),
                "final_document_ready": result_state.get("final_document_ready"),
                "result_download_url": (
                    "/sign/result/download" if sign_request else False
                ),
            },
        )

    @http.route(
        "/sign/result/download",
        type="http",
        auth="public",
        methods=["GET"],
        website=True,
        sitemap=False,
    )
    def download_current_signed_document(self):
        summary = request.session.get(SIGN_RESULT_SESSION_KEY, {})
        if summary.get("status") != "success" or not summary.get("request_id"):
            return request.not_found()
        sign_request = request.env["sign.oca.request"].sudo().browse(
            summary["request_id"],
        ).exists()
        if not sign_request or not sign_request.data:
            return request.not_found()
        source_name = sign_request.filename or f"{sign_request.name}.pdf"
        stem = source_name[:-4] if source_name.lower().endswith(".pdf") else source_name
        filename = (
            sign_request.final_filename
            if sign_request.final_data and sign_request.data == sign_request.final_data
            else f"{stem}-signed-so-far.pdf"
        )
        stream = request.env["ir.binary"]._get_stream_from(
            sign_request,
            "data",
            filename=filename,
            mimetype="application/pdf",
        )
        response = stream.get_response(as_attachment=True)
        response.headers["Cache-Control"] = "no-store, max-age=0"
        return response

    @http.route(
        "/sign/external/<int:journey_id>/document",
        type="http",
        auth="user",
        methods=["GET"],
    )
    def external_document(self, journey_id):
        journey = request.env["usl.sign.external.journey"].browse(journey_id).exists()
        if not journey:
            return request.not_found()
        journey.check_access("read")
        stream = request.env["ir.binary"]._get_stream_from(
            journey.request_id,
            "original_data",
            filename=journey.request_id.original_filename,
            mimetype="application/pdf",
        )
        return stream.get_response(as_attachment=True)

    def get_sign_requests_domain(self, http_request):
        return [
            (
                "request_id.state",
                "in",
                [
                    "sent",
                    "viewed",
                    "partial",
                    "completed",
                    "external_archived",
                    "action_required",
                ],
            ),
            (
                "partner_id",
                "=",
                http_request.env.user.partner_id.id,
            ),
        ]

    def _get_my_sign_requests_searchbar_filters(self):
        return {
            "all": {"label": request.env._("All"), "domain": []},
            "waiting": {
                "label": request.env._("Waiting for me"),
                "domain": [
                    ("request_id.state", "in", ["sent", "viewed", "partial"]),
                    ("state", "!=", "signed"),
                ],
            },
            "completed": {
                "label": request.env._("Completed"),
                "domain": [
                    ("request_id.state", "in", ["completed", "external_archived"]),
                ],
            },
        }

    @http.route()
    def portal_download_signed(self, request_id, **kwargs):
        del kwargs
        sign_request = request.env["sign.oca.request"].browse(request_id).exists()
        partner = request.env.user.partner_id
        if (
            not sign_request
            or sign_request.state not in {"completed", "external_archived"}
            or not sign_request.signer_ids.filtered(
                lambda signer: signer.partner_id == partner,
            )
        ):
            return request.not_found()
        field_name = "data" if sign_request.state == "external_archived" else "final_data"
        filename = (
            sign_request.filename
            if sign_request.state == "external_archived"
            else sign_request.final_filename
        )
        stream = request.env["ir.binary"]._get_stream_from(
            sign_request,
            field_name,
            filename=filename,
            mimetype="application/pdf",
        )
        return stream.get_response(as_attachment=True)
