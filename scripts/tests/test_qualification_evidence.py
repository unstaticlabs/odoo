from __future__ import annotations

import unittest

from operations.qualification_evidence import (
    QualificationEvidenceError, create, verify_merge_group, verify_production_pr,
)


def evidence() -> dict:
    return create(
        repository="unstaticlabs/odoo",
        event="pull_request",
        pull_request=42,
        source_ref="19-usl-staging",
        source_sha="a" * 40,
        base_ref="19-usl",
        base_sha="b" * 40,
        qualified_commit="c" * 40,
        qualified_tree="d" * 40,
        database_mode="all",
        workflow_run_id=123,
        results={"compatibility": "success", "database": "success", "source_policy": "success"},
    )


class QualificationEvidenceTests(unittest.TestCase):
    def test_production_pr_matches_exact_evidence(self):
        self.assertEqual(
            verify_production_pr(
                evidence(), repository="unstaticlabs/odoo", pull_request=42,
                source_ref="19-usl-staging", source_sha="a" * 40,
                base_sha="b" * 40, qualified_tree="d" * 40,
            )["database_mode"],
            "all",
        )

    def test_merge_group_reuses_only_identical_tree(self):
        self.assertEqual(
            verify_merge_group(
                evidence(), repository="unstaticlabs/odoo", pull_request=42,
                source_ref="19-usl-staging", source_sha="a" * 40,
                qualified_tree="d" * 40,
            )["qualified_commit"],
            "c" * 40,
        )
        with self.assertRaisesRegex(QualificationEvidenceError, "tree differs"):
            verify_merge_group(
                evidence(), repository="unstaticlabs/odoo", pull_request=42,
                source_ref="19-usl-staging", source_sha="a" * 40,
                qualified_tree="e" * 40,
            )

    def test_wrong_source_and_stale_base_are_rejected(self):
        with self.assertRaises(QualificationEvidenceError):
            verify_production_pr(
                evidence(), repository="unstaticlabs/odoo", pull_request=42,
                source_ref="urgent/other", source_sha="a" * 40,
                base_sha="b" * 40, qualified_tree="d" * 40,
            )
        with self.assertRaisesRegex(QualificationEvidenceError, "base_sha differs"):
            verify_production_pr(
                evidence(), repository="unstaticlabs/odoo", pull_request=42,
                source_ref="19-usl-staging", source_sha="a" * 40,
                base_sha="e" * 40, qualified_tree="d" * 40,
            )


if __name__ == "__main__":
    unittest.main()
