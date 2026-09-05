from __future__ import annotations

import json
import os
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
                    target = line.split("uses:", 1)[1].strip()
                    if target.startswith("./"):
                        self.assertRegex(target, r"^\./\.github/workflows/.+\.yml$")
                    else:
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

    def test_receipt_images_are_qualified_before_attestation(self) -> None:
        self.assertIn("Qualify isolated receipt component", self.workflow)
        self.assertIn("-m unittest -v /app/test_app.py", self.workflow)
        self.assertIn("chromium_sandbox=True", self.workflow)
        self.assertIn("page.expect_download()", self.workflow)
        self.assertIn(
            "seccomp=$GITHUB_WORKSPACE/services/usl-receipt-fetcher/seccomp_profile.json",
            self.workflow,
        )

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

    def test_release_attests_the_exact_verified_renderer_digest(self) -> None:
        self.assertIn(
            "name: Attest verified renderer as distribution integrator",
            self.workflow,
        )
        self.assertIn(
            "ghcr.io/unstaticlabs/usl-document-renderer@sha256:*",
            self.workflow,
        )
        self.assertIn(
            "subject-name: ${{ steps.renderer.outputs.subject_name }}",
            self.workflow,
        )
        self.assertIn(
            "subject-digest: ${{ steps.renderer.outputs.subject_digest }}",
            self.workflow,
        )
        renderer_attestation = self.workflow.split(
            "- name: Attest verified renderer as distribution integrator",
            1,
        )[1].split("- name: Install ORAS", 1)[0]
        self.assertIn("create-storage-record: true", renderer_attestation)
        self.assertNotIn("push-to-registry", renderer_attestation)

    def test_release_is_only_published_from_permanent_release_branches(self) -> None:
        qualification = (ROOT / ".github/workflows/qualification.yml").read_text(encoding="utf-8")
        self.assertIn("workflow_call:", self.workflow)
        self.assertNotIn("\n  push:", self.workflow)
        self.assertIn("uses: ./.github/workflows/product-image.yml", qualification)
        self.assertIn("if: github.event_name == 'push'", qualification)
        self.assertIn("usl-odoo-release", self.workflow)
        self.assertIn("actions/attest@", self.workflow)

    def test_release_requires_exact_successful_qualification_or_recovery_tag(self) -> None:
        self.assertIn('test "$SOURCE_COMMIT" = "$GITHUB_SHA"', self.workflow)
        self.assertIn("QUALIFICATION_EVIDENCE_SHA256", self.workflow)
        self.assertNotIn("for attempt in", self.workflow)
        self.assertNotIn("  admission:", self.workflow)
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
        self.assertIn("scripts/qualification-test-plan --all", qualification)
        self.assertIn("USL_USE_PREBUILT_TEST_IMAGE=1", qualification)
        self.assertIn("type=gha,scope=usl-odoo-test", qualification)
        self.assertGreaterEqual(qualification.count("scripts/sync-oca-addons"), 2)
        database_job = qualification.split("  database:\n", 1)[1].split("\n  result:\n", 1)[0]
        self.assertIn("github.event_name == 'push'", database_job)
        self.assertIn("github.event.pull_request.base.ref == '19-usl'", database_job)
        self.assertNotIn("merge_group", database_job)

    def test_production_promotion_reuses_only_exact_pr_tree_evidence(self) -> None:
        qualification = (ROOT / ".github/workflows/qualification.yml").read_text(
            encoding="utf-8",
        )
        self.assertIn(
            "HEAD_REPOSITORY: ${{ github.event.pull_request.head.repo.full_name }}",
            qualification,
        )
        self.assertIn("EXPECTED_REPOSITORY: ${{ github.repository }}", qualification)
        self.assertIn("name: USL production promotion", qualification)
        self.assertNotIn("production-promotion-evidence:", qualification)
        self.assertNotIn("gh attestation verify", qualification)
        self.assertIn("production-qualification-$PR_NUMBER-$PR_HEAD_SHA", qualification)
        self.assertIn("commits/$GITHUB_SHA/pulls?per_page=100", qualification)
        self.assertIn('merge_tree="$(git rev-parse "$GITHUB_SHA^{tree}")"', qualification)
        self.assertIn("scripts/qualification-evidence verify-merge-group", qualification)

    def test_compatibility_consolidates_all_host_side_checks(self) -> None:
        qualification = (ROOT / ".github/workflows/qualification.yml").read_text(encoding="utf-8")
        for obsolete in ("  changes:\n", "  static:\n", "  accounting:\n", "  security:\n", "  ui:\n"):
            self.assertNotIn(obsolete, qualification)
        compatibility = qualification.split("  compatibility:\n", 1)[1].split("\n  database:\n", 1)[0]
        self.assertIn("python3 -m unittest discover", compatibility)
        self.assertIn("make product-migration-source-boundary", compatibility)
        self.assertIn("make action-risk-inventory", compatibility)
        self.assertIn("scripts/check-github-governance", compatibility)
        self.assertIn("ElementTree", compatibility)

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

    def test_real_production_compose_exposes_only_the_release_upgrade_runner(self) -> None:
        environment = {
            **os.environ,
            "ODOO_UPGRADE_MODULES": "usl_home",
            "USL_RECEIPT_FETCHER_IMAGE": "ghcr.io/unstaticlabs/usl-receipt-fetcher@sha256:" + "a" * 64,
            "USL_RECEIPT_EGRESS_IMAGE": "ghcr.io/unstaticlabs/usl-receipt-egress@sha256:" + "b" * 64,
            "ODOO_HTTP_PORT": "18069",
            "ODOO_GEVENT_PORT": "18072",
            "PAPERLESS_HTTP_PORT": "18010",
            "ODOO_MCP_HTTP_PORT": "18000",
            "PAPERLESS_IMAGE": "ghcr.io/unstaticlabs/paperless@sha256:" + "a" * 64,
            "PAPERLESS_AI_LLM_EMBEDDING_ENDPOINT": "http://ollama:11434",
            "PAPERLESS_AI_LLM_EMBEDDING_MODEL": "bge-m3:latest",
            "PAPERLESS_AI_LLM_EMBEDDING_BATCH_SIZE": "32",
            "USL_OLLAMA_MANIFEST_SHA256": "7" * 64,
            "USL_OLLAMA_EMBEDDING_DIMENSION": "1024",
            "USL_EXTERNAL_OLLAMA_NETWORK": "ollama",
        }
        rendered = json.loads(subprocess.run(
            [
                "docker", "compose", "--env-file", ".env.example",
                "-f", "compose.yaml", "-f", "compose.production.yaml",
                "--profile", "init", "--profile", "release",
                "config", "--format", "json",
            ],
            cwd=ROOT,
            env=environment,
            capture_output=True,
            text=True,
            check=True,
        ).stdout)
        self.assertNotIn("init-db", rendered["services"])
        upgrade = rendered["services"]["odoo-upgrade"]
        self.assertEqual(upgrade["environment"]["ODOO_MAX_CRON_THREADS"], "0")
        self.assertEqual(upgrade["environment"]["USL_EINVOICE_LIVE_ENABLED"], "0")
        self.assertIn('--update="$${ODOO_UPGRADE_MODULES}"', upgrade["command"][0])
        self.assertFalse(any("custom-addons" in item["source"] for item in upgrade["volumes"]))

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
