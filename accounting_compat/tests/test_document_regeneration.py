import unittest

from accounting_compat.cli import document_regeneration_case_classification


class TestDocumentRegenerationClassification(unittest.TestCase):
    def test_cancelled_source_with_accounting_lines_is_a_native_candidate(self):
        result = document_regeneration_case_classification({
            "state": "cancel",
            "move_type": "entry",
            "source_line_count": 2,
            "source_accounting_line_count": 2,
        })

        self.assertEqual(result, {
            "generation_scope": "cancelled_source_record",
            "case_status": "candidate_ready",
            "generation_status": "not_generated",
        })

    def test_empty_cancelled_source_remains_review_only(self):
        result = document_regeneration_case_classification({
            "state": "cancel",
            "move_type": "entry",
            "source_line_count": 0,
            "source_accounting_line_count": 0,
        })

        self.assertEqual(result, {
            "generation_scope": "cancelled_source_record",
            "case_status": "review_only_cancelled_source",
            "generation_status": "not_applicable",
        })
