"""Build-time check of pyhanko-fill-fields.py: appearances stay vector and transparent.

Run with the service's pyHanko environment. It builds a page with a rule and a
reserved push-button field over it, fills the field with a one-page text
overlay, and checks that the filled appearance draws that page as vector
content, with no raster image that would paint over the rule.
"""

import base64
import json
import subprocess
import sys
import tempfile
from io import BytesIO
from pathlib import Path

from pyhanko.pdf_utils import generic
from pyhanko.pdf_utils.reader import PdfFileReader
from pyhanko.pdf_utils.writer import PdfFileWriter

FILL_SCRIPT = Path(sys.argv[1] if len(sys.argv) > 1 else "/app/pyhanko-fill-fields.py")
PAGE = (0, 0, 595, 842)
FIELD = (300, 600, 180, 24)  # x, y, width, height in page units


def _name(value):
    return generic.pdf_name(value)


def _page(writer, content, resources=None):
    stream = generic.StreamObject(stream_data=content)
    page = generic.DictionaryObject(
        {
            _name("/Type"): _name("/Page"),
            _name("/MediaBox"): generic.ArrayObject(map(generic.NumberObject, PAGE)),
            _name("/Contents"): writer.add_object(stream),
            _name("/Resources"): resources or generic.DictionaryObject(),
        },
    )
    writer.insert_page(page)
    return page


def _helvetica():
    return generic.DictionaryObject(
        {
            _name("/Font"): generic.DictionaryObject(
                {
                    _name("/F1"): generic.DictionaryObject(
                        {
                            _name("/Type"): _name("/Font"),
                            _name("/Subtype"): _name("/Type1"),
                            _name("/BaseFont"): _name("/Helvetica"),
                        },
                    ),
                },
            ),
        },
    )


def _serialize(writer):
    output = BytesIO()
    writer.write(output)
    return output.getvalue()


def base_document():
    """One page with a rule under the field and a reserved push button."""
    x, y, width, height = FIELD
    writer = PdfFileWriter()
    page = _page(writer, f"0 0 0 RG 1 w {x} {y + 6} m {x + width} {y + 6} l S\n".encode())
    widget = generic.DictionaryObject(
        {
            _name("/Type"): _name("/Annot"),
            _name("/Subtype"): _name("/Widget"),
            _name("/FT"): _name("/Btn"),
            _name("/Ff"): generic.NumberObject(65537),  # read-only push button
            _name("/T"): generic.TextStringObject("usl_sign_1"),
            _name("/Rect"): generic.ArrayObject(
                map(generic.NumberObject, (x, y, x + width, y + height)),
            ),
            _name("/F"): generic.NumberObject(4),
        },
    )
    widget_ref = writer.add_object(widget)
    page[_name("/Annots")] = generic.ArrayObject([widget_ref])
    writer.root[_name("/AcroForm")] = generic.DictionaryObject(
        {_name("/Fields"): generic.ArrayObject([widget_ref])},
    )
    return _serialize(writer)


def overlay_document():
    """The field's overlay: typed text only, no background."""
    x, y, _width, _height = FIELD
    writer = PdfFileWriter()
    _page(writer, f"BT /F1 11 Tf {x + 2} {y + 8} Td (1990-04-12) Tj ET\n".encode(), _helvetica())
    return _serialize(writer)


def main():
    x, y, width, height = FIELD
    with tempfile.TemporaryDirectory() as directory:
        directory = Path(directory)
        source = base_document()
        (directory / "input.pdf").write_bytes(source)
        payload = {
            "fields": [
                {
                    "name": "usl_sign_1",
                    "document": base64.b64encode(overlay_document()).decode(),
                    "origin_x": x,
                    "origin_y": y,
                    "source_width": width,
                    "source_height": height,
                    "box_width": width,
                    "box_height": height,
                },
            ],
        }
        (directory / "fields.json").write_text(json.dumps(payload), encoding="utf-8")
        subprocess.run(
            [
                sys.executable,
                str(FILL_SCRIPT),
                str(directory / "input.pdf"),
                str(directory / "fields.json"),
                str(directory / "output.pdf"),
            ],
            check=True,
        )
        result = (directory / "output.pdf").read_bytes()

    assert result.startswith(source), "the fill must be an incremental update"
    reader = PdfFileReader(BytesIO(result))
    field = reader.root["/AcroForm"]["/Fields"][0].get_object()
    appearance = field["/AP"]["/N"]
    assert [float(value) for value in appearance["/BBox"]] == [0, 0, width, height]
    xobjects = appearance["/Resources"]["/XObject"]
    assert list(xobjects) == ["/Fm0"], "the appearance draws exactly the overlay page"
    form = xobjects["/Fm0"]
    assert form["/Subtype"] == "/Form", "the overlay is drawn as vector content, not an image"
    assert b"(1990-04-12) Tj" in form.data, "typed text stays text"
    assert b"re f" not in form.data and b"/Im" not in form.data
    assert "/XObject" not in form["/Resources"], "no raster image paints over the page"
    assert f"1 0 0 1 {-x:.4f} {-y:.4f} cm /Fm0 Do".encode() in appearance.data
    print("pyhanko-fill-fields: vector, transparent appearance PASS")


if __name__ == "__main__":
    main()
