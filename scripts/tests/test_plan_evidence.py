from __future__ import annotations

import copy
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
from operations.plan_evidence import PlanEvidenceError, sign, verify


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
    import hashlib
    body["sha256"] = hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return body


def smoke() -> dict:
    return {
        "status": "passed",
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
        value["staging"]["release_controls_sha256"] = "e" * 64
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


if __name__ == "__main__":
    unittest.main()
