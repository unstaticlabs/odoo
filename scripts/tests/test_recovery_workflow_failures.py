"""An isolated restore preserves failures; it must not invent or replace them."""
import copy
import hashlib
import itertools
import unittest
from operations import control_manifest as controls
from scripts.tests.test_control_manifest import controls_v2


def fingerprint(seed):
    return {"schema": "usl-recovery-workflow-failures/v1", "roles": {
        role: {"count": int(role == "failed_documents"), "sha256": hashlib.sha256(
            (seed if role == "failed_documents" else "empty").encode()).hexdigest()}
        for role in controls.FAILED_QUEUE_KEYS
    }}


def compare(before, after, source, restored):
    method = getattr(controls, "validate_recovery_restore", None)
    if method is None:  # Exercise the original observable rejection before the fix.
        return controls.validate_restore(before, after, require_unchanged_release=True)
    return method(before, after, source_failures=source, restored_failures=restored)


class RecoveryWorkflowFailureTests(unittest.TestCase):
    def test_existing_failure_survives_a_usable_restore_without_clearing_business_work(self):
        before = controls_v2(); before["odoo"]["failed_documents"] = 1
        original = copy.deepcopy(before)
        evidence = compare(before, copy.deepcopy(before), fingerprint("invented-row-1:error"), fingerprint("invented-row-1:error"))
        self.assertEqual(before, original)
        self.assertEqual(evidence["workflow_failures"]["roles"]["failed_documents"]["count"], 1)
        with self.assertRaises(controls.ControlManifestError):
            controls.validate_restore(before, before, require_unchanged_release=True)

    def test_same_count_replacement_or_changed_error_is_not_a_faithful_restore(self):
        before = controls_v2(); before["odoo"]["failed_documents"] = 1
        for changed in ("invented-row-2:error", "invented-row-1:different-error"):
            with self.subTest(changed=changed), self.assertRaises(controls.ControlManifestError):
                compare(before, before, fingerprint("invented-row-1:error"), fingerprint(changed))

    def test_new_failures_and_business_changes_still_fail(self):
        before = controls_v2(); before["odoo"]["failed_documents"] = 1
        for key in ("failed_documents", "messages"):
            after = copy.deepcopy(before); after["odoo"][key] += 1
            with self.subTest(key=key), self.assertRaises(controls.ControlManifestError):
                compare(before, after, fingerprint("invented-row-1:error"), fingerprint("invented-row-1:error"))

    def test_each_failed_role_requires_typed_evidence_and_order_is_irrelevant(self):
        before = controls_v2()
        source = fingerprint("invented-row-1:error")
        before["odoo"]["failed_documents"] = 1
        roles = list(controls.FAILED_QUEUE_KEYS)
        for order in (roles, list(reversed(roles))):
            reordered = {"schema": source["schema"], "roles": {key: source["roles"][key] for key in order}}
            compare(before, before, source, reordered)
        for role, bad in itertools.product(roles, (None, {"count": True, "sha256": "a" * 64}, {"count": 0, "sha256": "invalid"})):
            broken = copy.deepcopy(source); broken["roles"][role] = bad
            with self.subTest(role=role, bad=bad), self.assertRaises(controls.ControlManifestError):
                compare(before, before, source, broken)


    def test_cron_quarantine_can_disable_activation_but_not_rewrite_failure_evidence(self):
        before = controls_v2(); before["odoo"]["cron_failures"] = 3
        after = copy.deepcopy(before); after["odoo"]["cron_failures"] = 0
        failures = fingerprint("unused"); failures["roles"]["failed_documents"]["count"] = 0
        failures["roles"]["cron_failures"]["count"] = 1
        compare(before, after, failures, failures)
        changed = copy.deepcopy(failures); changed["roles"]["cron_failures"]["sha256"] = "f" * 64
        with self.assertRaises(controls.ControlManifestError):
            compare(before, after, failures, changed)
        cron_sql = controls.workflow_failure_sql().split("FROM ir_cron WHERE ", 1)[1].split(")", 1)[0]
        self.assertEqual(cron_sql, "failure_count > 0")
