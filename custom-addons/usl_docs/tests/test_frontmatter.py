from odoo.tests import BaseCase, tagged

from ..tools import frontmatter


@tagged("post_install", "-at_install", "usl_docs")
class TestFrontMatter(BaseCase):
    """The viewer and the repository checks read exactly the same subset."""

    def test_a_page_without_a_block_is_left_alone(self):
        data, body = frontmatter.split("# Title\n\nBody\n")
        self.assertEqual(data, {})
        self.assertEqual(body, "# Title\n\nBody\n")

    def test_scalars_and_lists_round_trip(self):
        page = {
            "title": "Match: a bank transaction",
            "type": "how-to",
            "description": "Match a line.",
            "lang": "fr",
            "source": ["a/b.py", "c/d.js"],
            "generated": True,
        }
        data, body = frontmatter.split(frontmatter.dump(page) + "\n# Title\n")
        self.assertEqual(data, page)
        self.assertEqual(body, "# Title\n")

    def test_the_block_must_close_and_stay_flat(self):
        with self.assertRaises(frontmatter.FrontMatterError):
            frontmatter.split("---\ntitle: x\n")
        with self.assertRaises(frontmatter.FrontMatterError):
            frontmatter.split("---\nnested:\n  key: value\n---\n")
        with self.assertRaises(frontmatter.FrontMatterError):
            frontmatter.split("---\ntitle: x\ntitle: y\n---\n")

    def test_validation_ties_the_type_to_the_directory(self):
        data = {"title": "T", "type": "reference", "description": "D"}
        self.assertEqual(frontmatter.validate(data, "reference/x.md"), [])
        self.assertIn(
            "type 'reference' does not match the directory ('how-to')",
            frontmatter.validate(data, "how-to/x.md"),
        )
        self.assertEqual(frontmatter.validate({**data, "type": "tutorial"}, "TUTORIAL.md"), [])

    def test_a_generated_guide_names_its_journey(self):
        data = {"title": "T", "type": "how-to", "description": "D", "generated": True}
        self.assertIn("a generated tutorial or how-to names its journey", frontmatter.validate(data, "how-to/x.md"))
        self.assertEqual(frontmatter.validate({**data, "journey": "usl_docs.example"}, "how-to/x.md"), [])
