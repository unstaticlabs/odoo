from __future__ import annotations

import unittest

from operations.source_policy import SourcePolicyError, validate


class SourcePolicyTests(unittest.TestCase):
    def test_production_accepts_only_staging_and_urgent(self):
        validate(event="pull_request", base="19-usl", head="19-usl-staging")
        validate(event="pull_request", base="19-usl", head="urgent/fix-ledger")
        with self.assertRaises(SourcePolicyError):
            validate(event="pull_request", base="19-usl", head="feat/direct")

    def test_integration_accepts_feature_branches(self):
        validate(event="pull_request", base="19-usl-staging", head="feat/inventory")

    def test_merge_queue_is_checked_for_supported_base(self):
        validate(event="merge_group", base="19-usl", head=None)
        with self.assertRaises(SourcePolicyError):
            validate(event="merge_group", base="main", head=None)
