import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "migration/documents_archive/evidence.py"


class DocumentsEvidenceTest(unittest.TestCase):
    def test_extracts_one_result_and_seals_digest(self):
        result = {
            "schema": "usl-documents-source-restore-result-v1",
            "source_snapshot": "source-example",
            "documents": [{"source_document_ids": [1]}],
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            log = root / "restore.log"
            output = root / "restore.json"
            log.write_text(
                "runtime line\nDOCUMENTS_SOURCE_RESTORE_RESULT="
                + json.dumps(result)
                + "\n",
                encoding="utf-8",
            )
            subprocess.run(
                [sys.executable, str(SCRIPT), str(log), str(output)],
                check=True,
                capture_output=True,
                text=True,
            )
            payload = output.read_bytes()
            self.assertEqual(json.loads(payload), result)
            self.assertEqual(
                output.with_suffix(".json.sha256").read_text().split()[0],
                hashlib.sha256(payload).hexdigest(),
            )

    def test_rejects_missing_or_ambiguous_results(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for content in ("no result\n", "\n".join([
                'DOCUMENTS_SOURCE_RESTORE_RESULT={"schema":"usl-documents-source-restore-result-v1"}',
                'DOCUMENTS_SOURCE_RESTORE_RESULT={"schema":"usl-documents-source-restore-result-v1"}',
            ])):
                log = root / "restore.log"
                log.write_text(content, encoding="utf-8")
                completed = subprocess.run(
                    [sys.executable, str(SCRIPT), str(log), str(root / "out.json")],
                    capture_output=True,
                    text=True,
                )
                self.assertNotEqual(completed.returncode, 0)

    def test_rejects_an_unknown_evidence_schema(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            log = root / "restore.log"
            log.write_text(
                'DOCUMENTS_SOURCE_RESTORE_RESULT={"schema":"unknown"}\n',
                encoding="utf-8",
            )
            completed = subprocess.run(
                [sys.executable, str(SCRIPT), str(log), str(root / "out.json")],
                capture_output=True,
                text=True,
            )
            self.assertNotEqual(completed.returncode, 0)
            self.assertFalse((root / "out.json").exists())


if __name__ == "__main__":
    unittest.main()
