import unittest
from datetime import date, datetime

from migration.documents_archive.classification import (
    classify_group,
    classify_item,
    document_date,
    folder_contexts,
    normalized_source_tag,
)


class DocumentsClassificationTest(unittest.TestCase):
    def item(self, **values):
        return {
            "attachment_id": 1,
            "document_id": 1,
            "active": True,
            "filename": "evidence.pdf",
            "folder_path": "",
            "source_tag_names": [],
            "res_model": None,
            "create_date": datetime(2025, 12, 6, 13, 0),
            **values,
        }

    def test_source_tag_aliases_remove_copy_and_legacy_labels(self):
        self.assertEqual(
            normalized_source_tag("Notes de frais employés (copie)"),
            "Expenses",
        )
        self.assertEqual(normalized_source_tag("Banque Revolut"), "Revolut")

    def test_folder_context_preserves_path_and_inherits_nearest_company(self):
        paths, companies = folder_contexts(
            [
                {
                    "id": 3,
                    "name": {"en_US": "Finance", "fr_FR": "Finance"},
                    "parent_path": "3/",
                    "company_id": 1,
                },
                {
                    "id": 17,
                    "name": {"en_US": "Bank", "fr_FR": "Banque"},
                    "parent_path": "3/17/",
                    "company_id": None,
                },
            ],
        )
        self.assertEqual(paths[17], "Finance / Bank")
        self.assertEqual(companies[17], 1)

    def test_account_move_uses_business_date_and_type(self):
        item = self.item(
            res_model="account.move",
            move_type="in_invoice",
            invoice_date=date(2025, 11, 30),
            folder_path="Finance / Purchase",
            source_tag_names=["Purchases"],
        )
        result = classify_item(item)
        self.assertEqual(result["document_type"], "Supplier invoice")
        self.assertEqual(result["document_date"], date(2025, 11, 30))
        self.assertEqual(result["tags"], ["Accounting", "Purchases"])
        self.assertTrue(result["accounting_evidence"])

    def test_translated_accounting_journal_name_is_classified(self):
        result = classify_item(
            self.item(
                res_model="account.move",
                move_type="entry",
                journal_name={"en_US": "Payroll", "fr_FR": "Paie"},
            ),
        )
        self.assertEqual(result["document_type"], "Payroll record")
        self.assertTrue(result["hr_restricted"])

    def test_bank_folder_recovers_type_institution_and_filename_date(self):
        item = self.item(
            filename="account-statement_01-May-2026_31-May-2026.pdf",
            folder_path="Finance / Bank / Revolut Business / Relevés",
        )
        result = classify_item(item)
        self.assertEqual(result["document_type"], "Bank statement")
        self.assertEqual(result["document_date"], date(2026, 5, 31))
        self.assertIn("Banking", result["tags"])
        self.assertIn("Revolut Business", result["tags"])
        self.assertEqual(result["institution_partner_name"], "REVOLUT BANK UAB")

    def test_month_only_bank_statement_uses_month_end(self):
        self.assertEqual(
            document_date(
                self.item(filename="RELEVE_DE_COMPTE_2024-02.pdf"),
            ),
            date(2024, 2, 29),
        )

    def test_savings_folder_distinguishes_account_and_flexible_fund_statements(self):
        savings = classify_item(
            self.item(
                filename="savings_monthly-statement_01-Oct-2025_31-Dec-2025.pdf",
                folder_path="Finance / Bank / Revolut Business / Savings",
            ),
        )
        flexible = classify_item(
            self.item(
                filename=(
                    "flexible-cash-funds_monthly-statement_"
                    "01-Oct-2025_31-Dec-2025.pdf"
                ),
                folder_path="Finance / Bank / Revolut Business / Savings",
            ),
        )
        self.assertIn("Revolut Savings EUR", savings["tags"])
        self.assertNotIn("Revolut Flexible Cash Funds", savings["tags"])
        self.assertIn("Revolut Flexible Cash Funds", flexible["tags"])
        self.assertNotIn("Revolut Savings EUR", flexible["tags"])
        self.assertEqual(flexible["document_type"], "Bank statement")

    def test_unassigned_bank_export_has_deterministic_business_classification(self):
        result = classify_item(
            self.item(
                document_id=None,
                kind="unassigned_evidence",
                filename="EUR_savings_monthly-statement_01-Dec-2025_01-Jun-2026.csv",
            ),
        )

        self.assertEqual(result["document_type"], "Bank statement")
        self.assertEqual(result["tags"], ["Accounting", "Banking"])
        self.assertTrue(result["accounting_evidence"])
        self.assertFalse(result["needs_attention"])

    def test_locked_private_strategy_is_classified_without_copying_ai_config(self):
        result = classify_item(
            self.item(
                attachment_id=1611,
                document_id=None,
                kind="restricted_unassigned_evidence",
                filename="2601 GBC Updated Vision and Strategy.pdf",
            ),
        )

        self.assertEqual(result["document_type"], "Product document")
        self.assertEqual(result["tags"], ["Product"])
        self.assertFalse(result["needs_attention"])

    def test_hr_folder_is_restricted_and_linkable(self):
        result = classify_item(
            self.item(folder_path="Employees - Unstatic Labs / Valentin Viennot"),
        )
        self.assertTrue(result["hr_restricted"])
        self.assertEqual(result["document_type"], "Employee document")
        self.assertIn("HR", result["tags"])

    def test_group_unions_duplicate_context_and_preserves_first_add_date(self):
        first = self.item(
            document_id=2,
            attachment_id=2,
            create_date=datetime(2025, 1, 2, 9, 0),
            write_date=datetime(2025, 1, 3, 10, 0),
            folder_path="Finance / Bank / Shine / Relevés",
        )
        second = self.item(
            document_id=3,
            attachment_id=3,
            create_date=datetime(2025, 1, 1, 8, 0),
            write_date=datetime(2025, 1, 1, 9, 0),
            folder_path="Legal / Contracts",
        )
        result = classify_group([first, second])
        self.assertEqual(result["added_at"], datetime(2025, 1, 1, 8, 0))
        self.assertEqual(result["modified_at"], datetime(2025, 1, 3, 10, 0))
        self.assertIn("Banking", result["tags"])
        self.assertIn("Contracts & legal", result["tags"])
        self.assertEqual(result["document_type"], "Contract")


if __name__ == "__main__":
    unittest.main()
