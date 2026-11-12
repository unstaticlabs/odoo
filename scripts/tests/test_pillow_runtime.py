"""Opt-in native Pillow codec proof; run in the built Distribution image.

USL_PILLOW_QA=1 python -m unittest scripts.tests.test_pillow_runtime -v
"""
from __future__ import annotations

import io
import os
import unittest

ODOO_ENV = globals().get("ODOO_ENV")


@unittest.skipUnless(os.environ.get("USL_PILLOW_QA") == "1", "native codec QA is opt-in")
class PillowRuntimeTests(unittest.TestCase):
    def test_valid_tiff_active_area_roundtrip(self):
        # Ordinary valid metadata from Pillow's upstream field-definition
        # regression; no malformed TIFF or memory-disclosure fixture.
        # https://github.com/python-pillow/Pillow/blob/13.0.0/Tests/test_file_libtiff.py
        from PIL import Image, TiffImagePlugin, TiffTags, features

        self.assertTrue(features.check("libtiff"))
        metadata = TiffImagePlugin.ImageFileDirectory_v2()
        metadata[50829] = (0, 0, 1, 1)  # Standard ActiveArea within a 1x1 image.
        metadata.tagtype[50829] = TiffTags.LONG
        output = io.BytesIO()
        Image.new("L", (1, 1), 17).save(
            output, format="TIFF", compression="tiff_lzw", tiffinfo=metadata,
        )
        output.seek(0)
        with Image.open(output) as image:
            image.load()
            self.assertEqual(image.tag_v2[50829], (0, 0, 1, 1))
            self.assertEqual(image.getpixel((0, 0)), 17)


@unittest.skipUnless(
    os.environ.get("USL_PILLOW_QA") == "1" and ODOO_ENV is not None,
    "native Odoo registry QA is opt-in",
)
class PillowOdooRuntimeTests(unittest.TestCase):
    def test_html_editor_reencodes_valid_compressed_tiff(self):
        from types import SimpleNamespace
        from unittest.mock import patch
        from PIL import Image, TiffImagePlugin, TiffTags

        metadata = TiffImagePlugin.ImageFileDirectory_v2()
        metadata[50829] = (0, 0, 1, 1)
        metadata.tagtype[50829] = TiffTags.LONG
        source = io.BytesIO()
        Image.new("L", (1, 1), 17).save(
            source, format="TIFF", compression="tiff_lzw", tiffinfo=metadata,
        )
        with patch(
            "odoo.addons.html_editor.models.ir_qweb_fields.requests.get",
            return_value=SimpleNamespace(content=source.getvalue()),
        ) as request:
            result = ODOO_ENV["ir.qweb.field.image"].load_remote_url(
                "https://fixture.invalid/synthetic.tiff",
            )
        request.assert_called_once()
        with Image.open(io.BytesIO(result)) as image:
            image.load()
            self.assertEqual(image.format, "TIFF")
            self.assertEqual(image.tag_v2[50829], (0, 0, 1, 1))
            self.assertEqual(image.getpixel((0, 0)), 17)

    def test_native_exif_orientation_and_image_pdf(self):
        from types import SimpleNamespace
        from PIL import Image
        from odoo.tools.image import image_process
        from odoo.tools import pdf

        source = io.BytesIO()
        exif = Image.Exif()
        exif[274] = 6
        Image.new("RGB", (8, 4), (24, 48, 96)).save(source, format="JPEG", exif=exif)
        converted = image_process(source.getvalue(), output_format="PNG")
        with Image.open(io.BytesIO(converted)) as image:
            self.assertEqual(image.size, (4, 8))
        attachment = SimpleNamespace(
            raw=converted, mimetype="image/png", _get_pdf_raw=lambda: None,
        )
        stream = pdf.to_pdf_stream(attachment)
        reader = pdf.PdfFileReader(stream)
        self.assertEqual(len(reader.pages), 1)


if __name__ == "__main__":
    result = unittest.main(argv=["pillow-runtime", "-v"], exit=False).result
    if not result.wasSuccessful():
        raise SystemExit(1)
