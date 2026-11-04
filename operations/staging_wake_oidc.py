"""Sign-in with Pocket ID for the staging wake page (authorization code + PKCE).

A sleeping staging has no Odoo to authenticate anyone, so the lifecycle daemon
speaks OpenID Connect itself, with the same identity provider and the same
group that guard staging Odoo.  Standard library only: the daemon image carries
no JWT or HTTP libraries.

What is verified, and why that is enough:

* The authorization code is exchanged **directly with the token endpoint over
  TLS** with a confidential client.  OpenID Connect Core 3.1.3.7 allows the
  relying party to rely on that TLS channel instead of checking the ID token's
  signature, so no key handling is needed.  The ID token's ``iss``, ``aud``,
  ``exp`` and ``nonce`` are still checked.
* Identity and groups come from the **userinfo endpoint**, fetched with the
  access token over TLS, and its ``sub`` must equal the ID token's.
* ``state`` and PKCE bind the callback to the browser that started the login.
  They travel in a short-lived signed cookie, so the daemon keeps no session
  table and survives restarts.
* The session is a signed cookie (HMAC keyed from the client secret, so
  rotating the secret signs everyone out), ``HttpOnly``, ``Secure`` and
  ``SameSite=Strict``.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import http.client
import json
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass

LOGIN_TTL_SECONDS = 600
SESSION_TTL_SECONDS = 8 * 3600
HTTP_TIMEOUT_SECONDS = 10
SCOPES = "openid profile email groups"


class OidcError(Exception):
    """A sign-in that must be refused; the message is safe to show a person."""


@dataclass(frozen=True)
class Identity:
    subject: str
    email: str
    name: str

    @property
    def label(self) -> str:
        return self.email or self.subject


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


class _RefuseRedirects(urllib.request.HTTPRedirectHandler):
    """A provider that redirects would be handed our client secret or access token."""

    def redirect_request(self, *args, **kwargs):
        return None


_NO_REDIRECTS = urllib.request.build_opener(_RefuseRedirects)


def same(left: str, right: str) -> bool:
    """Constant-time equality that never raises on non-ASCII input."""
    return hmac.compare_digest(str(left).encode(), str(right).encode())


def default_http(method: str, url: str, *, data: dict | None = None, headers: dict | None = None):
    """Return ``(status, parsed JSON)``.  Only ``https`` is ever contacted."""
    if not url.startswith("https://"):
        raise OidcError("identity provider URLs must use https")
    body = urllib.parse.urlencode(data).encode() if data is not None else None
    request = urllib.request.Request(url, data=body, method=method, headers=headers or {})
    try:
        with _NO_REDIRECTS.open(request, timeout=HTTP_TIMEOUT_SECONDS) as response:
            return response.status, json.loads(response.read().decode())
    except urllib.error.HTTPError as error:
        return error.code, {}
    except (
        urllib.error.URLError, TimeoutError, json.JSONDecodeError, UnicodeDecodeError,
        http.client.HTTPException, OSError,
    ) as error:
        raise OidcError("the identity provider did not answer") from error


class WakeOidc:
    def __init__(
        self, *, issuer: str, client_id: str, client_secret: str, redirect_uri: str,
        required_group: str, http=default_http, clock=time.time,
    ) -> None:
        if not issuer.startswith("https://") or not redirect_uri.startswith("https://"):
            raise OidcError("issuer and redirect URI must use https")
        if not client_id or not client_secret or not required_group:
            raise OidcError("OIDC client is not configured")
        self.issuer = issuer.rstrip("/")
        self.client_id = client_id
        self.client_secret = client_secret
        self.redirect_uri = redirect_uri
        self.required_group = required_group
        self.http = http
        self.clock = clock
        self._key = hmac.new(client_secret.encode(), b"usl-wake-session/v1", hashlib.sha256).digest()
        self._metadata: dict | None = None

    # -- signed cookies ---------------------------------------------------
    def _seal(self, kind: str, payload: dict) -> str:
        body = _b64(json.dumps({"k": kind, **payload}, sort_keys=True).encode())
        return body + "." + _b64(hmac.new(self._key, body.encode(), hashlib.sha256).digest())

    def _open(self, kind: str, value: str | None) -> dict | None:
        if not value or value.count(".") != 1:
            return None
        body, signature = value.split(".")
        expected = _b64(hmac.new(self._key, body.encode(), hashlib.sha256).digest())
        if not same(signature, expected):
            return None
        try:
            payload = json.loads(_unb64(body))
        except (ValueError, json.JSONDecodeError):
            return None
        if payload.get("k") != kind or payload.get("exp", 0) < self.clock():
            return None
        return payload

    # -- provider metadata ------------------------------------------------
    def metadata(self) -> dict:
        if self._metadata is None:
            status, document = self.http("GET", self.issuer + "/.well-known/openid-configuration")
            if status != 200 or document.get("issuer", "").rstrip("/") != self.issuer:
                raise OidcError("the identity provider's metadata does not match its issuer")
            for field in ("authorization_endpoint", "token_endpoint", "userinfo_endpoint"):
                if not str(document.get(field, "")).startswith("https://"):
                    raise OidcError("the identity provider's metadata is incomplete")
            self._metadata = document
        return self._metadata

    # -- the flow ---------------------------------------------------------
    def begin(self) -> tuple[str, str]:
        """Return ``(authorization URL, signed login cookie)``."""
        state, nonce, verifier = (secrets.token_urlsafe(24), secrets.token_urlsafe(24), secrets.token_urlsafe(48))
        challenge = _b64(hashlib.sha256(verifier.encode()).digest())
        query = urllib.parse.urlencode({
            "response_type": "code", "client_id": self.client_id, "redirect_uri": self.redirect_uri,
            "scope": SCOPES, "state": state, "nonce": nonce,
            "code_challenge": challenge, "code_challenge_method": "S256",
        })
        cookie = self._seal("login", {
            "state": state, "nonce": nonce, "verifier": verifier,
            "exp": self.clock() + LOGIN_TTL_SECONDS,
        })
        return self.metadata()["authorization_endpoint"] + "?" + query, cookie

    def complete(self, query: dict[str, str], login_cookie: str | None) -> Identity:
        login = self._open("login", login_cookie)
        if login is None:
            raise OidcError("the sign-in expired; start again")
        if query.get("error"):
            raise OidcError("the identity provider refused the sign-in")
        if not same(query.get("state", ""), login["state"]) or not query.get("code"):
            raise OidcError("the sign-in does not belong to this browser; start again")
        metadata = self.metadata()
        status, tokens = self.http("POST", metadata["token_endpoint"], data={
            "grant_type": "authorization_code", "code": query["code"],
            "redirect_uri": self.redirect_uri, "code_verifier": login["verifier"],
        }, headers={
            "Authorization": "Basic " + base64.b64encode(
                f"{urllib.parse.quote(self.client_id, safe='')}:"
                f"{urllib.parse.quote(self.client_secret, safe='')}".encode()).decode(),
            "Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json",
        })
        if status != 200 or not tokens.get("access_token") or not tokens.get("id_token"):
            raise OidcError("the identity provider rejected the sign-in")
        claims = self._id_token_claims(tokens["id_token"])
        if (
            claims.get("iss", "").rstrip("/") != self.issuer
            or self.client_id not in ([claims["aud"]] if isinstance(claims.get("aud"), str) else claims.get("aud", []))
            or claims.get("exp", 0) < self.clock()
            or not same(str(claims.get("nonce", "")), login["nonce"])
            or not claims.get("sub")
        ):
            raise OidcError("the identity token is not valid for this sign-in")
        status, info = self.http(
            "GET", metadata["userinfo_endpoint"],
            headers={"Authorization": "Bearer " + tokens["access_token"], "Accept": "application/json"},
        )
        if status != 200 or info.get("sub") != claims["sub"]:
            raise OidcError("the identity provider could not confirm who you are")
        groups = info.get("groups") or claims.get("groups") or []
        if isinstance(groups, str):
            groups = [groups]
        if self.required_group not in groups:
            raise OidcError("your account is not allowed to wake staging")
        return Identity(subject=claims["sub"], email=str(info.get("email", "")), name=str(info.get("name", "")))

    @staticmethod
    def _id_token_claims(token: str) -> dict:
        # Received straight from the token endpoint over TLS: see the module docstring.
        try:
            return json.loads(_unb64(token.split(".")[1]))
        except (IndexError, ValueError, json.JSONDecodeError) as error:
            raise OidcError("the identity token is malformed") from error

    # -- session ----------------------------------------------------------
    def session_cookie(self, identity: Identity) -> str:
        return self._seal("session", {
            "sub": identity.subject, "email": identity.email, "name": identity.name,
            "exp": self.clock() + SESSION_TTL_SECONDS,
        })

    def read_session(self, cookie: str | None) -> Identity | None:
        payload = self._open("session", cookie)
        if payload is None:
            return None
        return Identity(subject=payload["sub"], email=payload.get("email", ""), name=payload.get("name", ""))


def parse_cookies(header: str | None) -> dict[str, str]:
    cookies = {}
    for part in (header or "").split(";"):
        name, separator, value = part.strip().partition("=")
        if separator:
            cookies[name] = value
    return cookies
