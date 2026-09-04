from __future__ import annotations

import copy
import importlib.util
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = (
    ROOT
    / "migration/accounting_restore/scripts/invoice_currency_rate_lifecycle.py"
)
SPEC = importlib.util.spec_from_file_location(
    "invoice_currency_rate_lifecycle",
    SCRIPT,
)
assert SPEC and SPEC.loader
lifecycle = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(lifecycle)


class FakeCursor:
    def __init__(self, rows):
        self.rows = rows
        self.description = []

    def execute(self, _query, _parameters=()):
        self.description = [(key,) for key in self.rows[0]]

    def fetchall(self):
        return [tuple(row.values()) for row in self.rows]


class InvoiceCurrencyRateLifecycleTest(unittest.TestCase):
    def test_native_identity_normalizes_company_registration(self):
        common = {
            "journal_code": "INV",
            "name": "INV/25-26/0028",
            "move_type": "out_invoice",
            "invoice_date": "2026-08-01",
            "currency_name": "USD",
            "reference": "Platform fee",
            "payment_reference": "INV/25-26/0028",
            "partner_name": "Example",
        }
        source = {
            **common,
            "company_vat": "FR 83 983 982 950",
            "company_name": "Unstatic Labs",
        }
        target = {
            **common,
            "company_vat": "FR83983982950",
            "company_name": "USL",
        }
        self.assertEqual(
            lifecycle.document_key(source),
            lifecycle.document_key(target),
        )

    def test_native_identity_treats_empty_draft_name_as_slash(self):
        row = {
            "company_vat": "FR48983982950",
            "company_name": "USL",
            "journal_code": "BILL",
            "name": None,
            "move_type": "in_invoice",
            "invoice_date": None,
            "currency_name": "EUR",
            "reference": None,
            "payment_reference": None,
            "partner_name": None,
        }
        self.assertEqual(
            lifecycle.document_key(row),
            lifecycle.document_key({**row, "name": "/"}),
        )

    def test_analysis_aggregates_keep_customer_and_vendor_signs(self):
        records = [
            {
                "target_id": 10,
                "identity": {"company": "FR83983982950"},
            },
            {
                "target_id": 11,
                "identity": {"company": "FR83983982950"},
            },
        ]
        cursor = FakeCursor([
            {
                "move_id": 10,
                "move_type": "out_invoice",
                "state": "posted",
                "invoice_currency_rate": 1.2,
                "price_total": 100,
            },
            {
                "move_id": 11,
                "move_type": "in_invoice",
                "state": "posted",
                "invoice_currency_rate": 1.25,
                "price_total": 50,
            },
        ])

        aggregates = lifecycle._analysis_aggregates(
            cursor,
            records,
            "target_id",
        )

        self.assertEqual(aggregates, [
            {
                "company": "FR83983982950",
                "move_type": "in_invoice",
                "state": "posted",
                "document_count": 1,
                "raw_total": "-40.00000000",
                "ui_total": "-40.00",
            },
            {
                "company": "FR83983982950",
                "move_type": "out_invoice",
                "state": "posted",
                "document_count": 1,
                "raw_total": "83.33333333",
                "ui_total": "83.33",
            },
        ])

    def test_manifest_fingerprint_covers_rates_and_aggregates(self):
        manifest = {
            "schema": lifecycle.SCHEMA,
            "source_dump_sha256": "a" * 64,
            "source_database": "online",
            "target_database": "odoo_dev",
            "records": [{"source_id": 1, "target_id": 2, "rate": "1.2"}],
            "analysis_aggregates": [{"ui_total": "83.33"}],
        }
        original = lifecycle.manifest_fingerprint(manifest)
        changed_rate = copy.deepcopy(manifest)
        changed_rate["records"][0]["rate"] = "1.3"
        changed_total = copy.deepcopy(manifest)
        changed_total["analysis_aggregates"][0]["ui_total"] = "83.34"
        self.assertNotEqual(
            original,
            lifecycle.manifest_fingerprint(changed_rate),
        )
        self.assertNotEqual(
            original,
            lifecycle.manifest_fingerprint(changed_total),
        )

    def test_duplicate_native_identity_fails_closed(self):
        row = {
            "id": 1,
            "company_vat": "FR83983982950",
            "company_name": "USL",
            "journal_code": "INV",
            "name": "INV/1",
            "move_type": "out_invoice",
            "invoice_date": "2026-08-01",
            "currency_name": "USD",
            "reference": "",
            "payment_reference": "",
            "partner_name": "Example",
        }
        with self.assertRaisesRegex(RuntimeError, "not unique"):
            lifecycle._unique_by_key(
                [row, {**row, "id": 2}],
                "Target",
            )


if __name__ == "__main__":
    unittest.main()
