from __future__ import annotations

import json
import unittest
from pathlib import Path

from operations.runtime import load_target
from operations.stack import RESOURCE_FIELDS, _resource_overlay


ROOT = Path(__file__).resolve().parents[2]
TARGETS = ROOT / "operations/targets"


class RuntimeResourceTests(unittest.TestCase):
    def policy(self, target_name: str) -> dict:
        target = load_target(target_name, TARGETS)
        return json.loads(_resource_overlay(target))["services"]

    def test_every_long_running_service_has_a_complete_budget(self) -> None:
        for target_name in ("production", "staging"):
            target = load_target(target_name, TARGETS)
            services = self.policy(target_name)
            self.assertEqual(set(services), set(target.value["services"].values()))
            for limits in services.values():
                self.assertEqual(set(limits), RESOURCE_FIELDS)

    def test_staging_yields_to_production_under_contention(self) -> None:
        production = self.policy("production")
        staging = self.policy("staging")
        for service in production:
            self.assertLess(staging[service]["cpu_shares"], production[service]["cpu_shares"])
            self.assertLessEqual(staging[service]["cpus"], production[service]["cpus"])
            self.assertGreater(staging[service]["oom_score_adj"], production[service]["oom_score_adj"])
            self.assertEqual(staging[service]["mem_swappiness"], 0)
            self.assertEqual(staging[service]["mem_limit"], staging[service]["memswap_limit"])

    def test_local_runtime_does_not_inherit_vps_limits(self) -> None:
        target = load_target("local", TARGETS)
        self.assertIsNone(_resource_overlay(target))

    def test_runtime_schema_covers_every_target_field(self) -> None:
        schema = json.loads(
            (ROOT / "operations/contracts/usl-runtime-v1.schema.json").read_text(
                encoding="utf-8",
            ),
        )
        self.assertEqual(set(schema["required"]), set(schema["properties"]))
        for target_name in ("local", "production", "staging"):
            target = json.loads((TARGETS / f"{target_name}.json").read_text(encoding="utf-8"))
            self.assertEqual(set(target), set(schema["properties"]))


if __name__ == "__main__":
    unittest.main()
