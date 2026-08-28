import unittest

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


if __name__ == "__main__":
    unittest.main()
