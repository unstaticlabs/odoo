from __future__ import annotations

import unittest

from accounting_compat import cli


class ProductImportReadinessEvidenceTest(unittest.TestCase):
    def setUp(self):
        self.discrepancy = {
            "name": cli.PRODUCT_DRAFT_REGENERATION_DISCREPANCY,
            "severity": "P1",
            "classification": "missing_capability",
        }

    def test_complete_product_native_drafts_supersede_exact_replay_warning(self):
        rows = cli.apply_product_import_discrepancy_evidence(
            [self.discrepancy, {"name": "Keep me"}],
            {
                "status": "passed",
                "checks": {"draft_regeneration_matches": True},
                "draft_statistics": {
                    "candidate_count": 190,
                    "validated_count": 190,
                    "mismatch_count": 0,
                    "blocked_count": 0,
                    "incomplete_count": 0,
                },
            },
        )

        self.assertEqual(rows, [{"name": "Keep me"}])

    def test_incomplete_product_native_drafts_keep_warning(self):
        rows = cli.apply_product_import_discrepancy_evidence(
            [self.discrepancy],
            {
                "status": "passed",
                "checks": {"draft_regeneration_matches": True},
                "draft_statistics": {
                    "candidate_count": 190,
                    "validated_count": 189,
                    "mismatch_count": 0,
                    "blocked_count": 0,
                    "incomplete_count": 1,
                },
            },
        )

        self.assertEqual(rows, [self.discrepancy])
