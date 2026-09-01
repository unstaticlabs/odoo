import base64
import hashlib
import logging
import secrets
import time
from urllib.parse import urlencode, urlsplit

import werkzeug.urls
from werkzeug.exceptions import NotFound

from odoo import SUPERUSER_ID, _
from odoo.exceptions import AccessDenied
from odoo.http import request, route
from odoo.http.router import db_filter
from odoo.http.session import authenticate
from odoo.http.session import logout as session_logout
from odoo.tools import config

from ..exceptions import PocketIDAccessDenied, PocketIDReason
from ..models.oidc_identity import identity_fingerprint
from ..policy import (
    EMERGENCY_SESSION_KEY,
    END_SESSION_URL_SESSION_KEY,
    ID_TOKEN_SESSION_KEY,
    REAUTH_SESSION_KEY,
    emergency_window_active,
    is_sso_only,
)
from odoo.addons.auth_oauth.controllers.main import OAuthController
from odoo.addons.auth_oidc.controllers.main import OpenIDLogin
from odoo.addons.web.controllers.database import Database
from odoo.addons.web.controllers.session import Session as WebSession
from odoo.addons.web.controllers.utils import _get_login_redirect_url, ensure_db

_logger = logging.getLogger(__name__)

_STATE_PREFIX = "usl_pocketid_"
_TRANSACTIONS_KEY = "usl_pocketid_transactions"
_TRANSACTION_MAX_AGE = 300
_TRANSACTION_LIMIT = 5
_SSO_LOGOUT_BRIDGE_PATH = "/usl/pocketid/sso-logout"
_ODOO_ANDROID_PACKAGE = "com.odoo.mobile"


def _is_odoo_store_app_request():
    """Identify the unsupported Odoo store app without blocking mobile browsers."""
    requested_with = request.httprequest.headers.get("X-Requested-With", "")
    user_agent = request.httprequest.user_agent.string or ""
    normalized_user_agent = " ".join(user_agent.lower().split())
    compact_user_agent = normalized_user_agent.replace(" ", "")
    return (
        requested_with.strip().lower() == _ODOO_ANDROID_PACKAGE
        or "odoo mobile" in normalized_user_agent
        or "odoomobile" in compact_user_agent
    )


def _allowed_end_session_url(url, provider):
    """Accept only the configured Pocket ID end-session endpoint."""
    if not url or not provider or not provider.usl_end_session_endpoint:
        return False
    target = urlsplit(url)
    expected = urlsplit(provider.usl_end_session_endpoint)
    return (
        target.scheme == expected.scheme
        and target.netloc == expected.netloc
        and target.path == expected.path
        and not target.fragment
    )


def _build_end_session_url(provider, *, id_token=None):
    parameters = {
        "post_logout_redirect_uri": (
            provider.usl_public_base_url.rstrip("/") + "/web/login"
        ),
        "client_id": provider.client_id,
    }
    if id_token:
        parameters["id_token_hint"] = id_token
    return (
        provider.usl_end_session_endpoint
        + "?"
        + urlencode(parameters)
    )


def _error_message(error_code):
    if error_code == "configuration":
        return _(
            "Pocket ID is not configured for this Odoo environment. "
            "Contact an administrator.",
        )
    if error_code == "group_required":
        return _(
            "Your Pocket ID account is not authorized for this Odoo environment.",
        )
    if error_code == "identity_conflict":
        return _(
            "Your Pocket ID identity cannot be linked safely. "
            "Contact an administrator and mention an identity conflict.",
        )
    if error_code == "identity_disabled":
        return _("This Pocket ID identity has been disabled in Odoo.")
    if error_code == "identity_unlinked":
        return _("Your Pocket ID identity is not linked to an approved Odoo user.")
    if error_code == "provider_denied":
        return _("Pocket ID did not authorize this sign-in.")
    if error_code == "provider_unavailable":
        return _("Pocket ID is temporarily unavailable. Please try again later.")
    if error_code == "state":
        return _(
            "This Pocket ID sign-in request expired or was already used. "
            "Please start again.",
        )
    if error_code == "token_invalid":
        return _("Pocket ID returned a sign-in response Odoo could not validate.")
    if error_code == "user_disabled":
        return _("The linked Odoo user is not enabled for Pocket ID login.")
    if error_code == "sso_required":
        return _("This Odoo Distribution accepts Pocket ID sign-in only.")
    return _("Pocket ID sign-in failed safely. Please try again.")


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


def _store_transaction(provider, redirect_path, *, purpose="login"):
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
        "purpose": purpose,
        "uid": request.session.uid if purpose == "reauth" else False,
    }
    request.session[_TRANSACTIONS_KEY] = _pruned_transactions(transactions)
    return state, nonce, code_verifier


def _authorization_link(provider, redirect_path, *, purpose="login"):
    state, nonce, code_verifier = _store_transaction(
        provider,
        redirect_path,
        purpose=purpose,
    )
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(code_verifier.encode("ascii")).digest(),
    ).rstrip(b"=").decode("ascii")
    parameters = {
        "response_type": "code",
        "client_id": provider["client_id"],
        "redirect_uri": provider["usl_public_base_url"].rstrip("/")
        + "/auth_oauth/signin",
        "scope": provider["scope"],
        "state": state,
        "nonce": nonce,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    if purpose == "reauth":
        parameters["prompt"] = "login"
    return f"{provider['auth_endpoint']}?{urlencode(parameters)}"


def _consume_transaction(state):
    transactions = _pruned_transactions(request.session.get(_TRANSACTIONS_KEY))
    transaction = transactions.pop(state, None)
    request.session[_TRANSACTIONS_KEY] = transactions
    return transaction


def _validate_callback_provider(provider, transaction, callback_parameters):
    if (
        not provider
        or not provider.enabled
        or not provider.usl_pocketid
        or transaction["redirect_uri"] != provider._usl_pocketid_redirect_uri()
        or callback_parameters.get("error")
    ):
        raise PocketIDAccessDenied(PocketIDReason.PROVIDER_DENIED)


def _validate_reauthentication_identity(user, transaction):
    if (
        not request.session.uid
        or request.session.uid != transaction.get("uid")
        or user.id != request.session.uid
    ):
        raise PocketIDAccessDenied(PocketIDReason.IDENTITY_CONFLICT)


def _require_database_manager_enabled():
    if not config["list_db"]:
        raise NotFound()


def _audit_state_denial():
    dbname = request.session.db
    if not dbname or not db_filter([dbname]):
        return
    try:
        ensure_db(db=dbname)
        request.env["usl.oidc.audit.event"]._record(
            event_type="login_denied",
            reason_code="state",
        )
        request.env.cr.commit()
    except Exception:
        request.env.cr.rollback()
        _logger.exception("Pocket ID state denial could not be audited.")


class PocketIDLogin(OpenIDLogin):
    def list_providers(self):
        providers = super().list_providers()
        redirect_path = _safe_redirect_path(request.params.get("redirect"))
        for provider in providers:
            if not provider.get("usl_pocketid"):
                continue
            provider["auth_link"] = _authorization_link(
                provider,
                redirect_path,
            )
        return providers

    @route()
    def web_login(self, *args, **kwargs):
        ensure_db()
        if (
            is_sso_only(request.env)
            and request.httprequest.method == "POST"
        ):
            return request.redirect("/web/login?sso_error=sso_required", 303)
        response = super().web_login(*args, **kwargs)
        if response.is_qweb:
            request.session.pop(END_SESSION_URL_SESSION_KEY, None)
            sso_only = is_sso_only(request.env)
            response.qcontext["usl_sso_only"] = sso_only
            if sso_only:
                providers = [
                    provider
                    for provider in response.qcontext.get("providers", [])
                    if provider.get("usl_pocketid")
                ]
                response.qcontext["usl_pocketid_provider"] = (
                    providers[0] if len(providers) == 1 else False
                )
                response.qcontext["usl_odoo_store_app"] = (
                    _is_odoo_store_app_request()
                )
                response.qcontext["usl_pwa_login_url"] = (
                    providers[0]["usl_public_base_url"].rstrip("/") + "/web/login"
                    if len(providers) == 1
                    else False
                )
                response.qcontext["disable_database_manager"] = True
            error_code = request.params.get("sso_error")
            if error_code:
                response.qcontext["error"] = _error_message(error_code)
        return response

    @route(
        "/usl/emergency-login",
        type="http",
        auth="none",
        methods=["GET", "POST"],
        readonly=False,
        sitemap=False,
    )
    def emergency_login(self, redirect="/odoo", **kwargs):
        if not emergency_window_active():
            raise NotFound()
        ensure_db()
        # Mirror Odoo's hybrid public/user login setup.  ``auth='none'`` is
        # required before a database is selected, but the login layout still
        # needs a real public user once the database is known.
        if request.env.uid is None:
            if request.session.uid is None:
                request.env["ir.http"]._auth_method_public()
            else:
                request.update_env(user=request.session.uid)
        error = False
        if request.httprequest.method == "POST":
            login = (kwargs.get("login") or "").strip()
            password = kwargs.get("password") or ""
            users = request.env["res.users"].sudo().with_context(
                active_test=False,
            )
            user = users.search(users._get_login_domain(login), limit=2)
            if len(user) == 1 and user.active and user.usl_local_break_glass:
                request.session[EMERGENCY_SESSION_KEY] = {"uid": user.id}
                try:
                    auth_info = authenticate(
                        request.session,
                        request.env,
                        {
                            "login": login,
                            "password": password,
                            "type": "password",
                        },
                    )
                except AccessDenied:
                    request.session.pop(EMERGENCY_SESSION_KEY, None)
                else:
                    request.env["usl.oidc.audit.event"]._record(
                        event_type="login_success",
                        reason_code="sealed_emergency_login",
                        user_id=user.id,
                    )
                    request.env.cr.commit()
                    return request.redirect(
                        _get_login_redirect_url(auth_info["uid"], redirect),
                        303,
                    )
            request.env["usl.oidc.audit.event"]._record(
                event_type="login_denied",
                reason_code="sealed_emergency_denied",
            )
            request.env.cr.commit()
            error = _("Emergency credentials were not accepted.")
        return request.render(
            "usl_pocketid.emergency_login",
            {
                "error": error,
                "redirect": _safe_redirect_path(redirect),
                "disable_database_manager": True,
            },
        )

    @route(
        "/usl/pocketid/reauth/start",
        type="http",
        auth="user",
        methods=["GET"],
        check_identity=False,
    )
    def reauth_start(self):
        if not is_sso_only(request.env) or not request.env.user.usl_pocketid_access:
            raise NotFound()
        provider = request.env.ref("usl_pocketid.provider_pocketid").sudo()
        provider_values = provider.read()[0]
        return request.redirect(
            _authorization_link(
                provider_values,
                "/usl/pocketid/reauth/complete",
                purpose="reauth",
            ),
            303,
        )

    @route(
        "/usl/pocketid/reauth/complete",
        type="http",
        auth="user",
        methods=["GET"],
        check_identity=False,
    )
    def reauth_complete(self, error=None):
        return request.render(
            "usl_pocketid.reauth_complete",
            {"error": error},
        )

    @route()
    def web_auth_signup(self, *args, **kwargs):
        if not is_sso_only(request.env):
            return super().web_auth_signup(*args, **kwargs)
        return request.redirect("/web/login?sso_error=sso_required", 303)

    @route()
    def web_auth_reset_password(self, *args, **kwargs):
        if not is_sso_only(request.env):
            return super().web_auth_reset_password(*args, **kwargs)
        return request.redirect("/web/login?sso_error=sso_required", 303)


class PocketIDController(OAuthController):
    @route()
    def signin(self, **kwargs):
        state = kwargs.get("state", "")
        if not state.startswith(_STATE_PREFIX):
            return super().signin(**kwargs)
        transaction = _consume_transaction(state)
        if not transaction:
            _audit_state_denial()
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
                _validate_callback_provider(provider, transaction, kwargs)
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
                if transaction.get("purpose") == "reauth":
                    user, identity = request.env["res.users"].with_user(
                        SUPERUSER_ID,
                    )._usl_pocketid_resolve_user(provider, claims)
                    _validate_reauthentication_identity(user, transaction)
                    request.session[REAUTH_SESSION_KEY] = {
                        "uid": user.id,
                        "expires_at": time.time() + 60,
                    }
                    request.env["usl.oidc.audit.event"]._record(
                        event_type="login_success",
                        reason_code="sensitive_action_reauthentication",
                        provider_id=provider.id,
                        identity_id=identity.id,
                        user_id=user.id,
                        subject_fingerprint=identity.subject_fingerprint,
                    )
                    return request.redirect(
                        "/usl/pocketid/reauth/complete",
                        303,
                    )
                _db, login, key, identity = request.env["res.users"].with_user(
                    SUPERUSER_ID,
                )._usl_pocketid_login(provider, claims, access_token)
            request.env.cr.commit()
            request.session["identity-check-last"] = time.time()

            credential = {"login": login, "token": key, "type": "oauth_token"}
            auth_info = authenticate(request.session, request.env, credential)
            request.session[ID_TOKEN_SESSION_KEY] = id_token
            request.env["usl.oidc.audit.event"]._record(
                event_type="login_success",
                reason_code="validated_oidc",
                provider_id=provider.id,
                identity_id=identity.id,
                user_id=identity.user_id.id,
                subject_fingerprint=identity.subject_fingerprint,
            )
            request.env.cr.commit()
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
        if transaction.get("purpose") == "reauth" and request.session.uid:
            response = request.redirect(
                "/usl/pocketid/reauth/complete?"
                + urlencode({"error": _error_message(reason)}),
                303,
            )
        else:
            response = request.redirect(
                f"/web/login?{urlencode({'sso_error': reason})}",
                303,
            )
        response.autocorrect_location_header = False
        return response


class PocketIDSession(WebSession):
    @route()
    def logout(self, redirect="/web/login"):
        provider = False
        if request.db:
            provider = request.env.ref(
                "usl_pocketid.provider_pocketid",
                raise_if_not_found=False,
            )
            if provider:
                provider = provider.sudo()
        sso_only = bool(provider and is_sso_only(request.env))
        end_session_endpoint = provider.usl_end_session_endpoint if provider else False
        public_base_url = provider.usl_public_base_url if provider else False
        id_token = request.session.get(ID_TOKEN_SESSION_KEY)
        session_logout(request.session, keep_db=True)
        # The webclient fetch()-follows this 303, then location.assign()s the
        # final same-origin URL. A direct cross-origin Location breaks CORS and
        # Odoo's same-origin redirect() guard, so bridge through a local page.
        if (
            sso_only
            and end_session_endpoint
            and public_base_url
            and id_token
        ):
            request.session[END_SESSION_URL_SESSION_KEY] = {
                "url": _build_end_session_url(provider, id_token=id_token),
                "created_at": time.time(),
            }
            return request.redirect(_SSO_LOGOUT_BRIDGE_PATH, 303)
        request.session.pop(END_SESSION_URL_SESSION_KEY, None)
        return request.redirect("/web/login" if sso_only else redirect, 303)

    @route(
        _SSO_LOGOUT_BRIDGE_PATH,
        type="http",
        auth="none",
        methods=["GET"],
        readonly=True,
        sitemap=False,
    )
    def sso_logout_bridge(self):
        provider = False
        if request.db:
            provider = request.env.ref(
                "usl_pocketid.provider_pocketid",
                raise_if_not_found=False,
            )
            if provider:
                provider = provider.sudo()
        payload = request.session.get(END_SESSION_URL_SESSION_KEY) or {}
        end_session_url = payload.get("url") if isinstance(payload, dict) else None
        created_at = payload.get("created_at", 0) if isinstance(payload, dict) else 0
        # The webclient fetch()-follows this page once, then location.assign()s
        # it again. Keep the URL until TTL so both hits can render the bridge.
        if (
            not _allowed_end_session_url(end_session_url, provider)
            or time.time() - created_at > 120
        ):
            request.session.pop(END_SESSION_URL_SESSION_KEY, None)
            return request.redirect("/web/login", 303)
        return request.render(
            "usl_pocketid.sso_logout_redirect",
            {"url": end_session_url},
        )


class PocketIDDatabase(Database):
    """Make ``list_db=False`` a complete database-manager boundary."""

    @route()
    def selector(self, **kwargs):
        _require_database_manager_enabled()
        return super().selector(**kwargs)

    @route()
    def manager(self, **kwargs):
        _require_database_manager_enabled()
        return super().manager(**kwargs)

    @route()
    def create(self, master_pwd, name, lang, password, **post):
        _require_database_manager_enabled()
        return super().create(master_pwd, name, lang, password, **post)

    @route()
    def duplicate(self, master_pwd, name, new_name, neutralize_database=False):
        _require_database_manager_enabled()
        return super().duplicate(
            master_pwd,
            name,
            new_name,
            neutralize_database=neutralize_database,
        )

    @route()
    def drop(self, master_pwd, name):
        _require_database_manager_enabled()
        return super().drop(master_pwd, name)

    @route()
    def backup(self, master_pwd, name, backup_format="zip", filestore=True):
        _require_database_manager_enabled()
        return super().backup(
            master_pwd,
            name,
            backup_format=backup_format,
            filestore=filestore,
        )

    @route()
    def restore(
        self,
        master_pwd,
        backup_file,
        name,
        copy=False,
        neutralize_database=False,
    ):
        _require_database_manager_enabled()
        return super().restore(
            master_pwd,
            backup_file,
            name,
            copy=copy,
            neutralize_database=neutralize_database,
        )

    @route()
    def change_password(self, master_pwd, master_pwd_new):
        _require_database_manager_enabled()
        return super().change_password(master_pwd, master_pwd_new)

    @route()
    def list(self):
        _require_database_manager_enabled()
        return super().list()
