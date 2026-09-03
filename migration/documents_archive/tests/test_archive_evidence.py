from __future__ import annotations

import importlib.util
import json
import stat
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
SPEC = importlib.util.spec_from_file_location(
    "archive_evidence", ROOT / "migration/documents_archive/archive_evidence.py"
)
assert SPEC and SPEC.loader
ARCHIVE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ARCHIVE)


class ArchiveEvidenceTest(unittest.TestCase):
    def test_seals_runtime_scoped_archive_identity(self):
        value = ARCHIVE.payload("a" * 64, {"paperless": "image"}, "b" * 64, 12, 2)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "archive.json"
            ARCHIVE.write(path, value)
            recorded = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        self.assertEqual(recorded, value)

    def test_rejects_invalid_counts_and_identities(self):
        with self.assertRaisesRegex(ARCHIVE.EvidenceError, "counts"):
            ARCHIVE.payload("a" * 64, {}, "b" * 64, 1, 2)
        with self.assertRaisesRegex(ARCHIVE.EvidenceError, "SHA-256"):
            ARCHIVE.payload("bad", {}, "b" * 64, 1, 0)


if __name__ == "__main__":
    unittest.main()
