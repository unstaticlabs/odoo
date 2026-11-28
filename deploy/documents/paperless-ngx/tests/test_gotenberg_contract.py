"""Check Paperless's Gotenberg request format with the shipped HTTP client."""

import json
import os
import tempfile
import unittest
from email import policy
from email.parser import BytesParser
from pathlib import Path
from unittest.mock import patch

import httpx
from gotenberg_client import GotenbergClient
from gotenberg_client.options import PdfAFormat

from paperless_usl_gotenberg import merge_email_pdfs
from paperless_usl_gotenberg import pdfa_format

RENDERER = "http://renderer.example.test"


class GotenbergContract(unittest.TestCase):
    def setUp(self):
        self.requests = []
        transport = patch.object(httpx.Client, "send", side_effect=self.capture)
        transport.start()
        self.addCleanup(transport.stop)
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.office = root / "document.rtf"
        self.office.write_text(r"{\rtf1 Synthetic Office fixture}")
        self.html = root / "index.html"
        self.html.write_text("<p>Synthetic message fixture</p>")
        self.pages = [root / "z-text.pdf", root / "a-html.pdf"]
        for path in self.pages:
            path.write_bytes(b"%PDF-1.7\n")

    def capture(self, request, **kwargs):
        headers = f"Content-Type: {request.headers['Content-Type']}\r\n\r\n".encode()
        parts = BytesParser(policy=policy.default).parsebytes(headers + request.read())
        fields = {}
        order = []
        for part in parts.iter_parts():
            name = part.get_param("name", header="Content-Disposition")
            filename = part.get_filename()
            if filename:
                order.append(filename)
            else:
                fields[name] = part.get_content()
        self.requests.append((request.url.path, fields, order))
        return httpx.Response(
            200,
            headers={"Content-Type": "application/pdf", "Content-Disposition": "attachment; filename=preview.pdf"},
            content=b"%PDF-1.7\n",
            request=request,
        )

    def render_office(self):
        with GotenbergClient(RENDERER) as client, client.libre_office.to_pdf() as route:
            route.update_indexes(update_indexes=False)
            route.pdf_format(pdfa_format(PdfAFormat.A2b, office=True)).convert(self.office).run()

    def test_existing_managed_renderer_uses_legacy_forms(self):
        with patch.dict(os.environ, {}, clear=True):
            self.render_office()
            merge_email_pdfs(RENDERER, 5, self.pages, PdfAFormat.A2b)
        office, merge = self.requests
        self.assertEqual(office, (
            "/forms/libreoffice/convert", {"updateIndexes": "false", "pdfa": "PDF/A-2b"}, ["document.rtf"],
        ))
        self.assertEqual(merge[:2], ("/forms/pdfengines/merge", {"pdfa": "PDF/A-2b"}))
        self.assert_merge_order(merge[2])

    def assert_merge_order(self, files):
        self.assertEqual(files, sorted(files))
        self.assertEqual([filename.partition("_")[2] for filename in files], ["z-text.pdf", "a-html.pdf"])

    def test_version_9_uses_native_office_and_ordered_email_stages(self):
        with patch.dict(os.environ, {"USL_PAPERLESS_GOTENBERG_IMAGE": "gotenberg/gotenberg:9.8"}, clear=True):
            self.render_office()
            with GotenbergClient(RENDERER) as client, client.chromium.html_to_pdf() as route:
                route.pdf_format(pdfa_format(PdfAFormat.A3b)).index(self.html).run()
            merge_email_pdfs(RENDERER, 5, self.pages, PdfAFormat.A3b)
        office, chromium, merge = self.requests
        remediation = {"op": "remediate", "profile": "PDF/A-3b"}
        self.assertEqual(office, (
            "/forms/libreoffice/convert", {"updateIndexes": "false", "nativePdfa": "PDF/A-2b"}, ["document.rtf"],
        ))
        self.assertEqual(chromium[0], "/forms/chromium/convert/html")
        self.assertEqual(json.loads(chromium[1]["stages"]), [remediation])
        self.assertNotIn("pdfa", chromium[1])
        self.assertEqual(merge[0], "/forms/pdf/pipe")
        self.assert_merge_order(merge[2])
        self.assertEqual(set(merge[1]), {"stages"})
        self.assertEqual(json.loads(merge[1]["stages"]), [{"op": "merge"}, remediation])

    def test_managed_version_9_without_archive_profile_only_merges(self):
        with patch.dict(os.environ, {"USL_PAPERLESS_GOTENBERG_API_MAJOR": "9"}, clear=True):
            merge_email_pdfs(RENDERER, 5, [self.pages[1]], None)
        self.assertEqual(len(self.requests), 1)
        path, form, files = self.requests[0]
        self.assertEqual(path, "/forms/pdf/pipe")
        self.assertEqual(json.loads(form["stages"]), [{"op": "merge"}])
        self.assertEqual(files, ["00000_a-html.pdf"])


if __name__ == "__main__":
    unittest.main()
