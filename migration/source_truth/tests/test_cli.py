import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from migration.source_truth.cli import (
    AuditError,
    build_gap_report,
    build_inventory,
    classify,
    current_distribution_blocking,
    default_source_dir,
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

    def fields(self):
        return [
            {
                "model": "account.move",
                "name": "name",
                "ttype": "char",
                "relation": "",
                "state": "base",
                "required": True,
                "readonly": False,
                "store": True,
                "company_dependent": False,
                "tracking": 1,
                "ai": False,
            },
            {
                "model": "knowledge.article",
                "name": "body",
                "ttype": "html",
                "relation": "",
                "state": "base",
                "required": False,
                "readonly": False,
                "store": True,
                "company_dependent": False,
                "tracking": 0,
                "ai": False,
            },
        ]

    def attachments(self):
        return []


class SourceTruthAuditCase(unittest.TestCase):
    def test_default_source_directory_is_repository_portable_and_overridable(self):
        self.assertEqual(default_source_dir({}), ROOT / "usl-online-dump")
        self.assertEqual(
            default_source_dir({"USL_ONLINE_DUMP_DIR": "/tmp/approved-source"}),
            Path("/tmp/approved-source"),
        )

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
            classify("x_content_payout_line", contract["model_rules"]),
            "platform_billing",
        )
        self.assertEqual(
            classify("x_content_payout_attachment_rel", contract["table_rules"]),
            "platform_billing",
        )
        self.assertEqual(
            classify("stock.location", contract["model_rules"]),
            "inventory_manufacturing",
        )
        self.assertEqual(
            classify("stock_route_warehouse", contract["table_rules"]),
            "inventory_manufacturing",
        )
        self.assertEqual(
            classify("sms.template", contract["model_rules"]),
            "collaboration",
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
            self.assertEqual(inventory["summary"]["stored_or_manual_fields"], 2)
            self.assertEqual(
                inventory["incomplete_scope_fields"],
                ["knowledge.article.body"],
            )
            report = build_gap_report(inventory)
            self.assertFalse(report["summary"]["production_ready"])
            self.assertEqual(report["summary"]["delivered_source_records"], 5)
            self.assertEqual(report["summary"]["blocked_source_records"], 1)
            self.assertEqual(
                report["blocked_scopes"]["knowledge"]["fields"][0]["name"],
                "body",
            )

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
