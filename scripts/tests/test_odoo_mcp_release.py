from __future__ import annotations

import hashlib
import importlib.machinery
import importlib.util
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from operations.mcp_release import McpReleaseError, load_release, resolve_release


ROOT = Path(__file__).resolve().parents[2]
LOADER = importlib.machinery.SourceFileLoader(
    "odoo_mcp_script",
    str(ROOT / "scripts/odoo-mcp"),
)
SPEC = importlib.util.spec_from_loader(LOADER.name, LOADER)
assert SPEC
odoo_mcp = importlib.util.module_from_spec(SPEC)
LOADER.exec_module(odoo_mcp)


def write_release(root: Path, ref: str, *, commit: str = "a" * 40) -> str:
    compatibility = {
        "schema": "usl-odoo-mcp-compatibility-v1",
        "odoo_series": "19.0",
        "mcp_server_version": "1.0.0",
        "required_modules": [],
        "source_rpc_actions": [],
        "dynamic_rpc_actions": [],
    }
    compatibility_bytes = (json.dumps(compatibility, indent=2) + "\n").encode()
    release_directory = root / "deploy/odoo-mcp"
    release_directory.mkdir(parents=True)
    (release_directory / "compatibility.json").write_bytes(compatibility_bytes)
    release = {
        "schema": "usl-odoo-mcp-release-v2",
        "repository": "https://github.com/unstaticlabs/odoo-mcp.git",
        "ref": ref,
        "commit": commit,
        "image_tag": f"ghcr.io/unstaticlabs/odoo-mcp:sha-{commit}",
        "image_digest": f"ghcr.io/unstaticlabs/odoo-mcp@sha256:{'b' * 64}",
        "compatibility": "deploy/odoo-mcp/compatibility.json",
        "compatibility_sha256": hashlib.sha256(compatibility_bytes).hexdigest(),
    }
    (release_directory / "release.json").write_text(
        json.dumps(release, indent=2) + "\n",
        encoding="utf-8",
    )
    return commit


def initialize_source_checkout(root: Path, *, origin: str | None = None) -> tuple[Path, str]:
    checkout = root / "source"
    checkout.mkdir()
    commands = (
        ("git", "init", "--quiet"),
        ("git", "config", "user.name", "Release Test"),
        ("git", "config", "user.email", "release-test@example.com"),
        (
            "git",
            "remote",
            "add",
            "origin",
            origin or "https://github.com/unstaticlabs/odoo-mcp.git",
        ),
    )
    for command in commands:
        subprocess.run(command, cwd=checkout, check=True)
    source = checkout / "src"
    source.mkdir()
    (source / "version.ts").write_text(
        'export const SERVER_VERSION = "1.0.0";\n',
        encoding="utf-8",
    )
    subprocess.run(("git", "add", "."), cwd=checkout, check=True)
    subprocess.run(
        ("git", "commit", "--quiet", "-m", "test: initial source"),
        cwd=checkout,
        check=True,
    )
    commit = subprocess.check_output(
        ("git", "rev-parse", "HEAD"),
        cwd=checkout,
        text=True,
    ).strip()
    return checkout, commit


def write_empty_action_surface(root: Path) -> None:
    path = root / "custom-addons/usl_access_control/policy/action_surface.json"
    path.parent.mkdir(parents=True)
    path.write_text('{"modules": [], "actions": []}\n', encoding="utf-8")


class OdooMcpReleaseIdentityTest(unittest.TestCase):
    def test_accepts_the_exact_commit_as_ref(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            commit = write_release(root, "a" * 40)

            release = load_release(root)

        self.assertEqual(release["ref"], commit)
        self.assertEqual(release["commit"], commit)

    def test_rejects_a_mutable_branch_ref(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_release(root, "main")

            with self.assertRaisesRegex(McpReleaseError, "ref must equal"):
                load_release(root)

    def test_rejects_a_different_commit_ref(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_release(root, "c" * 40)

            with self.assertRaisesRegex(McpReleaseError, "ref must equal"):
                load_release(root)

    def test_exact_commit_still_resolves_after_checkout_advances(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            checkout, commit = initialize_source_checkout(root)
            write_release(root, commit, commit=commit)
            write_empty_action_surface(root)
            (checkout / "README.md").write_text("new main work\n", encoding="utf-8")
            subprocess.run(("git", "add", "."), cwd=checkout, check=True)
            subprocess.run(
                ("git", "commit", "--quiet", "-m", "test: advance main"),
                cwd=checkout,
                check=True,
            )

            release = resolve_release(root, checkout)

        self.assertEqual(release["commit"], commit)

    def test_rejects_the_wrong_checkout_origin(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            checkout, commit = initialize_source_checkout(
                root,
                origin="https://github.com/example/odoo-mcp.git",
            )
            write_release(root, commit, commit=commit)
            write_empty_action_surface(root)

            with self.assertRaisesRegex(McpReleaseError, "origin differs"):
                resolve_release(root, checkout)

    def test_rejects_a_missing_source_commit(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            checkout, _commit = initialize_source_checkout(root)
            missing = "c" * 40
            write_release(root, missing, commit=missing)
            write_empty_action_surface(root)

            with self.assertRaises(McpReleaseError):
                resolve_release(root, checkout)

    def test_rejects_a_modified_compatibility_contract(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_release(root, "a" * 40)
            compatibility = root / "deploy/odoo-mcp/compatibility.json"
            compatibility.write_text("{}\n", encoding="utf-8")

            with self.assertRaisesRegex(McpReleaseError, "digest differs"):
                load_release(root)


class OdooMcpImageReleaseTest(unittest.TestCase):
    def setUp(self) -> None:
        self.commit = "a" * 40
        self.image = f"ghcr.io/unstaticlabs/odoo-mcp@sha256:{'b' * 64}"
        self.release = {
            "repository": "https://github.com/unstaticlabs/odoo-mcp.git",
            "commit": self.commit,
            "image": self.image,
            "image_tag": f"ghcr.io/unstaticlabs/odoo-mcp:sha-{self.commit}",
        }
        self.inspection = {
            "Id": "sha256:" + "c" * 64,
            "RepoDigests": [self.image],
            "Config": {
                "Labels": {
                    "org.opencontainers.image.revision": self.commit,
                    "org.opencontainers.image.source": self.release["repository"],
                }
            },
        }

    def test_accepts_registry_manifest_digest_distinct_from_config_id(self) -> None:
        with patch.object(odoo_mcp, "run", return_value=json.dumps(self.inspection)):
            result = odoo_mcp.verify_image(self.release)

        self.assertEqual(result["image"], self.image)
        self.assertEqual(result["image_id"], self.inspection["Id"])

    def test_rejects_image_without_the_pinned_repository_digest(self) -> None:
        self.inspection["RepoDigests"] = []
        with (
            patch.object(odoo_mcp, "run", return_value=json.dumps(self.inspection)),
            self.assertRaisesRegex(McpReleaseError, "image bytes"),
        ):
            odoo_mcp.verify_image(self.release)

    def test_rejects_an_image_with_the_wrong_revision_label(self) -> None:
        self.inspection["Config"]["Labels"]["org.opencontainers.image.revision"] = "d" * 40
        with (
            patch.object(odoo_mcp, "run", return_value=json.dumps(self.inspection)),
            self.assertRaisesRegex(McpReleaseError, "revision label"),
        ):
            odoo_mcp.verify_image(self.release)

    def test_rejects_an_image_with_the_wrong_source_label(self) -> None:
        self.inspection["Config"]["Labels"]["org.opencontainers.image.source"] = (
            "https://github.com/example/odoo-mcp.git"
        )
        with (
            patch.object(odoo_mcp, "run", return_value=json.dumps(self.inspection)),
            self.assertRaisesRegex(McpReleaseError, "source label"),
        ):
            odoo_mcp.verify_image(self.release)


if __name__ == "__main__":
    unittest.main()
