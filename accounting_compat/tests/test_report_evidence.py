from __future__ import annotations

import unittest
from pathlib import Path

from accounting_compat import cli

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


class SourceReportParityEvidenceTest(unittest.TestCase):
    def test_exact_ledger_proof_has_no_source_specific_vat_repair(self):
        harness = (REPOSITORY_ROOT / "accounting_compat" / "cli.py").read_text(
            encoding="utf-8",
        )
        importer = (
            REPOSITORY_ROOT
            / "custom-addons"
            / "rebuild_account_migration"
            / "models"
            / "import_run.py"
        ).read_text(encoding="utf-8")
        declaration = (
            REPOSITORY_ROOT
            / "custom-addons"
            / "rebuild_account_migration"
            / "models"
            / "declaration.py"
        ).read_text(encoding="utf-8")

        for source in (harness, importer, declaration):
            self.assertNotIn("classify_confirmed_vat_refund", source)
            self.assertNotIn("usl_vat_refund_reclassification", source)
        self.assertNotIn("apply_confirmed_bank_transformations", harness)
        self.assertNotIn('"confirmed_transformations"', harness)

    def test_capability_matrix_applies_final_capability_and_report_controls(self):
        matrix = cli.capability_matrix(
            [
                {
                    "id": 7,
                    "name": "Trial Balance",
                    "decision": "MANDATORY_PARITY",
                    "acceptance_evidence_required": "exports",
                },
            ],
            {
                "capabilities": {
                    "Accounting > Closing > Reconcile": {
                        "status": "PARTIAL",
                        "technical_status": "passed",
                    },
                },
                "reports": {
                    "7": {
                        "status": "PARTIAL",
                        "technical_status": "passed",
                        "parity_level": "level_4_evidence_partial",
                    },
                },
            },
        )

        reconcile = next(
            row
            for row in matrix
            if row["capability"] == "Accounting > Closing > Reconcile"
        )
        report = next(row for row in matrix if row.get("source_report_id") == 7)
        self.assertEqual(reconcile["status"], "PARTIAL")
        self.assertEqual(reconcile["technical_status"], "passed")
        self.assertEqual(report["status"], "PARTIAL")
        self.assertEqual(report["parity_level"], "level_4_evidence_partial")

    def test_sequence_chronology_summary_keeps_source_exceptions_visible(self):
        summary = cli.sequence_chronology_summary([
            {
                "source_move_id": "1",
                "source_journal_id": "10",
                "move_name": "BNK/0001",
                "date": "2025-01-31",
                "sequence_prefix": "BNK/",
                "sequence_number": "1",
            },
            {
                "source_move_id": "2",
                "source_journal_id": "10",
                "move_name": "BNK/0003",
                "date": "2025-01-15",
                "sequence_prefix": "BNK/",
                "sequence_number": "3",
            },
            {
                "source_move_id": "3",
                "source_journal_id": "10",
                "move_name": "BNK/0003",
                "date": "2025-02-01",
                "sequence_prefix": "BNK/",
                "sequence_number": "3",
            },
        ])

        self.assertEqual(summary["move_count"], 3)
        self.assertEqual(summary["duplicate_name_group_count"], 1)
        self.assertEqual(
            summary["duplicate_sequence_number_group_count"],
            1,
        )
        self.assertEqual(summary["sequence_gap_count"], 1)
        self.assertEqual(summary["sequence_date_decrease_count"], 1)

    def test_keeps_passed_family_evidence_when_aggregate_probe_is_partial(self):
        evidence = cli.source_report_parity_evidence_from_controls(
            {
                "status": "partial",
                "checks": {"trial_balance_balanced": True},
            },
            {
                "status": "partial",
                "results": {
                    "trial_balance": {
                        "status": "passed",
                        "action_model": "account.move.line",
                        "journal_item_count": 1,
                    },
                },
            },
            {
                "status": "passed",
                "results": {
                    "trial_balance": {
                        "status": "passed",
                        "row_count": 1,
                        "csv_row_count": 1,
                        "xlsx": {"status": "passed"},
                        "pdf": {"status": "passed"},
                        "preview": {"status": "passed"},
                    },
                },
            },
        )

        self.assertEqual(evidence["trial_balance"]["status"], "passed")
        self.assertEqual(evidence["trial_balance"]["drilldown_status"], "passed")
        self.assertEqual(evidence["trial_balance"]["view_status"], "passed")

    def test_missing_required_view_check_does_not_pass_by_default(self):
        evidence = cli.source_report_parity_evidence_from_controls(
            {"status": "partial", "checks": {}},
            {
                "status": "passed",
                "results": {
                    "trial_balance": {
                        "status": "passed",
                        "action_model": "account.move.line",
                        "journal_item_count": 1,
                    },
                },
            },
            {
                "status": "passed",
                "results": {
                    "trial_balance": {
                        "status": "passed",
                        "xlsx": {"status": "passed"},
                        "pdf": {"status": "passed"},
                        "preview": {"status": "passed"},
                    },
                },
            },
        )

        self.assertEqual(evidence["trial_balance"]["status"], "partial_or_failed")
        self.assertEqual(evidence["trial_balance"]["view_status"], "failed")


if __name__ == "__main__":
    unittest.main()
