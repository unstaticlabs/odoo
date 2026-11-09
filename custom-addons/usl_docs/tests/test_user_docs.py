import json
import os
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from odoo import Command
from odoo.exceptions import AccessError
from odoo.tests import HttpCase, TransactionCase, new_test_user, tagged

from ..tools import guide

COMMIT = "0801ff5b7cf6a1b2c3d4e5f60718293a4b5c6d7e"
PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000d49444154789c6360f8cfc00000030101c3ba0e9b0000000049454e44ae426082",
)


def _write_guide(root):
    """A small guide: a tested how-to with a screen, its French translation,
    a second how-to in another application, a hand-written explanation,
    an image and a journey sidecar."""
    root = Path(root)
    (root / "how-to").mkdir()
    (root / "how-to" / "example").mkdir()
    (root / "explanation").mkdir()
    (root / "README.md").write_text(
        "<!-- A comment that must not print. -->\n\n# Guide\n\nStart with [the example](how-to/example.md).\n",
        encoding="utf-8",
    )
    (root / "how-to" / "example.md").write_text(
        "---\n"
        "title: Do the example\n"
        "type: how-to\n"
        "description: An example journey.\n"
        "lang: en\n"
        "persona: accountant\n"
        "app: Expenses\n"
        "models:\n"
        "  - hr.expense\n"
        "actions:\n"
        "  - hr_expense.action_my_expenses\n"
        "screen: /odoo/expenses\n"
        "journey: usl_docs.example\n"
        "source:\n"
        "  - custom-addons/usl_docs/controllers/user_docs.py\n"
        "generated: true\n"
        "---\n\n"
        "# Do the example\n\n"
        "1. Open the screen.\n\n"
        "   ![The screen](example/01-open.png)\n\n"
        "## Afterwards\n\n"
        "See [the explanation](../explanation/why.md#reason).\n",
        encoding="utf-8",
    )
    (root / "how-to" / "exemple.md").write_text(
        "---\n"
        "title: Faire l'exemple\n"
        "type: how-to\n"
        "description: Un parcours d'exemple.\n"
        "lang: fr\n"
        "persona: accountant\n"
        "app: Expenses\n"
        "translation_of: how-to/example.md\n"
        "generated: false\n"
        "---\n\n"
        "# Faire l'exemple\n\nOuvrir l'écran.\n",
        encoding="utf-8",
    )
    (root / "how-to" / "other.md").write_text(
        "---\n"
        "title: Sign a document\n"
        "type: how-to\n"
        "description: Prepare a signature request.\n"
        "lang: en\n"
        "persona: employee\n"
        "app: Sign\n"
        "models:\n"
        "  - sign.request\n"
        "generated: false\n"
        "---\n\n"
        "# Sign a document\n\nPick the signers, then send the request.\n",
        encoding="utf-8",
    )
    (root / "how-to" / "example" / "01-open.png").write_bytes(PNG)
    (root / "how-to" / "example" / "journey.json").write_text(json.dumps({
        "schema": "usl-docs-journey-sidecar/v1",
        "journey": "usl_docs.example", "tour": "usl_docs_example", "type": "how-to", "title": "Do the example",
        "persona": "accountant", "lang": "en", "viewport": "desktop", "viewport_size": "1366x768", "login": "acc",
        "source_test": "odoo.addons.usl_docs.tests.test_x.TestX.test_y",
        "source_test_file": {"path": "custom-addons/usl_docs/tests/test_x.py", "line": 12},
        "source_tour_file": {"path": "custom-addons/usl_docs/static/tests/tours/x.js", "line": 3, "steps": {"open": 5}},
        "steps": [{"index": 1, "id": "open", "text": "Open **the screen**.", "assertion": "The screen is open",
                   "trigger": ".o_list_view", "run": "click", "screenshot": "01-open.png", "sha256": "ab" * 32}],
    }), encoding="utf-8")
    (root / "explanation" / "why.md").write_text(
        "---\n"
        "title: Pourquoi\n"
        "type: explanation\n"
        "description: Why it is so.\n"
        "lang: fr\n"
        "generated: false\n"
        "---\n\n"
        "# Pourquoi\n\n## Reason\n\nParce que.\n",
        encoding="utf-8",
    )
    return root


def _tested_evidence(finished):
    return {
        "schema": "usl-docs-evidence/v1",
        "qualified_commit": COMMIT,
        "workflow_run_id": 77,
        "run_url": "https://github.com/unstaticlabs/odoo/actions/runs/77",
        "chromium": "Chrome/152",
        "journeys": {
            "usl_docs.example": {
                "status": "success",
                "page": "how-to/example.md",
                "tour": "usl_docs_example",
                "source_test": "odoo.addons.usl_docs.tests.test_x.TestX.test_y",
                "started": "2026-09-08T10:00:00Z",
                "finished": finished,
                "viewports": ["desktop"],
                "screenshots": [
                    {"step": "open", "viewport": "desktop", "file": "01-open.png", "match": "exact"},
                ],
            },
        },
    }


class GuideFixture:
    def _mount_guide(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = _write_guide(self.tmp.name)
        patcher = patch.dict(os.environ, {guide.DOCS_ENV_VAR: str(self.root)})
        patcher.start()
        self.addCleanup(patcher.stop)
        guide._evidence_from_file.cache_clear()


@tagged("post_install", "-at_install", "usl_docs")
class TestUserDocsViewer(GuideFixture, HttpCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.reader = new_test_user(
            cls.env,
            login="usl-docs-reader",
            password="docs-reader",
            groups="base.group_user",
            company_id=cls.env.company.id,
            company_ids=[Command.set(cls.env.company.ids)],
        )
        cls.portal = new_test_user(
            cls.env,
            login="usl-docs-portal",
            password="docs-portal",
            groups="base.group_portal",
        )

    def setUp(self):
        super().setUp()
        self._mount_guide()

    def _get(self, path, **kwargs):
        kwargs.setdefault("allow_redirects", False)
        return self.url_open(guide.DOCS_ROUTE + path, **kwargs)

    def _reader(self):
        self.authenticate("usl-docs-reader", "docs-reader")

    def test_every_internal_user_can_read_the_guide(self):
        self._reader()
        response = self._get("")
        self.assertEqual(response.status_code, 200)
        self.assertIn("Start with", response.text)
        self.assertNotIn("A comment that must not print", response.text, "HTML comments must not print")
        self.assertNotIn("<style", response.text, "the page carries no inline style")
        self.assertIn("usl_docs.assets_guide", response.text + " ", "the stylesheet comes from the asset bundle")
        # The script is in <head>; deferred, it runs after the body it wires up is parsed.
        self.assertRegex(response.text, r'<script[^>]*\bdefer\b[^>]*usl_docs\.assets_guide', "the guide script loads deferred")
        self.assertLess(response.text.index("How-to guides"), response.text.index("Explanation"))
        self.assertIn("Do the example", response.text)
        self.assertIn('<span class="doc-lang">FR</span>', response.text)
        # How-to guides are grouped by application, alphabetically.
        self.assertLess(response.text.index('class="doc-app">Expenses'), response.text.index('class="doc-app">Sign'))

    def test_a_portal_user_is_told_nothing(self):
        self.authenticate("usl-docs-portal", "docs-portal")
        self.assertEqual(self._get("").status_code, 404)
        self.assertEqual(self._get("/index.json").status_code, 404)

    def test_a_page_carries_its_pills_sources_screen_and_footer(self):
        self._reader()
        response = self._get("/how-to/example.md")
        self.assertEqual(response.status_code, 200)
        self.assertIn('<html lang="en">', response.text)
        self.assertIn("How-to guide", response.text)
        self.assertIn("For accountants", response.text)
        self.assertIn("Unverified build", response.text)
        self.assertIn('href="/odoo/expenses"', response.text, "a page with a screen offers to open it")
        self.assertIn("Open this screen in Odoo", response.text)
        self.assertIn("generated from the journey", response.text)
        self.assertNotIn("title: Do the example", response.text, "front matter must not render as text")
        self.assertIn('src="/usl/user-docs/how-to/example/01-open.png"', response.text)
        self.assertIn('href="/usl/user-docs/explanation/why.md#reason"', response.text)
        # The French translation is one click away, and the way back too.
        self.assertIn('href="/usl/user-docs/how-to/exemple.md"', response.text)
        self.assertIn("Français", response.text)
        french = self._get("/how-to/exemple.md")
        self.assertIn('href="/usl/user-docs/how-to/example.md"', french.text)
        self.assertIn("English", french.text)

    def test_a_hand_written_page_says_so(self):
        self._reader()
        response = self._get("/explanation/why.md")
        self.assertIn('<html lang="fr">', response.text)
        self.assertIn("Not test-backed", response.text)
        self.assertIn("written by hand", response.text)

    def test_sources_link_to_the_running_release(self):
        self.env["ir.config_parameter"].sudo().set_str("usl.release.commit", COMMIT)
        self._reader()
        response = self._get("/how-to/example.md")
        self.assertIn(f"/blob/{COMMIT}/custom-addons/usl_docs/controllers/user_docs.py", response.text)

    def test_images_are_served_and_traversal_is_refused(self):
        self._reader()
        image = self._get("/how-to/example/01-open.png")
        self.assertEqual(image.status_code, 200)
        self.assertEqual(image.headers["Content-Type"], "image/png")
        self.assertEqual(image.content, PNG)
        self.assertEqual(self._get("/../../etc/passwd").status_code, 404)
        self.assertEqual(self._get("/how-to/../../README.md").status_code, 404)
        self.assertEqual(self._get("/how-to/missing.md").status_code, 404)

    def test_an_agent_gets_the_committed_markdown_and_the_indexes(self):
        self._reader()
        response = self._get("/how-to/example.md?format=md")
        self.assertEqual(response.headers["Content-Type"], "text/markdown; charset=utf-8")
        self.assertTrue(response.text.startswith("---\ntitle: Do the example\n"))
        response = self._get("/how-to/example.md", headers={"Accept": "text/markdown"})
        self.assertEqual(response.headers["Content-Type"], "text/markdown; charset=utf-8")
        index = self._get("/llms.txt")
        self.assertEqual(index.headers["Content-Type"], "text/markdown; charset=utf-8")
        self.assertTrue(index.text.startswith("# USL User Guide\n\n> "))
        self.assertIn("## How-to guides\n\n- [Do the example](http://", index.text)
        self.assertIn("/usl/user-docs/how-to/example.md?format=md): An example journey.", index.text)
        self.assertIn("## Optional\n\n- [Pourquoi](", index.text)
        full = self._get("/llms-full.txt")
        self.assertIn("<!-- http://", full.text)
        self.assertIn("Parce que.", full.text)
        self.assertIn("Pick the signers", full.text)

    def test_the_guide_is_data_too(self):
        self.env["ir.config_parameter"].sudo().set_str("usl.release.commit", COMMIT)
        self._reader()
        index = self._get("/index.json")
        self.assertEqual(index.headers["Content-Type"], "application/json; charset=utf-8")
        payload = index.json()
        self.assertEqual(payload["schema"], "usl-docs-index/v1")
        self.assertEqual(payload["release_commit"], COMMIT)
        example = next(page for page in payload["pages"] if page["path"] == "how-to/example.md")
        self.assertEqual(example["app"], "Expenses")
        self.assertEqual(example["models"], ["hr.expense"])
        self.assertTrue(example["screen_url"].endswith("/odoo/expenses"))
        self.assertEqual(example["evidence"]["kind"], "unverified")
        page = self._get("/how-to/example.md?format=json").json()
        self.assertEqual(page["schema"], "usl-docs-page/v1")
        self.assertTrue(page["markdown"].startswith("---\n"))
        self.assertEqual(page["headings"], [{"level": 2, "id": "afterwards", "title": "Afterwards"}])
        self.assertEqual(page["steps"][0]["id"], "open")
        self.assertTrue(page["steps"][0]["screenshot_url"].endswith("/how-to/example/01-open.png"))
        accept = self._get("/how-to/example.md", headers={"Accept": "application/json"})
        self.assertEqual(accept.headers["Content-Type"], "application/json; charset=utf-8")
        results = self._get("/search.json?q=signers").json()
        self.assertEqual([hit["path"] for hit in results["results"]], ["how-to/other.md"])
        self.assertIn("signers", results["results"][0]["snippet"])

    def test_the_help_menu_lands_on_the_page_about_the_screen(self):
        self._reader()
        by_model = self._get("?model=hr.expense")
        self.assertEqual(by_model.status_code, 303)
        self.assertTrue(by_model.headers["Location"].endswith("/usl/user-docs/how-to/example.md"))
        by_action = self._get("?action=hr_expense.action_my_expenses&model=res.partner")
        self.assertTrue(by_action.headers["Location"].endswith("/usl/user-docs/how-to/example.md"), "the action wins over the model")
        by_xmlid = self._get("?action=expenses&xmlid=hr_expense.action_my_expenses&model=res.partner")
        self.assertTrue(by_xmlid.headers["Location"].endswith("/usl/user-docs/how-to/example.md"), "the XML id matches when the path does not")
        by_other = self._get("?model=sign.request")
        self.assertTrue(by_other.headers["Location"].endswith("/usl/user-docs/how-to/other.md"))
        unknown = self._get("?model=res.partner&action=contacts.action_contacts")
        self.assertEqual(unknown.status_code, 200)
        self.assertIn("No guide page is written for this screen yet (contacts.action_contacts)", unknown.text)

    def test_the_evidence_pill_reads_the_shipped_evidence(self):
        finished = (datetime.now(timezone.utc) - timedelta(days=2, hours=1)).isoformat()
        evidence = Path(self.tmp.name) / "docs-evidence.json"
        evidence.write_text(
            '{"schema": "usl-docs-evidence/v1", "qualified_commit": "%s", '
            '"run_url": "https://github.com/unstaticlabs/odoo/actions/runs/1", '
            '"journeys": {"usl_docs.example": {"status": "success", "finished": "%s"}}}'
            % (COMMIT, finished),
            encoding="utf-8",
        )
        self.env["ir.config_parameter"].sudo().set_str("usl.release.commit", COMMIT)
        self._reader()
        with patch.dict(os.environ, {guide.EVIDENCE_ENV_VAR: str(evidence)}):
            response = self._get("/how-to/example.md")
        self.assertRegex(response.text, r"Last tested 2 days ago\s*</a>")
        self.assertIn('href="/usl/user-docs/evidence/usl_docs.example"', response.text)

    def test_the_evidence_stamped_into_the_database_is_read_and_has_a_test_record(self):
        """At deploy the release stamps usl.docs.evidence; the test record shows it."""
        finished = (datetime.now(timezone.utc) - timedelta(hours=5)).isoformat()
        evidence = _tested_evidence(finished)
        self.env["ir.config_parameter"].sudo().set_str(guide.EVIDENCE_PARAMETER, json.dumps(evidence))
        self.env["ir.config_parameter"].sudo().set_str("usl.release.commit", COMMIT)
        self._reader()
        page = self._get("/how-to/example.md")
        self.assertRegex(page.text, r"Last tested 5 hours ago\s*</a>")
        record = self._get("/evidence/usl_docs.example")
        self.assertEqual(record.status_code, 200)
        self.assertIn("Test record for “Do the example”", record.text)
        self.assertIn("GitHub run 77", record.text)
        self.assertIn(f"https://github.com/unstaticlabs/odoo/commit/{COMMIT}", record.text)
        self.assertIn(f"/blob/{COMMIT}/custom-addons/usl_docs/static/tests/tours/x.js#L5", record.text, "each step links to its line in the tour")
        self.assertIn(f"/blob/{COMMIT}/custom-addons/usl_docs/tests/test_x.py#L12", record.text)
        self.assertIn("trigger: .o_list_view", record.text)
        self.assertIn("Open <strong>the screen</strong>.", record.text, "step sentences render their Markdown")
        self.assertIn("identical to the published screen", record.text)
        self.assertIn('src="/usl/user-docs/how-to/example/01-open.png"', record.text)
        self.assertIn("make docs-journeys MODULE=usl_docs", record.text)
        self.assertIn('<dialog id="shot-box">', record.text)
        self.assertNotIn("proof", record.text.lower().replace("usl-docs", ""), "the word proof stays off the screen")
        as_json = self._get("/evidence/usl_docs.example?format=json").json()
        self.assertEqual(as_json["schema"], "usl-docs-test-record/v1")
        self.assertEqual(as_json["steps"][0]["id"], "open")
        self.assertEqual(self._get("/evidence/unknown").status_code, 404)
        # The deployment's injected environment outranks the database stamp.
        injected = dict(evidence, journeys={})
        with patch.dict(os.environ, {guide.EVIDENCE_JSON_ENV_VAR: json.dumps(injected)}):
            self._reader()
            self.assertIn("Not tested in this release", self._get("/how-to/example.md").text)
        self.authenticate("usl-docs-portal", "docs-portal")
        self.assertEqual(self._get("/evidence/usl_docs.example").status_code, 404)


@tagged("post_install", "-at_install", "usl_docs")
class TestGuideModel(GuideFixture, TransactionCase):
    """What an agent gets through the JSON-2 API."""

    def setUp(self):
        super().setUp()
        self._mount_guide()
        self.reader = new_test_user(self.env, login="usl-docs-model-reader", groups="base.group_user")
        self.portal = new_test_user(self.env, login="usl-docs-model-portal", groups="base.group_portal")

    def test_search_read_and_list(self):
        model = self.env["usl.docs.guide"].with_user(self.reader)
        hits = model.search_pages("example journey")
        self.assertEqual(hits[0]["path"], "how-to/example.md")
        self.assertEqual([hit["path"] for hit in model.search_pages("exemple", lang="fr")], ["how-to/exemple.md"])
        self.assertEqual(model.search_pages(""), [])
        page = model.read_page("how-to/other.md")
        self.assertEqual(page["schema"], "usl-docs-page/v1")
        self.assertEqual(page["title"], "Sign a document")
        self.assertIn("Pick the signers", page["markdown"])
        self.assertEqual(model.read_page("../../etc/passwd"), {})
        index = model.list_pages()
        self.assertEqual(index["schema"], "usl-docs-index/v1")
        self.assertEqual(len(index["pages"]), 4)

    def test_a_portal_user_gets_nothing(self):
        model = self.env["usl.docs.guide"].with_user(self.portal)
        with self.assertRaises(AccessError):
            model.search_pages("example")
        with self.assertRaises(AccessError):
            model.read_page("how-to/example.md")


@tagged("post_install", "-at_install", "usl_docs")
class TestGuideLibrary(TransactionCase):
    """The parts of the guide that are pure functions."""

    PAGE = {"type": "how-to", "journey": "usl_docs.example"}

    def test_states(self):
        now = datetime(2026, 9, 10, 12, 0, tzinfo=timezone.utc)
        tested = {
            "qualified_commit": COMMIT,
            "run_url": "https://example.test/run",
            "journeys": {"usl_docs.example": {"status": "success", "finished": "2026-09-08T11:00:00Z"}},
        }
        cases = [
            ({"type": "explanation"}, None, "not-test-backed"),
            ({"type": "reference", "generated": True}, None, "generated"),
            (self.PAGE, None, "unverified"),
            (self.PAGE, {"mode": "recovery"}, "recovery"),
            (self.PAGE, {"journeys": {}}, "untested-release"),
            (self.PAGE, tested, "tested"),
        ]
        for page, evidence, kind in cases:
            with self.subTest(kind=kind):
                state = guide.evidence_state(page, evidence, COMMIT, now)
                self.assertEqual(state["kind"], kind)
        state = guide.evidence_state(self.PAGE, tested, COMMIT, now)
        self.assertEqual(state["label"], "Last tested 2 days ago")
        self.assertEqual(state["run_url"], "https://example.test/run")
        self.assertEqual(state["detail"], "")
        other = guide.evidence_state(self.PAGE, tested, "f" * 40, now)
        self.assertIn("different commit", other["detail"])

    def test_relative_age_reads_like_a_person(self):
        now = datetime(2026, 9, 10, 12, 0, tzinfo=timezone.utc)
        for delta, expected in [
            (timedelta(seconds=30), "just now"),
            (timedelta(minutes=1), "a minute ago"),
            (timedelta(minutes=5), "5 minutes ago"),
            (timedelta(hours=1), "an hour ago"),
            (timedelta(hours=5), "5 hours ago"),
            (timedelta(days=1), "a day ago"),
            (timedelta(days=6), "6 days ago"),
            (timedelta(days=21), "3 weeks ago"),
            (timedelta(days=100), "3 months ago"),
            (timedelta(days=400), "a year ago"),
        ]:
            self.assertEqual(guide.relative_age(now - delta, now), expected)

    def test_navigation_groups_by_application_and_context_prefers_how_to(self):
        records = [
            {"path": "README.md", "type": "home", "title": "Guide", "description": "", "lang": "en", "app": None, "models": [], "actions": []},
            {"path": "how-to/b.md", "type": "how-to", "title": "B", "description": "", "lang": "en", "app": "Sign", "models": ["sign.request"], "actions": []},
            {"path": "how-to/a.md", "type": "how-to", "title": "A", "description": "", "lang": "fr", "app": None, "models": ["sign.request"], "actions": []},
            {"path": "explanation/e.md", "type": "explanation", "title": "E", "description": "", "lang": "en", "app": None, "models": ["sign.request"], "actions": ["x"]},
        ]
        nav = guide.navigation(records, "how-to/b.md")
        how_to = next(section for section in nav if section["key"] == "how-to")
        self.assertEqual([group["app"] for group in how_to["groups"]], ["Sign", "Everything else"])
        self.assertTrue(how_to["groups"][0]["pages"][0]["active"])
        explanation = next(section for section in nav if section["key"] == "explanation")
        self.assertEqual([group["app"] for group in explanation["groups"]], [None])
        self.assertEqual(guide.page_for_context(records, model="sign.request", lang="en")["path"], "how-to/b.md")
        self.assertEqual(guide.page_for_context(records, model="sign.request", lang="fr")["path"], "how-to/a.md")
        self.assertEqual(guide.page_for_context(records, action="x", model="sign.request")["path"], "explanation/e.md")
        self.assertIsNone(guide.page_for_context(records, model="res.partner"))
