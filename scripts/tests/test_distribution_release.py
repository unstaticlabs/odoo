from __future__ import annotations

import json
import re
import subprocess
import unittest
import xml.etree.ElementTree as ET
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
        self.assertIn("--release-notes operations/release-notes.json", self.workflow)
        notes = json.loads(
            (ROOT / "operations/release-notes.json").read_text(encoding="utf-8")
        )
        self.assertEqual(notes["schema"], "usl-release-notes/v1")
        self.assertTrue(notes["changes"])

    def test_release_is_only_published_from_permanent_release_branches(self) -> None:
        self.assertIn("- 19-usl-staging", self.workflow)
        self.assertIn("- 19-usl", self.workflow)
        self.assertNotIn("- codex/chore-post-migration-continuous-operations", self.workflow)
        self.assertIn("usl-odoo-release", self.workflow)
        self.assertIn("actions/attest@", self.workflow)

    def test_release_requires_exact_successful_qualification_or_recovery_tag(self) -> None:
        self.assertIn('.name == "USL qualification"', self.workflow)
        self.assertIn('.head_sha == env.GITHUB_SHA', self.workflow)
        self.assertIn('if [ "$conclusion" = success ]', self.workflow)
        self.assertIn("for attempt in $(seq 1 240)", self.workflow)
        self.assertIn("timeout-minutes: 45", self.workflow)
        self.assertIn("workflow_dispatch:refs/tags/recovery-*", self.workflow)
        self.assertIn('test "$RECOVERY_REF" = "${GITHUB_REF#refs/tags/}"', self.workflow)

    def test_stable_required_gate_includes_clean_database_qualification(self) -> None:
        qualification = (ROOT / ".github/workflows/qualification.yml").read_text(
            encoding="utf-8",
        )
        self.assertIn("pull_request:", qualification)
        self.assertIn("merge_group:", qualification)
        self.assertIn("push:", qualification)
        self.assertIn("branches: [19-usl, 19-usl-staging]", qualification)
        self.assertIn("name: USL qualification", qualification)
        self.assertIn("scripts/ci-product-database", qualification)
        self.assertIn("test \"$DATABASE\" = success", qualification)
        self.assertGreaterEqual(qualification.count("scripts/sync-oca-addons"), 2)
        database_job = qualification.split("  database:\n", 1)[1].split("\n  accounting:\n", 1)[0]
        self.assertIn("fetch-depth: 0", database_job)

    def test_affected_frontend_suites_run_on_desktop_and_mobile(self) -> None:
        database_gate = (ROOT / "scripts/ci-product-database").read_text(
            encoding="utf-8",
        )
        self.assertIn("static/tests", database_gate)
        self.assertIn("*.test.js", database_gate)
        self.assertIn("WebSuite.test_unit_desktop", database_gate)
        self.assertIn("MobileWebSuite.test_unit_mobile", database_gate)
        self.assertIn('--update="$module"', database_gate)

    def test_mcp_checkout_uses_the_exact_release_commit(self) -> None:
        self.assertIn(
            "ref: ${{ needs.resolve.outputs.mcp_commit }}",
            self.workflow,
        )
        self.assertNotIn("mcp_ref", self.workflow)
        release = json.loads(
            (ROOT / "deploy/odoo-mcp/release.json").read_text(encoding="utf-8")
        )
        self.assertEqual(release["ref"], release["commit"])

    def test_renderer_revision_is_consistent_across_release_inputs(self) -> None:
        release = json.loads(
            (ROOT / "deploy/document-renderer/release.json").read_text(
                encoding="utf-8",
            )
        )
        expected = release["commit"]
        gitlink = subprocess.check_output(
            ["git", "ls-files", "--stage", "services/usl-document-renderer"],
            cwd=ROOT,
            text=True,
        ).split()[1]
        self.assertEqual(gitlink, expected)

        pin_check = (ROOT / "scripts/check-document-renderer-submodule").read_text(
            encoding="utf-8",
        )
        self.assertIn(f'EXPECTED="{expected}"', pin_check)

        compose = (ROOT / "compose.yaml").read_text(encoding="utf-8")
        self.assertIn(f"USL_TEMPLATE_REVISION: {expected}", compose)
        self.assertIn(f"usl-document-renderer:{expected[:12]}", compose)

        data = ET.parse(
            ROOT
            / "custom-addons/usl_document_templates/data/document_template_data.xml"
        )
        revision = data.find(
            ".//record[@id='renderer_expected_revision']/field[@name='value']"
        )
        self.assertIsNotNone(revision)
        self.assertEqual(revision.text, expected)

    def test_production_compose_never_builds_from_checkout(self) -> None:
        overlay = (ROOT / "compose.production.yaml").read_text(encoding="utf-8")
        self.assertIn("build: !reset null", overlay)
        self.assertNotIn("./custom-addons", overlay)

    def test_release_image_can_load_installed_oca_tests(self) -> None:
        requirements = (ROOT / "requirements.txt").read_text(encoding="utf-8")
        self.assertRegex(requirements, r"(?m)^responses==0\.26\.2\b")

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
