from __future__ import annotations

import ast
import copy
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import distribution_release  # noqa: E402
from continuous_operations_contracts import (  # noqa: E402
    ARTIFACT_ROLES,
    with_checksum,
)
from release_identity import (  # noqa: E402
    PRODUCT_MODULES,
    expected_oca_pins,
    product_module_versions,
)

COMMIT = "a" * 40
DIGESTS = {role: f"sha256:{index:064x}" for index, role in enumerate(ARTIFACT_ROLES, 1)}


def artifact(*, commit: str = COMMIT, prior: dict | None = None) -> dict:
    versions = product_module_versions()
    build_roles = set(ARTIFACT_ROLES)
    reuse_roles: set[str] = set()
    if prior is not None:
        build_roles = {"odoo_distribution"}
        reuse_roles = set(ARTIFACT_ROLES) - build_roles
    artifacts = {}
    for role in ARTIFACT_ROLES:
        digest = DIGESTS[role]
        source_commit = commit
        origin = {"kind": "built_for_release", "release_commit_sha": commit}
        if role in reuse_roles:
            previous = prior["artifacts"][role]
            digest = previous["digest"]
            source_commit = previous["source_commit_sha"]
            origin = {
                "kind": "reused_from_release",
                "release_commit_sha": commit,
                "prior_release_contract_sha256": prior["contract_sha256"],
                "prior_release_source_commit_sha": prior["source"]["commit_sha"],
            }
        image = {
            "name": distribution_release.ARTIFACT_NAMES[role],
            "tag": f"sha-{source_commit}",
            "digest": digest,
            "digest_reference": f"{distribution_release.ARTIFACT_NAMES[role]}@{digest}",
            "source_commit_sha": source_commit,
            "attestations": {
                key: {"status": "generated", "subject_digest": digest}
                for key in (
                    "oci_sbom", "buildkit_provenance", "github_provenance",
                )
            },
        }
        image["identity_sha256"] = distribution_release._identity(image)
        image["origin"] = origin
        artifacts[role] = image
    return with_checksum({
        "schema": "usl-distribution-release/v3",
        "source": {"repository": "unstaticlabs/odoo", "commit_sha": commit},
        "artifacts": artifacts,
        "product": {
            "modules": [
                {"name": name, "version": versions[name]} for name in sorted(versions)
            ],
            "oca": {"bundle_sha256": "b" * 64, "repositories": expected_oca_pins()},
            "action_risk": {"policy_sha256": "c" * 64},
        },
        "component_sources": {
            "document_renderer": {
                "repository": "unstaticlabs/usl-document-renderer",
                "commit_sha": "d" * 40,
            },
        },
        "build": {
            "workflow_run_id": 123,
            "workflow_run_attempt": 1,
            "workflow_url": "https://github.com/unstaticlabs/odoo/actions/runs/123",
        },
        "artifact_plan": {
            "schema": "usl-artifact-build-plan/v1",
            "from_commit_sha": prior["source"]["commit_sha"] if prior else None,
            "to_commit_sha": commit,
            "mode": "selective" if prior else "build_all",
            "reason": "changed_runtime_inputs" if prior else "prior_release_unavailable",
            "changed_paths": ["custom-addons/usl_base/__manifest__.py"] if prior else [],
            "build_roles": sorted(build_roles),
            "reuse_roles": sorted(reuse_roles),
        },
        "upgrade_plan": {
            "schema": "usl-upgrade-plan/v1",
            "from_commit_sha": prior["source"]["commit_sha"] if prior else None,
            "to_commit_sha": commit,
            "mode": "dependency_closure" if prior else "full_fallback",
            "reason": "dependent_closure" if prior else "prior_release_unavailable",
            "changed_modules": [sorted(PRODUCT_MODULES)[0]] if prior else [],
            "upgrade_modules": [sorted(PRODUCT_MODULES)[0]] if prior else sorted(PRODUCT_MODULES),
            "foundation_paths": [],
        },
        "prior_release": (
            {
                "source_commit_sha": prior["source"]["commit_sha"],
                "contract_sha256": prior["contract_sha256"],
            }
            if prior else None
        ),
    })


class DistributionReleaseContractTest(unittest.TestCase):
    def test_accepts_complete_v3_identity(self) -> None:
        self.assertEqual(
            distribution_release.validate(artifact(), commit=COMMIT)["schema"],
            "usl-distribution-release/v3",
        )

    def test_rejects_missing_runtime_artifact(self) -> None:
        for role in ARTIFACT_ROLES:
            with self.subTest(role=role):
                value = copy.deepcopy(artifact())
                del value["artifacts"][role]
                value = with_checksum(value)
                with self.assertRaisesRegex(
                    distribution_release.ReleaseArtifactError, "artifacts keys differ",
                ):
                    distribution_release.validate(value)

    def test_rejects_every_artifact_tampering_vector(self) -> None:
        for role in ARTIFACT_ROLES:
            for field, replacement in (
                ("tag", "latest"),
                ("digest", "sha256:" + "e" * 64),
                ("source_commit_sha", "f" * 40),
            ):
                with self.subTest(role=role, field=field):
                    value = copy.deepcopy(artifact())
                    value["artifacts"][role][field] = replacement
                    value = with_checksum(value)
                    with self.assertRaises(distribution_release.ReleaseArtifactError):
                        distribution_release.validate(value)

    def test_rejects_floating_or_unproven_reuse(self) -> None:
        value = copy.deepcopy(artifact())
        value["artifacts"]["operations_tool"]["origin"] = {
            "kind": "reused",
            "release_commit_sha": "f" * 40,
        }
        value = with_checksum(value)
        with self.assertRaisesRegex(
            distribution_release.ReleaseArtifactError, "unsupported|disagrees",
        ):
            distribution_release.validate(value)

    def test_accepts_only_exact_artifacts_from_complete_prior_release(self) -> None:
        prior = artifact(commit="9" * 40)
        current = artifact(commit=COMMIT, prior=prior)
        self.assertEqual(
            distribution_release.validate(current, prior_release=prior)["prior_release"],
            {
                "source_commit_sha": "9" * 40,
                "contract_sha256": prior["contract_sha256"],
            },
        )

    def test_reused_artifact_requires_complete_prior_release(self) -> None:
        prior = artifact(commit="9" * 40)
        current = artifact(commit=COMMIT, prior=prior)
        with self.assertRaisesRegex(
            distribution_release.ReleaseArtifactError, "complete validated prior",
        ):
            distribution_release.validate(current)

    def test_rejects_reuse_tampering_even_with_recomputed_contract_checksum(self) -> None:
        prior = artifact(commit="9" * 40)
        for mutation in ("digest", "attestation", "identity", "prior_checksum"):
            with self.subTest(mutation=mutation):
                current = copy.deepcopy(artifact(commit=COMMIT, prior=prior))
                reused = current["artifacts"]["operations_tool"]
                if mutation == "digest":
                    reused["digest"] = "sha256:" + "f" * 64
                    reused["digest_reference"] = f"{reused['name']}@{reused['digest']}"
                    for attestation in reused["attestations"].values():
                        attestation["subject_digest"] = reused["digest"]
                    reused["identity_sha256"] = distribution_release._identity(reused)
                elif mutation == "attestation":
                    reused["attestations"]["oci_sbom"]["subject_digest"] = "sha256:" + "f" * 64
                elif mutation == "identity":
                    reused["identity_sha256"] = "f" * 64
                else:
                    current["prior_release"]["contract_sha256"] = "f" * 64
                current = with_checksum(current)
                with self.assertRaises(distribution_release.ReleaseArtifactError):
                    distribution_release.validate(current, prior_release=prior)

    def test_rejects_incomplete_product_and_risk_identity(self) -> None:
        value = copy.deepcopy(artifact())
        value["product"]["modules"].pop()
        value = with_checksum(value)
        with self.assertRaisesRegex(
            distribution_release.ReleaseArtifactError, "canonical product perimeter",
        ):
            distribution_release.validate(value)
        value = copy.deepcopy(artifact())
        value["product"]["action_risk"]["policy_sha256"] = "invalid"
        value = with_checksum(value)
        with self.assertRaises(distribution_release.ReleaseArtifactError):
            distribution_release.validate(value)

    def test_rejects_partial_full_fallback(self) -> None:
        value = copy.deepcopy(artifact())
        value["upgrade_plan"]["upgrade_modules"].pop()
        value = with_checksum(value)
        with self.assertRaisesRegex(
            distribution_release.ReleaseArtifactError, "entire canonical perimeter",
        ):
            distribution_release.validate(value)


class DistributionWorkflowPolicyTest(unittest.TestCase):
    def setUp(self) -> None:
        self.workflow = (ROOT / ".github/workflows/product-image.yml").read_text(
            encoding="utf-8",
        )

    def test_all_external_actions_are_pinned_to_full_shas(self) -> None:
        for workflow in (ROOT / ".github/workflows").glob("*.yml"):
            for line in workflow.read_text(encoding="utf-8").splitlines():
                if re.match(r"\s*uses:\s*", line):
                    self.assertRegex(
                        line, r"uses:\s+[^@\s]+@[0-9a-f]{40}(?:\s+#.*)?$", workflow,
                    )

    def test_workflow_is_post_merge_only_and_has_no_production_credentials(
        self,
    ) -> None:
        self.assertIn("push:\n    branches:\n      - 19-usl", self.workflow)
        self.assertNotIn("pull_request:", self.workflow)
        self.assertNotIn("merge_group:", self.workflow)
        self.assertNotIn("workflow_dispatch:", self.workflow)
        self.assertNotRegex(self.workflow.lower(), r"ssh|production_password|infisical")

    def test_workflow_publishes_every_repository_owned_runtime(self) -> None:
        for role, image in distribution_release.ARTIFACT_NAMES.items():
            self.assertIn(f"role: {role}", self.workflow)
            self.assertIn(f"image: {image}", self.workflow)
        self.assertGreaterEqual(self.workflow.count("provenance: mode=max"), 1)
        self.assertIn("actions/attest@", self.workflow)

    def test_workflow_uses_only_an_explicit_validated_prior_contract_for_reuse(self) -> None:
        self.assertIn("USL_DEPLOYED_RELEASE_RUN_ID", self.workflow)
        self.assertIn("scripts/artifact_build_plan.py", self.workflow)
        self.assertIn("--prior-release", self.workflow)
        self.assertIn("--from-commit", self.workflow)
        self.assertNotIn("imagetools inspect", self.workflow)
        self.assertNotIn("manifest unknown", self.workflow)

    def test_product_module_perimeters_are_identical(self) -> None:
        qa_environment = (ROOT / "scripts/qa-environment").read_text(encoding="utf-8")
        qa_match = re.search(r"product_modules='([^']+)'", qa_environment)
        self.assertIsNotNone(qa_match)
        qa_modules = set(qa_match.group(1).split(","))
        release_identity_path = ROOT / "scripts/odoo/release_identity.py"
        release_tree = ast.parse(release_identity_path.read_text(encoding="utf-8"))
        release_modules = None
        for statement in release_tree.body:
            if isinstance(statement, ast.Assign) and any(
                isinstance(target, ast.Name) and target.id == "PRODUCT_MODULES"
                for target in statement.targets
            ):
                release_modules = set(ast.literal_eval(statement.value))
                break
        self.assertEqual(PRODUCT_MODULES, qa_modules)
        self.assertEqual(PRODUCT_MODULES, release_modules)


if __name__ == "__main__":
    unittest.main()
