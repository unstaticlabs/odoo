from __future__ import annotations

import json
import hashlib
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
        source = self.checkout / "src/capabilities"
        source.mkdir(parents=True)
        (self.checkout / "src/version.ts").write_text(
            'export const SERVER_VERSION = "1.0.0";\n',
            encoding="utf-8",
        )
        (source / "example.ts").write_text(
            'requiredModules: ["base"]\nclient.call(context, "res.users", "read", {})\n',
            encoding="utf-8",
        )
        subprocess.run(("git", "add", "."), cwd=self.checkout, check=True)
        subprocess.run(("git", "commit", "-qm", "test"), cwd=self.checkout, check=True)
        self.commit = subprocess.run(
            ("git", "rev-parse", "HEAD"),
            cwd=self.checkout,
            text=True,
            capture_output=True,
            check=True,
        ).stdout.strip()
        subprocess.run(
            ("git", "branch", "codex/secure-document-materialization"),
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
        compatibility = directory / "compatibility.json"
        compatibility.write_text(
            json.dumps(
                {
                    "schema": "usl-odoo-mcp-compatibility-v1",
                    "odoo_series": "19.0",
                    "mcp_server_version": "1.0.0",
                    "required_modules": ["base"],
                    "source_rpc_actions": ["rpc:res.users.read"],
                    "dynamic_rpc_actions": [],
                },
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        policy = self.root / "custom-addons/usl_access_control/policy"
        policy.mkdir(parents=True)
        (policy / "action_surface.json").write_text(
            json.dumps(
                {
                    "modules": [{"name": "base"}],
                    "actions": [{"key": "rpc:res.users.read"}],
                }
            )
            + "\n",
            encoding="utf-8",
        )
        self.release_path = directory / "release.json"
        self.release_path.write_text(
            json.dumps(
                {
                    "schema": "usl-odoo-mcp-release-v2",
                    "repository": "https://github.com/unstaticlabs/odoo-mcp.git",
                    "ref": "codex/secure-document-materialization",
                    "commit": self.commit,
                    "image_tag": f"usl-odoo-mcp:{self.commit[:12]}",
                    "image_digest": f"usl-odoo-mcp@sha256:{'a' * 64}",
                    "compatibility": "deploy/odoo-mcp/compatibility.json",
                    "compatibility_sha256": hashlib.sha256(
                        compatibility.read_bytes()
                    ).hexdigest(),
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
            ("git", "branch", "-f", "codex/secure-document-materialization", "HEAD"),
            cwd=self.checkout,
            check=True,
        )
        with self.assertRaisesRegex(McpReleaseError, "ref differs"):
            resolve_release(self.root, self.checkout)

    def test_rejects_an_image_tag_not_bound_to_the_commit(self):
        value = json.loads(self.release_path.read_text(encoding="utf-8"))
        value["image_tag"] = "usl-odoo-mcp:latest"
        self.release_path.write_text(json.dumps(value) + "\n", encoding="utf-8")
        with self.assertRaisesRegex(McpReleaseError, "release commit"):
            load_release(self.root)

    def test_accepts_registry_commit_tag(self):
        value = json.loads(self.release_path.read_text(encoding="utf-8"))
        value["image_tag"] = f"ghcr.io/unstaticlabs/odoo-mcp:sha-{self.commit}"
        self.release_path.write_text(json.dumps(value) + "\n", encoding="utf-8")

        self.assertEqual(load_release(self.root)["image_tag"], value["image_tag"])

    def test_rejects_a_mutated_compatibility_contract(self):
        path = self.root / "deploy/odoo-mcp/compatibility.json"
        path.write_text("{}\n", encoding="utf-8")
        with self.assertRaisesRegex(McpReleaseError, "digest differs"):
            load_release(self.root)

    def test_rejects_an_odoo_surface_missing_a_required_rpc(self):
        path = self.root / "custom-addons/usl_access_control/policy/action_surface.json"
        path.write_text(json.dumps({"modules": [{"name": "base"}], "actions": []}) + "\n")
        with self.assertRaisesRegex(McpReleaseError, "compatibility contract is not satisfied"):
            resolve_release(self.root, self.checkout)


if __name__ == "__main__":
    unittest.main()
