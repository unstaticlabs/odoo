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
PAPERLESS_IMAGE = "ghcr.io/unstaticlabs/usl-paperless-ngx"
DOCUMENT_RENDERER_IMAGE = "ghcr.io/unstaticlabs/usl-document-renderer"
SIGN_DSS_IMAGE = "ghcr.io/unstaticlabs/usl-sign-dss"
RENDERER_COMMIT = "1" * 40
MCP_COMMIT = "d" * 40
MCP_IMAGE = f"ghcr.io/unstaticlabs/odoo-mcp@sha256:{'e' * 64}"
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
        "schema": "usl-distribution-release/v4",
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
        "paperless": {
            "name": PAPERLESS_IMAGE,
            "tag": f"sha-{COMMIT}",
            "digest": "sha256:" + "2" * 64,
            "digest_reference": f"{PAPERLESS_IMAGE}@sha256:{'2' * 64}",
        },
        "document_renderer": {
            "repository": "https://github.com/unstaticlabs/unstatic_latex_templates.git",
            "commit": RENDERER_COMMIT,
            "image": {
                "name": DOCUMENT_RENDERER_IMAGE,
                "tag": f"sha-{RENDERER_COMMIT}",
                "digest": "sha256:" + "3" * 64,
                "digest_reference": (
                    f"{DOCUMENT_RENDERER_IMAGE}@sha256:{'3' * 64}"
                ),
            },
        },
        "sign_dss": {
            "name": SIGN_DSS_IMAGE,
            "tag": f"sha-{COMMIT}",
            "digest": "sha256:" + "4" * 64,
            "digest_reference": f"{SIGN_DSS_IMAGE}@sha256:{'4' * 64}",
        },
        "mcp": {
            "repository": "https://github.com/unstaticlabs/odoo-mcp.git",
            "ref": "codex/odoo-mcp-vps-refactor",
            "commit": MCP_COMMIT,
            "image_digest": MCP_IMAGE,
            "compatibility_sha256": "f" * 64,
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
            for name in (
                "distribution",
                "backup_tool",
                "paperless",
                "document_renderer",
                "sign_dss",
            )
        },
    }


class DistributionReleaseContractTest(unittest.TestCase):
    def test_accepts_exact_immutable_identity(self) -> None:
        self.assertEqual(
            distribution_release.validate(
                artifact(), commit=COMMIT, image=IMAGE, backup_tool_image=BACKUP_TOOL_IMAGE
            )["schema"],
            "usl-distribution-release/v4",
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
        with self.assertRaisesRegex(distribution_release.ReleaseArtifactError, "unsupported status"):
            distribution_release.validate(value)

    def test_accepts_explicit_external_renderer_oci_status(self) -> None:
        value = artifact()
        value["attestations"]["document_renderer"] = {
            "oci_sbom": "external-qualified-oci",
            "buildkit_provenance": "external-qualified-oci",
            "github_provenance": "external-qualified-oci",
        }
        self.assertEqual(
            distribution_release.validate(value)["document_renderer"]["commit"],
            RENDERER_COMMIT,
        )

    def test_rejects_mutable_backup_tool_tag(self) -> None:
        value = copy.deepcopy(artifact())
        value["backup_tool"]["tag"] = "latest"
        with self.assertRaisesRegex(distribution_release.ReleaseArtifactError, "backup_tool.tag"):
            distribution_release.validate(value)

    def test_rejects_a_mutable_or_mismatched_mcp_release(self) -> None:
        value = copy.deepcopy(artifact())
        value["mcp"]["image_digest"] = "ghcr.io/unstaticlabs/odoo-mcp:latest"
        with self.assertRaisesRegex(distribution_release.ReleaseArtifactError, "mcp.image_digest"):
            distribution_release.validate(value)

        value = artifact()
        with self.assertRaisesRegex(distribution_release.ReleaseArtifactError, "MCP commit"):
            distribution_release.validate(value, mcp_commit="0" * 40)


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
        self.assertIn("ref: main", self.workflow)
        self.assertIn("scripts/odoo-mcp verify", self.workflow)

    def test_product_module_perimeters_are_identical(self) -> None:
        target_finalize = (ROOT / "migration/internal/finalize").read_text(encoding="utf-8")
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

        host_release_identity_path = ROOT / "migration/release_identity.py"
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

        self.assertEqual(PRODUCT_MODULES, target_modules)
        self.assertEqual(PRODUCT_MODULES, release_modules)
        self.assertEqual(PRODUCT_MODULES, host_release_modules)
        self.assertEqual(PRODUCT_MODULES, database_boundary_modules)

    def test_target_finalization_reconciles_native_multi_company_ui(self) -> None:
        target_finalize = (ROOT / "migration/internal/finalize").read_text(encoding="utf-8")
        synchronizer = (
            ROOT / "scripts/odoo/synchronize_multi_company_groups.py"
        ).read_text(encoding="utf-8")

        self.assertIn("synchronize_multi_company_groups.py", target_finalize)
        self.assertIn('env.ref("base.group_multi_company")', synchronizer)
        self.assertIn("len(user.company_ids) > 1", synchronizer)
        self.assertIn("Command.link(group.id)", synchronizer)
        self.assertIn("env.cr.commit()", synchronizer)

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
        inventory_excluded = assigned_set(
            ROOT / "scripts/action_risk_inventory.py",
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
        self.assertEqual(excluded, inventory_excluded)
        self.assertTrue(excluded)
        self.assertNotIn("contacts", excluded)
        self.assertIn("contacts", surface_modules)
        self.assertFalse(excluded & surface_modules)

    def test_target_finalization_rechecks_scope_after_module_installation(self) -> None:
        target_finalize = (ROOT / "migration/internal/finalize").read_text(
            encoding="utf-8"
        )
        scope_gate = "< scripts/odoo/enforce_product_module_scope.py"

        self.assertEqual(target_finalize.count(scope_gate), 2)
        self.assertGreater(
            target_finalize.rindex(scope_gate),
            target_finalize.index('scripts/pocket-id-dev configure-odoo "$product_modules_csv"'),
        )

    def test_documents_evidence_seals_after_migration_bindings_are_removed(self) -> None:
        restore = (ROOT / "migration/internal/documents-restore").read_text(
            encoding="utf-8"
        )
        seal_case = restore.split("    seal-evidence)", 1)[1].split("        ;;", 1)[0]

        self.assertIn("validate_source", seal_case)
        self.assertIn("seal_archive_evidence", seal_case)
        self.assertNotIn("require_source_bindings", seal_case)

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

    def test_release_cohort_publishes_every_custom_runtime_image(self) -> None:
        self.assertIn("submodules: false", self.workflow)
        self.assertIn("usl-external-oci-image/v1", self.workflow)
        self.assertIn(
            "PAPERLESS_IMAGE: ghcr.io/unstaticlabs/usl-paperless-ngx",
            self.workflow,
        )
        self.assertIn(
            "DOCUMENT_RENDERER_IMAGE: ghcr.io/unstaticlabs/usl-document-renderer",
            self.workflow,
        )
        self.assertIn(
            "SIGN_DSS_IMAGE: ghcr.io/unstaticlabs/usl-sign-dss",
            self.workflow,
        )
        self.assertIn("file: deploy/documents/paperless-ngx/Dockerfile", self.workflow)
        self.assertIn("context: services/usl-document-renderer", self.workflow)
        self.assertIn("file: services/usl-sign-dss/Dockerfile", self.workflow)
        self.assertIn('--paperless-digest "$PAPERLESS_DIGEST"', self.workflow)
        self.assertIn(
            '--document-renderer-digest "$DOCUMENT_RENDERER_DIGEST"',
            self.workflow,
        )
        self.assertIn('--sign-dss-digest "$SIGN_DSS_DIGEST"', self.workflow)
        self.assertIn("scripts/odoo-mcp verify-image", self.workflow)

    def test_release_metadata_binds_the_external_mcp_cohort(self) -> None:
        source = (ROOT / "scripts/distribution_release.py").read_text(encoding="utf-8")
        self.assertIn('"mcp": {', source)
        self.assertIn('"compatibility_sha256": mcp_release["compatibility_sha256"]', source)
        self.assertIn('"image_digest": mcp_release["image"]', source)

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

    def test_workflow_uses_the_current_release_identity_module(self) -> None:
        self.assertIn("from migration.release_identity import oca_bundle_sha256", self.workflow)
        self.assertIn(
            "from migration.release_identity import action_risk_policy_sha256",
            self.workflow,
        )
        self.assertNotIn("from scripts.release_identity import", self.workflow)

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
