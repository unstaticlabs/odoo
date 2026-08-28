from __future__ import annotations

import copy
import importlib.util
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("odoo_backup", ROOT / "scripts/odoo_backup.py")
assert SPEC and SPEC.loader
odoo_backup = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(odoo_backup)


def manifest() -> dict:
    return {
        "schema": "usl-odoo-backup/v1",
        "backup_id": "20260828t120000z-a1b2c3d4",
        "created_at": "2026-08-28T12:00:00Z",
        "consistency": "live",
        "source": {
            "database": "odoo",
            "postgres_version": "PostgreSQL 16.10",
            "postgres_version_num": 160010,
            "pg_dump_version": "16.10",
            "git_sha": "a" * 40,
            "image_digest_reference": f"ghcr.io/unstaticlabs/usl-odoo@sha256:{'b' * 64}",
        },
        "tool": {"image_digest_reference": f"ghcr.io/unstaticlabs/usl-odoo-backup@sha256:{'c' * 64}"},
        "database": {
            "dump_file": "database.dump",
            "dump_bytes": 42,
            "dump_sha256": "d" * 64,
            "row_counts": {
                "res_users": 4,
                "res_company": 1,
                "ir_module_module": 850,
                "res_partner": 20,
                "account_move": 2,
                "ir_attachment": 1,
            },
        },
        "filestore": {
            "directory": "filestore",
            "file_count": 1,
            "total_bytes": 5,
            "stored_attachment_count": 1,
            "missing_attachment_count": 0,
        },
    }


class ManifestTest(unittest.TestCase):
    def test_accepts_complete_manifest(self) -> None:
        self.assertEqual(odoo_backup.validate_manifest(manifest())["schema"], "usl-odoo-backup/v1")

    def test_rejects_empty_meaningful_table(self) -> None:
        value = copy.deepcopy(manifest())
        value["database"]["row_counts"]["res_users"] = 0
        with self.assertRaisesRegex(odoo_backup.BackupError, "res_users"):
            odoo_backup.validate_manifest(value)

    def test_rejects_mutable_source_image(self) -> None:
        value = copy.deepcopy(manifest())
        value["source"]["image_digest_reference"] = "ghcr.io/unstaticlabs/usl-odoo:latest"
        with self.assertRaisesRegex(odoo_backup.BackupError, "immutable"):
            odoo_backup.validate_manifest(value)

    def test_rejects_missing_attachment(self) -> None:
        value = copy.deepcopy(manifest())
        value["filestore"]["missing_attachment_count"] = 1
        with self.assertRaisesRegex(odoo_backup.BackupError, "missing"):
            odoo_backup.validate_manifest(value)

    def test_rejects_wrong_postgres_major(self) -> None:
        value = copy.deepcopy(manifest())
        value["source"]["postgres_version_num"] = 170001
        with self.assertRaisesRegex(odoo_backup.BackupError, "major"):
            odoo_backup.validate_manifest(value)


class FilestoreTest(unittest.TestCase):
    def test_counts_and_resolves_attachment(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "ab").mkdir()
            (root / "ab" / "checksum").write_bytes(b"hello")
            self.assertEqual(
                odoo_backup.filestore_metadata(root, ["ab/checksum"]),
                {"file_count": 1, "total_bytes": 5, "stored_attachment_count": 1, "missing_attachment_count": 0},
            )

    def test_reports_absent_attachment(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            metadata = odoo_backup.filestore_metadata(Path(directory), ["ab/missing"])
            self.assertEqual(metadata["missing_attachment_count"], 1)

    def test_rejects_attachment_path_traversal(self) -> None:
        with self.assertRaisesRegex(odoo_backup.BackupError, "unsafe"):
            odoo_backup.safe_store_name("../production-secret")

    def test_rejects_filestore_symlink(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "target").write_text("x", encoding="utf-8")
            (root / "link").symlink_to("target")
            with self.assertRaisesRegex(odoo_backup.BackupError, "symlink"):
                odoo_backup.filestore_metadata(root, [])


class SecretBoundaryTest(unittest.TestCase):
    def test_requires_dedicated_production_repository(self) -> None:
        environment = {
            "RESTIC_REPOSITORY": "s3:https://example.invalid/usl-backups/odoo-qa/prod",
            "RESTIC_PASSWORD": "x" * 32,
            "AWS_ACCESS_KEY_ID": "key",
            "AWS_SECRET_ACCESS_KEY": "secret",
        }
        with mock.patch.dict(os.environ, environment, clear=True):
            with self.assertRaisesRegex(odoo_backup.BackupError, "dedicated"):
                odoo_backup.restic_environment()

    def test_missing_secret_fails_before_restic(self) -> None:
        environment = {"RESTIC_REPOSITORY": "s3:https://example.invalid/usl-backups/odoo-production/prod"}
        with mock.patch.dict(os.environ, environment, clear=True):
            with self.assertRaisesRegex(odoo_backup.BackupError, "RESTIC_PASSWORD"):
                odoo_backup.restic_environment()

    def test_reads_secret_file_without_exposing_binding(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            password = Path(directory) / "password"
            password.write_text("secret-value\n", encoding="utf-8")
            with mock.patch.dict(os.environ, {"RESTIC_PASSWORD_FILE": str(password)}, clear=True):
                self.assertEqual(odoo_backup.secret("RESTIC_PASSWORD"), "secret-value")


class IdentityTest(unittest.TestCase):
    def test_requires_full_snapshot_id(self) -> None:
        with self.assertRaisesRegex(odoo_backup.BackupError, "full 64"):
            odoo_backup.require_snapshot("latest")

    def test_accepts_qualification_repository(self) -> None:
        environment = {
            "RESTIC_REPOSITORY": "s3:https://example.invalid/usl-backups/odoo-production/qualification/branch",
            "RESTIC_PASSWORD": "x" * 32,
            "AWS_ACCESS_KEY_ID": "key",
            "AWS_SECRET_ACCESS_KEY": "secret",
        }
        with mock.patch.dict(os.environ, environment, clear=True):
            result = odoo_backup.restic_environment()
        self.assertEqual(result["RESTIC_PASSWORD"], "x" * 32)


if __name__ == "__main__":
    unittest.main()
