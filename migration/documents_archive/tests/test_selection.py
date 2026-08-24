import sys
import unittest
from pathlib import Path
from unittest.mock import patch

MIGRATION_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(MIGRATION_ROOT))

import selection  # noqa: E402


def item(number, *, company=1, mime="application/pdf", **values):
    return {
        "active": True,
        "attachment_id": number,
        "checksum": f"sha-{number}",
        "company_id": company,
        "document_id": number,
        "folder_company_id": None,
        "kind": "document",
        "mimetype": mime,
        **values,
    }


class DocumentsSelectionTest(unittest.TestCase):
    def classification(self, group):
        marker = group[0].get("marker", "")
        return {
            "accounting_evidence": marker == "accounting",
            "hr_restricted": marker == "hr",
            "needs_attention": marker == "attention",
        }

    def setUp(self):
        self.groups = [
            [item(1, marker="accounting")],
            [item(2, company=2, marker="hr")],
            [item(3, marker="attention", active=False)],
            [item(4), item(5)],
            [item(6, kind="unassigned_evidence")],
            [item(7, mime="image/png")],
            [item(8, mime="text/plain", access_rows=[{"user_id": 1}])],
        ]

    def test_profiles_are_deterministic_and_relationship_complete(self):
        with patch.object(selection, "classify_group", self.classification):
            accounting = selection.select_groups(self.groups, "accounting")
            hr = selection.select_groups(self.groups, "hr")
            smoke_a = selection.select_groups(self.groups, "smoke")
            smoke_b = selection.select_groups(list(self.groups), "smoke")

        self.assertEqual(accounting, [self.groups[0]])
        self.assertEqual(hr, [self.groups[1]])
        self.assertEqual(smoke_a, smoke_b)
        self.assertIn(self.groups[3], smoke_a)
        self.assertIn(self.groups[6], smoke_a)
        self.assertEqual(len(next(group for group in smoke_a if group[0]["document_id"] == 4)), 2)

    def test_numeric_limit_remains_an_explicit_diagnostic(self):
        self.assertEqual(selection.select_groups(self.groups, "full", 2), self.groups[:2])

    def test_unknown_profile_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "unknown Documents restore profile"):
            selection.select_groups(self.groups, "random")

    def test_active_business_company_supersedes_inactive_unlinked_history(self):
        group = [
            item(10, company=1, active=False, res_model=None, res_id=0),
            item(11, company=8, res_model="account.move", res_id=10039),
        ]

        self.assertEqual(
            selection.resolve_company_scope(group),
            {
                "company_id": 8,
                "source_company_ids": [1, 8],
                "superseded_inactive_company_ids": [1],
            },
        )

    def test_active_cross_company_relationships_are_rejected(self):
        group = [
            item(12, company=1, res_model="account.move", res_id=1),
            item(13, company=8, res_model="account.move", res_id=2),
        ]

        with self.assertRaisesRegex(ValueError, "several legal companies"):
            selection.resolve_company_scope(group)

    def test_inactive_linked_cross_company_history_is_rejected(self):
        group = [
            item(14, company=1, active=False, res_model="account.move", res_id=1),
            item(15, company=8, res_model="account.move", res_id=2),
        ]

        with self.assertRaisesRegex(ValueError, "several legal companies"):
            selection.resolve_company_scope(group)


if __name__ == "__main__":
    unittest.main()
