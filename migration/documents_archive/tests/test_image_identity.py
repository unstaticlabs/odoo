from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path
from unittest.mock import patch


SCRIPT = Path(__file__).parents[1] / "image_identity.py"
SPEC = importlib.util.spec_from_file_location("image_identity", SCRIPT)
assert SPEC and SPEC.loader
image_identity = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(image_identity)


class ImageIdentityTest(unittest.TestCase):
    def setUp(self) -> None:
        self.config = {
            "services": {
                name: {"image": f"example.invalid/{name}@sha256:{'1' * 64}"}
                for name in image_identity.REQUIRED_SERVICES
            },
        }
        self.inspection = {
            "reference": "unused",
            "id": "sha256:" + "2" * 64,
            "repo_digests": ["example.invalid/image@sha256:" + "3" * 64],
            "architecture": "amd64",
            "os": "linux",
            "labels": {},
        }

    @patch.object(image_identity, "inspect")
    def test_external_ollama_topology_does_not_require_a_service(self, inspect) -> None:
        inspect.return_value = self.inspection

        result = image_identity.build(self.config, target_platform="linux/amd64")

        self.assertEqual(result["target_platform_status"], "passed")
        self.assertNotIn("paperless-ollama", result["images"])

    @patch.object(image_identity, "inspect")
    def test_owned_ollama_service_is_recorded_when_present(self, inspect) -> None:
        inspect.return_value = self.inspection
        self.config["services"]["paperless-ollama"] = {
            "image": "example.invalid/ollama@sha256:" + "4" * 64,
        }

        result = image_identity.build(self.config, target_platform="linux/amd64")

        self.assertIn("paperless-ollama", result["images"])


if __name__ == "__main__":
    unittest.main()
