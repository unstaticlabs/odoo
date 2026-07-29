import base64
import hashlib
import logging
import secrets
import time
from urllib.parse import urlencode, urlsplit

import werkzeug.urls

from odoo import SUPERUSER_ID, _
from odoo.exceptions import AccessDenied
from odoo.http import request, route
from odoo.http.router import db_filter
from odoo.http.session import authenticate

from odoo.addons.auth_oauth.controllers.main import OAuthController
from odoo.addons.auth_oidc.controllers.main import OpenIDLogin
from odoo.addons.web.controllers.utils import _get_login_redirect_url, ensure_db

from ..exceptions import PocketIDAccessDenied
from ..models.oidc_identity import identity_fingerprint

_logger = logging.getLogger(__name__)

_STATE_PREFIX = "usl_pocketid_"
_TRANSACTIONS_KEY = "usl_pocketid_transactions"
_TRANSACTION_MAX_AGE = 300
_TRANSACTION_LIMIT = 5

_ERROR_MESSAGES = {
    "configuration": _(
        "Pocket ID is not configured for this Odoo environment. "
        "Contact an administrator."
    ),
    "group_required": _(
        "Your Pocket ID account is not authorized for this Odoo environment."
    ),
    "identity_conflict": _(
        "Your Pocket ID identity cannot be linked safely. "
        "Contact an administrator and mention an identity conflict."
    ),
    "identity_disabled": _("This Pocket ID identity has been disabled in Odoo."),
    "identity_unlinked": _(
        "Your Pocket ID identity is not linked to an approved Odoo user."
    ),
    "provider_denied": _("Pocket ID did not authorize this sign-in."),
    "provider_unavailable": _(
        "Pocket ID is temporarily unavailable. Please try again later."
    ),
    "state": _(
        "This Pocket ID sign-in request expired or was already used. "
        "Please start again."
    ),
    "token_invalid": _("Pocket ID returned a sign-in response Odoo could not validate."),
    "user_disabled": _("The linked Odoo user is not enabled for Pocket ID login."),
}


def _safe_redirect_path(value):
    if not value:
        return "/odoo"
    parsed = urlsplit(value)
    if parsed.scheme or parsed.netloc or not parsed.path.startswith("/"):
        return "/odoo"
    if parsed.path.startswith("//"):
        return "/odoo"
    return parsed.path + (f"?{parsed.query}" if parsed.query else "")


def _pruned_transactions(transactions):
    now = time.time()
    valid = {
        key: value
        for key, value in (transactions or {}).items()
        if (
            isinstance(value, dict)
            and now - value.get("created_at", 0) <= _TRANSACTION_MAX_AGE
        )
    }
    return dict(
        sorted(
            valid.items(),
            key=lambda item: item[1]["created_at"],
        )[-_TRANSACTION_LIMIT:],
    )


def _store_transaction(provider, redirect_path):
    state = f"{_STATE_PREFIX}{secrets.token_urlsafe(32)}"
    nonce = secrets.token_urlsafe(32)
    code_verifier = secrets.token_urlsafe(64)
    transactions = _pruned_transactions(request.session.get(_TRANSACTIONS_KEY))
    transactions[state] = {
        "created_at": time.time(),
        "db": request.session.db,
        "provider_id": provider["id"],
        "nonce": nonce,
        "code_verifier": code_verifier,
        "redirect_path": redirect_path,
        "redirect_uri": provider["usl_public_base_url"].rstrip("/")
        + "/auth_oauth/signin",
    }
    request.session[_TRANSACTIONS_KEY] = _pruned_transactions(transactions)
    return state, nonce, code_verifier


def _consume_transaction(state):
    transactions = _pruned_transactions(request.session.get(_TRANSACTIONS_KEY))
    transaction = transactions.pop(state, None)
    request.session[_TRANSACTIONS_KEY] = transactions
    return transaction


class PocketIDLogin(OpenIDLogin):
    def list_providers(self):
        providers = super().list_providers()
        redirect_path = _safe_redirect_path(request.params.get("redirect"))
        for provider in providers:
            if not provider.get("usl_pocketid"):
                continue
            state, nonce, code_verifier = _store_transaction(
                provider,
                redirect_path,
            )
            challenge = base64.urlsafe_b64encode(
                hashlib.sha256(code_verifier.encode("ascii")).digest(),
            ).rstrip(b"=").decode("ascii")
            provider["auth_link"] = "{}?{}".format(
                provider["auth_endpoint"],
                urlencode(
                    {
                        "response_type": "code",
                        "client_id": provider["client_id"],
                        "redirect_uri": provider["usl_public_base_url"].rstrip("/")
                        + "/auth_oauth/signin",
                        "scope": provider["scope"],
                        "state": state,
                        "nonce": nonce,
                        "code_challenge": challenge,
                        "code_challenge_method": "S256",
                    },
                ),
            )
        return providers

    @route()
    def web_login(self, *args, **kwargs):
        response = super().web_login(*args, **kwargs)
        if response.is_qweb:
            error_code = request.params.get("sso_error")
            if error_code:
                response.qcontext["error"] = _ERROR_MESSAGES.get(
                    error_code,
                    _("Pocket ID sign-in failed safely. Please try again."),
                )
        return response


class PocketIDController(OAuthController):
    @route()
    def signin(self, **kwargs):
        state = kwargs.get("state", "")
        if not state.startswith(_STATE_PREFIX):
            return super().signin(**kwargs)
        transaction = _consume_transaction(state)
        if not transaction:
            return request.redirect("/web/login?sso_error=state", 303)
        dbname = transaction.get("db")
        if not dbname or not db_filter([dbname]):
            return request.redirect("/web/login?sso_error=state", 303)
        ensure_db(db=dbname)

        provider = None
        identity = None
        claims = None
        try:
            with request.env.cr.savepoint():
                provider = request.env["auth.oauth.provider"].with_user(
                    SUPERUSER_ID,
                ).browse(transaction["provider_id"]).exists()
                if (
                    not provider
                    or not provider.enabled
                    or not provider.usl_pocketid
                    or transaction["redirect_uri"]
                    != provider._usl_pocketid_redirect_uri()
                    or kwargs.get("error")
                ):
                    raise PocketIDAccessDenied("provider_denied")
                access_token, id_token = provider._usl_exchange_code(
                    code=kwargs.get("code"),
                    code_verifier=transaction["code_verifier"],
                    redirect_uri=transaction["redirect_uri"],
                )
                claims = provider._usl_validate_id_token(
                    id_token=id_token,
                    access_token=access_token,
                    nonce=transaction["nonce"],
                )
                db, login, key, identity = request.env["res.users"].with_user(
                    SUPERUSER_ID,
                )._usl_pocketid_login(provider, claims, access_token)
                request.env["usl.oidc.audit.event"]._record(
                    event_type="login_success",
                    reason_code="validated_oidc",
                    provider_id=provider.id,
                    identity_id=identity.id,
                    user_id=identity.user_id.id,
                    subject_fingerprint=identity.subject_fingerprint,
                )
            request.env.cr.commit()

            credential = {"login": login, "token": key, "type": "oauth_token"}
            auth_info = authenticate(request.session, request.env, credential)
            target = transaction["redirect_path"]
            response = request.redirect(
                _get_login_redirect_url(auth_info["uid"], target),
                303,
            )
            response.autocorrect_location_header = False
            if (
                werkzeug.urls.url_parse(response.location).path == "/web"
                and not request.env.user._is_internal()
            ):
                response.location = "/"
            return response
        except PocketIDAccessDenied as error:
            reason = error.reason
        except AccessDenied:
            reason = "user_disabled"
        except Exception:
            _logger.exception("Unexpected Pocket ID callback failure.")
            reason = "denied"

        fingerprint = (
            identity_fingerprint(provider.usl_oidc_issuer, claims["sub"])
            if provider and claims and claims.get("sub")
            else False
        )
        request.env["usl.oidc.audit.event"]._record(
            event_type="login_denied",
            reason_code=reason,
            provider_id=provider.id if provider else False,
            identity_id=identity.id if identity else False,
            user_id=identity.user_id.id if identity else False,
            subject_fingerprint=fingerprint,
        )
        request.env.cr.commit()
        response = request.redirect(
            f"/web/login?{urlencode({'sso_error': reason})}",
            303,
        )
        response.autocorrect_location_header = False
        return response

