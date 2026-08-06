from types import SimpleNamespace
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from odoo.exceptions import AccessError
from odoo.tests import TransactionCase, tagged

from ..controllers.strong import (
    _SESSION_COMPLETIONS,
    _SESSION_TRANSACTIONS,
    StrongSignController,
    _fresh_passkey_claims_summary,
)


@tagged("post_install", "-at_install")
class TestPocketIDStrongAuthorization(TransactionCase):
    def test_completed_ceremony_recovers_a_lost_final_response(self):
        controller = StrongSignController()
        fake_request = SimpleNamespace(
            session={
                _SESSION_COMPLETIONS: {
                    "42": {
                        "signer_id": 7,
                        "expires_unix": 150,
                        "redirect": "/sign/result/success",
                    },
                },
            },
        )
        with (
            patch("odoo.addons.usl_sign.controllers.strong.request", fake_request),
            patch("odoo.addons.usl_sign.controllers.strong.time.time", return_value=100),
        ):
            result = controller.strong_status(7, "already-revoked-token", 42)
        self.assertEqual(
            result,
            {"state": "completed", "redirect": "/sign/result/success"},
        )

    def test_fresh_passkey_claims_require_phr_and_current_auth_time(self):
        valid = {
            "iss": "https://id.example.test",
            "sub": "immutable-subject",
            "amr": ["phr"],
            "auth_time": 101,
            "groups": ["signers"],
        }
        summary = _fresh_passkey_claims_summary(
            valid,
            transaction_created=100,
            now=102,
        )
        self.assertEqual(summary["amr"], ["phr"])
        for changes in (
            {"amr": ["otp"]},
            {"amr": ["phr", "otp"]},
            {"amr": []},
            {"auth_time": 99},
            {"auth_time": 163},
        ):
            with self.subTest(changes=changes), self.assertRaises(AccessError):
                _fresh_passkey_claims_summary(
                    valid | changes,
                    transaction_created=100,
                    now=102,
                )

    def test_oidc_transaction_is_pkce_bound_and_single_use(self):
        controller = StrongSignController()
        configuration = SimpleNamespace(
            authorization_endpoint="https://id.example.test/authorize",
            client_id="sign-client",
            redirect_uri="https://odoo.example.test/sign/pocketid/callback",
            scopes="openid profile email groups",
        )
        fake_request = SimpleNamespace(session={})
        with (
            patch(
                "odoo.addons.usl_sign.controllers.strong.request",
                fake_request,
            ),
            patch.object(controller, "_pocket_client", return_value=configuration),
            patch("odoo.addons.usl_sign.controllers.strong.time.time", return_value=100),
        ):
            state, authorization_url = controller._create_oidc_transaction(
                purpose="strong_signature",
                nonce="document-binding-digest",
                values={"ceremony_id": 42},
            )
            parameters = parse_qs(urlsplit(authorization_url).query)
            self.assertEqual(parameters["prompt"], ["login"])
            self.assertEqual(parameters["max_age"], ["0"])
            self.assertEqual(parameters["nonce"], ["document-binding-digest"])
            self.assertEqual(parameters["code_challenge_method"], ["S256"])
            self.assertNotIn("code_verifier", parameters)

            transaction = controller._consume_oidc_transaction(state)
            self.assertEqual(transaction["ceremony_id"], 42)
            self.assertNotIn(state, fake_request.session[_SESSION_TRANSACTIONS])
            with self.assertRaises(AccessError):
                controller._consume_oidc_transaction(state)
