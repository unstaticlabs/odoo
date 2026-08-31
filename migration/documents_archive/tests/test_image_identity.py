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

    @patch.object(image_identity, "inspect")
    def test_airgapped_image_id_is_an_immutable_identity(self, inspect) -> None:
        image_id = "sha256:" + "5" * 64
        inspect.return_value = {
            **self.inspection,
            "reference": image_id,
            "id": image_id,
            "repo_digests": [],
        }
        self.config["services"]["paperless-webserver"]["image"] = image_id

        result = image_identity.build(self.config, target_platform="linux/amd64")

        self.assertEqual(result["target_platform_status"], "passed")

    @patch.object(image_identity, "inspect")
    def test_mutable_airgapped_tag_without_digest_is_partial(self, inspect) -> None:
        inspect.return_value = {**self.inspection, "repo_digests": []}

        result = image_identity.build(self.config, target_platform="linux/amd64")

        self.assertEqual(result["target_platform_status"], "partial")


if __name__ == "__main__":
    unittest.main()
