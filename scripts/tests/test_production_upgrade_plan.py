"""Production plans bind its own candidate without depending on staging."""
import copy
import unittest
from types import SimpleNamespace

from operations.module_release import ModuleReleaseError
from operations.stack import _validated_release_upgrade_plan
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
