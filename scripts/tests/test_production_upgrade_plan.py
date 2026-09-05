"""Production plans bind its own candidate without depending on staging."""
import copy
import unittest
from types import SimpleNamespace

from operations.module_release import ModuleReleaseError
from operations.stack import (
    _staging_release_definitions_sha256,
    _validated_release_upgrade_plan,
)
from scripts.tests.test_plan_evidence import plan


class ProductionUpgradePlanTests(unittest.TestCase):
    def test_production_accepts_its_own_plan_without_staging_keys(self):
        target = SimpleNamespace(value={"environment": "production"})
        value = plan()
        self.assertEqual(
            _validated_release_upgrade_plan(target, value, {"identity": value["candidate_release"]}),
            value,
        )

    def test_wrong_candidate_and_modified_plan_are_rejected(self):
        target = SimpleNamespace(value={"environment": "production"})
        value = plan()
        with self.assertRaisesRegex(ModuleReleaseError, "another candidate"):
            _validated_release_upgrade_plan(target, value, {"identity": "d" * 64})
        changed = copy.deepcopy(value)
        changed["upgrade_modules"] = []
        with self.assertRaisesRegex(ModuleReleaseError, "digest differs"):
            _validated_release_upgrade_plan(target, changed, {"identity": value["candidate_release"]})


class StagingReleaseDefinitionsTests(unittest.TestCase):
    def test_production_plan_without_staging_evidence_compares_nothing(self):
        self.assertIsNone(_staging_release_definitions_sha256(None))
        self.assertIsNone(_staging_release_definitions_sha256(plan()))

    def test_staging_evidence_and_promotion_envelope_expose_the_digest(self):
        digest = "a" * 64
        evidence = {"schema": "usl-staging-upgrade-plan-evidence/v2",
                    "staging": {"release_definitions_sha256": digest}}
        self.assertEqual(_staging_release_definitions_sha256(evidence), digest)
        envelope = {"schema": "usl-production-upgrade-plan-promotion/v1",
                    "staging_evidence": evidence}
        self.assertEqual(_staging_release_definitions_sha256(envelope), digest)

    def test_staging_evidence_without_digest_is_rejected(self):
        with self.assertRaisesRegex(RuntimeError, "release definitions digest"):
            _staging_release_definitions_sha256({"staging": {}})
