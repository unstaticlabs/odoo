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

COMMIT = "a" * 40
DIGEST = "sha256:" + "b" * 64
IMAGE = "ghcr.io/unstaticlabs/usl-odoo"


def artifact() -> dict:
    return {
        "schema": "usl-distribution-release/v1",
        "source": {"repository": "unstaticlabs/odoo", "commit_sha": COMMIT},
        "image": {
            "name": IMAGE,
            "tag": f"sha-{COMMIT}",
            "digest": DIGEST,
            "digest_reference": f"{IMAGE}@{DIGEST}",
        },
        "build": {
            "workflow_run_id": 123,
            "workflow_run_attempt": 1,
            "workflow_url": "https://github.com/unstaticlabs/odoo/actions/runs/123",
        },
        "attestations": {
            "oci_sbom": "generated",
            "buildkit_provenance": "generated",
            "github_provenance": "generated",
        },
    }


class DistributionReleaseContractTest(unittest.TestCase):
    def test_accepts_exact_immutable_identity(self) -> None:
        self.assertEqual(distribution_release.validate(artifact(), commit=COMMIT, image=IMAGE)["schema"], "usl-distribution-release/v1")

    def test_rejects_mutable_or_mismatched_tag(self) -> None:
        value = copy.deepcopy(artifact())
        value["image"]["tag"] = "latest"
        with self.assertRaisesRegex(distribution_release.ReleaseArtifactError, "image.tag"):
            distribution_release.validate(value)

    def test_rejects_mismatched_digest_reference(self) -> None:
        value = copy.deepcopy(artifact())
        value["image"]["digest_reference"] = f"{IMAGE}@sha256:{'c' * 64}"
        with self.assertRaisesRegex(distribution_release.ReleaseArtifactError, "digest_reference"):
            distribution_release.validate(value)

    def test_rejects_unverified_attestation(self) -> None:
        value = copy.deepcopy(artifact())
        value["attestations"]["github_provenance"] = "not-run"
        with self.assertRaisesRegex(distribution_release.ReleaseArtifactError, "github_provenance"):
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

    def test_publish_identity_is_commit_tag_plus_digest(self) -> None:
        self.assertIn("IMAGE_TAG: sha-${{ github.sha }}", self.workflow)
        self.assertIn("digest_reference=$DISTRIBUTION_IMAGE@$digest", self.workflow)
        self.assertNotIn("docker/metadata-action", self.workflow)
        self.assertNotRegex(self.workflow, r"(?m)^\s*(?:tags:|-).*latest")
        self.assertNotIn("type=ref,event=branch", self.workflow)

    def test_only_19_usl_pushes_can_publish(self) -> None:
        self.assertIn("if: github.event_name == 'push' && github.ref == 'refs/heads/19-usl'", self.workflow)
        self.assertIn("push: false", self.workflow)
        self.assertIn("push: true", self.workflow)

    def test_release_metadata_is_uploaded_for_downstream_consumers(self) -> None:
        self.assertIn("distribution-release-$GITHUB_SHA", self.workflow)
        self.assertIn("distribution-release.json", self.workflow)
        self.assertIn("actions/upload-artifact@", self.workflow)


if __name__ == "__main__":
    unittest.main()
