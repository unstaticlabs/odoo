"""Fill reserved PDF widget appearances with a strict incremental update."""

import base64
import json
import sys

from pyhanko.pdf_utils import generic
from pyhanko.pdf_utils.incremental_writer import IncrementalPdfFileWriter


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


def _appearance(writer, row):
    width = int(row["pixel_width"])
    height = int(row["pixel_height"])
    raw = base64.b64decode(row["rgb"], validate=True)
    if width <= 0 or height <= 0 or width * height > 4_000_000 or len(raw) != width * height * 3:
        msg = "A reserved field image is invalid."
        raise ValueError(msg)
    image = generic.StreamObject(
        {
            generic.pdf_name("/Type"): generic.pdf_name("/XObject"),
            generic.pdf_name("/Subtype"): generic.pdf_name("/Image"),
            generic.pdf_name("/Width"): generic.NumberObject(width),
            generic.pdf_name("/Height"): generic.NumberObject(height),
            generic.pdf_name("/ColorSpace"): generic.pdf_name("/DeviceRGB"),
            generic.pdf_name("/BitsPerComponent"): generic.NumberObject(8),
        },
        stream_data=raw,
    )
    image.compress()
    image_ref = writer.add_object(image)
    box_width = float(row["box_width"])
    box_height = float(row["box_height"])
    content = f"q {box_width:.4f} 0 0 {box_height:.4f} 0 0 cm /Im0 Do Q\n".encode()
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
                        {generic.pdf_name("/Im0"): image_ref},
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
