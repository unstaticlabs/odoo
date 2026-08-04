import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from migration.source_truth.cli import (
    AuditError,
    build_inventory,
    classify,
    current_distribution_blocking,
    load_contract,
    source_package,
    verify_filestore,
)

ROOT = Path(__file__).resolve().parents[3]


class FakeDatabase:
    def assert_source(self):
        return {
            "database": "source",
            "read_only": "on",
            "installed_modules": "2",
        }

    def models(self):
        return [
            {
                "model": "account.move",
                "table_name": "account_move",
                "owner_module": "account",
                "transient": False,
                "table_exists": True,
                "record_count": 3,
            },
            {
                "model": "documents.document",
                "table_name": "documents_document",
                "owner_module": "documents",
                "transient": False,
                "table_exists": True,
                "record_count": 2,
            },
            {
                "model": "knowledge.article",
                "table_name": "knowledge_article",
                "owner_module": "knowledge",
                "transient": False,
                "table_exists": True,
                "record_count": 1,
            },
        ]

    def tables(self):
        return [
            {"table_name": "account_move", "record_count": 3},
            {"table_name": "documents_document", "record_count": 2},
            {"table_name": "document_tag_rel", "record_count": 1},
            {"table_name": "knowledge_article", "record_count": 1},
        ]

    def attachments(self):
        return []


class SourceTruthAuditCase(unittest.TestCase):
    def test_contract_classifies_known_perimeters(self):
        contract = load_contract(ROOT / "migration/source_truth/coverage.json")
        self.assertEqual(classify("account.move", contract["model_rules"]), "accounting")
        self.assertEqual(
            classify("documents.document", contract["model_rules"]),
            "documents",
        )
        self.assertEqual(classify("x_custom_truth", contract["model_rules"]), "studio")
        self.assertEqual(
            classify("x_tese_payslip", contract["model_rules"]),
            "tese_payroll",
        )
        self.assertEqual(
            classify("res.users.apikeys", contract["model_rules"]),
            "credential_state",
        )
        self.assertEqual(
            classify("res.users.settings", contract["model_rules"]),
            "preferences",
        )
        self.assertEqual(classify("res.users", contract["model_rules"]), "identity")
        self.assertIsNone(classify("unknown.business.model", contract["model_rules"]))

    def test_filestore_verifies_checksum_and_reports_orphan(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            stored = root / "ab/cd"
            stored.parent.mkdir()
            stored.write_bytes(b"source truth")
            orphan = root / "orphan"
            orphan.write_bytes(b"preserve me")
            rows = [
                {
                    "id": 7,
                    "store_fname": "ab/cd",
                    "checksum": hashlib.sha1(b"source truth").hexdigest(),  # noqa: S324
                    "file_size": len(b"source truth"),
                    "type": "binary",
                    "has_db_data": False,
                },
            ]
            summary, errors = verify_filestore(root, rows)
            self.assertFalse(errors)
            self.assertEqual(summary["checked_stored_objects"], 1)
            self.assertEqual(summary["unreferenced_filestore_files"], 1)
            self.assertEqual(summary["unreferenced_paths"], ["orphan"])

    def test_filestore_blocks_missing_binary(self):
        with tempfile.TemporaryDirectory() as directory:
            rows = [
                {
                    "id": 8,
                    "store_fname": "missing/object",
                    "checksum": "0" * 40,
                    "file_size": 4,
                    "type": "binary",
                    "has_db_data": False,
                },
            ]
            _summary, errors = verify_filestore(Path(directory), rows)
            self.assertEqual(errors[0]["error"], "filestore object is missing")

    def test_incomplete_scope_blocks_inventory(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            dump = root / "dump.sql"
            dump.write_text("source", encoding="utf-8")
            (root / "filestore").mkdir()
            contract = load_contract(ROOT / "migration/source_truth/coverage.json")
            inventory = build_inventory(source_package(root), FakeDatabase(), contract)
            self.assertFalse(inventory["summary"]["complete"])
            self.assertEqual(
                inventory["blocking"]["incomplete_populated_scopes"],
                ["knowledge"],
            )
            self.assertFalse(any(current_distribution_blocking(inventory).values()))

    def test_current_distribution_gate_keeps_structural_errors_blocking(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "dump.sql").write_text("source", encoding="utf-8")
            (root / "filestore").mkdir()
            contract = load_contract(ROOT / "migration/source_truth/coverage.json")
            inventory = build_inventory(source_package(root), FakeDatabase(), contract)
            inventory["blocking"]["unclassified_populated_models"] = [
                "unknown.business.model",
            ]
            self.assertEqual(
                current_distribution_blocking(inventory)[
                    "unclassified_populated_models"
                ],
                ["unknown.business.model"],
            )

    def test_invalid_contract_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "contract.json"
            path.write_text(json.dumps({"schema": "wrong"}), encoding="utf-8")
            with self.assertRaises(AuditError):
                load_contract(path)


if __name__ == "__main__":
    unittest.main()
