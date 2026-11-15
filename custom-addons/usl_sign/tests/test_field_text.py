import re
from io import BytesIO

from reportlab.pdfgen import canvas

from odoo.tests import TransactionCase, tagged
from odoo.tools.pdf import PdfReader

from odoo.addons.usl_sign.models.field_text import (
    FieldTextOverflow,
    layout_text,
    render_text_overlay,
    wrap_text,
)

A4 = (0.0, 0.0, 595.5, 842.25)
# A synthetic role line as long as the ones signers type in a narrow box.
TITLE = (
    "Example Company Ltd director, owner of the Sample Trading brand and director "
    "of the Placeholder Media company, acting as the licensee and contracting party,"
)


def _drawn_lines(page):
    """Return ``[(x, y, text), ...]`` for every string the overlay draws."""
    content = page.get_contents().get_data().decode("latin-1")
    return [
        (float(x), float(y), text)
        for x, y, text in re.findall(
            r"1 0 0 1 ([\d.]+) ([\d.]+) Tm \((.*?)\) Tj", content,
        )
    ]


@tagged("post_install", "-at_install")
class TestFieldText(TransactionCase):
    def test_long_text_wraps_inside_the_box_and_shrinks_to_fit(self):
        # A narrow box: 28 % × 5 % of an A4 page.
        size, lines = layout_text(TITLE, 0.28 * 595.5, 0.05 * 842.25)
        self.assertLess(size, 11)
        self.assertGreater(len(lines), 1)
        self.assertEqual(" ".join(lines), TITLE)
        self.assertLessEqual(len(lines) * size * 1.2, 0.05 * 842.25 - 2)

    def test_explicit_line_breaks_are_kept(self):
        self.assertEqual(
            wrap_text("First line\nSecond line\r\n\nFourth", 10, 500),
            ["First line", "Second line", "", "Fourth"],
        )

    def test_a_word_wider_than_the_box_is_broken(self):
        lines = wrap_text("x" * 200, 10, 100)
        self.assertGreater(len(lines), 1)
        self.assertEqual("".join(lines), "x" * 200)

    def test_text_that_cannot_fit_is_refused(self):
        with self.assertRaises(FieldTextOverflow):
            layout_text("word " * 500, 60, 12)

    def test_value_is_drawn_as_plain_text_not_markup(self):
        page = render_text_overlay("A & <b>B</b>", A4, 10, 10, 50, 5)
        drawn = " ".join(text for _x, _y, text in _drawn_lines(page))
        self.assertIn("<b>B</b>", drawn)

    def test_block_is_centred_in_the_box_on_an_offset_media_box(self):
        # Some generated PDFs start their media box at y = 7.83 pt, not 0.
        box = (0.0, 7.8299813, 595.5, 850.07996)
        page = render_text_overlay("Alex Example", box, 67.4, 45.2, 24, 4.5)
        self.assertEqual([float(edge) for edge in page.mediabox], list(box))
        [(x, baseline, text)] = _drawn_lines(page)
        self.assertEqual(text, "Alex Example")
        height = box[3] - box[1]
        box_top = box[3] - 0.452 * height
        box_bottom = box_top - 0.045 * height
        self.assertAlmostEqual(x, 0.674 * 595.5 + 2, places=2)
        # The baseline sits below the box centre by about a third of the
        # font size, and the glyphs stay inside the box.
        self.assertGreater(baseline, box_bottom + 2)
        self.assertLess(baseline, (box_top + box_bottom) / 2)

    def test_every_line_stays_inside_the_box(self):
        page = render_text_overlay(TITLE, A4, 67.5, 50.6, 28, 5)
        drawn = _drawn_lines(page)
        self.assertGreater(len(drawn), 1)
        box_top = A4[3] - 0.506 * A4[3]
        box_bottom = box_top - 0.05 * A4[3]
        for _x, baseline, _text in drawn:
            self.assertLess(baseline, box_top)
            self.assertGreater(baseline, box_bottom)

    def test_overlay_merges_onto_a_reportlab_page(self):
        packet = BytesIO()
        document = canvas.Canvas(packet, pagesize=A4[2:])
        document.drawString(10, 10, "base")
        document.save()
        packet.seek(0)
        base = PdfReader(packet).pages[0]
        base.merge_page(render_text_overlay("Merged", A4, 10, 10, 30, 5))
        self.assertIn("Merged", base.extract_text())
