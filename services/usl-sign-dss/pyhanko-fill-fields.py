"""Fill reserved PDF widget appearances with a strict incremental update."""

import base64
import json
import math
import sys
from io import BytesIO

from pyhanko.pdf_utils import generic
from pyhanko.pdf_utils.incremental_writer import IncrementalPdfFileWriter
from pyhanko.pdf_utils.reader import PdfFileReader


def _named_fields(writer):
    acroform = writer.root["/AcroForm"]
    pending = list(acroform["/Fields"])
    result = {}
    while pending:
        reference = pending.pop()
        field = reference.get_object()
        name = field.get("/T")
        if name:
            result[str(name)] = (reference, field)
        pending.extend(field.get("/Kids", ()))
    return result


def _number(row, key, *, positive=False):
    value = float(row[key])
    if not math.isfinite(value) or (positive and value <= 0) or abs(value) > 100_000:
        msg = "A reserved field geometry is invalid."
        raise ValueError(msg)
    return value


def _appearance(writer, row):
    """Draw the field's overlay page as vector content clipped to its box.

    The overlay keeps no background, so whatever the page shows under the
    field stays visible, and typed text stays text.
    """
    raw = base64.b64decode(row["document"], validate=True)
    if not raw.startswith(b"%PDF-") or len(raw) > 32_000_000:
        msg = "A reserved field appearance is invalid."
        raise ValueError(msg)
    overlay = PdfFileReader(BytesIO(raw), strict=False)
    if int(overlay.root["/Pages"]["/Count"]) != 1:
        msg = "A reserved field appearance must contain exactly one page."
        raise ValueError(msg)
    page_ref = writer.import_page_as_xobject(overlay, page_ix=0)
    origin_x = _number(row, "origin_x")
    origin_y = _number(row, "origin_y")
    box_width = _number(row, "box_width", positive=True)
    box_height = _number(row, "box_height", positive=True)
    scale_x = box_width / _number(row, "source_width", positive=True)
    scale_y = box_height / _number(row, "source_height", positive=True)
    content = (
        f"q {scale_x:.6f} 0 0 {scale_y:.6f} 0 0 cm "
        f"1 0 0 1 {-origin_x:.4f} {-origin_y:.4f} cm /Fm0 Do Q\n"
    ).encode()
    appearance = generic.StreamObject(
        {
            generic.pdf_name("/Type"): generic.pdf_name("/XObject"),
            generic.pdf_name("/Subtype"): generic.pdf_name("/Form"),
            generic.pdf_name("/BBox"): generic.ArrayObject(
                map(generic.FloatObject, (0, 0, box_width, box_height)),
            ),
            generic.pdf_name("/Resources"): generic.DictionaryObject(
                {
                    generic.pdf_name("/XObject"): generic.DictionaryObject(
                        {generic.pdf_name("/Fm0"): page_ref},
                    ),
                },
            ),
        },
        stream_data=content,
    )
    appearance.compress()
    return writer.add_object(appearance)


def main():
    if len(sys.argv) != 4:
        msg = "Input PDF, field JSON and output PDF paths are required."
        raise SystemExit(msg)
    with open(sys.argv[1], "rb") as source, open(sys.argv[2], encoding="utf-8") as payload:
        writer = IncrementalPdfFileWriter(source, strict=True)
        available = _named_fields(writer)
        rows = json.load(payload)["fields"]
        if not 1 <= len(rows) <= 200:
            msg = "Between 1 and 200 reserved fields are required."
            raise ValueError(msg)
        used = set()
        for row in rows:
            name = row["name"]
            if name in used or name not in available:
                msg = "A reserved signing field is missing or duplicated."
                raise ValueError(msg)
            used.add(name)
            reference, field = available[name]
            field[generic.pdf_name("/AP")] = generic.DictionaryObject(
                {generic.pdf_name("/N"): _appearance(writer, row)},
            )
            writer.mark_update(reference)
        with open(sys.argv[3], "wb") as output:
            writer.write(output)


if __name__ == "__main__":
    main()
