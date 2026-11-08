"""An Agent reads the user guide through JSON-2, and only reads it."""

import json
import os
import tempfile
from pathlib import Path
from unittest.mock import patch

from odoo import Command
from odoo.tests import HttpCase, tagged

from ..models.agent_credential import UslAgentKeyWizard

GUIDE = (
    "---\ntitle: Match a line\ntype: how-to\ndescription: Match one bank line.\n"
    "lang: en\npersona: accountant\napp: Accounting\ngenerated: false\n---\n\n"
    "# Match a line\n\nOpen the bank journal and pick the counterpart.\n"
)


@tagged("post_install", "-at_install", "usl_access_control")
class TestAgentReadsTheGuide(HttpCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        if "usl.docs.guide" not in cls.env:
            cls.skipTest(cls, "usl_docs is not installed")
        cls.owner = cls.env["res.users"].with_context(
            no_reset_password=True, usl_governed_identity_provisioning=True,
        ).create({
            "name": "[QA guide] Owner", "login": "qa.guide.owner",
            "email": "qa.guide.owner@example.test",
            "company_id": cls.env.company.id,
            "company_ids": [Command.set(cls.env.company.ids)],
            "group_ids": [Command.set([cls.env.ref("project.group_project_user").id])],
            "usl_identity_classification": "active",
        })
        # A brand-new Agent: owner-scoped, read-only everywhere.
        cls.agent = cls.env["usl.agent"].with_user(cls.owner).create({
            "name": "[QA guide] Agent", "purpose": "Read the user guide",
            "company_id": cls.env.company.id,
            "company_ids": [Command.set(cls.env.company.ids)],
        })
        wizard = cls.env["usl.agent.key.wizard"].with_user(cls.owner).create({
            "agent_id": cls.agent.id, "name": "[QA guide] HTTP",
        })
        cls.key = UslAgentKeyWizard.action_generate.__wrapped__(wizard)["context"]["default_key"]

    def setUp(self):
        super().setUp()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        (root / "README.md").write_text("# Guide\n", encoding="utf-8")
        (root / "how-to").mkdir()
        (root / "how-to" / "match-a-line.md").write_text(GUIDE, encoding="utf-8")
        patcher = patch.dict(os.environ, {"USL_USER_DOCS_PATH": str(root)})
        patcher.start()
        self.addCleanup(patcher.stop)

    def _call(self, method, **kwargs):
        return self.url_open(
            f"/json/2/usl.docs.guide/{method}",
            data=json.dumps(kwargs),
            headers={
                "Authorization": f"Bearer {self.key}",
                "Content-Type": "application/json",
                "X-Odoo-Database": self.env.cr.dbname,
            },
        )

    def test_a_read_only_agent_searches_and_reads_the_guide(self):
        hits = self._call("search_pages", query="counterpart")
        self.assertEqual(hits.status_code, 200, hits.text)
        self.assertEqual([hit["path"] for hit in hits.json()], ["how-to/match-a-line.md"])
        page = self._call("read_page", path="how-to/match-a-line.md")
        self.assertEqual(page.status_code, 200, page.text)
        self.assertEqual(page.json()["title"], "Match a line")
        self.assertIn("pick the counterpart", page.json()["markdown"])
        index = self._call("list_pages")
        self.assertEqual(index.status_code, 200, index.text)
        self.assertEqual(index.json()["schema"], "usl-docs-index/v1")

    def test_the_agent_cannot_write_the_guide_model(self):
        for method, payload in (("create", {"vals_list": [{}]}), ("write", {"ids": [1], "vals": {}}), ("unlink", {"ids": [1]})):
            response = self._call(method, **payload)
            self.assertEqual(response.status_code, 403, f"{method} must be refused by the Agent gate: {response.text}")
