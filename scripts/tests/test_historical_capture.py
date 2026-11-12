"""Historical relocation never certifies current-runtime recoverability."""
import copy
import hashlib
import json
import unittest

from operations import cohort, historical_capture as h
from operations.stack import _digested_document
from scripts.tests.test_cohort import manifest


class HistoricalArchiveTests(unittest.TestCase):
    def setUp(self):
        self.tree = {"files": 12, "bytes": 1000, "sha256": "f" * 64}
        self.manifest = manifest(self.tree, self.tree)
        self.manifest.update(target="production", run_id="manual-old-capture")
        # This genuine historical shape predates current required key resources.
        for role in ("mcp_secrets", "renderer_secrets", "paperless_personal_ai_keys"):
            self.manifest["resources"].pop(role, None)
        self.raw = json.dumps(self.manifest)
        self.state = dict(schema=cohort.STATE_SCHEMA, target="production", run_id="manual-old-capture",
                          status="qualified", durable_snapshot_id="1" * 64, cache_snapshot_id="2" * 64)
        self.identity = h.capture_identity(self.raw, self.state, target="production", capture="manual-old-capture")
        self.archive = dict(schema=h.SCHEMA, target="production", capture="manual-old-capture",
                            repository="s3:https://example.invalid/existing-repository", snapshot_id="3" * 64,
                            tag=h.TAG, archive_path="/historical-capture", manifest_sha256=self.identity["manifest_sha256"],
                            source_tree=self.tree, restored_tree=self.tree, source_metadata_sha256="e" * 64,
                            restored_metadata_sha256="e" * 64, status="verified",
                            verified_at="2026-11-12T21:00:00Z")

    def validate(self, archive=None, tree=None):
        return h.validate_archive(_digested_document(archive or self.archive), target="production",
                                  capture="manual-old-capture", manifest_sha256=self.identity["manifest_sha256"],
                                  repository=self.archive["repository"], actual_tree=tree or self.tree,
                                  actual_metadata_sha256="e" * 64)

    def test_historical_preservation_keeps_modern_validation_strict(self):
        with self.assertRaisesRegex(cohort.CohortError, "complete MCP"):
            cohort.validate_manifest(self.manifest)
        self.assertEqual(self.validate()["status"], "verified")
        self.assertEqual(self.identity["manifest_sha256"], hashlib.sha256(self.raw.encode()).hexdigest())
        self.assertEqual(self.manifest["schema"], cohort.SCHEMA)
        self.assertNotIn("mcp_secrets", self.manifest["resources"])

    def test_archive_receipt_must_identify_exact_location_and_read_back_bytes(self):
        changes = dict(target="staging", capture="other-capture", repository="s3:https://other.invalid/repo",
                       snapshot_id="latest", status="uploaded", tag="usl-cohort", manifest_sha256="0" * 64,
                       restored_tree={**self.tree, "sha256": "0" * 64}, verified_at="2026-11-12",
                       restored_metadata_sha256="d" * 64)
        for key, value in changes.items():
            archive = {**self.archive, key: value}
            with self.subTest(key=key), self.assertRaises(h.HistoricalCaptureError):
                self.validate(archive)
        with self.assertRaises(h.HistoricalCaptureError):
            self.validate(tree={**self.tree, "bytes": self.tree["bytes"] + 1})

    def test_finished_historical_identity_cannot_adopt_unfinished_or_foreign_capture(self):
        for key, value in (("status", "uploaded"), ("target", "staging"), ("run_id", "another-capture")):
            with self.subTest(key=key), self.assertRaises(h.HistoricalCaptureError):
                h.capture_identity(self.raw, {**self.state, key: value}, target="production", capture="manual-old-capture")
        bad = copy.deepcopy(self.manifest)
        bad["schema"] = "unknown-cohort/v99"
        with self.assertRaises(h.HistoricalCaptureError):
            h.capture_identity(json.dumps(bad), self.state, target="production", capture="manual-old-capture")

    def test_native_cohort_retention_ignores_historical_archive_snapshots(self):
        import os
        from unittest import mock
        archive = dict(id="3" * 64, tags=[h.TAG, "cio-2374", "target-production"])
        environment = dict(USL_TARGET="production", RESTIC_REPOSITORY="/synthetic/durable",
                           USL_BACKUP_CACHE_REPOSITORY="/synthetic/cache",
                           RESTIC_PASSWORD="synthetic-password-1234567890",
                           USL_BACKUP_CACHE_PASSWORD="synthetic-password-1234567890")
        with mock.patch.dict(os.environ, environment), mock.patch.object(cohort, "_inventory", side_effect=[[archive], []]):
            plan = cohort.plan_retention()
        self.assertEqual(plan["delete_durable"], [])
        self.assertEqual(plan["delete_cache"], [])

    @unittest.skipUnless(hasattr(__import__("os"), "listxattr"), "Linux native metadata API")
    def test_metadata_fingerprint_detects_permissions_and_timestamp_changes(self):
        import os
        from pathlib import Path
        import tempfile
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / "synthetic"
            path.write_text("synthetic")
            original = h.metadata_identity(root)
            path.chmod(0o400)
            self.assertNotEqual(h.metadata_identity(root), original)
            original = h.metadata_identity(root)
            item = path.stat()
            os.utime(path, ns=(item.st_atime_ns, item.st_mtime_ns + 1_000_000_000))
            self.assertNotEqual(h.metadata_identity(root), original)

    @unittest.skipUnless(__import__("shutil").which("restic"), "native backup image provides Restic")
    def test_actual_restic_archive_restore_retention_restore(self):
        import os
        from pathlib import Path
        import shutil
        import tempfile
        from unittest import mock
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            source.mkdir(mode=0o700)
            (source / "manifest.json").write_text(self.raw)
            (source / "state.json").write_text(json.dumps(self.state))
            (source / "synthetic-payload").write_bytes(b"historical synthetic bytes" * 20000)
            (source / "synthetic-payload").chmod(0o600)
            self.assertEqual(os.geteuid(), 0, "run this native capability fixture as the root operator")
            os.chown(source / "synthetic-payload", 999, 999)
            self.assertEqual((source / "synthetic-payload").stat().st_uid, 999)
            tree, metadata = cohort.tree_identity(source), h.metadata_identity(source)
            repository = str(root / "encrypted-restic-repository")
            environment = dict(RESTIC_REPOSITORY=repository, RESTIC_PASSWORD="synthetic-password-for-archive-only",
                               RESTIC_CACHE_DIR=str(root / "cache"))
            with mock.patch.dict(os.environ, environment):
                receipt_path = root / "receipts/archive.json"
                first = h.archive_capture(source, root / "scratch", receipt_path,
                                          target="production", capture="manual-old-capture", repository=repository)
                second = h.archive_capture(source, root / "scratch", receipt_path,
                                           target="production", capture="manual-old-capture", repository=repository)
                self.assertEqual(first["snapshot_id"], second["snapshot_id"])
                self.assertEqual(h.verify_remote_archive(first)["status"], "present")
                native_environment = cohort.restic_environment("RESTIC_REPOSITORY", "RESTIC_PASSWORD")
                cohort.retry_restic(["restic", "tag", first["snapshot_id"], "--add", "synthetic-reviewed"], native_environment)
                current = h.archive_capture(source, root / "scratch", receipt_path,
                                            target="production", capture="manual-old-capture", repository=repository)
                self.assertNotEqual(current["snapshot_id"], first["snapshot_id"])
                self.assertEqual(h.verify_remote_archive(current)["status"], "present")
                # Actual scoped Restic pruning must not destroy the archive's
                # still-referenced packs, even when another snapshot is removed.
                obsolete = root / "synthetic-obsolete"
                obsolete.write_text("disposable")
                native_environment = cohort.restic_environment("RESTIC_REPOSITORY", "RESTIC_PASSWORD")
                extra = cohort.restic_backup([obsolete], native_environment, ["synthetic-disposable"])
                cohort.retry_restic(["restic", "forget", "--prune", extra], native_environment, attempts=1)
                shutil.rmtree(source)
                cohort.restore_snapshot(current["snapshot_id"], root / "restore-after-prune", native_environment)
                restored = root / "restore-after-prune" / source.relative_to("/")
                self.assertEqual(cohort.tree_identity(restored), tree)
                self.assertEqual(h.metadata_identity(restored), metadata)
                self.assertEqual(json.loads(receipt_path.read_text())["status"], "verified")

    def test_missing_or_changed_remote_archive_anchor_is_not_preservation(self):
        import os
        import subprocess
        from unittest import mock
        good = dict(id=self.archive["snapshot_id"], tags=[h.TAG], paths=[self.archive["archive_path"]])
        environment = dict(RESTIC_REPOSITORY=self.archive["repository"], RESTIC_PASSWORD="synthetic-password-1234567890")
        cases = [[], [{**good, "id": "4" * 64}], [{**good, "tags": []}], [{**good, "paths": ["/other-path"]}]]
        with mock.patch.dict(os.environ, environment):
            for snapshots in cases:
                with self.subTest(snapshots=snapshots), mock.patch.object(cohort, "retry_restic", return_value=subprocess.CompletedProcess([], 0, json.dumps(snapshots), "")):
                    with self.assertRaises(h.HistoricalCaptureError):
                        h.verify_remote_archive(self.archive)
            with mock.patch.object(cohort, "retry_restic", return_value=subprocess.CompletedProcess([], 0, json.dumps([good]), "")):
                self.assertEqual(h.verify_remote_archive(self.archive)["status"], "present")

    def test_receipt_digest_detects_changes_without_repairing_the_receipt(self):
        receipt = _digested_document(self.archive)
        receipt["snapshot_id"] = "4" * 64
        with self.assertRaises(h.HistoricalCaptureError):
            h.validate_archive(receipt, target="production", capture="manual-old-capture",
                               manifest_sha256=self.identity["manifest_sha256"],
                               repository=self.archive["repository"], actual_tree=self.tree,
                               actual_metadata_sha256="e" * 64)


if __name__ == "__main__":
    unittest.main()
