import os
import time
from unittest.mock import Mock, patch
from urllib.parse import parse_qs, urlsplit

from cryptography.hazmat.primitives.asymmetric import rsa
from jose import jwt
from jose.utils import long_to_base64
from requests import Timeout

from odoo.exceptions import ValidationError
from odoo.tests import TransactionCase, tagged

from ..controllers.main import (
    PocketIDLogin,
    _consume_transaction,
)
from ..exceptions import PocketIDAccessDenied
from ..models.auth_oauth_provider import PocketIDClientConfiguration
from odoo.addons.http_routing.tests.common import MockRequest


@tagged("post_install", "-at_install", "usl_pocketid")
class TestPocketIDOidcSecurity(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.provider = cls.env.ref("usl_pocketid.provider_pocketid")
        cls.provider._usl_pocketid_environment_write(
            {
                "enabled": True,
                "client_id": "odoo-client",
                "auth_endpoint": "https://id.example.test/authorize",
                "token_endpoint": "https://id.example.test/token",
                "jwks_uri": "https://id.example.test/jwks",
                "usl_oidc_issuer": "https://id.example.test",
                "usl_public_base_url": "https://odoo.example.test",
                "usl_required_group": "odoo-preprod",
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
            "kid": "test-key",
            "alg": "RS256",
            "n": long_to_base64(public_numbers.n).decode(),
            "e": long_to_base64(public_numbers.e).decode(),
        }

    def _claims(self, **overrides):
        now = int(time.time())
        claims = {
            "iss": self.provider.usl_oidc_issuer,
            "sub": "immutable-pocket-id-subject",
            "aud": self.provider.client_id,
            "exp": now + 300,
            "iat": now,
            "nonce": "expected-nonce",
            "groups": ["odoo-preprod"],
            "email": "person@example.test",
            "email_verified": True,
            "name": "Pocket ID Person",
        }
        claims.update(overrides)
        return claims

    def _token(self, **overrides):
        return jwt.encode(
            self._claims(**overrides),
            self.private_key,
            algorithm="RS256",
            headers={"kid": "test-key"},
        )

    def _validate(self, token=None, nonce="expected-nonce"):
        with patch(
            "odoo.addons.usl_pocketid.models.auth_oauth_provider."
            "AuthOauthProvider._usl_get_signing_keys",
            return_value=[self.jwk],
        ):
            return self.provider._usl_validate_id_token(
                id_token=token or self._token(),
                access_token="opaque-access-token",
                nonce=nonce,
            )

    def _sign_configuration(self):
        return PocketIDClientConfiguration(
            issuer=self.provider.usl_oidc_issuer,
            authorization_endpoint="https://id.example.test/authorize",
            token_endpoint="https://id.example.test/token",
            jwks_uri=self.provider.jwks_uri,
            client_id="sign-client",
            client_secret="environment-only-sign-secret",
            required_group="signers",
            redirect_uri="https://odoo.example.test/sign/pocketid/callback",
            token_auth_method="client_secret_basic",
            scopes="openid profile email groups",
            fresh_passkey_supported=True,
            discovery_snapshot={
                "issuer": self.provider.usl_oidc_issuer,
                "fresh_passkey_reauthentication_supported": True,
            },
        )

    def test_signed_token_validates_required_oidc_claims(self):
        claims = self._validate()
        self.assertEqual(claims["sub"], "immutable-pocket-id-subject")
        self.assertEqual(claims["iss"], self.provider.usl_oidc_issuer)

    def test_wrong_issuer_audience_expiry_nonce_and_group_are_denied(self):
        invalid_tokens = [
            self._token(iss="https://attacker.example.test"),
            self._token(aud="another-client"),
            self._token(exp=int(time.time()) - 120),
            self._token(groups=["unrelated-group"]),
        ]
        for token in invalid_tokens:
            with self.subTest(token=token[-12:]):
                with self.assertRaises(PocketIDAccessDenied):
                    self._validate(token)
        with self.assertRaises(PocketIDAccessDenied):
            self._validate(nonce="wrong-nonce")

    def test_unsupported_signing_algorithm_is_denied_before_key_use(self):
        token = jwt.encode(
            self._claims(),
            "not-a-real-client-secret",
            algorithm="HS256",
        )
        with self.assertRaises(PocketIDAccessDenied):
            self.provider._usl_validate_id_token(
                id_token=token,
                access_token="opaque-access-token",
                nonce="expected-nonce",
            )

    def test_wrong_signature_is_denied(self):
        attacker_key = rsa.generate_private_key(
            public_exponent=65537,
            key_size=2048,
        )
        token = jwt.encode(
            self._claims(),
            attacker_key,
            algorithm="RS256",
            headers={"kid": "test-key"},
        )
        with self.assertRaises(PocketIDAccessDenied):
            self._validate(token)

    def test_sign_client_validates_dedicated_audience_group_and_nonce(self):
        configuration = self._sign_configuration()
        token = self._token(
            aud=configuration.client_id,
            groups=[configuration.required_group],
            amr=["phr"],
            auth_time=int(time.time()),
        )
        with patch(
            "odoo.addons.usl_pocketid.models.auth_oauth_provider."
            "AuthOauthProvider._usl_get_signing_keys",
            return_value=[self.jwk],
        ):
            claims, keys = self.provider._usl_pocketid_validate_id_token_for_client(
                configuration,
                id_token=token,
                access_token="opaque-access-token",
                nonce="expected-nonce",
            )
        self.assertEqual(claims["sub"], "immutable-pocket-id-subject")
        self.assertEqual(keys, [self.jwk])
        for overrides in (
            {"aud": "login-client"},
            {"groups": ["odoo-preprod"]},
            {"nonce": "another-binding"},
        ):
            invalid_claims = {
                "aud": configuration.client_id,
                "groups": [configuration.required_group],
            }
            invalid_claims.update(overrides)
            invalid = self._token(**invalid_claims)
            with (
                patch(
                    "odoo.addons.usl_pocketid.models.auth_oauth_provider."
                    "AuthOauthProvider._usl_get_signing_keys",
                    return_value=[self.jwk],
                ),
                self.assertRaises(PocketIDAccessDenied),
            ):
                self.provider._usl_pocketid_validate_id_token_for_client(
                    configuration,
                    id_token=invalid,
                    access_token="opaque-access-token",
                    nonce="expected-nonce",
                )

    def test_sign_configuration_fails_closed_without_fresh_capability(self):
        environment = {
            "USL_POCKET_ID_ISSUER": "https://id.example.test",
            "USL_POCKET_ID_ODOO_BASE_URL": "https://odoo.example.test",
            "USL_POCKET_ID_SIGN_CLIENT_ID": "sign-client",
            "USL_POCKET_ID_SIGN_CLIENT_SECRET": "environment-only-sign-secret",
            "USL_POCKET_ID_SIGN_REQUIRED_GROUP": "signers",
            "USL_POCKET_ID_SIGN_FRESH_REQUIRED": "1",
        }
        discovery = {
            "issuer": "https://id.example.test",
            "authorization_endpoint": "https://id.example.test/authorize",
            "token_endpoint": "https://id.example.test/token",
            "jwks_uri": "https://id.example.test/jwks",
            "prompt_values_supported": ["none", "login"],
            "fresh_passkey_reauthentication_supported": False,
            "token_endpoint_auth_methods_supported": ["client_secret_basic"],
        }
        with (
            patch.dict(os.environ, environment, clear=False),
            patch.object(
                type(self.provider),
                "_usl_discover_pocketid",
                return_value=discovery,
            ),
            self.assertRaises(PocketIDAccessDenied),
        ):
            self.provider._usl_pocketid_sign_configuration()
        discovery["fresh_passkey_reauthentication_supported"] = True
        disabled_environment = environment | {
            "USL_POCKET_ID_SIGN_FRESH_REQUIRED": "0",
        }
        with (
            patch.dict(os.environ, disabled_environment, clear=False),
            patch.object(
                type(self.provider),
                "_usl_discover_pocketid",
                return_value=discovery,
            ),
            self.assertRaises(PocketIDAccessDenied),
        ):
            self.provider._usl_pocketid_sign_configuration()
        with (
            patch.dict(os.environ, environment, clear=False),
            patch.object(
                type(self.provider),
                "_usl_discover_pocketid",
                return_value=discovery,
            ),
        ):
            configuration = self.provider._usl_pocketid_sign_configuration()
        self.assertEqual(configuration.client_id, "sign-client")
        self.assertTrue(configuration.fresh_passkey_supported)
        self.assertTrue(
            configuration.discovery_snapshot[
                "fresh_passkey_reauthentication_supported"
            ],
        )

    def test_login_link_uses_session_bound_state_nonce_and_pkce(self):
        self.env["auth.oauth.provider"].search(
            [("id", "!=", self.provider.id)],
        ).write({"enabled": False})
        with MockRequest(self.env, url_root="https://odoo.example.test/") as req:
            req.session.db = self.env.cr.dbname
            req.params = {"redirect": "https://attacker.example.test/steal"}
            providers = PocketIDLogin().list_providers()
            pocket_provider = next(
                provider
                for provider in providers
                if provider["id"] == self.provider.id
            )
            params = parse_qs(urlsplit(pocket_provider["auth_link"]).query)
            self.assertEqual(params["response_type"], ["code"])
            self.assertEqual(params["redirect_uri"], [
                "https://odoo.example.test/auth_oauth/signin",
            ])
            self.assertEqual(params["code_challenge_method"], ["S256"])
            self.assertTrue(params["nonce"][0])
            self.assertTrue(params["code_challenge"][0])
            state = params["state"][0]
            transaction = _consume_transaction(state)
            self.assertEqual(transaction["redirect_path"], "/odoo")
            self.assertEqual(transaction["nonce"], params["nonce"][0])
            self.assertIsNone(_consume_transaction(state))

    def test_environment_configuration_uses_discovery_without_storing_secret(self):
        default_odoo_provider = self.env.ref("auth_oauth.provider_openerp")
        default_odoo_provider.sudo().write({"enabled": True})
        discovery = {
            "issuer": "https://id.example.test",
            "authorization_endpoint": "https://id.example.test/authorize",
            "token_endpoint": "https://id.example.test/token",
            "jwks_uri": "https://id.example.test/jwks",
            "end_session_endpoint": "https://id.example.test/logout",
            "response_types_supported": ["code"],
            "id_token_signing_alg_values_supported": ["RS256"],
            "token_endpoint_auth_methods_supported": ["client_secret_post"],
        }
        response = Mock()
        response.json.return_value = discovery
        response.raise_for_status.return_value = None
        environment = {
            "USL_POCKET_ID_ENABLED": "1",
            "USL_POCKET_ID_ISSUER": "https://id.example.test",
            "USL_POCKET_ID_CLIENT_ID": "configured-client",
            "USL_POCKET_ID_CLIENT_SECRET": "environment-only-secret",
            "USL_POCKET_ID_ODOO_BASE_URL": "https://odoo.example.test",
            "USL_POCKET_ID_REQUIRED_GROUP": "odoo-preprod",
            "USL_POCKET_ID_ALLOW_UNIQUE_EMAIL_LINK": "0",
        }
        with (
            patch.dict(os.environ, environment, clear=False),
            patch(
                "odoo.addons.usl_pocketid.models.auth_oauth_provider.requests.get",
                return_value=response,
            ),
        ):
            self.provider._usl_pocketid_apply_environment()
        self.assertTrue(self.provider.enabled)
        self.assertEqual(self.provider.client_id, "configured-client")
        self.assertFalse(self.provider.client_secret)
        self.assertEqual(
            self.provider.usl_end_session_endpoint,
            "https://id.example.test/logout",
        )
        self.assertEqual(
            self.provider.usl_token_auth_method,
            "client_secret_post",
        )
        self.assertFalse(default_odoo_provider.enabled)
        self.assertTrue(
            self.env["usl.oidc.audit.event"].search(
                [
                    ("provider_id", "=", default_odoo_provider.id),
                    ("event_type", "=", "configuration"),
                    ("reason_code", "=", "default_odoo_oauth_disabled"),
                ],
            ),
        )
        self.assertTrue(
            self.env["usl.oidc.audit.event"].search(
                [
                    ("provider_id", "=", self.provider.id),
                    ("event_type", "=", "configuration"),
                    ("reason_code", "=", "environment_enabled"),
                ],
            ),
        )

    def test_disabled_environment_disables_bundled_oauth_providers(self):
        default_odoo_provider = self.env.ref("auth_oauth.provider_openerp")
        default_odoo_provider.sudo().write({"enabled": True})
        self.provider._usl_pocketid_environment_write(
            {
                "enabled": True,
                "client_secret": False,
            },
        )

        with patch.dict(
            os.environ,
            {"USL_POCKET_ID_ENABLED": "0"},
            clear=False,
        ):
            self.provider._usl_pocketid_apply_environment()

        self.assertFalse(default_odoo_provider.enabled)
        self.assertFalse(self.provider.enabled)
        self.assertFalse(self.provider.client_secret)
        for provider, reason_code in (
            (default_odoo_provider, "default_odoo_oauth_disabled"),
            (self.provider, "environment_disabled"),
        ):
            self.assertTrue(
                self.env["usl.oidc.audit.event"].search(
                    [
                        ("provider_id", "=", provider.id),
                        ("event_type", "=", "configuration"),
                        ("reason_code", "=", reason_code),
                    ],
                ),
            )

    def test_environment_managed_provider_refuses_direct_changes(self):
        with self.assertRaises(ValidationError):
            self.provider.write({"client_id": "manual-change-is-forbidden"})
        with self.assertRaises(ValidationError):
            self.provider.unlink()

    def test_discovery_rejects_cross_origin_endpoints(self):
        response = Mock()
        response.json.return_value = {
            "issuer": "https://id.example.test",
            "authorization_endpoint": "https://attacker.example.test/authorize",
            "token_endpoint": "https://id.example.test/token",
            "jwks_uri": "https://id.example.test/jwks",
            "response_types_supported": ["code"],
            "id_token_signing_alg_values_supported": ["RS256"],
        }
        response.raise_for_status.return_value = None
        with (
            patch(
                "odoo.addons.usl_pocketid.models.auth_oauth_provider.requests.get",
                return_value=response,
            ),
            self.assertRaises(ValidationError),
        ):
            self.provider._usl_discover_pocketid("https://id.example.test")

    def test_issuer_and_public_base_url_reject_query_components(self):
        with self.assertRaises(ValidationError):
            self.provider._usl_discover_pocketid(
                "https://id.example.test?tenant=unsafe",
            )
        with self.assertRaises(ValidationError):
            self.provider._usl_validate_url(
                "https://odoo.example.test?db=unsafe",
                label="Odoo public base URL",
                allow_path=False,
                allow_query=False,
            )

    def test_http_is_limited_to_loopback_and_localhost_names(self):
        for url in (
            "http://127.0.0.1:1411",
            "http://[::1]:1411",
            "http://localhost:1411",
            "http://pocket-id.localhost:1411",
        ):
            with self.subTest(url=url):
                self.assertEqual(
                    self.provider._usl_validate_url(
                        url,
                        label="local test service",
                    ),
                    url,
                )
        for url in (
            "http://pocket-id.example.test:1411",
            "http://localhost.example.test:1411",
        ):
            with (
                self.subTest(url=url),
                self.assertRaises(ValidationError),
            ):
                self.provider._usl_validate_url(
                    url,
                    label="unsafe test service",
                )

    def test_private_http_requires_explicit_development_qa_opt_in(self):
        private_url = "http://100.79.30.44:1411"
        with (
            patch.dict(
                os.environ,
                {
                    "USL_DEPLOYMENT_ENV": "development",
                    "USL_POCKET_ID_ALLOW_PRIVATE_HTTP_QA": "0",
                },
            ),
            self.assertRaises(ValidationError),
        ):
            self.provider._usl_validate_url(
                private_url,
                label="private service without QA opt-in",
            )
        with patch.dict(
            os.environ,
            {
                "USL_DEPLOYMENT_ENV": "development",
                "USL_POCKET_ID_ALLOW_PRIVATE_HTTP_QA": "1",
            },
        ):
            self.assertEqual(
                self.provider._usl_validate_url(
                    private_url,
                    label="private QA service",
                ),
                private_url,
            )
        for deployment in ("preproduction", "production"):
            with (
                self.subTest(deployment=deployment),
                patch.dict(
                    os.environ,
                    {
                        "USL_DEPLOYMENT_ENV": deployment,
                        "USL_POCKET_ID_ALLOW_PRIVATE_HTTP_QA": "1",
                    },
                ),
                self.assertRaises(ValidationError),
            ):
                self.provider._usl_validate_url(
                    private_url,
                    label="unsafe private service",
                )

    def test_jwks_requires_bounded_valid_rsa_key_selection(self):
        second_key = {**self.jwk, "kid": "second-key"}
        response = Mock()
        response.headers = {}
        response.content = b'{"keys": []}'
        response.json.return_value = {"keys": [self.jwk, second_key]}
        response.raise_for_status.return_value = None
        with patch(
            "odoo.addons.usl_pocketid.models.auth_oauth_provider.requests.get",
            return_value=response,
        ):
            self.assertEqual(
                self.provider._usl_get_signing_keys("test-key"),
                [self.jwk],
            )
            with self.assertRaises(PocketIDAccessDenied):
                self.provider._usl_get_signing_keys(None)

    def test_jwks_outage_is_reported_as_provider_unavailable(self):
        with (
            patch(
                "odoo.addons.usl_pocketid.models.auth_oauth_provider.requests.get",
                side_effect=Timeout(),
            ),
            self.assertRaises(PocketIDAccessDenied) as denied,
        ):
            self.provider._usl_get_signing_keys("test-key")
        self.assertEqual(denied.exception.reason, "provider_unavailable")
