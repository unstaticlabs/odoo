from __future__ import annotations

import copy
import unittest

from operations.production_promotion import (
    ProductionPromotionError,
    create_merge_group_evidence,
    create_pull_request_evidence,
    verify,
)


REPOSITORY = "unstaticlabs/odoo"
SOURCE_TREE = "1" * 40
QUALIFIED_TREE = "2" * 40


def pull_request(*, repository: str = REPOSITORY, branch: str = "19-usl-staging"):
    return {
        "number": 63,
        "base": {"ref": "19-usl"},
        "head": {
            "ref": branch,
            "sha": SOURCE_TREE,
            "repo": {"full_name": repository},
        },
    }


class ProductionPromotionTests(unittest.TestCase):
    def test_pull_request_evidence_binds_source_and_qualified_tree(self):
        evidence = create_pull_request_evidence(
            repository=REPOSITORY,
            base_branch="19-usl",
            pull_request_number=63,
            source_repository=REPOSITORY,
            source_branch="19-usl-staging",
            source_tree=SOURCE_TREE,
            qualified_git_tree=QUALIFIED_TREE,
        )
        self.assertEqual(evidence["original_pr_number"], 63)
        self.assertEqual(evidence["source_repository"], REPOSITORY)
        self.assertEqual(evidence["source_branch"], "19-usl-staging")
        self.assertEqual(evidence["source_tree"], SOURCE_TREE)
        self.assertEqual(evidence["qualified_git_tree"], QUALIFIED_TREE)
        self.assertIsNone(evidence["production_merge_group_tree"])

    def test_merge_group_evidence_binds_original_pr_and_merge_tree(self):
        evidence = create_merge_group_evidence(
            repository=REPOSITORY,
            base_branch="19-usl",
            pull_requests=[pull_request(branch="urgent/fix")],
            qualified_git_tree=QUALIFIED_TREE,
            production_merge_group_tree=QUALIFIED_TREE,
        )
        self.assertEqual(evidence["original_pr_number"], 63)
        self.assertEqual(evidence["source_branch"], "urgent/fix")
        self.assertEqual(evidence["qualified_git_tree"], QUALIFIED_TREE)
        self.assertEqual(evidence["production_merge_group_tree"], QUALIFIED_TREE)

    def test_merge_group_rejects_fork_source(self):
        with self.assertRaisesRegex(ProductionPromotionError, "repository differs"):
            create_merge_group_evidence(
                repository=REPOSITORY,
                base_branch="19-usl",
                pull_requests=[pull_request(repository="fork-owner/odoo")],
                qualified_git_tree=QUALIFIED_TREE,
                production_merge_group_tree=QUALIFIED_TREE,
            )

    def test_merge_group_rejects_direct_feature_source(self):
        with self.assertRaisesRegex(ProductionPromotionError, "only 19-usl-staging"):
            create_merge_group_evidence(
                repository=REPOSITORY,
                base_branch="19-usl",
                pull_requests=[pull_request(branch="feat/x")],
                qualified_git_tree=QUALIFIED_TREE,
                production_merge_group_tree=QUALIFIED_TREE,
            )

    def test_merge_group_rejects_ambiguous_pull_request_set(self):
        with self.assertRaisesRegex(ProductionPromotionError, "exactly one"):
            create_merge_group_evidence(
                repository=REPOSITORY,
                base_branch="19-usl",
                pull_requests=[pull_request(), pull_request(branch="urgent/fix")],
                qualified_git_tree=QUALIFIED_TREE,
                production_merge_group_tree=QUALIFIED_TREE,
            )

    def test_merge_group_rejects_different_qualified_tree(self):
        with self.assertRaisesRegex(ProductionPromotionError, "differs"):
            create_merge_group_evidence(
                repository=REPOSITORY,
                base_branch="19-usl",
                pull_requests=[pull_request()],
                qualified_git_tree=QUALIFIED_TREE,
                production_merge_group_tree="3" * 40,
            )

    def test_verification_rejects_replayed_evidence(self):
        expected = create_merge_group_evidence(
            repository=REPOSITORY,
            base_branch="19-usl",
            pull_requests=[pull_request()],
            qualified_git_tree=QUALIFIED_TREE,
            production_merge_group_tree=QUALIFIED_TREE,
        )
        replay = copy.deepcopy(expected)
        replay["production_merge_group_tree"] = "3" * 40
        with self.assertRaisesRegex(ProductionPromotionError, "does not match"):
            verify(replay, expected)
