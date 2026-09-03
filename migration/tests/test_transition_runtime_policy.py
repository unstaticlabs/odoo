from __future__ import annotations

import unittest

from migration.scripts.transition_runtime_policy import (
    APPROVED_CRON_XMLIDS,
    activation_plan,
    cron_model,
    validate_active_ids,
)


class TransitionRuntimePolicyTests(unittest.TestCase):
    def test_cron_inventory_includes_neutralized_jobs(self):
        calls = []

        class FakeCron:
            def sudo(self):
                calls.append("sudo")
                return self

            def with_context(self, **context):
                calls.append(context)
                return self

        cron = FakeCron()
        self.assertIs(cron_model({"ir.cron": cron}), cron)
        self.assertEqual(calls, ["sudo", {"active_test": False}])

    def test_activation_plan_enables_only_approved_jobs(self):
        plan = activation_plan(range(1, 15), range(3, 11))
        self.assertEqual(
            {cron_id for cron_id, active in plan.items() if active},
            set(range(3, 11)),
        )

    def test_activation_plan_rejects_missing_approved_job(self):
        with self.assertRaisesRegex(RuntimeError, "absent"):
            activation_plan((1, 2), (2, 3))

    def test_validation_rejects_extra_or_missing_active_jobs(self):
        with self.assertRaisesRegex(RuntimeError, "differs"):
            validate_active_ids((1, 2, 9), (1, 2))
        with self.assertRaisesRegex(RuntimeError, "differs"):
            validate_active_ids((1,), (1, 2))

    def test_reviewed_transition_allowlist_is_exact(self):
        self.assertEqual(
            APPROVED_CRON_XMLIDS,
            (
                "usl_documents.ir_cron_usl_documents_sync",
                "usl_documents.ir_cron_usl_documents_poll",
                "usl_documents.ir_cron_usl_documents_attachment_queue",
                "usl_documents.ir_cron_usl_documents_classification",
                "usl_documents_accounting.ir_cron_archive_bank_statement_evidence",
                "usl_tese_payroll.ir_cron_tese_reconcile_documents",
                "usl_sign.ir_cron_sign_operations",
                "usl_sign.ir_cron_sign_daily_event_heads",
            ),
        )


if __name__ == "__main__":
    unittest.main()
