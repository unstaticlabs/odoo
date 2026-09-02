from __future__ import annotations

import copy
import unittest

from operations.release_manifest import ReleaseManifestError, validate


COMMIT = "a" * 40


def component(name: str) -> dict[str, object]:
    input_sha = ({"distribution": "1", "backup-tool": "2", "paperless": "3", "sign-dss": "4"}[name]) * 64
    image = f"ghcr.io/unstaticlabs/{name}"
    digest = "sha256:" + "a" * 64
    return {
        "input_sha256": input_sha,
        "image": image,
        "tag": f"content-{input_sha}",
        "digest": digest,
        "digest_reference": f"{image}@{digest}",
    }


def manifest() -> dict[str, object]:
    return {
        "schema": "usl-release/v2",
        "source": {"repository": "unstaticlabs/odoo", "commit": COMMIT},
        "components": {name: component(name) for name in ("distribution", "backup-tool", "paperless", "sign-dss")},
        "mcp": {
            "repository": "https://github.com/unstaticlabs/odoo-mcp.git",
            "ref": "b" * 40,
            "commit": "b" * 40,
            "image": "ghcr.io/unstaticlabs/odoo-mcp@sha256:" + "b" * 64,
            "compatibility_sha256": "c" * 64,
        },
        "renderer": {
            "repository": "https://github.com/unstaticlabs/unstatic_latex_templates",
            "commit": "d" * 40,
            "image": "ghcr.io/unstaticlabs/usl-document-renderer@sha256:" + "d" * 64,
        },
        "ollama": {
            "image": "ollama/ollama@sha256:" + "e" * 64,
            "model": "bge-m3:latest",
            "manifest_sha256": "f" * 64,
            "dimension": 1024,
        },
        "build": {
            "workflow_run_id": 123,
            "workflow_run_attempt": 1,
            "workflow_url": "https://github.com/unstaticlabs/odoo/actions/runs/123",
        },
    }


class ReleaseManifestTests(unittest.TestCase):
    def test_accepts_complete_content_addressed_release(self) -> None:
        self.assertEqual(validate(manifest(), commit=COMMIT)["schema"], "usl-release/v2")

    def test_rejects_component_tag_not_bound_to_inputs(self) -> None:
        value = copy.deepcopy(manifest())
        value["components"]["distribution"]["tag"] = "content-" + "9" * 64
        with self.assertRaisesRegex(ReleaseManifestError, "tag"):
            validate(value)

    def test_rejects_mutable_external_image(self) -> None:
        value = copy.deepcopy(manifest())
        value["mcp"]["image"] = "ghcr.io/unstaticlabs/odoo-mcp:latest"
        with self.assertRaisesRegex(ReleaseManifestError, "MCP identity"):
            validate(value)

    def test_rejects_wrong_embedding_dimension(self) -> None:
        value = copy.deepcopy(manifest())
        value["ollama"]["dimension"] = 768
        with self.assertRaisesRegex(ReleaseManifestError, "1024"):
            validate(value)


if __name__ == "__main__":
    unittest.main()
