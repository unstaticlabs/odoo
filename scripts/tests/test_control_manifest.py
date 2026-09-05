from __future__ import annotations

import copy
import unittest

from operations.control_manifest import (
    ControlManifestError,
    ODOO_PRESERVATION_KEYS,
    ODOO_PRESERVATION_KEYS_V2,
    ODOO_QUEUE_KEYS,
    ODOO_QUEUE_KEYS_V2,
    ODOO_RELEASE_KEYS,
    ODOO_RELEASE_KEYS_V2,
    ODOO_CONTROL_SQL,
    PAPERLESS_PRESERVATION_KEYS_V2,
    SCHEMA,
    SCHEMA_V1,
    classify,
    release_digest,
    release_definitions_digest,
    validate_restore,
)


def controls() -> dict:
    odoo = {
        key: 0
        for key in ODOO_PRESERVATION_KEYS | ODOO_RELEASE_KEYS | ODOO_QUEUE_KEYS
    }
    odoo.update(
        {
            "companies": 2,
            "users": 10,
            "moves": 100,
            "ledger_delta": 0,
            "acl_fingerprint": "acl-a",
            "record_rule_fingerprint": "rule-a",
            "pending_documents": 4,
        },
    )
    return {
        "odoo": odoo,
        "paperless": {
            "documents": 50,
            "with_ocr": 49,
            "missing_original_name": 0,
        },
    }


def controls_v2() -> dict:
    value = controls()
    for key in ODOO_PRESERVATION_KEYS_V2 - ODOO_PRESERVATION_KEYS:
        value["odoo"][key] = f"preservation-{key}"
    for key in ODOO_RELEASE_KEYS_V2 - ODOO_RELEASE_KEYS:
        value["odoo"][key] = f"release-{key}"
    for key in ODOO_QUEUE_KEYS_V2 - ODOO_QUEUE_KEYS:
        value["odoo"][key] = 0
    for key in PAPERLESS_PRESERVATION_KEYS_V2 - set(value["paperless"]):
        value["paperless"][key] = 0 if key == "trash_count" else f"paperless-{key}"
    return value


class ControlManifestTests(unittest.TestCase):
    def test_scheduler_delay_is_advisory_but_failed_jobs_still_block(self):
        before = controls_v2()
        after = copy.deepcopy(before)
        after["odoo"]["cron_lag"] = 3
        validate_restore(before, after)
        after["odoo"]["cron_failures"] = 1
        with self.assertRaises(ControlManifestError):
            validate_restore(before, after)

    def test_cron_definition_control_excludes_environment_activation(self):
        expression = ODOO_CONTROL_SQL.split("'cron_policy_fingerprint'", 1)[1].split(
            "'currency_rate_fingerprint'", 1
        )[0]
        self.assertNotIn("cron.active", expression)

    def test_classifies_legacy_and_current_controls(self):
        self.assertEqual(classify(controls())["schema"], SCHEMA_V1)
        self.assertEqual(classify(controls_v2())["schema"], SCHEMA)

    def test_unknown_control_fails_closed(self):
        value = controls()
        value["odoo"]["new_queue"] = 1
        with self.assertRaisesRegex(ControlManifestError, "unknown=.*new_queue"):
            classify(value)

    def test_missing_control_fails_closed(self):
        value = controls()
        value["paperless"].pop("with_ocr")
        with self.assertRaisesRegex(ControlManifestError, "missing=.*with_ocr"):
            classify(value)

    def test_release_change_may_match_staging_attestation(self):
        before = controls()
        after = copy.deepcopy(before)
        after["odoo"]["acl_fingerprint"] = "acl-b"
        result = validate_restore(
            before,
            after,
            expected_release_sha256=release_digest(after),
        )
        self.assertEqual(result["release_sha256"], release_digest(after))

    def test_release_change_cannot_bypass_staging_attestation(self):
        before = controls()
        after = copy.deepcopy(before)
        after["odoo"]["acl_fingerprint"] = "acl-b"
        with self.assertRaisesRegex(ControlManifestError, "staging-qualified"):
            validate_restore(
                before,
                after,
                expected_release_sha256=release_digest(before),
            )

    def test_same_release_must_preserve_release_controls(self):
        before = controls()
        after = copy.deepcopy(before)
        after["odoo"]["record_rule_fingerprint"] = "rule-b"
        with self.assertRaisesRegex(ControlManifestError, "same-release"):
            validate_restore(before, after, require_unchanged_release=True)

    def test_business_history_must_remain_identical(self):
        before = controls()
        after = copy.deepcopy(before)
        after["odoo"]["moves"] += 1
        with self.assertRaisesRegex(ControlManifestError, "business controls"):
            validate_restore(before, after)

    def test_pending_work_may_drain(self):
        before = controls()
        after = copy.deepcopy(before)
        after["odoo"]["pending_documents"] = 2
        result = validate_restore(before, after)
        self.assertEqual(result["queues"]["odoo"]["pending_documents"], 2)

    def test_pending_work_may_not_grow_while_quiesced(self):
        before = controls()
        after = copy.deepcopy(before)
        after["odoo"]["pending_documents"] = 5
        with self.assertRaisesRegex(ControlManifestError, "pending queues grew"):
            validate_restore(before, after)

    def test_failed_work_is_never_admitted(self):
        before = controls()
        after = copy.deepcopy(before)
        after["odoo"]["failed_documents"] = 1
        with self.assertRaisesRegex(ControlManifestError, "failed queues"):
            validate_restore(before, after)

    def test_v1_snapshot_can_be_restored_into_v2_runtime(self):
        before = controls()
        after = controls_v2()
        result = validate_restore(
            before,
            after,
            expected_release_sha256=release_digest(after),
        )
        self.assertEqual(result["control_schema"], SCHEMA)

    def test_v2_snapshot_cannot_regress_to_v1_controls(self):
        with self.assertRaisesRegex(ControlManifestError, "regressed"):
            validate_restore(controls_v2(), controls())

    def test_v2_identity_drift_is_rejected(self):
        before = controls_v2()
        after = copy.deepcopy(before)
        after["odoo"]["agent_authority_fingerprint"] = "changed"
        with self.assertRaisesRegex(ControlManifestError, "business controls"):
            validate_restore(before, after)

    def test_v2_paperless_permissions_drift_is_rejected(self):
        before = controls_v2()
        after = copy.deepcopy(before)
        after["paperless"]["permission_fingerprint"] = "changed"
        with self.assertRaisesRegex(ControlManifestError, "business controls"):
            validate_restore(before, after)




class SemanticReleaseDefinitionTests(unittest.TestCase):
    def test_row_order_does_not_change_qualification(self):
        first = {"acl": [{"identity": "base.access_a", "write": False},
                         {"identity": "usl.access_b", "write": True}],
                 "rules": [], "crons": [], "groups": [["base.user", "base.internal"]]}
        reordered = copy.deepcopy(first)
        reordered["acl"].reverse()
        self.assertEqual(release_definitions_digest(first), release_definitions_digest(reordered))

    def test_permission_schedule_and_group_changes_are_detected(self):
        original = {"acl": [{"identity": "usl.access_a", "write": False}],
                    "rules": [{"identity": "usl.rule", "groups": ["base.internal"]}],
                    "crons": [{"identity": "usl.cron", "interval_number": 5}],
                    "groups": [["base.user", "base.internal"]]}
        for section, replacement in {
            "acl": [{"identity": "usl.access_a", "write": True}],
            "rules": [{"identity": "usl.rule", "groups": ["base.admin"]}],
            "crons": [{"identity": "usl.cron", "interval_number": 1}],
            "groups": [["base.user", "base.admin"]],
        }.items():
            with self.subTest(section=section):
                changed = copy.deepcopy(original)
                changed[section] = replacement
                self.assertNotEqual(release_definitions_digest(original), release_definitions_digest(changed))


if __name__ == "__main__":
    unittest.main()
