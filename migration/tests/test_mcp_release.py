from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from migration.mcp_release import McpReleaseError, load_release, resolve_release


class McpReleaseTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.checkout = self.root / "source"
        self.checkout.mkdir()
        subprocess.run(("git", "init", "-q"), cwd=self.checkout, check=True)
        subprocess.run(("git", "config", "user.name", "Test"), cwd=self.checkout, check=True)
        subprocess.run(("git", "config", "user.email", "test@example.test"), cwd=self.checkout, check=True)
        (self.checkout / "package.json").write_text("{}\n", encoding="utf-8")
        subprocess.run(("git", "add", "package.json"), cwd=self.checkout, check=True)
        subprocess.run(("git", "commit", "-qm", "test"), cwd=self.checkout, check=True)
        self.commit = subprocess.run(
            ("git", "rev-parse", "HEAD"),
            cwd=self.checkout,
            text=True,
            capture_output=True,
            check=True,
        ).stdout.strip()
        subprocess.run(
            ("git", "branch", "codex/odoo-mcp-vps-refactor"),
            cwd=self.checkout,
            check=True,
        )
        subprocess.run(
            (
                "git",
                "remote",
                "add",
                "origin",
                "git@github.com:unstaticlabs/odoo-mcp.git",
            ),
            cwd=self.checkout,
            check=True,
        )
        directory = self.root / "deploy/odoo-mcp"
        directory.mkdir(parents=True)
        self.release_path = directory / "release.json"
        self.release_path.write_text(
            json.dumps(
                {
                    "schema": "usl-odoo-mcp-release-v1",
                    "repository": "https://github.com/unstaticlabs/odoo-mcp.git",
                    "ref": "codex/odoo-mcp-vps-refactor",
                    "commit": self.commit,
                    "image": f"usl-odoo-mcp:{self.commit[:12]}",
                }
            )
            + "\n",
            encoding="utf-8",
        )

    def tearDown(self):
        self.temporary.cleanup()

    def test_resolves_exact_ref_commit_and_normalized_origin(self):
        identity = resolve_release(self.root, self.checkout)
        self.assertEqual(identity["commit"], self.commit)
        self.assertEqual(identity["checkout"], str(self.checkout.resolve()))

    def test_rejects_a_ref_that_moves_without_release_update(self):
        (self.checkout / "package.json").write_text('{"changed":true}\n', encoding="utf-8")
        subprocess.run(("git", "add", "package.json"), cwd=self.checkout, check=True)
        subprocess.run(("git", "commit", "-qm", "move ref"), cwd=self.checkout, check=True)
        subprocess.run(
            ("git", "branch", "-f", "codex/odoo-mcp-vps-refactor", "HEAD"),
            cwd=self.checkout,
            check=True,
        )
        with self.assertRaisesRegex(McpReleaseError, "ref differs"):
            resolve_release(self.root, self.checkout)

    def test_rejects_an_image_tag_not_bound_to_the_commit(self):
        value = json.loads(self.release_path.read_text(encoding="utf-8"))
        value["image"] = "usl-odoo-mcp:latest"
        self.release_path.write_text(json.dumps(value) + "\n", encoding="utf-8")
        with self.assertRaisesRegex(McpReleaseError, "commit prefix"):
            load_release(self.root)


if __name__ == "__main__":
    unittest.main()
