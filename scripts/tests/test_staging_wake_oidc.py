"""Sign-in with Pocket ID for the staging wake page, against a fake provider."""
from __future__ import annotations

import base64
import hashlib
import json
import unittest
import urllib.parse

from operations.staging_wake_oidc import (
    OidcError,
    WakeOidc,
    default_http,
    parse_cookies,
)

ISSUER = "https://auth.example.test"
REDIRECT = "https://staging.example.test/__usl/callback"


def b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def id_token(**claims) -> str:
    return ".".join([b64(b'{"alg":"RS256"}'), b64(json.dumps(claims).encode()), "signature-not-checked"])


class FakeProvider:
    """Just enough of an OIDC provider; records every call."""

    def __init__(self, clock, issuer: str = ISSUER) -> None:
        self.clock = clock
        self.issuer = issuer
        self.calls: list[tuple] = []
        self.userinfo = {"sub": "user-1", "email": "valentin@example.test", "name": "V", "groups": ["c_suite"]}
        self.claims_override: dict = {}
        self.token_status = 200
        self.nonce = ""
        self.challenge = ""

    def __call__(self, method, url, *, data=None, headers=None):
        self.calls.append((method, url, data, headers))
        if url.endswith("/.well-known/openid-configuration"):
            return 200, {
                "issuer": self.issuer + "/",
                "authorization_endpoint": self.issuer + "/authorize",
                "token_endpoint": self.issuer + "/token",
                "userinfo_endpoint": self.issuer + "/userinfo",
            }
        if url == self.issuer + "/token":
            if self.token_status != 200:
                return self.token_status, {}
            claims = {"iss": self.issuer, "aud": "client-1", "exp": self.clock() + 300, "sub": "user-1",
                      "nonce": self.nonce, **self.claims_override}
            return 200, {"access_token": "access", "id_token": id_token(**claims)}
        if url == self.issuer + "/userinfo":
            return 200, self.userinfo
        raise AssertionError(url)


class WakeOidcTests(unittest.TestCase):
    def setUp(self) -> None:
        self.now = 1_000_000.0
        self.provider = FakeProvider(lambda: self.now)
        self.oidc = WakeOidc(
            issuer=ISSUER, client_id="client-1", client_secret="s3cret", redirect_uri=REDIRECT,
            required_group="c_suite", http=self.provider, clock=lambda: self.now,
        )

    def start(self) -> tuple[dict, str]:
        url, cookie = self.oidc.begin()
        query = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(url).query))
        self.provider.nonce = query["nonce"]
        self.query = query
        return query, cookie

    def test_the_authorization_request_is_confidential_pkce_with_the_required_scopes(self) -> None:
        query, cookie = self.start()
        self.assertEqual(query["response_type"], "code")
        self.assertEqual(query["redirect_uri"], REDIRECT)
        self.assertEqual(query["code_challenge_method"], "S256")
        self.assertEqual(set(query["scope"].split()), {"openid", "profile", "email", "groups"})
        verifier = self.oidc._open("login", cookie)["verifier"]
        self.assertEqual(query["code_challenge"], b64(hashlib.sha256(verifier.encode()).digest()))

    def test_a_valid_sign_in_returns_the_identity_and_sends_the_verifier_with_the_secret(self) -> None:
        query, cookie = self.start()
        who = self.oidc.complete({"state": query["state"], "code": "abc"}, cookie)
        self.assertEqual((who.subject, who.email, who.label), ("user-1", "valentin@example.test", "valentin@example.test"))
        method, url, data, headers = next(c for c in self.provider.calls if c[1].endswith("/token"))
        self.assertEqual(data["code"], "abc")
        self.assertTrue(data["code_verifier"])
        self.assertTrue(headers["Authorization"].startswith("Basic "))
        self.assertNotIn("s3cret", json.dumps(data))

    def test_every_way_a_sign_in_can_be_forged_or_replayed_is_refused(self) -> None:
        query, cookie = self.start()
        good = {"state": query["state"], "code": "abc"}
        cases = {
            "wrong state": ({"state": "other", "code": "abc"}, cookie),
            "no code": ({"state": query["state"]}, cookie),
            "provider error": ({"state": query["state"], "error": "access_denied"}, cookie),
            "no cookie": (good, None),
            "tampered cookie": (good, cookie[:-2] + ("AA" if not cookie.endswith("AA") else "BB")),
            "another kind of cookie": (good, self.oidc.session_cookie(
                __import__("operations.staging_wake_oidc", fromlist=["Identity"]).Identity("s", "e", "n"))),
        }
        for name, (params, login) in cases.items():
            with self.subTest(name), self.assertRaises(OidcError):
                self.oidc.complete(params, login)

    def test_a_login_cookie_expires(self) -> None:
        query, cookie = self.start()
        self.now += 601
        with self.assertRaisesRegex(OidcError, "expired"):
            self.oidc.complete({"state": query["state"], "code": "abc"}, cookie)

    def test_the_identity_token_must_match_issuer_audience_nonce_and_freshness(self) -> None:
        for override in (
            {"iss": "https://evil.example.test"}, {"aud": "someone-else"}, {"nonce": "replayed"},
            {"exp": 1}, {"sub": ""},
        ):
            with self.subTest(override):
                query, cookie = self.start()
                self.provider.claims_override = override
                with self.assertRaises(OidcError):
                    self.oidc.complete({"state": query["state"], "code": "abc"}, cookie)

    def test_the_userinfo_subject_must_match_the_token_subject(self) -> None:
        query, cookie = self.start()
        self.provider.userinfo = {**self.provider.userinfo, "sub": "someone-else"}
        with self.assertRaisesRegex(OidcError, "confirm"):
            self.oidc.complete({"state": query["state"], "code": "abc"}, cookie)

    def test_only_members_of_the_required_group_may_wake(self) -> None:
        for groups in ([], ["accountants"], ["c_suite_not"], None):
            with self.subTest(groups):
                query, cookie = self.start()
                self.provider.userinfo = {**self.provider.userinfo, "groups": groups}
                with self.assertRaisesRegex(OidcError, "not allowed"):
                    self.oidc.complete({"state": query["state"], "code": "abc"}, cookie)

    def test_a_rejected_code_exchange_is_refused(self) -> None:
        query, cookie = self.start()
        self.provider.token_status = 400
        with self.assertRaises(OidcError):
            self.oidc.complete({"state": query["state"], "code": "abc"}, cookie)

    def test_metadata_for_another_issuer_is_refused(self) -> None:
        other = WakeOidc(
            issuer="https://other.example.test", client_id="c", client_secret="s", redirect_uri=REDIRECT,
            required_group="g", http=self.provider,
        )
        with self.assertRaises(OidcError):
            other.begin()

    def test_a_session_round_trips_expires_and_dies_with_the_client_secret(self) -> None:
        query, cookie = self.start()
        who = self.oidc.complete({"state": query["state"], "code": "abc"}, cookie)
        session = self.oidc.session_cookie(who)
        self.assertEqual(self.oidc.read_session(session), who)
        self.assertIsNone(self.oidc.read_session(session + "x"))
        self.assertIsNone(self.oidc.read_session(None))
        rotated = WakeOidc(
            issuer=ISSUER, client_id="client-1", client_secret="new-secret", redirect_uri=REDIRECT,
            required_group="c_suite", http=self.provider, clock=lambda: self.now,
        )
        self.assertIsNone(rotated.read_session(session))
        self.now += 8 * 3600 + 1
        self.assertIsNone(self.oidc.read_session(session))

    def test_a_login_cookie_is_not_a_session(self) -> None:
        _, cookie = self.start()
        self.assertIsNone(self.oidc.read_session(cookie))

    def test_plain_http_is_never_contacted_or_configured(self) -> None:
        with self.assertRaises(OidcError):
            default_http("GET", "http://auth.example.test/.well-known/openid-configuration")
        with self.assertRaises(OidcError):
            WakeOidc(issuer="http://x", client_id="c", client_secret="s", redirect_uri=REDIRECT, required_group="g")
        with self.assertRaises(OidcError):
            WakeOidc(issuer=ISSUER, client_id="", client_secret="s", redirect_uri=REDIRECT, required_group="g")

    def test_non_ascii_forgeries_are_refused_not_raised(self) -> None:
        self.assertIsNone(self.oidc.read_session("a.\u00e9"))
        query, cookie = self.start()
        with self.assertRaises(OidcError):
            self.oidc.complete({"state": "\u00e9", "code": "x"}, cookie)

    def test_the_provider_cannot_redirect_the_credentials_elsewhere(self) -> None:
        import urllib.request
        from operations.staging_wake_oidc import _RefuseRedirects
        self.assertIsNone(_RefuseRedirects().redirect_request(None, None, 302, "Found", {}, "http://evil.test/"))
        self.assertTrue(issubclass(_RefuseRedirects, urllib.request.HTTPRedirectHandler))

    def test_the_provider_client_does_not_send_the_default_python_user_agent(self) -> None:
        """Cloudflare blocks "Python-urllib" (error 1010): found against the real provider."""
        import urllib.request
        from unittest import mock
        from operations import staging_wake_oidc as module

        seen = {}

        class Response:
            status = 200
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def read(self): return b"{}"

        def fake_open(request, timeout=None):
            seen["agent"] = request.get_header("User-agent")
            seen["auth"] = request.get_header("Authorization")
            return Response()

        with mock.patch.object(module._NO_REDIRECTS, "open", fake_open):
            module.default_http("GET", "https://auth.example.test/x", headers={"Authorization": "Bearer t"})
        self.assertEqual(seen["agent"], module.USER_AGENT)
        self.assertNotIn("Python-urllib", seen["agent"])
        self.assertEqual(seen["auth"], "Bearer t")

    def test_cookie_parsing(self) -> None:
        self.assertEqual(parse_cookies("a=1; b=2=3;junk"), {"a": "1", "b": "2=3"})
        self.assertEqual(parse_cookies(None), {})


if __name__ == "__main__":
    unittest.main()
