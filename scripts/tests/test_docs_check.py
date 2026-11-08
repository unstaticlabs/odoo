from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from operations.docs import check as docs_check

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

#: Findings the lead's content work still owes; the tooling check must not
#: wait for them. Remove entries as the pages are fixed.


class DocsChecksTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / "how-to" / "match").mkdir(parents=True)
        (self.root / "explanation").mkdir()
        (self.root / "README.md").write_text("# Guide\n\nStart with [matching](how-to/match.md).\n", encoding="utf-8")
        (self.root / "how-to" / "match.md").write_text(PAGE, encoding="utf-8")
        (self.root / "how-to" / "match" / "01.png").write_bytes(b"\x89PNG")
        (self.root / "explanation" / "reconciliation.md").write_text(
            "---\ntitle: Reconciliation\ntype: explanation\ndescription: Why.\n---\n\n# Reconciliation\n",
            encoding="utf-8",
        )

    def test_well_formed_pages_have_no_problems(self):
        pages = docs_check.load_pages(self.root)
        self.assertEqual([page.path for page in pages], ["README.md", "explanation/reconciliation.md", "how-to/match.md"])
        for page in pages:
            self.assertEqual(page.problems, [], page.path)
            self.assertEqual(docs_check.check_links(page, self.root), [])

    def test_the_landing_page_needs_no_front_matter_but_its_links_must_resolve(self):
        (self.root / "README.md").write_text("# Guide\n\nStart with [nothing](how-to/missing.md).\n", encoding="utf-8")
        findings = docs_check.check(self.root, self.root / "no-decisions")
        self.assertEqual(findings, [("docs/users/README.md", "link 'how-to/missing.md' does not exist")])

    def test_unknown_front_matter_keys_are_accepted(self):
        (self.root / "how-to" / "match.md").write_text(
            PAGE.replace("persona: accountant\n", "persona: accountant\napp: Accounting\nscreen: /odoo/accounting\nmodels:\n  - account.move\n"),
            encoding="utf-8",
        )
        page = next(page for page in docs_check.load_pages(self.root) if page.path == "how-to/match.md")
        self.assertEqual(page.problems, [])
        self.assertEqual(page.data["models"], ["account.move"])

    def test_a_page_in_the_wrong_directory_is_reported(self):
        (self.root / "explanation" / "wrong.md").write_text(
            "---\ntitle: Wrong\ntype: how-to\ndescription: D.\n---\n\n# Wrong\n", encoding="utf-8",
        )
        page = next(page for page in docs_check.load_pages(self.root) if page.path == "explanation/wrong.md")
        self.assertIn("type 'how-to' does not match the directory ('explanation')", page.problems)

    def test_dead_links_and_images_are_reported(self):
        (self.root / "how-to" / "match" / "01.png").unlink()
        (self.root / "explanation" / "reconciliation.md").unlink()
        page = next(page for page in docs_check.load_pages(self.root) if page.path == "how-to/match.md")
        problems = docs_check.check_links(page, self.root)
        self.assertIn("link '../explanation/reconciliation.md' does not exist", problems)
        self.assertIn("image 'match/01.png' does not exist", problems)
        self.assertEqual(docs_check.check_links(docs_check.Page("x.md", {}, "[a](/abs.md) [b](../../up.md)"), self.root), [
            "absolute link '/abs.md' (the viewer blanks it)",
            "link '../../up.md' escapes docs/users",
        ])

    def test_a_journey_claimed_twice_is_reported(self):
        claimed = PAGE.replace("generated: false\n", "generated: true\njourney: match\n")
        (self.root / "how-to" / "match.md").write_text(claimed, encoding="utf-8")
        (self.root / "how-to" / "again.md").write_text(claimed, encoding="utf-8")
        findings = docs_check.check(self.root, self.root / "no-decisions")
        self.assertEqual(findings, [("docs/users/how-to/match.md", "journey 'match' is also claimed by how-to/again.md")])

    def test_coverage_counts_test_backed_pages_by_type_and_skips_the_landing_page(self):
        (self.root / "how-to" / "broken.md").write_text("# No front matter\n", encoding="utf-8")
        coverage = docs_check.coverage(docs_check.load_pages(self.root))
        self.assertEqual(coverage, {"how-to": (0, 1), "explanation": (0, 1), "(untyped)": (0, 1)})
        # The report sorts its keys; an untyped page must not break that.
        self.assertEqual(sorted(coverage), ["(untyped)", "explanation", "how-to"])

    def test_llms_txt_follows_the_convention(self):
        decisions = [{"file": "0001-x.md", "number": 1, "title": "X", "date": "2026-09-08", "status": "accepted", "problems": []}]
        text = docs_check.render_llms_txt(docs_check.load_pages(self.root), decisions)
        self.assertTrue(text.startswith("# USL Odoo Distribution\n\n> "))
        self.assertIn("## How-to guides\n\n- [Match a bank transaction](docs/users/how-to/match.md): Match a line.", text)
        self.assertIn("## Decisions\n\n- [X](docs/product/decisions/0001-x.md): accepted, 2026-09-08", text)
        self.assertIn("## Optional\n\n- [Reconciliation](docs/users/explanation/reconciliation.md)", text)
        self.assertNotIn("README.md", text, "the landing page is not a guide entry")
        self.assertNotIn("## Decisions", docs_check.render_llms_txt(docs_check.load_pages(self.root)))


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
        decisions = docs_check.load_decisions(self.root)
        self.assertEqual([decision["title"] for decision in decisions], ["Use the thing", "Use the thing"])
        self.assertIn("decision numbers must be 0001, 0002, … without gaps or repeats", decisions[0]["problems"])
        self._write("0002-broken.md", "# 2. Broken\n\nStatus: maybe\n\n## Decision\n")
        broken = next(decision for decision in docs_check.load_decisions(self.root) if decision["file"] == "0002-broken.md")
        self.assertIn("a `Date: YYYY-MM-DD` line follows the title", broken["problems"])
        self.assertIn("a `Status:` line names one of proposed, accepted, deprecated, superseded", broken["problems"])
        self.assertIn("section '## Context' is missing", broken["problems"])

    def test_a_malformed_file_name_is_reported(self):
        self._write("use-the-thing.md", "# Use the thing\n\nDate: 2026-09-08\nStatus: accepted\n\n## Context\n\n## Decision\n\n## Consequences\n")
        decisions = docs_check.load_decisions(self.root)
        self.assertEqual(decisions[0]["problems"], ["file name must be NNNN-kebab-title.md"])


class RepositoryDocsTests(unittest.TestCase):
    def test_the_repository_documentation_passes_its_own_check(self):
        # Temporarily tolerates the content findings the lead is fixing; the
        # tooling must report nothing beyond them. Shrink the set as pages land.
        findings = set(docs_check.check())
        self.assertEqual(findings, set())

    def test_the_coverage_report_prints_for_the_repository(self):
        coverage = docs_check.coverage()
        self.assertIn("how-to", coverage)
        sorted(coverage)  # every key is a string


if __name__ == "__main__":
    unittest.main()
