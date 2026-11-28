"""Browser page and token endpoint that connect an Agent to the MCP OAuth server."""

import os
import re
import time
from urllib.parse import urlencode, urlsplit

from werkzeug.exceptions import BadRequest, NotFound

from odoo import SUPERUSER_ID, _, api
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.http import Controller, request, route

CONNECT_PATH = "/usl/agent-access/connect"
TOKEN_PATH = "/usl/agent-access/token"
MCP_ORIGIN_VARIABLE = "USL_MCP_PUBLIC_ORIGIN"
HANDOFF_VARIABLE = "USL_MCP_OAUTH_ODOO_HANDOFF"
MCP_CALLBACK_PATH = "/oauth/enroll/callback"
# The same window as ``check_identity`` in ``base``, which protects the wizard
# that creates an Agent API key in the backend.
IDENTITY_WINDOW_SECONDS = 10 * 60

_STATE_RE = re.compile(r"[A-Za-z0-9_-]{16,128}")
_TOKEN_RE = re.compile(r"[A-Za-z0-9_-]{43}")
_NO_STORE = [("Cache-Control", "no-store")]
_PAGE_HEADERS = {
    "Cache-Control": "no-store",
    "Content-Security-Policy": "frame-ancestors 'none'",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
}


def mcp_return_url():
    """The only URL that can receive a connection code, or ``None``.

    The deployment enables the handoff on both services and names the public
    origin of its MCP server.
    """
    if os.getenv(HANDOFF_VARIABLE, "").lower() not in {"1", "true"}:
        return None
    origin = os.getenv(MCP_ORIGIN_VARIABLE, "").strip().rstrip("/")
    parsed = urlsplit(origin)
    if parsed.scheme not in ("http", "https") or not parsed.netloc or parsed.path or parsed.query:
        return None
    return f"{origin}{MCP_CALLBACK_PATH}"


class AgentConnectController(Controller):
    def _parameters(self, values):
        allowed = mcp_return_url()
        if not allowed:
            raise NotFound()
        state = values.get("state") or ""
        code_challenge = values.get("code_challenge") or ""
        if (
            values.get("return_url") != allowed
            or not _STATE_RE.fullmatch(state)
            or not _TOKEN_RE.fullmatch(code_challenge)
        ):
            raise BadRequest()
        client_name = "".join(
            character if character.isprintable() else " "
            for character in str(values.get("client_name") or "")
        )
        return {
            "state": state,
            "code_challenge": code_challenge,
            "return_url": allowed,
            "client_name": " ".join(client_name.split())[:80] or _("MCP client"),
        }

    def _return(self, parameters, **result):
        return f"{parameters['return_url']}?{urlencode({**result, 'state': parameters['state']})}"

    def _render(self, template, parameters, status=200, **values):
        response = request.render(
            template,
            {
                **values,
                "parameters": parameters,
                "cancel_url": self._return(parameters, error="access_denied"),
                "manual_url": self._return(parameters, error="manual_entry"),
            },
        )
        response.status_code = status
        response.headers.update(_PAGE_HEADERS)
        return response

    def _owned_agents(self):
        # The backend list hides an Agent when its default company is not
        # selected in the company switcher. This page reads with every company
        # of the user so the list is complete.
        user = request.env.user
        return (
            request.env["usl.agent"]
            .with_context(allowed_company_ids=user.company_ids.ids)
            .search([("owner_id", "=", user.id)], order="state, name, id")
        )

    @route(
        CONNECT_PATH,
        type="http",
        auth="public",
        methods=["GET", "POST"],
        sitemap=False,
    )
    def connect(self, **values):
        parameters = self._parameters(values)
        here = f"{CONNECT_PATH}?{urlencode(parameters)}"
        if request.session.uid is None:
            return self._render(
                "usl_access_control.agent_connect_sign_in",
                parameters,
                login_url=f"/web/login?{urlencode({'redirect': here})}",
            )
        user = request.env.user
        if not user._is_internal() or user.usl_is_ai_agent:
            return self._render("usl_access_control.agent_connect_denied", parameters, status=403)
        if request.session.get("identity-check-last", 0) <= time.time() - IDENTITY_WINDOW_SECONDS:
            return request.redirect(f"/web/session/identity?{urlencode({'redirect': here})}", 303)
        error = None
        if request.httprequest.method == "POST":
            try:
                # A refused grant must not leave a new Agent behind.
                with request.env.cr.savepoint():
                    agent = self._selected_agent(values)
                    code = request.env["usl.agent.connect.grant"]._issue(
                        agent,
                        code_challenge=parameters["code_challenge"],
                        return_url=parameters["return_url"],
                        client_name=parameters["client_name"],
                    )
            except (AccessError, UserError, ValidationError) as exception:
                error = str(exception)
            else:
                response = request.redirect(self._return(parameters, code=code), 303, local=False)
                response.headers.update(_PAGE_HEADERS)
                return response
        return self._render(
            "usl_access_control.agent_connect",
            parameters,
            agents=self._owned_agents(),
            error=error,
            selected=values.get("agent") or "",
            new_name=values.get("name") or "",
            new_purpose=values.get("purpose") or "",
        )

    def _selected_agent(self, values):
        choice = values.get("agent") or ""
        if choice == "new":
            name = (values.get("name") or "").strip()
            purpose = (values.get("purpose") or "").strip()
            if not name or not purpose:
                raise UserError(_("Give the new Agent a name and a purpose."))
            user = request.env.user
            return (
                request.env["usl.agent"]
                .with_context(allowed_company_ids=user.company_ids.ids)
                .create({"name": name[:120], "purpose": purpose[:2000]})
            )
        agent = self._owned_agents().filtered(lambda record: str(record.id) == choice)
        if not agent:
            raise UserError(_("Select an Agent."))
        return agent

    @route(
        TOKEN_PATH,
        type="http",
        auth="none",
        methods=["POST"],
        csrf=False,
        save_session=False,
        # The exchange locks and writes the grant. A route without a user
        # defaults to a read-only cursor.
        readonly=False,
    )
    def token(self, code=None, code_verifier=None, **_values):
        if not mcp_return_url():
            raise NotFound()
        if not _TOKEN_RE.fullmatch(code or "") or not _TOKEN_RE.fullmatch(code_verifier or ""):
            return request.make_json_response({"error": "invalid_request"}, headers=_NO_STORE, status=400)
        env = api.Environment(request.env.cr, SUPERUSER_ID, {})
        result = env["usl.agent.connect.grant"]._redeem(code, code_verifier)
        if not result:
            return request.make_json_response({"error": "invalid_grant"}, headers=_NO_STORE, status=400)
        return request.make_json_response(result, headers=_NO_STORE)
