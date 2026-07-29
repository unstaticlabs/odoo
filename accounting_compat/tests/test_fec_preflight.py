from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from accounting_compat import cli


HEADER = "|".join(cli.FEC_BIC_IS_REQUIRED_HEADER)


class FecStructuralPreflightTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.old_root = cli.ROOT
        self.old_private_artifacts = cli.PRIVATE_ARTIFACTS
        cli.ROOT = self.root
        cli.PRIVATE_ARTIFACTS = self.root / "artifacts" / "accounting-compat" / "private"
        cli.PRIVATE_ARTIFACTS.mkdir(parents=True)

    def tearDown(self):
        cli.ROOT = self.old_root
        cli.PRIVATE_ARTIFACTS = self.old_private_artifacts
        self.tmp.cleanup()

    def _write_fec(self, body: str) -> Path:
        path = self.root / "983982950FEC20250930.txt"
        path.write_text(body, encoding="utf-8")
        return path

    def test_preflight_accepts_balanced_required_bic_is_file(self):
        path = self._write_fec(
            "\n".join([
                HEADER,
                "MISC|Journal|MOVE1|20250930|627100|Fees|||DOC1|20250930|Label| 000000000000100,00|0,00|||20250930| 000000000000100,00|EUR",
                "MISC|Journal|MOVE1|20250930|401000|Supplier|||DOC1|20250930|Label|0,00| 000000000000100,00|||20250930|-000000000000100,00|EUR",
            ])
            + "\n"
        )

        result = cli.fec_structural_preflight(path, generated_file_name=path.name)

        self.assertEqual(result["status"], "passed")
        self.assertTrue(result["checks"]["required_header_order"])
        self.assertTrue(result["checks"]["entries_balance_by_journal_and_number"])
        self.assertEqual(result["statistics"]["entry_group_count"], 1)
        self.assertEqual(result["statistics"]["invalid_row_count"], 0)
        self.assertEqual(result["errors"], [])

    def test_preflight_reports_invalid_dates_amounts_and_unbalanced_entries(self):
        path = self._write_fec(
            "\n".join([
                HEADER,
                "MISC|Journal|MOVE1|2025-09-30|627100|Fees|||DOC1|20250930|Label|100.00|0,00|||20250930|100.00|EUR",
            ])
            + "\n"
        )

        result = cli.fec_structural_preflight(path, generated_file_name=path.name)

        self.assertEqual(result["status"], "failed")
        self.assertFalse(result["checks"]["dates_are_aaaammjj"])
        self.assertFalse(result["checks"]["amounts_use_comma_decimal_character_format"])
        self.assertFalse(result["checks"]["entries_balance_by_journal_and_number"])
        self.assertEqual(result["statistics"]["invalid_date_count"], 1)
        self.assertEqual(result["statistics"]["invalid_amount_count"], 2)
        self.assertEqual(result["statistics"]["unbalanced_entry_count"], 1)
        self.assertEqual(result["samples"]["invalid_rows"][0]["row_number"], 2)


if __name__ == "__main__":
    unittest.main()
