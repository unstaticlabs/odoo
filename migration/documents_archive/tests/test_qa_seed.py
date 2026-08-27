import hashlib
import importlib.util
import json
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SPEC = importlib.util.spec_from_file_location("qa_seed", ROOT / "scripts/qa_seed.py")
qa_seed = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(qa_seed)


class QaSeedManifestTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name) / "root"
        self.seed = Path(self.temporary.name) / "seed"
        self.source = Path(self.temporary.name) / "source"
        self.root.mkdir()
        self.seed.mkdir()
        self.source.mkdir()
        for relative in set(qa_seed.MIGRATION_INPUTS + qa_seed.QA_STATE_INPUTS):
            path = self.root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            if Path(relative).suffix:
                path.write_text(f"input:{relative}\n", encoding="utf-8")
            else:
                path.mkdir(parents=True, exist_ok=True)
                (path / "input.txt").write_text(f"input:{relative}\n", encoding="utf-8")
        self.dump = self.source / "dump.sql"
        self.dump.write_text("source database", encoding="utf-8")
        (self.source / "filestore").mkdir()
        (self.source / "filestore/document").write_text("original", encoding="utf-8")
        (self.seed / "odoo.dump").write_bytes(b"odoo")
        (self.seed / "odoo-filestore.tgz").write_bytes(b"filestore")
        (self.seed / "paperless-export").mkdir()
        (self.seed / "paperless-export/manifest.json").write_text("{}", encoding="utf-8")
        self.collaboration = self.seed / "collaboration-disposition.json"
        self.collaboration.write_text('{"sealed": true}\n', encoding="utf-8")
        self.runtime = self.seed / "runtime.json"
        self.runtime.write_text('{"images":{"paperless":"qualified"}}', encoding="utf-8")
        self.qualification = Path(self.temporary.name) / "qualification.json"
        collaboration_sha256 = hashlib.sha256(self.collaboration.read_bytes()).hexdigest()
        self.qualification.write_text(
            json.dumps({
                "accounting": {
                    "controls": {"move_count": 1},
                    "performance": {
                        "schema": "timing-v1",
                        "stages": [{"name": "moves", "duration_seconds": 1}],
                    },
                    "status": "passed",
                },
                "documents": {
                    "controls": {"odoo_document_count": 1},
                    "paperless_document_count": 1,
                    "status": "passed",
                },
                "collaboration": {
                    "deliberately_not_copied_message_count": 620,
                    "evidence_sha256": collaboration_sha256,
                    "external_message_count": 0,
                    "status": "passed",
                    "visible_message_count": 49385,
                },
                "migration_boundary": "passed",
                "product_database_boundary": "passed",
                "profile": "full",
                "regulatory_live_guards": "disabled",
                "module_versions": {"usl_accounting": "19.0.1.0.0"},
                "status": "passed",
            }),
            encoding="utf-8",
        )

    def tearDown(self):
        self.temporary.cleanup()

    def args(self):
        return Namespace(
            commit="abc123",
            qualification_json=self.qualification,
            root=self.root,
            runtime_json=self.runtime,
            seed_dir=self.seed,
            source_dump=self.dump,
        )

    def test_sealed_seed_verifies_and_is_private(self):
        qa_seed.seal(self.args())
        manifest = qa_seed.verify(self.args())

        self.assertEqual(manifest["qualification"]["status"], "passed")
        self.assertEqual(qa_seed.SCHEMA, "usl-qa-reconstruction-seed-v4")
        self.assertIn("source_filestore_sha256", manifest["identity"])
        self.assertEqual((self.seed / "manifest.json").stat().st_mode & 0o777, 0o600)

    def test_changed_source_filestore_is_rejected(self):
        qa_seed.seal(self.args())
        (self.source / "filestore/document").write_text("changed", encoding="utf-8")

        with self.assertRaisesRegex(qa_seed.SeedError, "source_filestore_sha256"):
            qa_seed.verify(self.args())

    def test_non_runtime_product_files_do_not_invalidate_seed(self):
        before = qa_seed.migration_digest(self.root)
        state_before = qa_seed.qa_state_digest(self.root)
        ignored = self.root / "custom-addons" / "module" / "views" / "form.xml"
        ignored.parent.mkdir(parents=True)
        ignored.write_text("<form/>", encoding="utf-8")
        (self.root / "migration" / "notes.md").write_text("docs", encoding="utf-8")

        self.assertEqual(qa_seed.migration_digest(self.root), before)
        self.assertNotEqual(qa_seed.qa_state_digest(self.root), state_before)

    def test_runtime_migration_file_invalidates_seed(self):
        before = qa_seed.migration_digest(self.root)
        runtime = self.root / "migration" / "scope" / "models" / "restore.py"
        runtime.parent.mkdir(parents=True)
        runtime.write_text("value = 1\n", encoding="utf-8")

        self.assertNotEqual(qa_seed.migration_digest(self.root), before)

    def test_changed_artifact_is_rejected(self):
        qa_seed.seal(self.args())
        (self.seed / "odoo.dump").write_bytes(b"tampered")

        with self.assertRaisesRegex(qa_seed.SeedError, "seed artifacts differ"):
            qa_seed.verify(self.args())

    def test_changed_collaboration_evidence_is_rejected(self):
        qa_seed.seal(self.args())
        self.collaboration.write_text('{"sealed": false}\n', encoding="utf-8")

        with self.assertRaisesRegex(qa_seed.SeedError, "seed artifacts differ"):
            qa_seed.verify(self.args())

    def test_changed_runtime_is_rejected(self):
        qa_seed.seal(self.args())
        self.runtime.write_text('{"images":{"paperless":"new"}}', encoding="utf-8")

        with self.assertRaisesRegex(qa_seed.SeedError, "runtime"):
            qa_seed.verify(self.args())

    def test_compatible_seed_is_reusable_from_another_commit(self):
        qa_seed.seal(self.args())
        arguments = self.args()
        arguments.commit = "different"

        manifest = qa_seed.verify(arguments)

        self.assertEqual(manifest["created_from_commit"], "abc123")

    def test_runtime_uses_resolved_compose_images(self):
        config = {
            "services": {
                name: {"image": f"registry/{name}:1"}
                for name in (
                    "db", "odoo", "paperless-db", "paperless-webserver",
                    "paperless-gotenberg", "paperless-tika",
                )
            },
        }
        config["services"]["paperless-webserver"]["environment"] = {
            "PAPERLESS_OCR_LANGUAGE": "fra+eng",
        }
        image_ids = {name: f"sha256:{name}" for name in config["services"]}
        runtime = qa_seed.compose_runtime(config, image_ids)

        self.assertEqual(
            runtime["images"]["paperless-webserver"]["image_id"],
            "sha256:paperless-webserver",
        )
        self.assertEqual(runtime["paperless"]["ocr_language"], "fra+eng")

        with self.assertRaisesRegex(
            qa_seed.SeedError,
            "service image must exist locally",
        ):
            qa_seed.compose_runtime(config, {})

    def test_odoo_seed_sanitizer_removes_runtime_credentials(self):
        script = (ROOT / "scripts/odoo/qa_seed_sanitize.py").read_text(
            encoding="utf-8",
        )

        self.assertIn('null_column("res_users", "password")', script)
        self.assertIn("DELETE FROM", script)
        self.assertIn("usl_documents.paperless_token", script)
        self.assertIn('if table_exists("auth_oauth_provider")', script)
        self.assertIn('"client_secret"', script)
        self.assertIn('delete_table("usl_paperless_user_mapping")', script)
        self.assertIn('delete_table("usl_oidc_identity")', script)
        self.assertIn('if table_exists("payment_provider")', script)
        self.assertIn("Migration source-binding columns remain", script)

    def test_paperless_seed_export_is_portable_and_identity_free(self):
        script = (ROOT / "scripts/qa-seed").read_text(encoding="utf-8")

        self.assertIn(
            "paperless_seed_sanitize.py --portable-candidate",
            script,
        )


if __name__ == "__main__":
    unittest.main()
