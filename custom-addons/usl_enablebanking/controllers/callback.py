"""Authenticated browser return; no anonymous account/session mutation."""
import hashlib

from werkzeug.exceptions import BadRequest
from werkzeug.utils import redirect

from odoo import http
from odoo.http import request

from ..services.protocol import ProviderDataError


class EnableBankingCallback(http.Controller):
    @http.route("/enablebanking/callback", type="http", auth="user", methods=["GET"], readonly=False)
    def callback(self, state=None, code=None, error=None, **unused):
        if not state or len(state) > 512:
            raise BadRequest("Invalid bank return")
        # Find only the opaque state's owner with a narrow elevation, then
        # return to the request user's ACL/company context before any action.
        digest = hashlib.sha256(state.encode()).hexdigest()
        ids = request.env["usl.enablebanking.connection"].sudo().search([
            ("state_hash", "=", digest), ("state_user_id", "=", request.env.uid),
        ], limit=2).ids
        if len(ids) != 1:
            raise BadRequest("Expired bank return")
        connection = request.env["usl.enablebanking.connection"].browse(ids)
        connection._consume_state(state)
        if error or not code:
            connection._set_error("consent_declined")
        else:
            try:
                with request.env.cr.savepoint():
                    connection._apply_session(connection._client().session(code))
            except (ProviderDataError, ValueError, KeyError, TypeError) as exception:
                connection._set_error(str(exception) if isinstance(exception, ProviderDataError) else "invalid_session")
        result = redirect(f"/odoo/action-usl_enablebanking.connection_action/{connection.id}", code=303)
        result.headers["Cache-Control"] = "no-store, max-age=0"
        result.headers["Referrer-Policy"] = "no-referrer"
        return result
