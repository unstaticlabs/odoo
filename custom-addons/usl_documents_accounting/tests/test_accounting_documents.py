from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install", "usl_documents_accounting")
class TestAccountingDocumentContexts(TransactionCase):
    def test_accountant_reviewer_profile_includes_documents_review(self):
        profile = self.env[
            "res.users"
        ]._usl_pocketid_profile_definitions()["accountant_reviewer"]

        self.assertIn(
            "usl_documents.group_documents_accountant",
            profile["groups"],
        )

    def test_tax_and_closing_records_support_archived_evidence(self):
        allowed = self.env["usl.document.link"]._allowed_models()
        self.assertIn("rebuild.account.declaration", allowed)
        self.assertIn("rebuild.account.closing.period", allowed)
        for model_name in (
            "rebuild.account.declaration",
            "rebuild.account.closing.period",
        ):
            self.assertIn("archived_document_count", self.env[model_name]._fields)
            self.assertTrue(
                callable(getattr(self.env[model_name], "action_open_documents_workspace")),
            )

    def test_accounting_forms_use_one_contextual_documents_entry_point(self):
        for xmlid in (
            "usl_documents_accounting.view_rebuild_account_declaration_documents",
            "usl_documents_accounting.view_rebuild_account_closing_documents",
        ):
            arch = self.env.ref(xmlid).arch_db
            self.assertEqual(arch.count('name="action_open_documents_workspace"'), 2)
            self.assertIn('string="Upload"', arch)
            self.assertIn('string="Documents"', arch)
            self.assertIn('invisible="archived_document_count != 0"', arch)
            self.assertIn('invisible="archived_document_count == 0"', arch)
            self.assertNotIn("Find / upload", arch)
            self.assertNotIn("Evidence", arch)
            self.assertNotIn("action_open_archived_documents", arch)
