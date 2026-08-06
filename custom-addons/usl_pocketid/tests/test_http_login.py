import datetime
import json
import os
import time
from contextlib import contextmanager
from unittest.mock import Mock, patch
from urllib.parse import parse_qs, urlencode, urlsplit

from cryptography.hazmat.primitives.asymmetric import rsa
from jose import jwt
from jose.utils import long_to_base64
from lxml import html

from odoo import Command
from odoo.tests import HttpCase, tagged
from odoo.tools import config

from ..policy import LOGIN_POLICY_PARAMETER, LOGIN_POLICY_SSO_ONLY


@tagged("post_install", "-at_install", "usl_pocketid_http")
class TestPocketIDHttpLogin(HttpCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.provider = cls.env.ref("usl_pocketid.provider_pocketid")
        cls.provider._usl_pocketid_environment_write(
            {
                "enabled": True,
                "client_id": "http-login-test",
                "auth_endpoint": "https://id.example.test/authorize",
                "token_endpoint": "https://id.example.test/token",
                "jwks_uri": "https://id.example.test/jwks",
                "usl_end_session_endpoint": "https://id.example.test/logout",
                "usl_oidc_issuer": "https://id.example.test",
                "usl_public_base_url": cls.base_url(),
                "usl_required_group": "odoo-http-test",
            },
        )
        cls.env["auth.oauth.provider"].search(
            [
                ("id", "!=", cls.provider.id),
                ("enabled", "=", True),
            ],
        ).write({"enabled": False})
        cls.user = cls.env["res.users"].with_context(
            no_reset_password=True,
        ).create(
            {
                "login": "http.pocketid.user@example.invalid",
                "name": "HTTP Pocket ID User",
                "email": "http.pocketid.user@example.invalid",
                "password": "ordinary-password-must-not-work",
                "company_id": cls.env.company.id,
                "company_ids": [Command.set(cls.env.company.ids)],
                "group_ids": [Command.set(cls.env.ref("base.group_user").ids)],
                "usl_pocketid_access": True,
                "usl_identity_classification": "active",
            },
        )
        cls.identity = cls.env["usl.oidc.identity"].create(
            {
                "issuer": cls.provider.usl_oidc_issuer,
                "subject": "http-login-subject",
                "provider_id": cls.provider.id,
                "user_id": cls.user.id,
            },
        )
        cls.private_key = rsa.generate_private_key(
            public_exponent=65537,
            key_size=2048,
        )
        public_numbers = cls.private_key.public_key().public_numbers()
        cls.jwk = {
            "kty": "RSA",
            "use": "sig",
            "kid": "http-test-key",
            "alg": "RS256",
            "n": long_to_base64(public_numbers.n).decode(),
            "e": long_to_base64(public_numbers.e).decode(),
        }

    def _login_transaction(self):
        response = self.url_open(
            f"/web/login?{urlencode({'db': self.env.cr.dbname})}",
        )
        self.assertEqual(response.status_code, 200)
        document = html.fromstring(response.content)
        auth_links = document.xpath(
            "//a[contains(@href, 'https://id.example.test/authorize')]/@href",
        )
        self.assertEqual(len(auth_links), 1)
        parameters = parse_qs(urlsplit(auth_links[0]).query)
        self.assertEqual(parameters["response_type"], ["code"])
        self.assertEqual(parameters["code_challenge_method"], ["S256"])
        return parameters

    @contextmanager
    def _sso_only(self):
        parameters = self.env["ir.config_parameter"].sudo()
        previous_policy = parameters.get_str(LOGIN_POLICY_PARAMETER)
        previous_reset = parameters.get_bool("auth_signup.reset_password")
        parameters.set_str(
            LOGIN_POLICY_PARAMETER,
            LOGIN_POLICY_SSO_ONLY,
        )
        parameters.set_bool(
            "auth_signup.reset_password",
            False,
        )
        try:
            yield
        finally:
            parameters.set_str(
                LOGIN_POLICY_PARAMETER,
                previous_policy or "standard",
            )
            parameters.set_bool("auth_signup.reset_password", previous_reset)

    def test_sso_only_login_page_has_no_local_credential_form(self):
        with self._sso_only():
            response = self.url_open(
                f"/web/login?{urlencode({'db': self.env.cr.dbname})}",
            )
            document = html.fromstring(response.content)
            self.assertFalse(document.xpath("//input[@name='password']"))
            self.assertFalse(document.xpath("//input[@name='login']"))
            buttons = document.xpath(
                "//a[contains(normalize-space(.), 'Continue with Pocket ID')]",
            )
            self.assertEqual(len(buttons), 1)
            for path in ("/web/signup", "/web/reset_password"):
                disabled = self.url_open(path, allow_redirects=False)
                self.assertEqual(disabled.status_code, 303)
                self.assertIn("sso_error=sso_required", disabled.headers["Location"])
            authentication = self.url_open(
                "/web/session/authenticate",
                data=json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "method": "call",
                        "id": 1,
                        "params": {
                            "db": self.env.cr.dbname,
                            "login": self.user.login,
                            "password": "ordinary-password-must-not-work",
                        },
                    },
                ),
                headers={"Content-Type": "application/json"},
            ).json()
            self.assertNotIn("result", authentication)
            self.assertEqual(
                self.url_open("/usl/emergency-login").status_code,
                404,
            )
            with patch.dict(config.options, {"list_db": False}):
                self.assertEqual(
                    self.url_open("/web/database/manager").status_code,
                    404,
                )

    def test_logout_uses_the_external_provider_endpoint(self):
        self.authenticate(None, None)
        with self._sso_only():
            csrf_token = self.csrf_token()
            response = self.url_open(
                "/web/session/logout",
                data={
                    "csrf_token": csrf_token,
                    "redirect": "/web/login",
                },
                allow_redirects=False,
            )
        self.assertEqual(response.status_code, 303)
        location = urlsplit(response.headers["Location"])
        self.assertEqual(location.scheme, "https")
        self.assertEqual(location.netloc, "id.example.test")
        self.assertEqual(location.path, "/logout")
        self.assertEqual(
            parse_qs(location.query)["post_logout_redirect_uri"],
            [self.base_url() + "/web/login"],
        )

    def test_emergency_login_is_time_limited_classified_and_audited(self):
        emergency_user = self.env["res.users"].with_context(
            no_reset_password=True,
        ).create(
            {
                "login": "sealed.emergency@example.invalid",
                "name": "Sealed Emergency Administrator",
                "email": "sealed.emergency@example.invalid",
                "password": "temporary-emergency-password",
                "company_id": self.env.company.id,
                "company_ids": [Command.set(self.env.company.ids)],
                "group_ids": [
                    Command.set(self.env.ref("base.group_system").ids),
                ],
                "usl_identity_classification": "break_glass",
                "usl_local_break_glass": True,
            },
        )
        now = datetime.datetime.now(datetime.UTC)
        environment = {
            "USL_POCKET_ID_BREAK_GLASS_ENABLED": "1",
            "USL_POCKET_ID_BREAK_GLASS_EXPIRES_AT": (
                now + datetime.timedelta(minutes=10)
            ).isoformat(),
        }
        with (
            self._sso_only(),
            patch.dict(os.environ, environment, clear=False),
            patch(
                "odoo.addons.usl_pocketid.policy._PROCESS_STARTED_AT",
                now,
            ),
        ):
            page = self.url_open("/usl/emergency-login")
            self.assertEqual(page.status_code, 200)
            document = html.fromstring(page.content)
            csrf_token = document.xpath("//input[@name='csrf_token']/@value")[0]
            refused = self.url_open(
                "/usl/emergency-login",
                data={
                    "csrf_token": csrf_token,
                    "login": emergency_user.login,
                    "password": "wrong-password",
                },
                allow_redirects=False,
            )
            self.assertEqual(refused.status_code, 200)
            accepted = self.url_open(
                "/usl/emergency-login",
                data={
                    "csrf_token": csrf_token,
                    "login": emergency_user.login,
                    "password": "temporary-emergency-password",
                },
                allow_redirects=False,
            )
            self.assertEqual(accepted.status_code, 303)
            self.assertTrue(
                self.env["usl.oidc.audit.event"].search(
                    [
                        ("event_type", "=", "login_success"),
                        ("reason_code", "=", "sealed_emergency_login"),
                        ("user_id", "=", emergency_user.id),
                    ],
                ),
            )
            self.url_open(
                "/web/session/logout",
                data={"csrf_token": csrf_token},
                allow_redirects=False,
            )

        self.assertEqual(
            self.url_open("/usl/emergency-login").status_code,
            404,
        )

    def _token_response(self, nonce):
        now = int(time.time())
        id_token = jwt.encode(
            {
                "iss": self.provider.usl_oidc_issuer,
                "sub": self.identity.subject,
                "aud": self.provider.client_id,
                "exp": now + 300,
                "iat": now,
                "nonce": nonce,
                "groups": ["odoo-http-test"],
                "email": self.user.email,
                "email_verified": True,
                "name": "HTTP Pocket ID User",
            },
            self.private_key,
            algorithm="RS256",
            headers={"kid": self.jwk["kid"]},
        )
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {
            "access_token": "http-access-token",
            "id_token": id_token,
            "token_type": "Bearer",
        }
        return response

    def _jwks_response(self):
        payload = {"keys": [self.jwk]}
        response = Mock()
        response.raise_for_status.return_value = None
        response.headers = {}
        response.content = json.dumps(payload).encode()
        response.json.return_value = payload
        return response

    def test_existing_user_login_and_state_replay_denial(self):
        user_count = self.env["res.users"].with_context(
            active_test=False,
        ).search_count([])
        parameters = self._login_transaction()
        callback_query = urlencode(
            {
                "state": parameters["state"][0],
                "code": "one-time-code",
            },
        )
        environment = {"USL_POCKET_ID_CLIENT_SECRET": "http-test-secret"}
        with (
            patch.dict(os.environ, environment, clear=False),
            patch(
                "odoo.addons.usl_pocketid.models."
                "auth_oauth_provider.requests.post",
                return_value=self._token_response(parameters["nonce"][0]),
            ) as token_exchange,
            patch(
                "odoo.addons.usl_pocketid.models."
                "auth_oauth_provider.requests.get",
                return_value=self._jwks_response(),
            ),
        ):
            callback = self.url_open(
                f"/auth_oauth/signin?{callback_query}",
                allow_redirects=False,
            )
        self.assertEqual(callback.status_code, 303)
        self.assertTrue(
            urlsplit(callback.headers["Location"]).path.startswith("/odoo"),
        )
        exchanged_values = token_exchange.call_args.kwargs["data"]
        self.assertEqual(exchanged_values["grant_type"], "authorization_code")
        self.assertEqual(exchanged_values["code"], "one-time-code")
        self.assertTrue(exchanged_values["code_verifier"])
        self.assertEqual(
            exchanged_values["redirect_uri"],
            f"{self.base_url()}/auth_oauth/signin",
        )
        self.assertEqual(
            self.env["res.users"].with_context(
                active_test=False,
            ).search_count([]),
            user_count,
        )
        self.assertEqual(self.user.oauth_access_token, "http-access-token")
        self.assertTrue(
            self.env["usl.oidc.audit.event"].search(
                [
                    ("event_type", "=", "login_success"),
                    ("identity_id", "=", self.identity.id),
                    ("user_id", "=", self.user.id),
                ],
            ),
        )

        replay = self.url_open(
            f"/auth_oauth/signin?{callback_query}",
            allow_redirects=False,
        )
        self.assertEqual(replay.status_code, 303)
        self.assertIn("sso_error=state", replay.headers["Location"])
        self.assertTrue(
            self.env["usl.oidc.audit.event"].search(
                [
                    ("event_type", "=", "login_denied"),
                    ("reason_code", "=", "state"),
                ],
            ),
        )
