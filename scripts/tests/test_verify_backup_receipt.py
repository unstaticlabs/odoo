"""A deployed reconstruction stage must refuse an unusable rollback point."""

import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "migration" / "internal"))

from verify_backup_receipt import BackupError, verify  # noqa: E402

NOW = datetime(2026, 9, 6, 21, 0, tzinfo=timezone.utc)


def _write(directory, receipt, capture):
    path = Path(directory)
    (path / "receipt.json").write_text(json.dumps(receipt))
    (path / "capture.json").write_text(json.dumps(capture))
    return path / "receipt.json"


def _receipt(**overrides):
    return {"schema": "usl-backup-run/v2", "status": "qualified"} | overrides


def _capture(created=None, database="odoo_staging", **overrides):
    return {
        "schema": "usl-recovery-cohort/v2",
        "run_id": "intent-0856",
        "target": "staging",
        "created_at": (created or NOW - timedelta(minutes=5)).isoformat(),
        "databases": {
            "odoo": {"name": database, "bytes": 26158419},
            "paperless": {"name": "paperless_staging", "bytes": 435671},
        },
    } | overrides


class TestBackupReceipt(unittest.TestCase):
    def verify(self, receipt, capture, database="odoo_staging", max_age=120):
        with tempfile.TemporaryDirectory() as directory:
            path = _write(directory, receipt, capture)
            return verify(path, database, max_age, now=NOW)

    def test_a_qualified_current_backup_of_the_named_database_is_accepted(self):
        capture = self.verify(_receipt(), _capture())
        self.assertEqual(capture["run_id"], "intent-0856")

    def test_a_backup_that_did_not_qualify_is_refused(self):
        with self.assertRaisesRegex(BackupError, "status"):
            self.verify(_receipt(status="failed"), _capture())

    def test_a_receipt_of_another_schema_is_refused(self):
        with self.assertRaisesRegex(BackupError, "receipt"):
            self.verify(_receipt(schema="usl-disposable-recovery-proof/v2"), _capture())

    def test_a_backup_of_a_different_database_is_refused(self):
        """Production's backup must not authorise writing to staging."""
        with self.assertRaisesRegex(BackupError, "not 'odoo_staging'"):
            self.verify(_receipt(), _capture(database="odoo_production"))

    def test_a_stale_backup_is_refused(self):
        with self.assertRaisesRegex(BackupError, "older than"):
            self.verify(_receipt(), _capture(created=NOW - timedelta(hours=9)))

    def test_a_backup_dated_in_the_future_is_refused(self):
        with self.assertRaisesRegex(BackupError, "future"):
            self.verify(_receipt(), _capture(created=NOW + timedelta(hours=1)))

    def test_a_missing_capture_is_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "receipt.json"
            path.write_text(json.dumps(_receipt()))
            with self.assertRaisesRegex(BackupError, "cannot be read"):
                verify(path, "odoo_staging", 120, now=NOW)

    def test_a_missing_receipt_is_refused(self):
        with self.assertRaisesRegex(BackupError, "cannot be read"):
            verify(Path("/nonexistent/receipt.json"), "odoo_staging", 120, now=NOW)


if __name__ == "__main__":
    unittest.main()
