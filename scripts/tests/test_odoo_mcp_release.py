from __future__ import annotations

import importlib.machinery
import importlib.util
import json
import unittest
from pathlib import Path
from unittest.mock import patch

from operations.mcp_release import McpReleaseError


ROOT = Path(__file__).resolve().parents[2]
LOADER = importlib.machinery.SourceFileLoader(
    "odoo_mcp_script",
    str(ROOT / "scripts/odoo-mcp"),
)
SPEC = importlib.util.spec_from_loader(LOADER.name, LOADER)
assert SPEC
odoo_mcp = importlib.util.module_from_spec(SPEC)
LOADER.exec_module(odoo_mcp)


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


if __name__ == "__main__":
    unittest.main()
