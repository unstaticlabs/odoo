import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SPEC = importlib.util.spec_from_file_location(
    "documents_release_bundle",
    ROOT / "migration/documents_archive/release_bundle.py",
)
bundle = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(bundle)


class DocumentsReleaseBundleTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name) / "release"
        self.root.mkdir(mode=0o700)
        for relative in bundle.REQUIRED_PATHS:
            path = self.root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            if relative.endswith(".json"):
                path.write_text('{"status":"passed"}\n', encoding="utf-8")
            else:
                path.write_text(
                    f"fixture:{relative}\nstatus=passed\n",
                    encoding="utf-8",
                )
            path.chmod(0o600)
        self.identity = Path(self.temporary.name) / "identity.json"

    def tearDown(self):
        self.temporary.cleanup()

    def write_identity(self, *, status="passed", **qualification):
        value = {
            "schema": "usl-documents-release-identity-v1",
            "release_id": "documents-2026-08-test",
            "qualification": {
                "status": status,
                "eligible_attachment_pending": 0,
                "eligible_attachment_unresolved": 0,
                "odoo_operations_failed": 0,
                "odoo_operations_pending": 0,
                "odoo_operations_processing": 0,
                "paperless_active_tasks": 0,
                "paperless_personal_profiles": 0,
                "permission_failures": 0,
                "unauthorized_results": 0,
                **qualification,
            },
        }
        self.identity.write_text(json.dumps(value), encoding="utf-8")

    def test_seal_verify_and_accept_complete_cohort(self):
        self.write_identity()
        sealed = bundle.seal(self.root, self.identity)

        verified = bundle.verify(self.root)
        accepted = bundle.accept(self.root)

        self.assertEqual(sealed["manifest_sha256"], verified["manifest_sha256"])
        self.assertEqual(accepted["release_id"], "documents-2026-08-test")
        self.assertEqual((self.root / "manifest.json").stat().st_mode & 0o777, 0o600)

    def test_changed_artifact_and_checksum_file_are_rejected(self):
        self.write_identity()
        bundle.seal(self.root, self.identity)
        (self.root / "paperless/paperless-data.tgz").write_text(
            "tampered",
            encoding="utf-8",
        )
        with self.assertRaisesRegex(bundle.BundleError, "artifacts differ"):
            bundle.verify(self.root)

    def test_partial_cohort_verifies_but_cannot_be_accepted(self):
        self.write_identity(
            status="partial",
            blockers=["eligible attachments remain pending"],
            eligible_attachment_pending=3,
        )
        bundle.seal(self.root, self.identity)

        bundle.verify(self.root)
        with self.assertRaisesRegex(bundle.BundleError, "not releasable"):
            bundle.accept(self.root)

    def test_nonzero_security_or_queue_counter_rejects_acceptance(self):
        self.write_identity(unauthorized_results=1)
        bundle.seal(self.root, self.identity)
        with self.assertRaisesRegex(bundle.BundleError, "nonzero"):
            bundle.accept(self.root)

    def test_missing_required_path_is_rejected(self):
        self.write_identity()
        (self.root / "mcp/image-identity.json").unlink()
        with self.assertRaisesRegex(bundle.BundleError, "required cohort artifacts"):
            bundle.seal(self.root, self.identity)

    def test_symlinks_and_secret_shaped_names_are_rejected(self):
        self.write_identity()
        target = self.root / "paperless/paperless.dump"
        target.unlink()
        target.symlink_to(self.root / "odoo/odoo.dump")
        with self.assertRaisesRegex(bundle.BundleError, "symlink"):
            bundle.seal(self.root, self.identity)

        target.unlink()
        target.write_text("fixture", encoding="utf-8")
        (self.root / "configuration/gemini-api-key.txt").write_text(
            "forbidden",
            encoding="utf-8",
        )
        with self.assertRaisesRegex(bundle.BundleError, "secret-shaped"):
            bundle.seal(self.root, self.identity)

    def test_failed_evidence_file_rejects_acceptance(self):
        self.write_identity()
        (self.root / "evidence/permission-evaluation.json").write_text(
            '{"status":"failed"}\n',
            encoding="utf-8",
        )
        bundle.seal(self.root, self.identity)
        with self.assertRaisesRegex(bundle.BundleError, "did not pass"):
            bundle.accept(self.root)

    def test_paperless_clone_sanitizer_bypasses_service_init(self):
        script = (ROOT / "migration/internal/documents-release").read_text(
            encoding="utf-8",
        )

        self.assertIn(
            'run --rm --no-deps -T --entrypoint python \\\n',
            script,
        )
        self.assertIn('paperless-webserver manage.py shell \\\n', script)

    def test_extracted_inventory_ends_with_a_real_newline(self):
        script = (ROOT / "migration/internal/documents-release").read_text(
            encoding="utf-8",
        )

        self.assertIn(
            'json.dumps(value, indent=2, sort_keys=True) + "\\n"',
            script,
        )
        self.assertNotIn(
            'json.dumps(value, indent=2, sort_keys=True) + "\\\\n"',
            script,
        )

    def test_restore_provisions_odoo_runtime_volume_ownership(self):
        script = (ROOT / "migration/internal/documents-release").read_text(
            encoding="utf-8",
        )

        self.assertIn("--entrypoint id odoo -u", script)
        self.assertIn("--entrypoint id odoo -g", script)
        self.assertIn("mkdir -p /target/filestore /target/sessions", script)
        self.assertIn(
            'chown "$uid:$gid" /target /target/filestore /target/sessions',
            script,
        )


if __name__ == "__main__":
    unittest.main()
