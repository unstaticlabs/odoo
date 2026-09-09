from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from operations import user_docs

PAGE = (
    "---\n"
    "title: Match a bank transaction\n"
    "type: how-to\n"
    "description: Match a line.\n"
    "lang: en\n"
    "persona: accountant\n"
    "generated: false\n"
    "---\n\n"
    "# Match a bank transaction\n\n"
    "See [reconciliation](../explanation/reconciliation.md) and ![shot](match/01.png).\n"
)


class UserDocsChecksTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / "how-to" / "match").mkdir(parents=True)
        (self.root / "explanation").mkdir()
        (self.root / "README.md").write_text("# Guide\n", encoding="utf-8")
        (self.root / "how-to" / "match.md").write_text(PAGE, encoding="utf-8")
        (self.root / "how-to" / "match" / "01.png").write_bytes(b"\x89PNG")
        (self.root / "explanation" / "reconciliation.md").write_text(
            "---\ntitle: Reconciliation\ntype: explanation\ndescription: Why.\n---\n\n# Reconciliation\n",
            encoding="utf-8",
        )

    def test_well_formed_pages_have_no_problems(self):
        pages = user_docs.load_pages(self.root)
        self.assertEqual([page.path for page in pages], ["explanation/reconciliation.md", "how-to/match.md"])
        for page in pages:
            self.assertEqual(page.problems, [])
            self.assertEqual(user_docs.check_links(page, self.root), [])

    def test_a_page_in_the_wrong_directory_is_reported(self):
        (self.root / "explanation" / "wrong.md").write_text(
            "---\ntitle: Wrong\ntype: how-to\ndescription: D.\n---\n\n# Wrong\n", encoding="utf-8",
        )
        page = next(page for page in user_docs.load_pages(self.root) if page.path == "explanation/wrong.md")
        self.assertIn("type 'how-to' does not match the directory ('explanation')", page.problems)

    def test_dead_links_and_images_are_reported(self):
        (self.root / "how-to" / "match" / "01.png").unlink()
        (self.root / "explanation" / "reconciliation.md").unlink()
        page = next(page for page in user_docs.load_pages(self.root) if page.path == "how-to/match.md")
        problems = user_docs.check_links(page, self.root)
        self.assertIn("link '../explanation/reconciliation.md' does not exist", problems)
        self.assertIn("image 'match/01.png' does not exist", problems)
        self.assertEqual(user_docs.check_links(user_docs.Page("x.md", {}, "[a](/abs.md) [b](../../up.md)"), self.root), [
            "absolute link '/abs.md' (the viewer blanks it)",
            "link '../../up.md' escapes docs/users",
        ])

    def test_the_users_index_groups_by_type_and_names_the_audience(self):
        index = user_docs.render_users_index(user_docs.load_pages(self.root))
        self.assertTrue(index.startswith(user_docs.GENERATED_NOTICE))
        self.assertLess(index.index("## Complete a task"), index.index("## Understand the design"))
        self.assertIn("- [Match a bank transaction](how-to/match.md): Match a line. (for accountants)", index)
        self.assertIn("- [Reconciliation](explanation/reconciliation.md): Why.", index)

    def test_llms_txt_follows_the_convention(self):
        decisions = [{"file": "0001-x.md", "number": 1, "title": "X", "date": "2026-09-08", "status": "accepted", "problems": []}]
        text = user_docs.render_llms_txt(user_docs.load_pages(self.root), decisions)
        self.assertTrue(text.startswith("# USL Odoo Distribution\n\n> "))
        self.assertIn("## How-to guides\n\n- [Match a bank transaction](docs/users/how-to/match.md): Match a line.", text)
        self.assertIn("## Decisions\n\n- [X](docs/product/decisions/0001-x.md): accepted, 2026-09-08", text)
        self.assertIn("## Optional\n\n- [Reconciliation](docs/users/explanation/reconciliation.md)", text)


class DecisionRecordsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def _write(self, name, text):
        (self.root / name).write_text(text, encoding="utf-8")

    def test_records_are_numbered_without_gaps_and_carry_the_four_parts(self):
        record = "# 1. Use the thing\n\nDate: 2026-09-08\nStatus: accepted\n\n## Context\n\n## Decision\n\n## Consequences\n"
        self._write("0001-use-the-thing.md", record)
        self._write("0003-skip.md", record.replace("1.", "3."))
        decisions = user_docs.load_decisions(self.root)
        self.assertEqual([decision["title"] for decision in decisions], ["Use the thing", "Use the thing"])
        self.assertIn("decision numbers must be 0001, 0002, … without gaps or repeats", decisions[0]["problems"])
        self._write("0002-broken.md", "# 2. Broken\n\nStatus: maybe\n\n## Decision\n")
        broken = next(decision for decision in user_docs.load_decisions(self.root) if decision["file"] == "0002-broken.md")
        self.assertIn("a `Date: YYYY-MM-DD` line follows the title", broken["problems"])
        self.assertIn("a `Status:` line names one of proposed, accepted, deprecated, superseded", broken["problems"])
        self.assertIn("section '## Context' is missing", broken["problems"])

    def test_the_index_lists_every_record(self):
        decisions = [{"file": "0001-x.md", "number": 1, "title": "X", "date": "2026-09-08", "status": "accepted", "problems": []}]
        index = user_docs.render_decisions_index(decisions)
        self.assertIn("| 0001 | [X](0001-x.md) | accepted | 2026-09-08 |", index)


class RepositoryDocsTests(unittest.TestCase):
    def test_the_repository_documentation_passes_its_own_check(self):
        self.assertEqual(user_docs.check(), [])


if __name__ == "__main__":
    unittest.main()
