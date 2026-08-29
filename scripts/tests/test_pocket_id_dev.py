import contextlib
import io
import json
import os
import unittest
from unittest import mock

from scripts import pocket_id_dev


class _PocketIdApi:
    def __init__(
        self,
        *,
        existing_client=None,
        updated_client=None,
        legacy_secret_api=False,
    ):
        self.existing_client = existing_client
        self.updated_client = updated_client
        self.legacy_secret_api = legacy_secret_api
        self.calls = []

    def request(self, method, path, payload=None):
        self.calls.append((method, path, payload))
        if method == "GET" and path.endswith("/secrets"):
            if self.legacy_secret_api:
                raise pocket_id_dev.PocketIDError(
                    "Pocket ID returned HTTP 404: API endpoint not found",
                )
            return []
        if method == "GET":
            clients = [self.existing_client] if self.existing_client else []
            return {"data": clients}
        if method == "PUT" and path.endswith("/allowed-user-groups"):
            return None
        if method == "PUT":
            return self.updated_client
        if method == "POST" and path == "/api/oidc/clients":
            return self.updated_client
        if method == "POST" and path.endswith("/secrets"):
            return {"id": "credential-id", "isActive": True}
        if method == "POST" and path.endswith("/secret"):
            return {"id": "credential-id", "isActive": True}
        raise AssertionError(f"Unexpected request: {method} {path}")


class PocketIdClientProvisioningTest(unittest.TestCase):
    def test_existing_active_secret_is_not_duplicated(self):
        client = {
            "id": "odoo-client",
            "credentials": {"secrets": [{"id": "existing", "isActive": True}]},
        }
        api = _PocketIdApi(existing_client=client, updated_client=client)

        pocket_id_dev._ensure_client(
            api,
            {"id": "odoo-client", "name": "Odoo"},
            "configured-secret",
            "odoo-users",
        )

        secret_request = (
            "POST",
            "/api/oidc/clients/odoo-client/secrets",
            {"secret": "configured-secret"},
        )
        self.assertNotIn(secret_request, api.calls)

    def test_missing_secret_uses_plural_pocket_id_endpoint(self):
        client = {"id": "odoo-client", "credentials": {"secrets": []}}
        api = _PocketIdApi(updated_client=client)

        pocket_id_dev._ensure_client(
            api,
            {"id": "odoo-client", "name": "Odoo"},
            "configured-secret",
            "odoo-users",
        )

        secret_request = (
            "POST",
            "/api/oidc/clients/odoo-client/secrets",
            {"secret": "configured-secret"},
        )
        self.assertIn(secret_request, api.calls)

    def test_existing_client_on_legacy_api_keeps_write_only_secret(self):
        client = {"id": "odoo-client"}
        api = _PocketIdApi(
            existing_client=client,
            updated_client=client,
            legacy_secret_api=True,
        )

        pocket_id_dev._ensure_client(
            api,
            {"id": "odoo-client", "name": "Odoo"},
            "configured-secret",
            "odoo-users",
        )

        self.assertFalse(any(path.endswith("/secret") for _, path, _ in api.calls))

    def test_new_client_on_legacy_api_uses_singular_secret_endpoint(self):
        client = {"id": "odoo-client"}
        api = _PocketIdApi(updated_client=client, legacy_secret_api=True)

        pocket_id_dev._ensure_client(
            api,
            {"id": "odoo-client", "name": "Odoo"},
            "configured-secret",
            "odoo-users",
        )

        self.assertIn(
            (
                "POST",
                "/api/oidc/clients/odoo-client/secret",
                {"secret": "configured-secret"},
            ),
            api.calls,
        )


class PocketIdOdooPolicyTest(unittest.TestCase):
    def _policy(self, **environment):
        values = {
            "POCKET_ID_APP_URL": "http://pocket-id.localhost:1411",
            "POCKET_ID_VALENTIN_ID": "valentin-subject",
            "POCKET_ID_ROGER_ID": "roger-subject",
            "POCKET_ID_PROSPER_ID": "prosper-subject",
            "POCKET_ID_PROSPER_EMAIL": "prosper@example.test",
        }
        output = io.StringIO()
        with mock.patch.dict(os.environ, environment, clear=False):
            with contextlib.redirect_stdout(output):
                pocket_id_dev.odoo_policy(values)
        return json.loads(output.getvalue())

    def test_reconstructed_policy_contains_historical_login_once(self):
        policy = self._policy(
            USL_POCKET_ID_POLICY_CLEAN_DATABASE="0",
            USL_POCKET_ID_POLICY_BASE_PROFILES_ONLY="0",
            USL_POCKET_ID_POLICY_SINGLE_COMPANY="0",
        )
        historical = [row for row in policy if row["login"] == "roger@xaic.cat"]

        self.assertEqual(
            historical,
            [{"login": "roger@xaic.cat", "profile": "historical"}],
        )

    def test_clean_policy_keeps_historical_login_optional(self):
        policy = self._policy(
            USL_POCKET_ID_POLICY_CLEAN_DATABASE="1",
            USL_POCKET_ID_POLICY_BASE_PROFILES_ONLY="0",
            USL_POCKET_ID_POLICY_SINGLE_COMPANY="0",
        )
        historical = [row for row in policy if row["login"] == "roger@xaic.cat"]

        self.assertEqual(
            historical,
            [
                {
                    "login": "roger@xaic.cat",
                    "profile": "historical",
                    "optional_if_missing": True,
                },
            ],
        )


if __name__ == "__main__":
    unittest.main()
