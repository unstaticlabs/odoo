"""Append a semantic no-op xref section after PDFBox signs a PDF."""

import sys

from pyhanko.pdf_utils import generic
from pyhanko.pdf_utils.incremental_writer import IncrementalPdfFileWriter


def main():
    if len(sys.argv) != 3:
        msg = "Input and output PDF paths are required."
        raise SystemExit(msg)
    with open(sys.argv[1], "rb") as source:
        writer = IncrementalPdfFileWriter(source, strict=True)
        # PDFBox's signature writer can omit the xref stream's own entry and
        # does not count xref-stream objects when allocating the next object.
        # Append an unreferenced padding object in a classic xref section, so
        # the next signature receives a genuinely unused object number. No
        # reachable document object or signed revision is changed.
        writer.stream_xrefs = False
        writer.add_object(generic.DictionaryObject())
        writer._prep_dom_for_writing = lambda: None
        with open(sys.argv[2], "wb") as output:
            writer.write(output)


if __name__ == "__main__":
    main()
