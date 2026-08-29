from __future__ import annotations

import ast
import copy
import importlib.util
import json
import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("distribution_release", ROOT / "scripts/distribution_release.py")
assert SPEC and SPEC.loader
distribution_release = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(distribution_release)

COMMIT = "a" * 40
DIGEST = "sha256:" + "b" * 64
IMAGE = "ghcr.io/unstaticlabs/usl-odoo"
BACKUP_TOOL_IMAGE = "ghcr.io/unstaticlabs/usl-odoo-backup"
PRODUCT_MODULES = {
    "rebuild_account_migration",
    "usl_access_control",
    "usl_accounting",
    "usl_b2c",
    "usl_documents",
    "usl_documents_accounting",
    "usl_documents_b2c",
    "usl_expense_batch",
    "usl_home",
    "usl_locale",
    "usl_platform_billing",
    "usl_platform_billing_pocketid",
    "usl_pocketid",
    "usl_project",
    "usl_sign",
    "usl_tese_accounting",
    "usl_tese_payroll",
}


def artifact() -> dict:
    return {
        "schema": "usl-distribution-release/v2",
        "source": {"repository": "unstaticlabs/odoo", "commit_sha": COMMIT},
        "image": {
            "name": IMAGE,
            "tag": f"sha-{COMMIT}",
            "digest": DIGEST,
            "digest_reference": f"{IMAGE}@{DIGEST}",
        },
        "backup_tool": {
            "name": BACKUP_TOOL_IMAGE,
            "tag": f"sha-{COMMIT}",
            "digest": "sha256:" + "c" * 64,
            "digest_reference": f"{BACKUP_TOOL_IMAGE}@sha256:{'c' * 64}",
        },
        "build": {
            "workflow_run_id": 123,
            "workflow_run_attempt": 1,
            "workflow_url": "https://github.com/unstaticlabs/odoo/actions/runs/123",
        },
        "attestations": {
            name: {
                "oci_sbom": "generated",
                "buildkit_provenance": "generated",
                "github_provenance": "generated",
            }
            for name in ("distribution", "backup_tool")
        },
    }


class DistributionReleaseContractTest(unittest.TestCase):
    def test_accepts_exact_immutable_identity(self) -> None:
        self.assertEqual(
            distribution_release.validate(
                artifact(), commit=COMMIT, image=IMAGE, backup_tool_image=BACKUP_TOOL_IMAGE
            )["schema"],
            "usl-distribution-release/v2",
        )

    def test_rejects_mutable_or_mismatched_tag(self) -> None:
        value = copy.deepcopy(artifact())
        value["image"]["tag"] = "latest"
        with self.assertRaisesRegex(distribution_release.ReleaseArtifactError, "image.tag"):
            distribution_release.validate(value)

    def test_rejects_mismatched_digest_reference(self) -> None:
        value = copy.deepcopy(artifact())
        value["image"]["digest_reference"] = f"{IMAGE}@sha256:{'e' * 64}"
        with self.assertRaisesRegex(distribution_release.ReleaseArtifactError, "digest_reference"):
            distribution_release.validate(value)

    def test_rejects_unverified_attestation(self) -> None:
        value = copy.deepcopy(artifact())
        value["attestations"]["backup_tool"]["github_provenance"] = "not-run"
        with self.assertRaisesRegex(distribution_release.ReleaseArtifactError, "backup_tool.github_provenance"):
            distribution_release.validate(value)

    def test_rejects_mutable_backup_tool_tag(self) -> None:
        value = copy.deepcopy(artifact())
        value["backup_tool"]["tag"] = "latest"
        with self.assertRaisesRegex(distribution_release.ReleaseArtifactError, "backup_tool.tag"):
            distribution_release.validate(value)


class DistributionWorkflowPolicyTest(unittest.TestCase):
    def setUp(self) -> None:
        self.workflow = (ROOT / ".github/workflows/product-image.yml").read_text(encoding="utf-8")

    def test_all_external_actions_are_pinned_to_full_shas(self) -> None:
        for workflow in (ROOT / ".github/workflows").glob("*.yml"):
            for line in workflow.read_text(encoding="utf-8").splitlines():
                if re.match(r"\s*uses:\s*", line):
                    self.assertRegex(line, r"uses:\s+[^@\s]+@[0-9a-f]{40}(?:\s+#.*)?$", workflow)

    def test_workflow_runs_only_after_19_usl_push(self) -> None:
        self.assertIn("push:\n    branches:\n      - 19-usl", self.workflow)
        self.assertNotIn("pull_request:", self.workflow)
        self.assertNotIn("merge_group:", self.workflow)
        self.assertNotIn("workflow_dispatch:", self.workflow)

    def test_product_module_perimeters_are_identical(self) -> None:
        qa_environment = (ROOT / "scripts/qa-environment").read_text(encoding="utf-8")
        qa_match = re.search(r"product_modules='([^']+)'", qa_environment)
        self.assertIsNotNone(qa_match)
        qa_modules = set(qa_match.group(1).split(","))

        target_finalize = (ROOT / "scripts/target-finalize").read_text(encoding="utf-8")
        target_match = re.search(r"product_modules=\(\n(?P<body>.*?)\n\)", target_finalize, re.DOTALL)
        self.assertIsNotNone(target_match)
        target_modules = {
            line.strip()
            for line in target_match.group("body").splitlines()
            if line.strip()
        }

        release_identity_path = ROOT / "scripts/odoo/release_identity.py"
        release_tree = ast.parse(release_identity_path.read_text(encoding="utf-8"))
        release_modules = None
        for statement in release_tree.body:
            if (
                isinstance(statement, ast.Assign)
                and any(
                    isinstance(target, ast.Name) and target.id == "PRODUCT_MODULES"
                    for target in statement.targets
                )
            ):
                release_modules = set(ast.literal_eval(statement.value))
                break
        self.assertIsNotNone(release_modules)

        host_release_identity_path = ROOT / "scripts/release_identity.py"
        host_release_tree = ast.parse(
            host_release_identity_path.read_text(encoding="utf-8")
        )
        host_release_modules = None
        for statement in host_release_tree.body:
            if (
                isinstance(statement, ast.Assign)
                and any(
                    isinstance(target, ast.Name) and target.id == "PRODUCT_MODULES"
                    for target in statement.targets
                )
            ):
                host_release_modules = set(ast.literal_eval(statement.value))
                break
        self.assertIsNotNone(host_release_modules)

        database_boundary_path = ROOT / "scripts/odoo/product_database_boundary.py"
        database_boundary_tree = ast.parse(
            database_boundary_path.read_text(encoding="utf-8")
        )
        database_boundary_modules = None
        for statement in database_boundary_tree.body:
            if (
                isinstance(statement, ast.Assign)
                and any(
                    isinstance(target, ast.Name) and target.id == "PRODUCT_MODULES"
                    for target in statement.targets
                )
            ):
                database_boundary_modules = set(ast.literal_eval(statement.value))
                break
        self.assertIsNotNone(database_boundary_modules)

        preprod_release = (ROOT / "scripts/preprod-release").read_text(encoding="utf-8")
        preprod_match = re.search(r'product_modules="([^"]+)"', preprod_release)
        self.assertIsNotNone(preprod_match)
        preprod_modules = set(preprod_match.group(1).split(","))

        self.assertEqual(PRODUCT_MODULES, qa_modules)
        self.assertEqual(PRODUCT_MODULES, target_modules)
        self.assertEqual(PRODUCT_MODULES, release_modules)
        self.assertEqual(PRODUCT_MODULES, host_release_modules)
        self.assertEqual(PRODUCT_MODULES, database_boundary_modules)
        self.assertEqual(PRODUCT_MODULES, preprod_modules)

    def test_action_surface_matches_lean_product_module_scope(self) -> None:
        def assigned_set(path: Path, name: str) -> set[str]:
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for statement in tree.body:
                if (
                    isinstance(statement, ast.Assign)
                    and any(
                        isinstance(target, ast.Name) and target.id == name
                        for target in statement.targets
                    )
                ):
                    return set(ast.literal_eval(statement.value))
            self.fail(f"{path} does not assign {name}")

        excluded = assigned_set(
            ROOT / "scripts/odoo/enforce_product_module_scope.py",
            "EXCLUDED_AUTO_INSTALL_MODULES",
        )
        boundary_excluded = assigned_set(
            ROOT / "scripts/odoo/product_database_boundary.py",
            "EXCLUDED_AUTO_INSTALL_MODULES",
        )
        surface = json.loads(
            (
                ROOT
                / "custom-addons/usl_access_control/policy/action_surface.json"
            ).read_text(encoding="utf-8")
        )
        surface_modules = {module["name"] for module in surface["modules"]}

        self.assertEqual(excluded, boundary_excluded)
        self.assertTrue(excluded)
        self.assertFalse(excluded & surface_modules)

    def test_publish_identity_is_commit_tag_plus_digest(self) -> None:
        self.assertIn("IMAGE_TAG: sha-${{ github.sha }}", self.workflow)
        self.assertIn("digest_reference=$DISTRIBUTION_IMAGE@$digest", self.workflow)
        self.assertNotIn("docker/metadata-action", self.workflow)
        self.assertNotRegex(self.workflow, r"(?m)^\s*(?:tags:|-).*latest")
        self.assertNotIn("type=ref,event=branch", self.workflow)

    def test_backup_tool_uses_the_same_immutable_identity_boundary(self) -> None:
        self.assertIn("BACKUP_TOOL_IMAGE: ghcr.io/unstaticlabs/usl-odoo-backup", self.workflow)
        self.assertIn("file: docker/backup.Dockerfile", self.workflow)
        self.assertIn("digest_reference=$BACKUP_TOOL_IMAGE@$digest", self.workflow)
        self.assertIn("backup_tool_digest_reference:", self.workflow)
        self.assertIn('--backup-tool-digest "$BACKUP_TOOL_DIGEST"', self.workflow)

    def test_both_release_images_receive_sbom_and_provenance(self) -> None:
        publish = self.workflow
        self.assertGreaterEqual(publish.count("provenance: mode=max"), 2)
        self.assertGreaterEqual(publish.count("sbom: true"), 2)
        self.assertGreaterEqual(publish.count("uses: actions/attest@"), 2)
        publish_permissions = publish.split("    outputs:\n", 1)[0]
        self.assertIn("artifact-metadata: write", publish_permissions)

    def test_only_19_usl_pushes_can_publish(self) -> None:
        self.assertIn("push:\n    branches:\n      - 19-usl", self.workflow)
        self.assertNotIn("push: false", self.workflow)
        self.assertIn("push: true", self.workflow)

    def test_release_metadata_is_uploaded_for_downstream_consumers(self) -> None:
        self.assertIn("distribution-release-$GITHUB_SHA", self.workflow)
        self.assertIn("distribution-release.json", self.workflow)
        self.assertIn("actions/upload-artifact@", self.workflow)

    def test_document_service_versions_match_deployment_examples(self) -> None:
        compose = (ROOT / "compose.yaml").read_text(encoding="utf-8")
        examples = (
            ROOT / "deploy/documents/qa.env",
            ROOT / "deploy/documents/preprod.env.example",
            ROOT / "deploy/preprod.env.example",
            ROOT / "deploy/production.external-pocket-id.env.example",
        )
        values_by_key = {}
        for key in ("PAPERLESS_TIKA_IMAGE", "OLLAMA_IMAGE"):
            values = []
            for path in examples:
                value = next(
                    line.split("=", 1)[1]
                    for line in path.read_text(encoding="utf-8").splitlines()
                    if line.startswith(f"{key}=")
                )
                values.append(value)
            self.assertEqual([values[0]] * len(values), values, key)
            self.assertIn(f"${{{key}:-{values[0]}}}", compose)
            values_by_key[key] = values[0]
        development_tika = next(
            line.split("=", 1)[1]
            for line in (ROOT / ".env.example").read_text(encoding="utf-8").splitlines()
            if line.startswith("PAPERLESS_TIKA_IMAGE=")
        )
        self.assertEqual(development_tika, values_by_key["PAPERLESS_TIKA_IMAGE"])


if __name__ == "__main__":
    unittest.main()
