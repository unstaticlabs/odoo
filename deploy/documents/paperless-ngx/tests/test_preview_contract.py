import importlib.util
import tempfile
from pathlib import Path
from unittest import TestCase


MODULE_PATH = Path(__file__).parents[1] / "verify_preview_contract.py"
SPEC = importlib.util.spec_from_file_location("verify_preview_contract", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class TestPreviewContract(TestCase):
    def verify(self, source):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "views.py"
            path.write_text(source, encoding="utf-8")
            return MODULE.verify(path)

    def test_requires_request_on_every_call_site(self):
        source = """
def serve_file(*, request, doc) -> HttpResponse:
    return doc

def first(request, doc):
    return serve_file(request=request, doc=doc)

def second(request, doc):
    return serve_file(request=request, doc=doc)

def third(request, doc):
    return serve_file(request=request, doc=doc)
"""
        self.assertEqual(self.verify(source), (True, 3))

    def test_rejects_one_call_site_without_request(self):
        source = """
def serve_file(*, request, doc) -> HttpResponse:
    return doc

def first(request, doc):
    return serve_file(request=request, doc=doc)

def second(request, doc):
    return serve_file(request=request, doc=doc)

def third(request, doc):
    return serve_file(doc=doc)
"""
        with self.assertRaisesRegex(RuntimeError, "signature and call sites differ"):
            self.verify(source)
