import importlib.util
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SPEC = importlib.util.spec_from_file_location(
    "documents_release_recovery",
    ROOT / "migration/documents_archive/release_recovery.py",
)
recovery = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(recovery)


class DocumentsReleaseRecoveryTest(unittest.TestCase):
    def setUp(self):
        self.paperless = {
            "status": "passed",
            "document_count": 46,
            "document_id_min": 1,
            "document_id_max": 47,
            "live_documents": 44,
            "trash_documents": 2,
        }
        self.vector = {"status": "passed", "index_sha256": "a" * 64}
        self.odoo = {
            "root_count": 44,
            "active_link_count": 29,
            "version_count": 46,
            "root_role_counts": {"background": 25},
            "link_role_counts": {"evidence": 22},
            "ledger_state_counts": {"archived_evidence": 2},
            "accounting": {"posted_moves": 5258, "balanced": True},
        }
        self.manifest = {
            "release_id": "documents-test",
            "manifest_sha256": "b" * 64,
            "identity": {
                "documents": {
                    "roots": 44,
                    "active_links": 29,
                    "versions": 46,
                    "root_roles": {"background": 25},
                    "link_roles": {"evidence": 22},
                    "ledger_states": {"archived_evidence": 2},
                },
                "accounting": self.odoo["accounting"],
                "paperless": self.paperless,
                "embedding": {
                    "model": {"manifest_sha256": "c" * 64},
                    "vector_index": self.vector,
                },
            },
        }

    def build(self, **overrides):
        values = {
            "manifest": self.manifest,
            "odoo": self.odoo,
            "paperless": self.paperless,
            "vector": self.vector,
            "bge_manifest_sha256": "c" * 64,
            "tantivy_output": "Search index is up to date.\n",
            "project": "documents-restore",
            "database": "odoo_documents_release_restore",
            **overrides,
        }
        return recovery.build(**values)

    def test_exact_restore_passes_without_rebuild_claims(self):
        evidence = self.build()

        self.assertEqual(evidence["status"], "passed")
        self.assertFalse(evidence["mass_ocr"])
        self.assertFalse(evidence["llm_rebuild"])
        self.assertFalse(evidence["model_download"])
        self.assertIn("status=passed", recovery.render_text(evidence))

    def test_stable_id_or_vector_drift_fails(self):
        changed_paperless = {**self.paperless, "document_id_max": 48}
        changed_vector = {**self.vector, "index_sha256": "d" * 64}

        evidence = self.build(
            paperless=changed_paperless,
            vector=changed_vector,
        )

        self.assertEqual(evidence["status"], "failed")
        self.assertIn("paperless_equal", evidence["blockers"])
        self.assertIn("stable_ids", evidence["blockers"])
        self.assertIn("vector_equal", evidence["blockers"])

    def test_missing_tantivy_noop_or_wrong_model_fails(self):
        evidence = self.build(
            tantivy_output="Indexed 46 documents",
            bge_manifest_sha256="d" * 64,
        )

        self.assertEqual(evidence["status"], "failed")
        self.assertIn("tantivy_noop", evidence["blockers"])
        self.assertIn("bge_manifest_equal", evidence["blockers"])

    def test_failed_recovery_does_not_replace_bundle_evidence(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            output = root / "external.json"
            bundle_evidence = root / "recovery-rehearsal.txt"
            bundle_evidence.write_text("status=pending\n", encoding="utf-8")
            failed = self.build(tantivy_output="Index changed")

            persisted = recovery.persist(
                failed,
                output,
                bundle_evidence,
            )

            self.assertFalse(persisted)
            self.assertTrue(output.is_file())
            self.assertEqual(
                bundle_evidence.read_text(encoding="utf-8"),
                "status=pending\n",
            )


if __name__ == "__main__":
    unittest.main()
