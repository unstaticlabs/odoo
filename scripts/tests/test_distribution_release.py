from __future__ import annotations

import re
import unittest
from pathlib import Path

from operations.component_build import COMPONENTS, resolve


ROOT = Path(__file__).resolve().parents[2]


class DistributionReleaseWorkflowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.workflow = (ROOT / ".github/workflows/product-image.yml").read_text(
            encoding="utf-8",
        )

    def test_all_external_actions_are_pinned(self) -> None:
        for workflow in (ROOT / ".github/workflows").glob("*.yml"):
            for line in workflow.read_text(encoding="utf-8").splitlines():
                if re.match(r"\s*uses:\s*", line):
                    self.assertRegex(
                        line,
                        r"uses:\s+[^@\s]+@[0-9a-f]{40}(?:\s+#.*)?$",
                        str(workflow),
                    )

    def test_release_components_are_content_addressed(self) -> None:
        components = resolve()["components"]
        self.assertEqual(set(components), set(COMPONENTS))
        for component in components.values():
            self.assertEqual(
                component["tag"],
                f"content-{component['input_sha256']}",
            )

    def test_workflow_builds_components_in_parallel_with_registry_cache(self) -> None:
        self.assertIn("matrix: ${{ fromJSON(needs.resolve.outputs.matrix) }}", self.workflow)
        self.assertIn("type=registry,ref=${{ matrix.image }}:buildcache", self.workflow)
        self.assertIn("mode=max", self.workflow)
        self.assertIn("type=gha,scope=${{ matrix.name }}", self.workflow)
        self.assertNotIn("cache-to: type=gha", self.workflow)

    def test_existing_content_image_skips_build_and_attestation(self) -> None:
        self.assertGreaterEqual(
            self.workflow.count("if: steps.existing.outputs.exists != 'true'"),
            2,
        )
        self.assertIn("docker buildx imagetools inspect", self.workflow)

    def test_release_binds_external_services_and_ollama(self) -> None:
        self.assertIn("scripts/odoo-mcp verify", self.workflow)
        self.assertIn("deploy/document-renderer/release.json", self.workflow)
        self.assertIn("OLLAMA_MANIFEST_SHA256", self.workflow)
        self.assertIn("scripts/release-manifest create", self.workflow)

    def test_production_compose_never_builds_from_checkout(self) -> None:
        overlay = (ROOT / "compose.production.yaml").read_text(encoding="utf-8")
        self.assertIn("build: !reset null", overlay)
        self.assertNotIn("./custom-addons", overlay)

    def test_external_ingress_alias_is_explicit(self) -> None:
        overlay = (ROOT / "compose.odoo-ingress.yaml").read_text(encoding="utf-8")
        self.assertIn(
            "${ODOO_INGRESS_ALIAS:?A unique Odoo ingress alias is required}",
            overlay,
        )
        self.assertIn(
            "${USL_EXTERNAL_INGRESS_NETWORK:?Existing ingress network is required}",
            overlay,
        )


if __name__ == "__main__":
    unittest.main()
