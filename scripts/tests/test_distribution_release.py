from __future__ import annotations

import copy
import importlib.util
import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("distribution_release", ROOT / "scripts/distribution_release.py")
assert SPEC and SPEC.loader
distribution_release = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(distribution_release)

RELEASE_IDENTITY_SPEC = importlib.util.spec_from_file_location(
    "release_identity", ROOT / "scripts/release_identity.py"
)
assert RELEASE_IDENTITY_SPEC and RELEASE_IDENTITY_SPEC.loader
release_identity = importlib.util.module_from_spec(RELEASE_IDENTITY_SPEC)
RELEASE_IDENTITY_SPEC.loader.exec_module(release_identity)

COMMIT = "a" * 40
DIGEST = "sha256:" + "b" * 64
IMAGE = "ghcr.io/unstaticlabs/usl-odoo"
BACKUP_TOOL_IMAGE = "ghcr.io/unstaticlabs/usl-odoo-backup"


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

    def test_pr_qualification_has_no_write_permission_or_secret(self) -> None:
        qualify = self.workflow.split("\n  publish:\n", 1)[0]
        self.assertIn("python3 -m unittest scripts.tests.test_distribution_release", qualify)
        self.assertNotIn("packages: write", qualify)
        self.assertNotIn("id-token: write", qualify)
        self.assertNotIn("attestations: write", qualify)
        self.assertNotIn("secrets.", qualify)

    def test_pr_qualification_installs_the_canonical_product_registry(self) -> None:
        match = re.search(r"product_modules='([^']+)'", self.workflow)
        self.assertIsNotNone(match)
        self.assertEqual(
            release_identity.PRODUCT_MODULES,
            set(match.group(1).split(",")),
        )

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

    def test_pr_qualification_builds_but_never_pushes_backup_tool(self) -> None:
        qualify = self.workflow.split("\n  publish:\n", 1)[0]
        self.assertIn("Build backup tool without publishing", qualify)
        self.assertIn("push: false", qualify)
        self.assertNotIn("docker/login-action", qualify)
        self.assertNotIn("RESTIC_PASSWORD", qualify)

    def test_both_release_images_receive_sbom_and_provenance(self) -> None:
        publish = self.workflow.split("\n  publish:\n", 1)[1]
        self.assertGreaterEqual(publish.count("provenance: mode=max"), 2)
        self.assertGreaterEqual(publish.count("sbom: true"), 2)
        self.assertGreaterEqual(publish.count("uses: actions/attest@"), 2)

    def test_only_19_usl_pushes_can_publish(self) -> None:
        self.assertIn("if: github.event_name == 'push' && github.ref == 'refs/heads/19-usl'", self.workflow)
        self.assertIn("push: false", self.workflow)
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


if __name__ == "__main__":
    unittest.main()
