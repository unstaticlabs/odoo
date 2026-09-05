from __future__ import annotations

import copy
import hashlib
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from operations.control_manifest import (
    ODOO_PRESERVATION_KEYS,
    ODOO_QUEUE_KEYS,
    ODOO_RELEASE_KEYS,
    PAPERLESS_PRESERVATION_KEYS,
)
from operations.plan_evidence import PlanEvidenceError, promote, sign, verify, verify_promotion
from scripts.tests.test_release_manifest import manifest


def plan() -> dict:
    body = {
        "schema": "usl-module-upgrade-plan/v1",
        "active_release": "a" * 64,
        "candidate_release": "b" * 64,
        "candidate_module_inventory_sha256": "c" * 64,
        "installed_modules": ["usl_a"],
        "upgrade_modules": ["usl_a"],
        "reasons": {"usl_a": ["source-changed"]},
    }
    body["sha256"] = hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return body


def smoke() -> dict:
    return {
        "status": "passed",
        "release_definitions_sha256": "9" * 64,
        "controls": {
            "odoo": {
                key: 0
                for key in ODOO_PRESERVATION_KEYS | ODOO_RELEASE_KEYS | ODOO_QUEUE_KEYS
            },
            "paperless": {key: 0 for key in PAPERLESS_PRESERVATION_KEYS},
        },
    }


class PlanEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        root = Path(self.directory.name)
        self.private = root / "private.pem"
        self.public = root / "public.pem"
        subprocess.run(["openssl", "genpkey", "-algorithm", "ED25519", "-out", self.private], check=True)
        subprocess.run(["openssl", "pkey", "-in", self.private, "-pubout", "-out", self.public], check=True)

    def evidence(self):
        return sign(
            plan(),
            self.private,
            snapshot="d" * 64,
            generation="g-qualified",
            health={"status": "passed"},
            smoke=smoke(),
        )

    def test_attestation_requires_semantic_release_definitions(self):
        value = smoke()
        value.pop("release_definitions_sha256")
        with self.assertRaisesRegex(PlanEvidenceError, "release definitions"):
            sign(plan(), self.private, snapshot="d" * 64,
                 generation="g-qualified", health={"status": "passed"}, smoke=value)

    def test_round_trip_returns_exact_plan(self):
        self.assertEqual(verify(self.evidence(), self.public), plan())

    def test_modified_plan_is_rejected(self):
        value = self.evidence()
        value["plan"]["candidate_release"] = "e" * 64
        with self.assertRaises((PlanEvidenceError, ValueError)):
            verify(value, self.public)

    def test_wrong_key_is_rejected(self):
        other_private = Path(self.directory.name) / "other-private.pem"
        path = Path(self.directory.name) / "other.pem"
        subprocess.run(["openssl", "genpkey", "-algorithm", "ED25519", "-out", other_private], check=True)
        subprocess.run(["openssl", "pkey", "-in", other_private, "-pubout", "-out", path], check=True)
        with self.assertRaisesRegex(PlanEvidenceError, "identity"):
            verify(self.evidence(), path)

    def test_unsigned_plan_is_rejected(self):
        with self.assertRaisesRegex(PlanEvidenceError, "fields"):
            verify(copy.deepcopy(plan()), self.public)

    def test_attestation_requires_passing_gates(self):
        with self.assertRaisesRegex(PlanEvidenceError, "must pass"):
            sign(
                plan(),
                self.private,
                snapshot="d" * 64,
                generation="g-qualified",
                health={"status": "failed"},
                smoke=smoke(),
            )

    def test_attestation_rejects_unsafe_private_key_permissions(self):
        self.private.chmod(0o644)
        with self.assertRaisesRegex(PlanEvidenceError, "permissions"):
            self.evidence()

    def test_attestation_rejects_invalid_snapshot_identity(self):
        with self.assertRaisesRegex(PlanEvidenceError, "snapshot"):
            sign(
                plan(),
                self.private,
                snapshot="not-a-snapshot",
                generation="g-qualified",
                health={"status": "passed"},
                smoke=smoke(),
            )

    def test_attestation_binds_release_owned_controls(self):
        value = self.evidence()
        value["staging"]["release_definitions_sha256"] = "e" * 64
        with self.assertRaisesRegex(PlanEvidenceError, "signature"):
            verify(value, self.public)

    def test_attestation_rejects_incomplete_controls(self):
        invalid = smoke()
        invalid["controls"]["odoo"].pop("acl_fingerprint")
        with self.assertRaisesRegex(PlanEvidenceError, "controls"):
            sign(
                plan(),
                self.private,
                snapshot="d" * 64,
                generation="g-qualified",
                health={"status": "passed"},
                smoke=invalid,
            )

    @staticmethod
    def production_release(staging):
        value = copy.deepcopy(staging)
        value["source"]["ref"] = "refs/heads/19-usl"
        value["source"]["commit"] = "e" * 40
        value["identity"] = hashlib.sha256(
            json.dumps(
                {key: item for key, item in value.items() if key != "identity"},
                sort_keys=True,
                separators=(",", ":"),
            ).encode(),
        ).hexdigest()
        return value

    def test_promotes_equivalent_production_release_without_replacing_staging_signature(self):
        staging = manifest()
        staging["identity"] = plan()["candidate_release"]
        # Rebind the synthetic plan to the valid manifest identity.
        staging["identity"] = hashlib.sha256(
            json.dumps(
                {key: item for key, item in staging.items() if key != "identity"},
                sort_keys=True,
                separators=(",", ":"),
            ).encode(),
        ).hexdigest()
        unsigned = plan()
        unsigned["candidate_release"] = staging["identity"]
        unsigned.pop("sha256")
        unsigned["sha256"] = hashlib.sha256(
            json.dumps(unsigned, sort_keys=True, separators=(",", ":")).encode(),
        ).hexdigest()
        staging_evidence = sign(
            unsigned,
            self.private,
            snapshot="d" * 64,
            generation="g-qualified",
            health={"status": "passed"},
            smoke=smoke(),
        )
        production = self.production_release(staging)
        promoted = promote(
            staging_evidence, staging, production, self.private, self.public,
        )
        self.assertEqual(promoted["staging_evidence"], staging_evidence)
        result = verify_promotion(promoted, self.public, production)
        self.assertEqual(result["candidate_release"], production["identity"])
        self.assertEqual(result["upgrade_modules"], unsigned["upgrade_modules"])

    def test_operator_recovery_preserves_exact_signed_staging_plan(self):
        staging = manifest()
        staging["source"]["ref"] = "refs/tags/recovery-local"
        staging["build"] = {"operator_run_id": "local", "evidence_sha256": "a" * 64}
        staging.pop("identity")
        staging["identity"] = hashlib.sha256(json.dumps(staging, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        unsigned = plan()
        unsigned["candidate_release"] = staging["identity"]
        unsigned.pop("sha256")
        unsigned["sha256"] = hashlib.sha256(json.dumps(unsigned, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        evidence = sign(unsigned, self.private, snapshot="d" * 64, generation="g-qualified", health={"status": "passed"}, smoke=smoke())
        promoted = promote(evidence, staging, copy.deepcopy(staging), self.private, self.public)
        self.assertEqual(verify_promotion(promoted, self.public, staging), unsigned)
        self.assertEqual(promoted["staging_evidence"], evidence)
        different = copy.deepcopy(staging)
        different["build"]["operator_run_id"] = "other"
        different.pop("identity")
        different["identity"] = hashlib.sha256(json.dumps(different, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        with self.assertRaisesRegex(PlanEvidenceError, "staging branch"):
            promote(evidence, staging, different, self.private, self.public)

    def test_promotion_allows_branch_specific_release_notes(self):
        staging = manifest()
        unsigned = plan()
        unsigned["candidate_release"] = staging["identity"]
        unsigned.pop("sha256")
        unsigned["sha256"] = hashlib.sha256(
            json.dumps(unsigned, sort_keys=True, separators=(",", ":")).encode(),
        ).hexdigest()
        evidence = sign(
            unsigned, self.private, snapshot="d" * 64,
            generation="g-qualified", health={"status": "passed"}, smoke=smoke(),
        )
        production = self.production_release(staging)
        production["release_notes"] = {
            "schema": "usl-release-notes/v1",
            "title": "Production promotion",
            "summary": "Promote the exact tree qualified by staging.",
            "changes": ["Promote the exact tree qualified by staging."],
            "action_required": None,
        }
        production["identity"] = hashlib.sha256(
            json.dumps(
                {key: item for key, item in production.items() if key != "identity"},
                sort_keys=True, separators=(",", ":"),
            ).encode(),
        ).hexdigest()
        promoted = promote(evidence, staging, production, self.private, self.public)
        self.assertEqual(
            verify_promotion(promoted, self.public, production)["candidate_release"],
            production["identity"],
        )

    def test_promotion_rejects_different_source_repository(self):
        staging = manifest()
        unsigned = plan()
        unsigned["candidate_release"] = staging["identity"]
        unsigned.pop("sha256")
        unsigned["sha256"] = hashlib.sha256(
            json.dumps(unsigned, sort_keys=True, separators=(",", ":")).encode(),
        ).hexdigest()
        evidence = sign(
            unsigned, self.private, snapshot="d" * 64,
            generation="g-qualified", health={"status": "passed"}, smoke=smoke(),
        )
        production = self.production_release(staging)
        production["source"]["repository"] = "fork-owner/odoo"
        production["build"]["workflow_url"] = (
            "https://github.com/fork-owner/odoo/actions/runs/"
            + str(production["build"]["workflow_run_id"])
        )
        production["identity"] = hashlib.sha256(
            json.dumps(
                {key: item for key, item in production.items() if key != "identity"},
                sort_keys=True, separators=(",", ":"),
            ).encode(),
        ).hexdigest()
        with self.assertRaisesRegex(PlanEvidenceError, "different repositories"):
            promote(evidence, staging, production, self.private, self.public)

    def test_promotion_rejects_changed_component(self):
        staging = manifest()
        unsigned = plan()
        unsigned["candidate_release"] = staging["identity"]
        unsigned.pop("sha256")
        unsigned["sha256"] = hashlib.sha256(
            json.dumps(unsigned, sort_keys=True, separators=(",", ":")).encode(),
        ).hexdigest()
        evidence = sign(
            unsigned, self.private, snapshot="d" * 64,
            generation="g-qualified", health={"status": "passed"}, smoke=smoke(),
        )
        production = self.production_release(staging)
        production["components"]["distribution"]["digest"] = "sha256:" + "f" * 64
        production["components"]["distribution"]["digest_reference"] = (
            production["components"]["distribution"]["image"] + "@" +
            production["components"]["distribution"]["digest"]
        )
        production["components"]["distribution"]["attestations"]["sbom"]["subject_digest"] = production["components"]["distribution"]["digest"]
        production["components"]["distribution"]["attestations"]["provenance"]["subject_digest"] = production["components"]["distribution"]["digest"]
        production["identity"] = hashlib.sha256(
            json.dumps(
                {key: item for key, item in production.items() if key != "identity"},
                sort_keys=True, separators=(",", ":"),
            ).encode(),
        ).hexdigest()
        with self.assertRaisesRegex(PlanEvidenceError, "deployable inputs"):
            promote(evidence, staging, production, self.private, self.public)

    def test_promotion_rejects_wrong_branches(self):
        staging = manifest()
        staging["source"]["ref"] = "refs/heads/19-usl"
        staging["identity"] = hashlib.sha256(
            json.dumps(
                {key: item for key, item in staging.items() if key != "identity"},
                sort_keys=True, separators=(",", ":"),
            ).encode(),
        ).hexdigest()
        unsigned = plan()
        unsigned["candidate_release"] = staging["identity"]
        unsigned.pop("sha256")
        unsigned["sha256"] = hashlib.sha256(
            json.dumps(unsigned, sort_keys=True, separators=(",", ":")).encode(),
        ).hexdigest()
        evidence = sign(
            unsigned, self.private, snapshot="d" * 64,
            generation="g-qualified", health={"status": "passed"}, smoke=smoke(),
        )
        with self.assertRaisesRegex(PlanEvidenceError, "staging branch"):
            promote(
                evidence, staging, self.production_release(staging),
                self.private, self.public,
            )


if __name__ == "__main__":
    unittest.main()
