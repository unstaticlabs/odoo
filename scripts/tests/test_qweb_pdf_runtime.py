"""Render offline through the built QWeb binary and inspect the PDF with Poppler.

    USL_QWEB_TEST_IMAGE=local-image python3 -m unittest scripts.tests.test_qweb_pdf_runtime
"""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


IMAGE = os.environ.get("USL_QWEB_TEST_IMAGE")
# A fictional 48 × 16 wordmark, drawn for this test.
LOGO = (
    "iVBORw0KGgoAAAANSUhEUgAAADAAAAAQCAIAAADfzGvmAAAAWElEQVR4nGPUyCpkGEyAiWGQASaGQQaYGAYZYEHj"
    "X5/ah6lIM7sIjywlAG7y4A0hJoZBHmWkhjDVwRAPoetkJWqSwnXQhRATwyADjKN12VCLMqaBdgA6AAD0Wgwxbf5vdQAAAABJRU5ErkJggg=="
)


@unittest.skipUnless(IMAGE, "set USL_QWEB_TEST_IMAGE to the built Odoo image")
class QWebPDFRuntimeTest(unittest.TestCase):
    def test_native_html_header_footer_logo_and_page_numbers(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            (path / "header.html").write_text(
                '<!DOCTYPE html><html><head><meta charset="utf-8"></head>'
                '<body style="font-size:10pt"><img src="data:image/png;base64,' + LOGO
                + '"> Atelier Démo</body></html>', encoding="utf-8",
            )
            # Odoo's web.minimal_layout also reads page/topage from the URL
            # supplied by Qt when it prints the header or footer.
            (path / "footer.html").write_text(
                """<meta charset="utf-8"><body onload="numberPages()"><span id="number"></span>
                <script>function numberPages() {
                  var numbers = {};
                  location.search.slice(1).split('&').forEach(function(item) {
                    var pair = item.split('=');
                    numbers[pair[0]] = pair[1];
                  });
                  document.getElementById('number').textContent =
                      'Page ' + numbers.page + ' / ' + numbers.topage;
                }</script></body>""", encoding="utf-8",
            )
            (path / "body.html").write_text(
                '<meta charset="utf-8"><p>Quantité facturée</p>'
                '<p style="page-break-before:always">Échéance en €</p>', encoding="utf-8",
            )
            for orientation in ("Portrait", "Landscape"):
                with self.subTest(orientation=orientation):
                    result = path / "result.pdf"
                    with result.open("wb") as pdf:
                        subprocess.run([
                            "docker", "run", "--rm", "--network=none", "--read-only",
                            "--tmpfs", "/tmp", "--env", "XDG_CACHE_HOME=/tmp/cache",
                            "--cap-drop=ALL", "--security-opt=no-new-privileges",
                            "--mount", f"type=bind,source={path},target=/fixture,readonly",
                            "--entrypoint", "wkhtmltopdf", IMAGE,
                            "--quiet", "--disable-local-file-access", "--encoding", "utf-8",
                            "--orientation", orientation, "--page-size", "A4",
                            "--margin-top", "24mm", "--margin-bottom", "20mm",
                            "--header-spacing", "4", "--header-html", "/fixture/header.html", "--footer-html", "/fixture/footer.html",
                            "/fixture/body.html", "-",
                        ], check=True, stdout=pdf)
                    pages = subprocess.check_output(
                        ["pdftotext", "-layout", str(result), "-"], text=True,
                    ).rstrip("\f").split("\f")
                    self.assertEqual(len(pages), 2)
                    for index, body in enumerate(("Quantité facturée", "Échéance en €"), start=1):
                        self.assertIn(body, pages[index - 1])
                        self.assertIn("Atelier Démo", pages[index - 1])
                        self.assertIn(f"Page {index} / 2", pages[index - 1])
                        images = subprocess.check_output(
                            ["pdfimages", "-f", str(index), "-l", str(index), "-list", str(result)],
                            text=True,
                        )
                        self.assertRegex(images, r"image\s+48\s+16\s+")
                    size = subprocess.check_output(["pdfinfo", str(result)], text=True)
                    expected = "595 x 842" if orientation == "Portrait" else "842 x 595"
                    self.assertRegex(size, rf"Page size:\s+{expected} pts")
