from __future__ import annotations

import unittest

from operations.source_policy import SourcePolicyError, validate, validate_merge_group


class SourcePolicyTests(unittest.TestCase):
    def test_production_accepts_only_staging_and_urgent(self):
        validate(
            event="pull_request",
            base="19-usl",
            head="19-usl-staging",
            head_repository="unstaticlabs/odoo",
            expected_repository="unstaticlabs/odoo",
        )
        validate(
            event="pull_request",
            base="19-usl",
            head="urgent/fix-ledger",
            head_repository="unstaticlabs/odoo",
            expected_repository="unstaticlabs/odoo",
        )
        with self.assertRaises(SourcePolicyError):
            validate(
                event="pull_request",
                base="19-usl",
                head="feat/direct",
                head_repository="unstaticlabs/odoo",
                expected_repository="unstaticlabs/odoo",
            )

    def test_production_rejects_fork_with_staging_branch_name(self):
        with self.assertRaisesRegex(SourcePolicyError, "protected repository"):
            validate(
                event="pull_request",
                base="19-usl",
                head="19-usl-staging",
                head_repository="fork-owner/odoo",
                expected_repository="unstaticlabs/odoo",
            )

    def test_production_rejects_fork_with_urgent_branch_name(self):
        with self.assertRaisesRegex(SourcePolicyError, "protected repository"):
            validate(
                event="pull_request",
                base="19-usl",
                head="urgent/fix",
                head_repository="fork-owner/odoo",
                expected_repository="unstaticlabs/odoo",
            )

    def test_production_rejects_missing_repository_identity(self):
        with self.assertRaisesRegex(SourcePolicyError, "repository identity"):
            validate(event="pull_request", base="19-usl", head="19-usl-staging")

    def test_integration_accepts_feature_branches(self):
        validate(event="pull_request", base="19-usl-staging", head="feat/inventory")

    def test_merge_queue_requires_one_valid_production_pull_request(self):
        pull_request = {
            "number": 17,
            "base": {"ref": "19-usl"},
            "head": {
                "ref": "19-usl-staging",
                "repo": {"full_name": "unstaticlabs/odoo"},
            },
        }
        self.assertEqual(
            validate_merge_group(
                base="19-usl",
                pull_requests=[pull_request],
                expected_repository="unstaticlabs/odoo",
            ),
            pull_request,
        )
        for invalid in ([], [pull_request, pull_request]):
            with self.assertRaisesRegex(SourcePolicyError, "exactly one"):
                validate_merge_group(
                    base="19-usl",
                    pull_requests=invalid,
                    expected_repository="unstaticlabs/odoo",
                )

    def test_merge_queue_rejects_wrong_source(self):
        with self.assertRaisesRegex(SourcePolicyError, "19-usl-staging"):
            validate_merge_group(
                base="19-usl",
                pull_requests=[{
                    "number": 17,
                    "base": {"ref": "19-usl"},
                    "head": {
                        "ref": "feat/direct",
                        "repo": {"full_name": "unstaticlabs/odoo"},
                    },
                }],
                expected_repository="unstaticlabs/odoo",
            )

    def test_protected_branch_push_is_qualified_after_merge(self):
        validate(event="push", base="19-usl", head=None)
        validate(event="push", base="19-usl-staging", head=None)
        with self.assertRaises(SourcePolicyError):
            validate(event="push", base="main", head=None)
