import hashlib
import hmac
import json

from werkzeug.exceptions import BadRequest, Forbidden, NotFound

from odoo import http
from odoo.exceptions import ValidationError
from odoo.http import request


class SignWebhookController(http.Controller):
    @http.route(
        "/sign/webhooks/yousign/<int:company_id>",
        type="http",
        auth="none",
        methods=["POST"],
        csrf=False,
        save_session=False,
    )
    def yousign_webhook(self, company_id):
        raw = request.httprequest.get_data(cache=True)
        if len(raw) > 1_000_000:
            raise BadRequest()
        company = request.env["res.company"].sudo().browse(company_id).exists()
        if not company:
            raise NotFound()
        try:
            configuration = company._sign_webhook_configuration()
        except ValidationError as error:
            raise Forbidden() from error
        supplied = request.httprequest.headers.get("X-Yousign-Signature-256", "")
        expected = "sha256=" + hmac.new(
            configuration["webhook_secret"].encode(), raw, hashlib.sha256
        ).hexdigest()
        if not hmac.compare_digest(supplied, expected):
            raise Forbidden()
        try:
            payload = json.loads(raw)
        except (TypeError, ValueError) as error:
            raise BadRequest() from error
        expected_sandbox = configuration["environment"] == "sandbox"
        if payload.get("sandbox") is not expected_sandbox:
            raise Forbidden()
        event_id = payload.get("event_id")
        if not event_id:
            raise BadRequest()
        if request.env["usl.sign.provider.event"].sudo().search_count(
            [("provider_code", "=", "yousign"), ("event_id", "=", event_id)],
            limit=1,
        ):
            return request.make_response("", status=204)
        provider_request_id = request.env[
            "sign.oca.request"
        ]._provider_request_id_from_event(payload)
        sign_request = request.env["sign.oca.request"].sudo().search(
            [
                ("company_id", "=", company.id),
                ("provider_code", "=", "yousign"),
                ("provider_environment", "=", configuration["environment"]),
                ("provider_transaction_id", "=", provider_request_id),
            ],
            limit=1,
        )
        if not sign_request:
            raise NotFound()
        sign_request._apply_provider_event(payload)
        return request.make_response("", status=204)
