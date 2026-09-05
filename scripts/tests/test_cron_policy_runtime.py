from __future__ import annotations

import json
import unittest
from pathlib import Path

from operations.cron_policy import (
    CronPolicyError,
    desired_active,
    load,
    parse,
    render_odoo_apply_script,
    validate_runtime,
)


ROOT = Path(__file__).resolve().parents[2]


def policy():
    return {
        "schema": "usl-production-cron-policy-v1",
        "gates": ["always", "smtp"],
        "crons": {
            "base.autovacuum_job": {"gate": "always", "reason": "maintenance"},
            "mail.scheduler": {"gate": "smtp", "reason": "outbound mail"},
            "mail.telemetry": {"gate": None, "reason": "disabled telemetry"},
        },
    }


class CronPolicyRuntimeTests(unittest.TestCase):
    def test_parse_accepts_policy_text_from_remote_transport(self):
        self.assertEqual(parse(json.dumps(policy())), policy())

    def test_repository_production_policy_accepts_declared_target(self):
        target = json.loads((ROOT / "operations/targets/production.json").read_text())
        declared = target["cron_policy"]
        value = load(ROOT / "deploy/production.cron-policy.json")
        installed = list(value["crons"])
        active = [
            xmlid
            for xmlid, rule in value["crons"].items()
            if rule["gate"] is not None and declared["gates"][rule["gate"]]
        ]
        result = validate_runtime(
            value,
            mode=declared["mode"],
            gates=declared["gates"],
            installed=installed,
            active=active,
            invalid_identity_count=0,
        )
        self.assertEqual(len(result["installed_xmlids"]), 58)

    def test_managed_runtime_requires_exact_active_set(self):
        result = validate_runtime(
            policy(),
            mode="managed",
            gates={"always": True, "smtp": True},
            installed=["mail.telemetry", "mail.scheduler", "base.autovacuum_job"],
            active=["mail.scheduler", "base.autovacuum_job"],
            invalid_identity_count=0,
        )
        self.assertEqual(result["status"], "passed")

    def test_unknown_cron_fails_closed(self):
        with self.assertRaisesRegex(CronPolicyError, "unknown"):
            validate_runtime(
                policy(),
                mode="managed",
                gates={"always": True, "smtp": True},
                installed=[
                    "base.autovacuum_job",
                    "mail.scheduler",
                    "mail.telemetry",
                    "unknown.cron",
                ],
                active=["base.autovacuum_job", "mail.scheduler"],
                invalid_identity_count=0,
            )

    def test_wrong_activation_fails_closed(self):
        with self.assertRaisesRegex(CronPolicyError, "active cron"):
            validate_runtime(
                policy(),
                mode="managed",
                gates={"always": True, "smtp": False},
                installed=list(policy()["crons"]),
                active=["base.autovacuum_job", "mail.scheduler"],
                invalid_identity_count=0,
            )

    def test_neutralized_runtime_keeps_inventory_but_no_active_jobs(self):
        result = validate_runtime(
            policy(),
            mode="neutralized",
            gates={"always": True, "smtp": False},
            installed=list(policy()["crons"]),
            active=[],
            invalid_identity_count=0,
        )
        self.assertEqual(result["active_xmlids"], [])

    def test_ambiguous_identity_fails_closed(self):
        with self.assertRaisesRegex(CronPolicyError, "invalid_identity_count"):
            validate_runtime(
                policy(),
                mode="managed",
                gates={"always": True, "smtp": True},
                installed=list(policy()["crons"]),
                active=["base.autovacuum_job", "mail.scheduler"],
                invalid_identity_count=1,
            )

    def test_unmanaged_mode_is_observational_only(self):
        result = validate_runtime(
            None,
            mode="unmanaged",
            gates={},
            installed=["custom.cron"],
            active=["custom.cron"],
            invalid_identity_count=0,
        )
        self.assertEqual(result["status"], "observed")

    def test_candidate_apply_program_is_self_contained_and_exact(self):
        gates = {"always": True, "smtp": False}
        self.assertEqual(
            desired_active(policy(), mode="managed", gates=gates),
            ["base.autovacuum_job"],
        )
        program = render_odoo_apply_script(policy(), mode="managed", gates=gates)
        compile(program, "<cron-policy>", "exec")
        self.assertIn("USL_CRON_POLICY_RESULT=", program)
        self.assertIn('"base.autovacuum_job"', program)

    def test_neutralized_candidate_disables_every_known_cron(self):
        self.assertEqual(
            desired_active(
                policy(),
                mode="neutralized",
                gates={"always": True, "smtp": True},
            ),
            [],
        )


if __name__ == "__main__":
    unittest.main()
