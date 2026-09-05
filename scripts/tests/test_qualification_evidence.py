from __future__ import annotations

import unittest

from operations.qualification_evidence import (
    QualificationEvidenceError, create, verify_merge_group, verify_production_pr, verify_origin,
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
    def test_artifact_must_belong_to_successful_qualification_run(self):
        value = evidence()
        artifact = {"expired": False, "workflow_run": {"id": 123}}
        run = {"id": 123, "event": "pull_request", "path": ".github/workflows/qualification.yml", "head_sha": "a" * 40,
               "repository": {"full_name": "unstaticlabs/odoo"}, "status": "completed", "conclusion": "success",
               "pull_requests": [{"number": 42, "base": {"sha": "b" * 40}, "head": {"sha": "a" * 40}}]}
        verify_origin(value, artifact, run, current_run_id=456, event="merge_group")
        for change in ({"conclusion": "failure"}, {"id": 124}, {"head_sha": "e" * 40}, {"pull_requests": []}):
            with self.subTest(change=change), self.assertRaises(QualificationEvidenceError):
                verify_origin(value, artifact, {**run, **change}, current_run_id=456, event="merge_group")
        verify_origin(value, artifact, {**run, "status": "in_progress", "conclusion": None}, current_run_id=123, event="pull_request")
        with self.assertRaises(QualificationEvidenceError):
            verify_origin(value, artifact, run, current_run_id=456, event="pull_request")

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
