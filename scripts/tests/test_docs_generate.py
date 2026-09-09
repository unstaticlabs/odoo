from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from operations import docs_evidence, docs_generate

PNG_A = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000d49444154789c6360f8cfc00000030101c3ba0e9b0000000049454e44ae426082",
)
PNG_B = PNG_A.replace(b"\x63\x60\xf8\xcf", b"\x63\x60\xf8\x0f")
COMMIT = "0801ff5b7cf6a1b2c3d4e5f60718293a4b5c6d7e"


def write_record(root, *, journey="match-a-line", viewport="desktop", png=PNG_A, page_type="how-to"):
    directory = Path(root) / "tour_match" / viewport
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "01-open.png").write_bytes(png)
    record = {
        "schema": docs_generate.RECORD_SCHEMA,
        "journey": journey,
        "tour": "tour_match",
        "type": page_type,
        "title": "Match a line",
        "description": "Match a bank line.",
        "persona": "accountant",
        "lang": "en",
        "viewport": viewport,
        "viewport_size": "1366x768",
        "source_test": "odoo.addons.usl_accounting.tests.test_journeys.TestJourneys.test_match",
        "login": "acc",
        "started": "2026-09-08T10:00:00Z",
        "finished": "2026-09-08T10:00:30Z",
        "status": "success",
        "git_commit": COMMIT,
        "chromium": "Chrome/152",
        "module_versions": {"usl_docs": "saas~19.3.1.0.0"},
        "steps": [
            {"index": 1, "id": "open", "text": "Open **Journals**. Then wait.", "assertion": "Journals open",
             "screenshot": {"file": "01-open.png", "sha256": hashlib.sha256(png).hexdigest(), "bytes": len(png)}},
            {"index": 2, "id": "pick", "text": "Pick a line.", "assertion": "", "screenshot": None},
        ],
    }
    (directory / "journey.json").write_text(json.dumps(record), encoding="utf-8")
    return record


class DocsGenerateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.records = Path(self.tmp.name) / "records"
        self.docs = Path(self.tmp.name) / "users"
        self.docs.mkdir()

    def test_a_record_renders_a_deterministic_page_and_its_images(self):
        write_record(self.records)
        written = docs_generate.render_all(self.records, self.docs)
        self.assertEqual(
            sorted(path.relative_to(self.docs).as_posix() for path in written),
            ["how-to/match-a-line.md", "how-to/match-a-line/01-open.png", "how-to/match-a-line/journey.json"],
        )
        sidecar = json.loads((self.docs / "how-to" / "match-a-line" / "journey.json").read_text(encoding="utf-8"))
        self.assertEqual(sidecar["schema"], "usl-docs-journey-sidecar/v1")
        self.assertEqual(sidecar["steps"][0]["screenshot"], "01-open.png")
        self.assertEqual(sidecar["steps"][0]["sha256"], hashlib.sha256(PNG_A).hexdigest())
        self.assertNotIn("started", sidecar)
        page = (self.docs / "how-to" / "match-a-line.md").read_text(encoding="utf-8")
        self.assertTrue(page.startswith("---\ntitle: Match a line\ntype: how-to\n"))
        self.assertIn("journey: match-a-line\n", page)
        self.assertIn("source:\n  - custom-addons/usl_accounting/tests/test_journeys.py\n", page)
        self.assertIn(f"screenshot_01-open_png: {hashlib.sha256(PNG_A).hexdigest()}\n", page)
        self.assertIn("generated: true\n", page)
        self.assertIn("1. Open **Journals**. Then wait.\n\n   ![Open Journals](match-a-line/01-open.png)\n\n2. Pick a line.\n", page)
        self.assertEqual(docs_generate.render_all(self.records, self.docs), [], "a second render changes nothing")
        self.assertEqual(docs_generate.check_all(self.records, self.docs), [])

    def test_a_tutorial_renders_at_the_root(self):
        write_record(self.records, journey="tour", page_type="tutorial")
        docs_generate.render_all(self.records, self.docs)
        page = (self.docs / "TUTORIAL.md").read_text(encoding="utf-8")
        self.assertIn("![Open Journals](tutorial/tour/01-open.png)", page)
        self.assertTrue((self.docs / "tutorial" / "tour" / "01-open.png").exists())

    def test_the_check_reports_a_stale_page_and_a_changed_screen(self):
        write_record(self.records)
        self.assertEqual(
            [problem for _path, problem in docs_generate.check_all(self.records, self.docs)],
            ["missing; run `make docs`"] * 3,
        )
        docs_generate.render_all(self.records, self.docs)
        write_record(self.records, png=PNG_B)
        findings = docs_generate.check_all(self.records, self.docs)
        self.assertEqual(len(findings), 3, findings)
        self.assertIn("differs from what the journey produces", findings[0][1])
        self.assertIn("the screen changed materially", findings[1][1])
        self.assertTrue(findings[2][0].endswith("journey.json"), "the sidecar carries the new digest too")

    def test_identical_bytes_never_count_as_different(self):
        self.assertFalse(docs_generate.materially_different(PNG_A, PNG_A))

    def test_a_journey_recorded_twice_is_refused(self):
        write_record(self.records)
        other = Path(self.tmp.name) / "records" / "tour_other" / "desktop"
        other.mkdir(parents=True)
        (other / "journey.json").write_text(
            (self.records / "tour_match" / "desktop" / "journey.json").read_text(encoding="utf-8"), encoding="utf-8",
        )
        with self.assertRaises(docs_generate.DocsGenerateError):
            docs_generate.load_records(self.records)


class DocsEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.records = Path(self.tmp.name) / "records"
        self.docs = Path(self.tmp.name) / "users"
        write_record(self.records)
        docs_generate.render_all(self.records, self.docs)

    def _build(self):
        return docs_evidence.build(
            self.records,
            repository="unstaticlabs/odoo",
            qualified_commit=COMMIT,
            workflow_run_id=42,
            run_url="https://github.com/unstaticlabs/odoo/actions/runs/42",
            committed_docs=self.docs,
        )

    def test_evidence_lists_every_journey_and_matches_committed_screens(self):
        payload = self._build()
        docs_evidence.validate(payload, qualified_commit=COMMIT, workflow_run_id=42)
        entry = payload["journeys"]["match-a-line"]
        self.assertEqual(entry["page"], "how-to/match-a-line.md")
        self.assertEqual(entry["viewports"], ["desktop"])
        self.assertEqual(entry["screenshots"][0]["match"], "exact")
        self.assertEqual(payload["mode"], "qualification")
        self.assertEqual(payload["chromium"], "Chrome/152")

    def test_a_tampered_body_or_wrong_run_is_refused(self):
        payload = self._build()
        with self.assertRaises(docs_evidence.DocsEvidenceError):
            docs_evidence.validate({**payload, "tested_at": "2020-01-01T00:00:00Z"})
        with self.assertRaises(docs_evidence.DocsEvidenceError):
            docs_evidence.validate(payload, workflow_run_id=43)
        with self.assertRaises(docs_evidence.DocsEvidenceError):
            docs_evidence.validate(payload, qualified_commit="f" * 40)

    def test_a_recovery_release_proves_nothing_and_says_so(self):
        payload = docs_evidence.recovery(repository="unstaticlabs/odoo", commit=COMMIT)
        docs_evidence.validate(payload)
        self.assertEqual(payload["mode"], "recovery")
        self.assertEqual(payload["journeys"], {})
        self.assertIsNone(payload["workflow_run_id"])

    def test_round_trip_through_a_file(self):
        path = docs_evidence.dump(self._build(), Path(self.tmp.name) / "e" / "docs-evidence.json")
        self.assertEqual(docs_evidence.load(path)["qualified_commit"], COMMIT)


if __name__ == "__main__":
    unittest.main()
