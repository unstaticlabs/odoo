from __future__ import annotations

import unittest

from accounting_compat import cli


class SourceReportParityEvidenceTest(unittest.TestCase):
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
