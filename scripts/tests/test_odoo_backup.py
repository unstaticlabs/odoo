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

    def test_rejects_unknown_manifest_field(self) -> None:
        value = manifest()
        value["surprise"] = "ignored by a permissive parser"
        with self.assertRaisesRegex(odoo_backup.BackupError, "unexpected surprise"):
            odoo_backup.validate_manifest(value)

    def test_rejects_malformed_numeric_field_cleanly(self) -> None:
        value = manifest()
        value["database"]["dump_bytes"] = "42"
        with self.assertRaisesRegex(odoo_backup.BackupError, "database.dump_bytes"):
            odoo_backup.validate_manifest(value)

    def test_rejects_non_scalar_consistency_cleanly(self) -> None:
        value = manifest()
        value["consistency"] = ["live"]
        with self.assertRaisesRegex(odoo_backup.BackupError, "consistency"):
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


class DatabaseEvidenceTest(unittest.TestCase):
    def test_missing_required_tables_fail_clearly(self) -> None:
        cursor = mock.Mock()
        cursor.fetchone.return_value = (False,)
        with self.assertRaisesRegex(odoo_backup.BackupError, "missing required table"):
            odoo_backup.table_counts(cursor)

    def test_all_required_tables_are_counted(self) -> None:
        cursor = mock.Mock()
        cursor.fetchone.side_effect = [
            *((True,) for _table in odoo_backup.COUNT_TABLES),
            *((3,) for _table in odoo_backup.COUNT_TABLES),
        ]
        self.assertEqual(
            odoo_backup.table_counts(cursor),
            {table: 3 for table in odoo_backup.COUNT_TABLES},
        )


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

    def test_finalize_requires_successful_restore_receipt_before_restic(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with mock.patch.dict(os.environ, {"ODOO_BACKUP_STATE": directory}, clear=True):
                arguments = type("Arguments", (), {"snapshot": "a" * 64})()
                with self.assertRaisesRegex(odoo_backup.BackupError, "verification receipt"):
                    odoo_backup.finalize(arguments)


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


class OrchestrationPolicyTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.compose = (ROOT / "deploy/odoo-backup/compose.yaml").read_text(encoding="utf-8")
        cls.backup_wrapper = (ROOT / "scripts/odoo-backup").read_text(encoding="utf-8")
        cls.restore_wrapper = (ROOT / "scripts/odoo-restore").read_text(encoding="utf-8")
        cls.dockerfile = (ROOT / "docker/backup.Dockerfile").read_text(encoding="utf-8")
        cls.qualification = (ROOT / "deploy/odoo-backup/compose.qualification.yaml").read_text(encoding="utf-8")
        cls.r2_qualification = (ROOT / "deploy/odoo-backup/compose.r2-qualification.yaml").read_text(encoding="utf-8")

    def test_backup_runtime_inputs_are_digest_pinned(self) -> None:
        self.assertIn("postgres:16-bookworm@sha256:", self.dockerfile)
        self.assertIn("restic/restic:0.19.1@sha256:", self.dockerfile)

    def test_prepare_is_db_first_then_filestore(self) -> None:
        source = (ROOT / "scripts/odoo_backup.py").read_text(encoding="utf-8")
        self.assertLess(source.index('"pg_dump"'), source.index("shutil.copytree(source_root"))

    def test_only_prepare_mounts_production_data_and_network(self) -> None:
        services, resources = self.compose.split("\nvolumes:\n", 1)
        prepare, later = services.split("\n  push:\n", 1)
        self.assertIn("source-data:/source-data:ro", prepare)
        self.assertIn("production-db", prepare)
        self.assertNotIn("source-data:/source-data", later)
        self.assertNotIn("production-db", later)
        self.assertIn("internal: true", resources)
        self.assertNotIn("ports:", services)

    def test_restore_has_no_production_overwrite_command(self) -> None:
        self.assertIn("scripts/odoo-restore clone", self.restore_wrapper)
        self.assertIn("--confirm", self.restore_wrapper)
        self.assertNotIn("production restore", self.restore_wrapper.lower())
        self.assertNotIn("dropdb", self.restore_wrapper)

    def test_shared_volumes_require_backup_ownership(self) -> None:
        for wrapper in (self.backup_wrapper, self.restore_wrapper):
            self.assertIn("backup volume ownership check failed", wrapper)
            self.assertIn('com.unstaticlabs.owner', wrapper)

    def test_cleanup_preflights_every_volume_before_removal(self) -> None:
        preflight = self.restore_wrapper.index("# Validate the complete deletion set")
        down = self.restore_wrapper.index("compose down --remove-orphans", preflight)
        remove = self.restore_wrapper.index('docker volume rm "$volume"', down)
        self.assertLess(preflight, down)
        self.assertLess(down, remove)

    def test_create_runs_all_stages_in_order(self) -> None:
        preflight = self.backup_wrapper.index("run_tool preflight preflight")
        prepare = self.backup_wrapper.index("run_tool prepare prepare")
        push = self.backup_wrapper.index("run_tool push push", prepare)
        verify = self.backup_wrapper.index('odoo-backup\" verify', push)
        self.assertLess(preflight, prepare)
        self.assertLess(prepare, push)
        self.assertLess(push, verify)

    def test_komodo_services_have_zero_argument_stage_commands(self) -> None:
        for command in (
            '["preflight"]',
            '["prepare", "--mode", "live"]',
            '["push"]',
            '["restore-fetch"]',
            '["restore-reset-apply"]',
            '["verify"]',
            '["finalize"]',
        ):
            self.assertIn(f"command: {command}", self.compose)

    def test_scheduled_reset_is_confined_to_isolated_clone(self) -> None:
        source = (ROOT / "scripts/odoo_backup.py").read_text(encoding="utf-8")
        self.assertIn('os.environ.get("PGHOST") != "clone-db"', source)
        self.assertIn('!= "odoo_restore"', source)
        self.assertIn("USL_RESTORE_RESET_CONFIRMED", source)

    def test_local_qualification_overlay_is_opt_in(self) -> None:
        self.assertIn("ODOO_BACKUP_QUALIFICATION", self.backup_wrapper)
        self.assertNotIn("ODOO_BACKUP_COMPOSE_OVERRIDE", self.backup_wrapper)
        self.assertIn("qualification-restic:/restic", self.qualification)
        self.assertIn("ODOO_QUALIFICATION_SOURCE_IMAGE", self.qualification)

    def test_r2_qualification_cannot_inject_production_resources(self) -> None:
        self.assertIn("ODOO_QUALIFICATION_SOURCE_IMAGE", self.r2_qualification)
        self.assertNotIn("volumes:", self.r2_qualification)
        self.assertNotIn("networks:", self.r2_qualification)
        self.assertNotIn("source-data", self.r2_qualification)
        self.assertNotIn("production-db", self.r2_qualification)

    def test_cli_never_uses_latest_snapshot(self) -> None:
        self.assertNotRegex(self.backup_wrapper, r"(?:restore|verify)\s+latest")
        self.assertNotRegex(self.restore_wrapper, r"(?:restore|verify)\s+latest")

    def test_usl_documents_neutralization_removes_paperless_connectivity(self) -> None:
        sql = (ROOT / "custom-addons/usl_documents/data/neutralize.sql").read_text(encoding="utf-8")
        self.assertIn("usl_documents.paperless", sql)
        self.assertIn("usl_documents.sync", sql)
        self.assertIn("DELETE FROM ir_config_parameter", sql)

    def test_usl_accounting_neutralization_forces_demo_einvoice_transport(self) -> None:
        sql = (ROOT / "custom-addons/usl_accounting/data/neutralize.sql").read_text(encoding="utf-8")
        self.assertIn("account_peppol.edi.mode", sql)
        self.assertIn("'demo'", sql)
        self.assertIn("ON CONFLICT", sql)

    def test_restore_verifies_native_side_effect_boundaries(self) -> None:
        source = (ROOT / "scripts/odoo_backup.py").read_text(encoding="utf-8")
        for boundary in (
            "ir_act_server WHERE state='webhook'",
            'table_exists(cursor, "auth_oauth_provider")',
            'table_exists(cursor, "payment_provider")',
            "account_peppol.edi.mode",
        ):
            self.assertIn(boundary, source)


if __name__ == "__main__":
    unittest.main()
