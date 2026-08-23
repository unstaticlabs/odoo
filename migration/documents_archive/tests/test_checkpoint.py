import importlib.util
import json
import stat
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
MODULE_PATH = ROOT / "migration" / "documents_archive" / "checkpoint.py"
SPEC = importlib.util.spec_from_file_location("documents_checkpoint", MODULE_PATH)
CHECKPOINT = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(CHECKPOINT)


class DocumentsCheckpointTest(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.input_path = self.root / "migration.py"
        self.input_path.write_text("version = 1\n", encoding="utf-8")
        self.inputs = ("migration.py",)
        self.source_sha256 = "a" * 64
        self.runtime = {"paperless": "3.0.4", "ocr_language": "fra+eng"}
        self.archive = {
            "document_count": 548,
            "roots_sha256": "b" * 64,
            "trash_count": 9,
        }

    def tearDown(self):
        self.temporary_directory.cleanup()

    def identity(self, runtime=None):
        return CHECKPOINT.reuse_identity(runtime or self.runtime)

    def provenance(self, source_sha256=None):
        return CHECKPOINT.current_provenance(
            self.root,
            source_sha256 or self.source_sha256,
            self.inputs,
        )

    def test_matching_checkpoint_is_reusable(self):
        identity = self.identity()
        provenance = self.provenance()
        payload = CHECKPOINT.checkpoint_payload(
            identity,
            provenance,
            self.archive,
        )

        self.assertEqual(
            CHECKPOINT.verify_payload(
                payload,
                identity,
                provenance,
                self.archive,
            ),
            [],
        )

    def test_source_dump_change_is_reconciled_incrementally(self):
        identity = self.identity()
        payload = CHECKPOINT.checkpoint_payload(
            identity,
            self.provenance(),
            self.archive,
        )

        self.assertEqual(
            CHECKPOINT.verify_payload(
                payload,
                identity,
                self.provenance("c" * 64),
                self.archive,
            ),
            ["source_dump_sha256"],
        )

    def test_transformation_change_is_reconciled_incrementally(self):
        identity = self.identity()
        payload = CHECKPOINT.checkpoint_payload(
            identity,
            self.provenance(),
            self.archive,
        )
        self.input_path.write_text("version = 2\n", encoding="utf-8")

        self.assertEqual(
            CHECKPOINT.verify_payload(
                payload,
                identity,
                self.provenance(),
                self.archive,
            ),
            ["transformation_sha256"],
        )

    def test_runtime_change_rejects_reuse(self):
        identity = self.identity()
        payload = CHECKPOINT.checkpoint_payload(
            identity,
            self.provenance(),
            self.archive,
        )

        with self.assertRaisesRegex(CHECKPOINT.CheckpointError, "runtime"):
            CHECKPOINT.verify_payload(
                payload,
                self.identity({**self.runtime, "paperless": "3.1.0"}),
                self.provenance(),
                self.archive,
            )

    def test_archive_drift_rejects_reuse(self):
        identity = self.identity()
        provenance = self.provenance()
        payload = CHECKPOINT.checkpoint_payload(
            identity,
            provenance,
            self.archive,
        )
        changed_archive = {**self.archive, "document_count": 549}

        with self.assertRaisesRegex(
            CHECKPOINT.CheckpointError,
            "archive.document_count",
        ):
            CHECKPOINT.verify_payload(
                payload,
                identity,
                provenance,
                changed_archive,
            )

    def test_checkpoint_write_is_atomic_private_json(self):
        path = self.root / "private" / "paperless.json"
        payload = CHECKPOINT.checkpoint_payload(
            self.identity(),
            self.provenance(),
            self.archive,
        )

        CHECKPOINT.write_checkpoint(path, payload)

        self.assertEqual(json.loads(path.read_text(encoding="utf-8")), payload)
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)


if __name__ == "__main__":
    unittest.main()
