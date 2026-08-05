import hashlib
import secrets
from datetime import timedelta

from werkzeug.exceptions import NotFound

from odoo import fields, http
from odoo.exceptions import AccessError, MissingError
from odoo.http import request
from odoo.http.stream import Stream
from odoo.tools import email_normalize

from odoo.addons.sign_oca.controllers.main import PortalSign


class SignPortalController(PortalSign):
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
        try:
            signer = self._secure_signer(signer_id, access_token)
        except NotFound:
            return request.render("usl_sign.portal_sign_unavailable")
        signer._mark_viewed()
        if signer.request_id.requested_assurance == "qualified":
            return request.render(
                "usl_sign.portal_sign_qualified_redirect",
                {
                    "signer": signer,
                    "signature_link": signer.provider_signature_link,
                },
            )
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
        type="jsonrpc",
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

    @http.route(
        "/sign/result/<string:status>",
        type="http",
        auth="public",
        methods=["GET"],
        website=True,
        sitemap=False,
    )
    def sign_result(self, status):
        return request.render(
            "usl_sign.portal_sign_result",
            {"successful": status == "success", "declined": status == "declined"},
        )

    def get_sign_requests_domain(self, http_request):
        return [
            (
                "request_id.state",
                "in",
                ["sent", "viewed", "partial", "completed", "action_required"],
            ),
            (
                "partner_id",
                "child_of",
                [http_request.env.user.partner_id.commercial_partner_id.id],
            ),
        ]

    @http.route()
    def get_sign_oca_content_access(self, signer_id, access_token):
        """Keep the OCA PDF route compatible with Odoo 19's HTTP package."""
        try:
            signer_sudo = self._document_check_access(
                "sign.oca.request.signer", signer_id, access_token,
            )
        except (AccessError, MissingError):
            return request.redirect("/my")
        return Stream.from_binary_field(
            signer_sudo.request_id, "data",
        ).get_response(mimetype="application/pdf")

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
                "domain": [("request_id.state", "=", "completed")],
            },
        }

    @http.route()
    def portal_download_signed(self, request_id, **kwargs):
        del kwargs
        sign_request = request.env["sign.oca.request"].sudo().browse(request_id).exists()
        partner = request.env.user.partner_id.commercial_partner_id
        permitted = sign_request.signer_ids.filtered(
            lambda signer: signer.partner_id.commercial_partner_id == partner
        )
        if not sign_request or not permitted or sign_request.state != "completed":
            return request.not_found()
        if not sign_request.final_data:
            return request.not_found()
        stream = Stream.from_binary_field(sign_request, "final_data")
        stream.download_name = sign_request.final_filename or "signed-document.pdf"
        return stream.get_response(as_attachment=True)

    def _public_template(self, access_token):
        return (
            request.env["sign.oca.template"]
            .sudo()
            .search([("public_access_token", "=", access_token)], limit=1)
        )

    def _public_source_hash(self):
        secret = request.env["ir.config_parameter"].sudo().get_str("database.secret")
        source = request.httprequest.remote_addr or "unknown"
        return hashlib.sha256(f"{secret}|{source}".encode()).hexdigest()

    @http.route(
        "/sign/public/<string:access_token>",
        type="http",
        auth="public",
        methods=["GET", "POST"],
        website=True,
        sitemap=False,
    )
    def public_sign(self, access_token, **post):
        template = self._public_template(access_token)
        available, explanation = (
            template._public_link_status()
            if template
            else (False, request.env._("This signing link is invalid or no longer available."))
        )
        if not available:
            return request.render(
                "usl_sign.portal_public_unavailable", {"explanation": explanation}
            )
        if request.httprequest.method == "GET":
            return request.render(
                "usl_sign.portal_public_identity",
                {
                    "template": template,
                    "submission_token": secrets.token_urlsafe(32),
                    "errors": {},
                    "values": {},
                },
            )
        values = {
            "name": (post.get("name") or "").strip(),
            "email": (post.get("email") or "").strip(),
            "mobile": (post.get("mobile") or "").strip(),
        }
        normalized_email = email_normalize(values["email"])
        errors = {}
        if len(values["name"]) < 2:
            errors["name"] = request.env._("Enter your full name.")
        if not normalized_email:
            errors["email"] = request.env._("Enter a valid email address.")
        if template.policy_id.authentication_method == "otp_sms" and not values["mobile"]:
            errors["mobile"] = request.env._("Enter the mobile number used for verification.")
        if not post.get("consent"):
            errors["consent"] = request.env._("Confirm that the information belongs to you.")
        submission_token = post.get("submission_token") or ""
        if len(submission_token) < 32:
            errors["form"] = request.env._("This form expired. Reload the page and try again.")
        source_hash = self._public_source_hash()
        recent = request.env["usl.sign.public.submission"].sudo().search_count(
            [
                ("source_hash", "=", source_hash),
                ("create_date", ">=", fields.Datetime.now() - timedelta(hours=1)),
            ],
            limit=11,
        )
        if recent >= 10:
            errors["form"] = request.env._("Too many attempts. Please try again later.")
        if errors:
            return request.render(
                "usl_sign.portal_public_identity",
                {
                    "template": template,
                    "submission_token": submission_token or secrets.token_urlsafe(32),
                    "errors": errors,
                    "values": values,
                },
            )
        request.env["usl.sign.public.submission"].sudo()._create_submission(
            template,
            {
                "name": values["name"],
                "email": normalized_email,
                "phone": values["mobile"] or False,
            },
            submission_token,
            source_hash,
        )
        return request.render(
            "usl_sign.portal_public_received",
            {"company": template.company_id},
        )
