"""The anchor a target looks for must be a service that target actually runs.

`operations/targets/staging.json` named its Odoo service `odoo`, but the deployed
staging stack calls it `odoo-staging`; production calls it `odoo`. Every
observation of staging therefore failed with
`expected one usl-odoo-staging-main/odoo container, found 0`, for months, while
staging was deployed and serving. It read as a broken deployment and was written
into the merge-train protocol as "staging's running commit cannot be proved".

Nothing caught it because the two names were consistently wrong together, and the
deployed compose files live in a different repository. These tests pin what can be
checked from here: the anchor is a service the target declares, and the two
environments stay structurally identical so a change to one is visible as a
divergence from the other.
"""
from __future__ import annotations

import unittest
from pathlib import Path

from operations.runtime import load_target

ROOT = Path(__file__).resolve().parents[2]
TARGETS = ROOT / "operations/targets"
DEPLOYED = ("production", "staging")


class TargetServiceNameTests(unittest.TestCase):
    def test_the_anchor_is_a_service_the_target_declares(self) -> None:
        for name in DEPLOYED:
            target = load_target(name, TARGETS)
            anchor = target.value["compose"]["anchor_service"]
            declared = set(target.value["services"].values())
            self.assertIn(
                anchor, declared,
                f"{name}: anchor_service {anchor!r} is not among the declared services",
            )

    def test_the_anchor_is_the_odoo_service(self) -> None:
        """A half-applied rename is the likely way this drifts next."""
        for name in DEPLOYED:
            target = load_target(name, TARGETS)
            self.assertEqual(
                target.value["compose"]["anchor_service"],
                target.value["services"]["odoo"],
                f"{name}: anchor_service and services.odoo name different services",
            )

    def test_the_odoo_service_is_named_for_its_environment(self) -> None:
        """Pins the values that were wrong, so a change has to be deliberate."""
        expected = {"production": "odoo", "staging": "odoo-staging"}
        for name, service in expected.items():
            target = load_target(name, TARGETS)
            self.assertEqual(target.value["services"]["odoo"], service)

    def test_both_environments_declare_the_same_service_roles(self) -> None:
        production = load_target("production", TARGETS).value["services"]
        staging = load_target("staging", TARGETS).value["services"]
        self.assertEqual(set(production), set(staging))

    def test_each_service_role_resolves_to_a_non_empty_name(self) -> None:
        for name in DEPLOYED:
            target = load_target(name, TARGETS)
            for role, service in target.value["services"].items():
                self.assertTrue(
                    isinstance(service, str) and service.strip(),
                    f"{name}: service role {role!r} has no name",
                )


if __name__ == "__main__":
    unittest.main()
