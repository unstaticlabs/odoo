"""Fixed provider transport and authenticated callback without bank traffic."""
from datetime import timedelta
import hashlib
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

import httpx
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from jose import jwt

from odoo import Command, fields
from odoo.tests import HttpCase, TransactionCase, new_test_user, tagged

from ..services.client import EnableBankingClient
from ..services.protocol import ProviderDataError
from .test_enablebanking import session


@tagged("post_install", "-at_install", "usl_enablebanking")
class TestEnableBankingTransport(TransactionCase):
    def test_rs256_application_token_is_short_lived_and_company_key_scoped(self):
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        with TemporaryDirectory() as directory:
            path = Path(directory) / "synthetic-key.pem"
            path.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
            path.chmod(0o600)
            with patch.dict(os.environ, {"USL_ENABLEBANKING_KEY_FILE_4": str(path)}, clear=True):
                token = EnableBankingClient("synthetic-app", 4)._token()
                with self.assertRaisesRegex(ProviderDataError, "signing_not_configured"):
                    EnableBankingClient("synthetic-app", 5)._token()
        public = key.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
        claims = jwt.decode(token, public, algorithms=["RS256"], audience="api.enablebanking.com", issuer="enablebanking.com")
        self.assertEqual(claims["exp"] - claims["iat"], 300)
        self.assertEqual(jwt.get_unverified_header(token), {"alg": "RS256", "kid": "synthetic-app", "typ": "JWT"})

    def test_provider_errors_never_echo_sensitive_response_or_transport_detail(self):
        client = EnableBankingClient("synthetic-app", 4)
        response = Mock(status_code=403)
        response.json.return_value = {"error": "EXPIRED_SESSION", "message": "synthetic-private-payload"}
        with patch.object(client, "_token", return_value="synthetic-token"), patch("odoo.addons.usl_enablebanking.services.client.httpx.Client") as transport:
            transport.return_value.__enter__.return_value.request.return_value = response
            with self.assertRaises(ProviderDataError) as error:
                client.transactions("session-account", {})
            self.assertEqual(str(error.exception), "consent_expired")
            response.json.return_value = {"error": "ASPSP_PSU_ACTION_REQUIRED"}
            with self.assertRaisesRegex(ProviderDataError, "consent_renewal_required"):
                client.transactions("session-account", {})
            self.assertFalse(transport.call_args.kwargs["follow_redirects"])
            self.assertFalse(transport.call_args.kwargs["trust_env"])
            transport.return_value.__enter__.return_value.request.side_effect = httpx.ReadTimeout("synthetic-private-detail")
            with self.assertRaises(ProviderDataError) as error:
                client.transactions("session-account", {})
            self.assertEqual(str(error.exception), "provider_unavailable")

    def test_unhashable_provider_error_codes_have_safe_fallback(self):
        client = EnableBankingClient("synthetic-app", 4)
        response = Mock(status_code=403)
        with patch.object(client, "_token", return_value="synthetic-token"), patch("odoo.addons.usl_enablebanking.services.client.httpx.Client") as transport:
            transport.return_value.__enter__.return_value.request.return_value = response
            for code in ([], {}, ["EXPIRED_SESSION"]):
                response.json.return_value = {"error": code}
                with self.subTest(code=code):
                    with self.assertRaises(ProviderDataError) as error:
                        client.transactions("session-account", {})
                    self.assertEqual(str(error.exception), "provider_unavailable")

    def test_malformed_company_signing_key_has_safe_configuration_error(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "synthetic-malformed-key.pem"
            path.write_bytes(b"not-a-pem-synthetic-qa-key")
            with patch.dict(os.environ, {"USL_ENABLEBANKING_KEY_FILE_4": str(path)}, clear=True):
                with self.assertRaises(ProviderDataError) as error:
                    EnableBankingClient("synthetic-app", 4)._token()
                self.assertEqual(str(error.exception), "signing_not_configured")
                self.assertNotIn(str(path), str(error.exception))
                self.assertNotIn("not-a-pem", str(error.exception))

    def test_redirect_must_remain_on_provider_authorization_origin(self):
        client = EnableBankingClient("synthetic-app", 4)
        for url in ("http://auth.enablebanking.com/", "https://auth.enablebanking.com.example.invalid/", "https://name@auth.enablebanking.com/"):
            with self.subTest(url=url), patch.object(client, "request", return_value={"url": url}), self.assertRaisesRegex(ProviderDataError, "invalid_authorization_url"):
                client.authorize({})


@tagged("post_install", "-at_install", "usl_enablebanking")
class TestEnableBankingCallback(HttpCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.manager = new_test_user(cls.env, login="banking-http-qa", password="qa", groups="account.group_account_manager,account.group_account_user",
                                    company_id=cls.env.company.id, company_ids=[Command.set(cls.env.company.ids)])
        cls.connection = cls.env["usl.enablebanking.connection"].with_user(cls.manager).create({"name": "Synthetic HTTP bank", "app_id": "synthetic-app", "company_id": cls.env.company.id})

    def _prepare_state(self):
        state = "synthetic-http-return-state"
        self.connection.sudo().write({"state_hash": hashlib.sha256(state.encode()).hexdigest(), "state_user_id": self.manager.id, "state_until": fields.Datetime.now() + timedelta(minutes=5)})
        return state

    def test_authenticated_return_consumes_state_and_replay_never_exchanges_again(self):
        state = self._prepare_state()
        self.authenticate(self.manager.login, "qa")
        client = Mock()
        client.session.return_value = session()
        with patch.object(type(self.connection), "_client", return_value=client):
            result = self.url_open("/enablebanking/callback", params={"state": state, "code": "synthetic-code"}, allow_redirects=False)
            self.assertEqual(result.status_code, 303)
            self.assertEqual(result.headers["Referrer-Policy"], "no-referrer")
            self.assertEqual(result.headers["Cache-Control"], "no-store, max-age=0")
            self.assertIn("action-usl_enablebanking.connection_action", result.headers["Location"])
            self.connection.invalidate_recordset()
            self.assertFalse(self.connection.last_error_code)
            self.assertEqual(len(self.connection.account_ids), 1)
            self.assertFalse(self.connection.sudo().state_hash)
            again = self.url_open("/enablebanking/callback", params={"state": state, "code": "synthetic-code"}, allow_redirects=False)
            self.assertEqual(again.status_code, 400)
            client.session.assert_called_once_with("synthetic-code")

    def test_denied_consent_commits_safe_result_without_exchanging_code(self):
        state = self._prepare_state()
        self.authenticate(self.manager.login, "qa")
        with patch.object(type(self.connection), "_client", side_effect=AssertionError("No provider request")):
            result = self.url_open("/enablebanking/callback", params={"state": state, "error": "access_denied", "error_description": "untrusted bank message"}, allow_redirects=False)
        self.assertEqual(result.status_code, 303)
        self.connection.invalidate_recordset()
        self.assertEqual(self.connection.last_error_code, "consent_declined")
        self.assertFalse(self.connection.account_ids)
        self.assertFalse(self.connection.sudo().state_hash)
