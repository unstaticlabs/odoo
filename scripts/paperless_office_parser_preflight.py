"""Exercise Paperless's real Office parser against Tika and Gotenberg."""

import json
import tempfile
from pathlib import Path

from paperless.parsers.tika import TikaDocumentParser


MARKER = "USL Paperless Office parser compatibility probe"
RTF_PAYLOAD = (
    r"{\rtf1\ansi\deff0 {\fonttbl {\f0 Arial;}}"
    rf"\f0\fs24 {MARKER}}}"
).encode()

with tempfile.NamedTemporaryFile(suffix=".rtf") as source:
    source.write(RTF_PAYLOAD)
    source.flush()
    with TikaDocumentParser() as parser:
        parser.parse(Path(source.name), "text/rtf", produce_archive=False)
        extracted_text = parser.get_text()
        archive_path = parser.get_archive_path()
        if MARKER not in extracted_text:
            raise RuntimeError("Tika did not return the compatibility marker")
        if archive_path is None or not archive_path.is_file():
            raise RuntimeError("Gotenberg did not return an Office PDF rendition")
        if not archive_path.read_bytes().startswith(b"%PDF-"):
            raise RuntimeError("Gotenberg returned an invalid Office PDF rendition")

print(
    "PAPERLESS_OFFICE_PARSER_COMPATIBILITY="
    + json.dumps(
        {
            "gotenberg_pdf": True,
            "status": "passed",
            "tika_text": True,
        },
        sort_keys=True,
    ),
)
