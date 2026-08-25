import importlib.util
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
        for relative in qa_seed.MIGRATION_INPUTS:
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
        self.runtime = Path(self.temporary.name) / "runtime.json"
        self.runtime.write_text('{"images":{"paperless":"qualified"}}', encoding="utf-8")
        self.qualification = Path(self.temporary.name) / "qualification.json"
        self.qualification.write_text('{"status":"passed"}', encoding="utf-8")

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
        self.assertIn("source_filestore_sha256", manifest["identity"])
        self.assertEqual((self.seed / "manifest.json").stat().st_mode & 0o777, 0o600)

    def test_changed_source_filestore_is_rejected(self):
        qa_seed.seal(self.args())
        (self.source / "filestore/document").write_text("changed", encoding="utf-8")

        with self.assertRaisesRegex(qa_seed.SeedError, "source_filestore_sha256"):
            qa_seed.verify(self.args())

    def test_changed_artifact_is_rejected(self):
        qa_seed.seal(self.args())
        (self.seed / "odoo.dump").write_bytes(b"tampered")

        with self.assertRaisesRegex(qa_seed.SeedError, "seed artifacts differ"):
            qa_seed.verify(self.args())

    def test_changed_runtime_is_rejected(self):
        qa_seed.seal(self.args())
        self.runtime.write_text('{"images":{"paperless":"new"}}', encoding="utf-8")

        with self.assertRaisesRegex(qa_seed.SeedError, "runtime"):
            qa_seed.verify(self.args())

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

        self.assertIn("UPDATE res_users SET password = NULL", script)
        self.assertIn("DELETE FROM", script)
        self.assertIn("usl_documents.paperless_token", script)
        self.assertIn("UPDATE auth_oauth_provider SET client_secret = NULL", script)
        self.assertLess(
            script.index('"usl_paperless_user_mapping"'),
            script.index('"usl_oidc_identity"'),
        )


if __name__ == "__main__":
    unittest.main()
