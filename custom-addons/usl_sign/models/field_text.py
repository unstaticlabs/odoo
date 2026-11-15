"""Text field layout for signed PDFs.

A text field is drawn inside the box the sender placed: lines wrap at the box
width, explicit line breaks are kept, the font shrinks until every line fits,
and the block is centred vertically. The value is drawn as plain text, never
interpreted as markup. Text that cannot fit even at the smallest size is
refused, so a signed document never silently loses part of what was typed.
"""

from io import BytesIO

from reportlab.pdfbase.pdfmetrics import getAscent, getDescent, stringWidth
from reportlab.pdfgen import canvas

from odoo.tools.pdf import PdfReader

FONT = "Helvetica"
MAX_FONT_SIZE = 11.0
MIN_FONT_SIZE = 4.0
FONT_STEP = 0.25
LINE_HEIGHT = 1.2
PADDING_X = 2.0
PADDING_Y = 1.0


class FieldTextOverflow(ValueError):
    """The value does not fit in its box even at the smallest font size."""


def _break_word(word, size, width):
    """Split one word that is wider than the box into chunks that fit."""
    chunks = []
    current = ""
    for character in word:
        if current and stringWidth(current + character, FONT, size) > width:
            chunks.append(current)
            current = character
        else:
            current += character
    if current:
        chunks.append(current)
    return chunks


def wrap_text(value, size, width):
    """Wrap ``value`` at ``width`` points, keeping explicit line breaks."""
    lines = []
    for paragraph in value.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        words = paragraph.split()
        if not words:
            lines.append("")
            continue
        current = ""
        for word in words:
            pieces = (
                _break_word(word, size, width)
                if stringWidth(word, FONT, size) > width
                else [word]
            )
            for piece in pieces:
                candidate = f"{current} {piece}" if current else piece
                if stringWidth(candidate, FONT, size) <= width:
                    current = candidate
                else:
                    lines.append(current)
                    current = piece
        lines.append(current)
    return lines


def layout_text(value, box_width, box_height):
    """Return ``(font_size, lines)`` for the largest size that fits the box."""
    inner_width = max(box_width - 2 * PADDING_X, 1.0)
    inner_height = max(box_height - 2 * PADDING_Y, 1.0)
    size = min(MAX_FONT_SIZE, inner_height / LINE_HEIGHT)
    size = max(round(size / FONT_STEP) * FONT_STEP, MIN_FONT_SIZE)
    while size >= MIN_FONT_SIZE - 1e-9:
        lines = wrap_text(value, size, inner_width)
        if len(lines) * size * LINE_HEIGHT <= inner_height + 1e-6:
            return size, lines
        size -= FONT_STEP
    msg = "The text does not fit in its field."
    raise FieldTextOverflow(msg)


def render_text_overlay(value, page_box, x, y, width, height):
    """Draw ``value`` in the box given in page percentages; return a PDF page.

    ``page_box`` is the source page's ``(left, bottom, right, top)`` media box,
    whose origin is not always ``(0, 0)``. ``x``/``y`` are the field's top-left
    corner in percent of that box, measured from its top-left, like every Sign
    field position. The returned page has the same media box, so it lines up
    whether it is merged onto the page or cropped by percentage.
    """
    left_edge, bottom_edge, right_edge, top_edge = (float(edge) for edge in page_box)
    page_width = right_edge - left_edge
    page_height = top_edge - bottom_edge
    box_width = width / 100 * page_width
    box_height = height / 100 * page_height
    size, lines = layout_text(value, box_width, box_height)
    leading = size * LINE_HEIGHT
    ascent = getAscent(FONT, size)
    descent = -getDescent(FONT, size)
    block_height = len(lines) * leading
    left = left_edge + x / 100 * page_width + PADDING_X
    box_top = top_edge - y / 100 * page_height
    # Centre the block; each line's glyphs sit centred in their own leading.
    top = box_top - (box_height - block_height) / 2
    glyph_offset = (leading - (ascent + descent)) / 2 + ascent
    packet = BytesIO()
    drawing = canvas.Canvas(packet, pagesize=(right_edge, top_edge))
    drawing.setFont(FONT, size)
    for index, line in enumerate(lines):
        if line:
            drawing.drawString(left, top - index * leading - glyph_offset, line)
    drawing.save()
    packet.seek(0)
    page = PdfReader(packet).pages[0]
    # Same rectangle class as the PDF library Odoo loaded (pypdf or PyPDF2).
    media_box = type(page.mediabox)([left_edge, bottom_edge, right_edge, top_edge])
    page.mediabox = media_box
    page.cropbox = media_box
    return page
