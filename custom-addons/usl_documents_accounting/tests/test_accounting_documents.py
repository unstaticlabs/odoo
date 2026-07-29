from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install", "usl_documents_accounting")
class TestAccountingDocumentContexts(TransactionCase):
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
                callable(getattr(self.env[model_name], "action_open_documents_workspace"))
            )
