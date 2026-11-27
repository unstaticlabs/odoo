from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from lxml import html

from odoo.exceptions import AccessError
from odoo.tests import TransactionCase, new_test_user, tagged


@tagged("post_install", "-at_install")
class TestProjectNotes(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.member = new_test_user(cls.env, login="project-notes-member", groups="project.group_project_user")
        cls.project, cls.other = cls.env["project.project"].create([
            {"name": name, "privacy_visibility": "followers"} for name in ("Demo Studio", "Second Studio")
        ])
        cls.project.message_subscribe(partner_ids=cls.member.partner_id.ids)

    def setUp(self):
        super().setUp()
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.folder = self.root / "Projects" / str(self.project.id)
        self.folder.mkdir(parents=True)
        self.second = self.root / "Projects" / str(self.other.id)
        self.second.mkdir()
        self.reader = self.project.with_user(self.member)
        names = patch.dict("os.environ", {
            "USL_PROJECT_NOTES_VAULT": str(self.root), "USL_PROJECT_NOTES_NAME": "Studio Notes",
            "USL_PROJECT_NOTES_DATABASE": self.env.cr.dbname,
        })
        names.start()
        self.addCleanup(names.stop)

    def test_note_and_links_follow_the_source_without_copying_it(self):
        (self.folder / "README.md").write_text(
            "---\nodoo: demo\n---\n# Read this first\n\n[[Process/Review|Review checklist]] and [the plan](Plan.md).\n"
            f"[[Projects/{self.project.id}/Plan|Vault link]]\n\n"
            "\n`[[Plan]]` is inline code.\n\n![Example image](https://example.invalid/image.png)", encoding="utf-8")
        (self.folder / "Process").mkdir()
        (self.folder / "Process" / "Review.md").write_text("# Before publishing", encoding="utf-8")
        (self.folder / "Plan.md").write_text("First version", encoding="utf-8")
        result = self.reader.get_team_notes()
        self.assertEqual(result["path"], "README.md")
        rendered = html.fragment_fromstring(result["html"], create_parent=True)
        self.assertEqual(rendered.xpath(".//a/@data-project-note"), ["Process/Review.md", "Plan.md", "Plan.md"])
        self.assertEqual(rendered.xpath(".//code/text()"), ["[[Plan]]"])
        self.assertFalse(rendered.xpath(".//img"))
        self.assertNotIn("odoo: demo", result["html"])
        target = self.reader.get_team_notes("Process/Review.md")
        self.assertIn("Before publishing", target["html"])
        self.assertEqual(parse_qs(urlsplit(target["obsidian_url"]).query), {
            "vault": ["Studio Notes"], "file": [f"Projects/{self.project.id}/Process/Review.md"],
        })
        previous = self.reader.get_team_notes("Plan.md")
        (self.folder / "Plan.md").write_text("Revised version", encoding="utf-8")
        current = self.reader.get_team_notes("Plan.md")
        self.assertNotEqual(current["digest"], previous["digest"])
        self.assertIn("Revised version", current["html"])
        (self.folder / "Plan.md").unlink()
        self.assertEqual(self.reader.get_team_notes("Plan.md")["status"], "missing")

    def test_current_project_rights_govern_direct_note_reads(self):
        (self.folder / "Meeting.md").write_text("Demo agenda", encoding="utf-8")
        (self.second / "Roadmap.md").write_text("Separate project", encoding="utf-8")
        self.assertIn("Demo agenda", self.reader.get_team_notes("Meeting.md")["html"])
        self.assertEqual(self.reader.get_team_notes("Roadmap.md")["status"], "missing")
        with self.assertRaises(AccessError):
            self.other.with_user(self.member).get_team_notes("Roadmap.md")
        self.project.message_unsubscribe(partner_ids=self.member.partner_id.ids)
        with self.assertRaises(AccessError):
            self.reader.get_team_notes("Meeting.md")

    def test_ordinary_unavailable_and_empty_states(self):
        self.assertEqual(self.reader.get_team_notes(), {"status": "empty"})
        with patch.dict("os.environ", {"USL_PROJECT_NOTES_VAULT": ""}):
            self.assertEqual(self.reader.get_team_notes(), {"status": "not_configured"})
        with patch.dict("os.environ", {"USL_PROJECT_NOTES_DATABASE": "another_demo_database"}):
            self.assertEqual(self.reader.get_team_notes(), {"status": "not_configured"})
        with patch.dict("os.environ", {"USL_PROJECT_NOTES_VAULT": str(self.root / "missing")}):
            self.assertEqual(self.reader.get_team_notes(), {"status": "unavailable"})

    def test_ambiguous_note_names_remain_text_until_the_folder_is_named(self):
        for folder in ("Design", "Delivery"):
            (self.folder / folder).mkdir()
            (self.folder / folder / "Review.md").write_text("# Review", encoding="utf-8")
        (self.folder / "README.md").write_text(
            "[[Review|Which review?]] and [[Design/Review|Design review]].", encoding="utf-8",
        )
        result = html.fragment_fromstring(self.reader.get_team_notes()["html"], create_parent=True)
        self.assertEqual(result.xpath(".//a/@data-project-note"), ["Design/Review.md"])
        self.assertIn("Which review?", result.text_content())
