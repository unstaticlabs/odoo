from __future__ import annotations

import unittest
from pathlib import Path

from operations.action_pins import (
    ActionPinError,
    Pin,
    audit,
    comment_matches,
    scan,
    verify,
)


ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = ROOT / ".github/workflows"

# actions/setup-python as it really was. Dependabot moved the pin from the
# v5.6.0 commit to the v7.0.0 one and carried the wrong comment across both.
SETUP_PYTHON = {
    "v5": "a26af69be951a213d495a4c3e4e4022e16d87065",
    "v5.6.0": "a26af69be951a213d495a4c3e4e4022e16d87065",
    "v7": "5fda3b95a4ea91299a34e894583c3862153e4b97",
    "v7.0.0": "5fda3b95a4ea91299a34e894583c3862153e4b97",
}


def resolver(tags: dict[str, dict[str, str]]):
    def resolve(action: str) -> dict[str, str]:
        return tags[action]
    return resolve


def pin(comment: str, sha: str = "5fda3b95a4ea91299a34e894583c3862153e4b97") -> Pin:
    return Pin("qualification.yml", 69, "actions/setup-python", sha, comment)


class CommentMatchTests(unittest.TestCase):
    def test_exact_version_matches_its_tag(self):
        self.assertTrue(comment_matches("v7.0.0", ["v7.0.0", "v7"]))

    def test_major_comment_matches_the_patch_tag_it_covers(self):
        self.assertTrue(comment_matches("v4", ["v4.2.2", "v4"]))

    def test_major_comment_survives_the_major_tag_moving_upstream(self):
        # Upstream released v4.3.0 and moved the v4 tag off this commit. The
        # pin is still an accurate v4, so CI must not turn red for it.
        self.assertTrue(comment_matches("v4", ["v4.2.2"]))

    def test_wrong_version_is_rejected(self):
        self.assertFalse(comment_matches("v6.0.0", ["v7.0.0", "v7"]))

    def test_major_comment_does_not_match_a_longer_number(self):
        self.assertFalse(comment_matches("v4", ["v41.0.0"]))

    def test_comment_more_precise_than_the_tag_is_rejected(self):
        self.assertFalse(comment_matches("v7.0.1", ["v7.0.0"]))

    def test_unparseable_version_falls_back_to_an_exact_tag(self):
        self.assertTrue(comment_matches("v2.0.1-rc1", ["v2.0.1-rc1"]))
        self.assertFalse(comment_matches("v2.0.1-rc1", ["v2.0.1"]))


class ScanTests(unittest.TestCase):
    def test_pin_and_comment_are_read(self):
        pins, problems = scan(
            "      - uses: actions/checkout@" + "a" * 40 + " # v7.0.1\n",
            workflow="w.yml",
        )
        self.assertEqual(problems, [])
        self.assertEqual([(item.action, item.comment) for item in pins],
                         [("actions/checkout", "v7.0.1")])

    def test_mutable_ref_is_rejected(self):
        _, problems = scan("      - uses: actions/checkout@v7\n", workflow="w.yml")
        self.assertIn("mutable ref", problems[0])

    def test_short_sha_is_rejected(self):
        _, problems = scan("      - uses: actions/checkout@3d3c42e5\n", workflow="w.yml")
        self.assertIn("mutable ref", problems[0])

    def test_pin_without_a_comment_is_rejected(self):
        _, problems = scan("      - uses: actions/checkout@" + "a" * 40 + "\n", workflow="w.yml")
        self.assertIn("trailing comment", problems[0])

    def test_local_workflow_reference_needs_no_pin(self):
        pins, problems = scan("    uses: ./.github/workflows/product-image.yml\n", workflow="w.yml")
        self.assertEqual((pins, problems), ([], []))


class VerifyTests(unittest.TestCase):
    def test_the_dependabot_comment_mismatch_is_caught(self):
        problems = verify([pin("v6.0.0")], resolver({"actions/setup-python": SETUP_PYTHON}))
        self.assertEqual(len(problems), 1)
        self.assertIn("v7.0.0", problems[0])

    def test_the_same_comment_was_already_wrong_before_the_bump(self):
        stale = pin("v6.0.0", "a26af69be951a213d495a4c3e4e4022e16d87065")
        problems = verify([stale], resolver({"actions/setup-python": SETUP_PYTHON}))
        self.assertIn("v5.6.0", problems[0])

    def test_the_corrected_comment_passes(self):
        self.assertEqual(verify([pin("v7.0.0")], resolver({"actions/setup-python": SETUP_PYTHON})), [])

    def test_a_commit_no_tag_names_is_reported(self):
        problems = verify([pin("v7.0.0", "b" * 40)], resolver({"actions/setup-python": SETUP_PYTHON}))
        self.assertIn("no tag", problems[0])

    def test_each_action_is_resolved_once(self):
        calls = []

        def counting(action: str) -> dict[str, str]:
            calls.append(action)
            return SETUP_PYTHON

        verify([pin("v7.0.0"), pin("v7.0.0"), pin("v7")], counting)
        self.assertEqual(calls, ["actions/setup-python"])


class WorkflowTests(unittest.TestCase):
    def test_every_action_is_pinned_and_commented(self):
        """Structural only. scripts/check-action-pins resolves the versions."""
        pins, problems = audit(WORKFLOWS, lambda action: {})
        self.assertTrue(pins)
        self.assertEqual([item for item in problems if "no tag" not in item], [])

    def test_setup_python_states_the_version_it_pins(self):
        pins, _ = audit(WORKFLOWS, lambda action: {})
        setup = [item for item in pins if item.action == "actions/setup-python"]
        self.assertTrue(setup)
        self.assertEqual(verify(setup, resolver({"actions/setup-python": SETUP_PYTHON})), [])

    def test_a_directory_without_workflows_is_an_error(self):
        with self.assertRaisesRegex(ActionPinError, "no workflow"):
            audit(ROOT / "operations/contracts", lambda action: {})


if __name__ == "__main__":
    unittest.main()
