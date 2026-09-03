from __future__ import annotations

import copy
import unittest

from operations.control_manifest import (
    ControlManifestError,
    ODOO_PRESERVATION_KEYS,
    ODOO_QUEUE_KEYS,
    ODOO_RELEASE_KEYS,
    classify,
    release_digest,
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


class ControlManifestTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
