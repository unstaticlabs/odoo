from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from operations.docs import mcp

COMMIT_PLACEHOLDER = "0" * 40
CATALOGUE = (
    "# Tool catalogue and profiles\n"
    "\n"
    "The executable source of truth is the registry.\n"
    "\n"
    "## Discovery\n"
    "\n"
    "| Tool | Rule |\n"
    "| --- | --- |\n"
    "| `odoo_search_models` | Find models. |\n"
    "\n"
    "See the [qualification runbook](document-materialization.md), the\n"
    "[handoff](source-evidence-handoff.md#inputs), the [root README](../README.md)\n"
    "and [OpenAI tool search](https://developers.openai.com/api/docs/guides/tools-tool-search).\n"
)


def git(repository, *arguments):
    return subprocess.run(
        ("git", "-C", str(repository), *arguments), text=True, check=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        env={"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com",
             "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com",
             "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1", "HOME": str(repository)},
    ).stdout.strip()


class DocsMcpTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        base = Path(self.tmp.name)
        # A stand-in odoo-mcp checkout with one commit holding the catalogue.
        self.repository = base / "odoo-mcp"
        (self.repository / "docs").mkdir(parents=True)
        (self.repository / "docs" / "tool-catalogue.md").write_text(CATALOGUE, encoding="utf-8")
        git(self.repository, "init", "-q")
        git(self.repository, "add", ".")
        git(self.repository, "commit", "-q", "-m", "catalogue")
        self.commit = git(self.repository, "rev-parse", "HEAD")
        # A stand-in distribution root pinning that commit.
        self.root = base / "odoo"
        (self.root / "deploy" / "odoo-mcp").mkdir(parents=True)
        (self.root / "deploy" / "odoo-mcp" / "release.json").write_text(
            json.dumps({"schema": "usl-odoo-mcp-release-v2", "commit": self.commit}), encoding="utf-8",
        )
        (self.root / "deploy" / "odoo-mcp" / "compatibility.json").write_text(
            json.dumps({"schema": "usl-odoo-mcp-compatibility-v1", "mcp_server_version": "1.1.0"}), encoding="utf-8",
        )
        self.docs = self.root / "docs" / "users"
        self.docs.mkdir(parents=True)

    def test_render_writes_the_page_once_and_check_agrees(self):
        written = mcp.render(self.repository, self.root, self.docs)
        self.assertEqual([path.relative_to(self.docs).as_posix() for path in written], ["reference/mcp-tools.md"])
        self.assertEqual(mcp.render(self.repository, self.root, self.docs), [], "a second render writes nothing")
        self.assertEqual(mcp.check(self.repository, self.root, self.docs), [])
        page = (self.docs / "reference" / "mcp-tools.md").read_text(encoding="utf-8")
        self.assertTrue(page.startswith(
            "---\ntitle: MCP tools and profiles\ntype: reference\n"
            "description: The tools, profiles and conventions of the pinned Odoo MCP server, from its own catalogue.\n"
            "lang: en\npersona: administrator\napp: Agents & MCP\nsource:\n  - deploy/odoo-mcp/release.json\n"
            f"mcp_commit: {self.commit}\ngenerated: true\n---\n",
        ), page)
        self.assertIn("\n# MCP tools and profiles\n", page)
        self.assertNotIn("# Tool catalogue and profiles", page, "the catalogue's heading gives way to the page title")
        self.assertIn(f"version 1.1.0 at commit [`{self.commit[:12]}`]", page)
        self.assertIn("`make docs-mcp`", page)
        self.assertIn("## Discovery\n\n| Tool | Rule |", page)
        base = f"https://github.com/unstaticlabs/odoo-mcp/blob/{self.commit}"
        self.assertIn(f"[qualification runbook]({base}/docs/document-materialization.md)", page)
        self.assertIn(f"[handoff]({base}/docs/source-evidence-handoff.md#inputs)", page)
        self.assertIn(f"[root README]({base}/README.md)", page)
        self.assertIn("[OpenAI tool search](https://developers.openai.com/api/docs/guides/tools-tool-search)", page)

    def test_check_fails_when_the_page_is_edited_or_missing(self):
        self.assertEqual(mcp.check(self.repository, self.root, self.docs), [
            (str(self.docs / "reference" / "mcp-tools.md"), "missing; run `make docs-mcp`"),
        ])
        mcp.render(self.repository, self.root, self.docs)
        page = self.docs / "reference" / "mcp-tools.md"
        page.write_text(page.read_text(encoding="utf-8") + "\nA hand edit.\n", encoding="utf-8")
        findings = mcp.check(self.repository, self.root, self.docs)
        self.assertEqual(len(findings), 1)
        self.assertIn("make docs-mcp", findings[0][1])

    def test_rendering_is_deterministic(self):
        first = mcp.render_page(CATALOGUE, self.commit, "1.1.0")
        self.assertEqual(first, mcp.render_page(CATALOGUE, self.commit, "1.1.0"))

    def test_a_pin_without_a_full_sha_is_refused(self):
        (self.root / "deploy" / "odoo-mcp" / "release.json").write_text(json.dumps({"commit": "main"}), encoding="utf-8")
        with self.assertRaises(mcp.DocsMcpError):
            mcp.pinned(self.root)

    def test_a_commit_the_checkout_lacks_is_an_error_not_a_page(self):
        (self.root / "deploy" / "odoo-mcp" / "release.json").write_text(
            json.dumps({"commit": COMMIT_PLACEHOLDER}), encoding="utf-8",
        )
        with self.assertRaises(mcp.DocsMcpError):
            mcp.render(self.repository, self.root, self.docs)
        self.assertFalse((self.docs / "reference" / "mcp-tools.md").exists())


if __name__ == "__main__":
    unittest.main()
