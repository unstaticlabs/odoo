"""The bearer API index is served from the stored copy, whatever the client's cache directive."""

from http import HTTPStatus
from unittest.mock import patch

from odoo import Command
from odoo.tests import HttpCase, tagged

from ..models.agent_credential import UslAgentKeyWizard
from odoo.addons.api_doc.controllers.api_doc import DocController


@tagged("post_install", "-at_install", "usl_access_control")
class TestAgentApiDocIndex(HttpCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.owner = cls.env["res.users"].with_context(
            no_reset_password=True, usl_governed_identity_provisioning=True,
        ).create({
            "name": "[QA api doc] Owner", "login": "qa.owner.api.doc",
            "email": "qa.owner.api.doc@example.test", "company_id": cls.company.id,
            "company_ids": [Command.set(cls.company.ids)],
            "group_ids": [Command.set(cls.env.ref("base.group_system").ids)],
            "usl_identity_classification": "active",
        })
        cls.agent = cls.env["usl.agent"].with_user(cls.owner).create({
            "name": "[QA api doc] Reader", "purpose": "Read the API documentation",
            "company_id": cls.company.id, "company_ids": [Command.set(cls.company.ids)],
            "delegated_group_ids": [Command.set(cls.env.ref("base.group_system").ids)],
            "access_mode": "read_only",
        })
        wizard = cls.env["usl.agent.key.wizard"].with_user(cls.owner).create({
            "agent_id": cls.agent.id, "name": "[QA api doc] HTTP",
        })
        cls.key = UslAgentKeyWizard.action_generate.__wrapped__(wizard)["context"]["default_key"]

    def _index(self, **headers):
        return self.url_open(
            "/doc-bearer/index.json",
            headers={
                "Authorization": f"Bearer {self.key}",
                "X-Odoo-Database": self.env.cr.dbname,
                **headers,
            },
            allow_redirects=False,
        )

    def test_bearer_index_ignores_client_no_cache_directive(self):
        # Node's fetch marks every conditional request `Cache-Control: no-cache`.
        # On the bearer route that directive must not force a rebuild: the stored
        # index is keyed by registry sequence, groups and Agent policy.
        first = self._index()
        first.raise_for_status()
        self.assertEqual(first.status_code, HTTPStatus.OK)
        etag = first.headers.get("ETag", "")
        self.assertTrue(etag, "the stored index answers with an ETag")

        with patch.object(
            DocController, "_doc_index", side_effect=DocController()._doc_index,
        ) as rebuild:
            revalidated = self._index(**{"If-None-Match": etag, "Cache-Control": "no-cache"})
        self.assertEqual(revalidated.status_code, HTTPStatus.NOT_MODIFIED)
        rebuild.assert_not_called()

        with patch.object(
            DocController, "_doc_index", side_effect=DocController()._doc_index,
        ) as rebuild:
            forced = self._index(**{"Cache-Control": "no-cache"})
        forced.raise_for_status()
        self.assertEqual(forced.status_code, HTTPStatus.OK)
        self.assertEqual(forced.headers.get("ETag"), etag)
        self.assertEqual(forced.json(), first.json())
        rebuild.assert_not_called()
