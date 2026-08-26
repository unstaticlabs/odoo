from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install", "usl_documents_b2c")
class TestB2cDocumentContexts(TransactionCase):
    def test_b2c_business_records_support_archived_documents(self):
        allowed = self.env["usl.document.link"]._allowed_models()
        for model_name in (
            "b2c.order",
            "b2c.payment.event",
            "b2c.fulfilment.event",
            "b2c.accounting.session",
        ):
            self.assertIn(model_name, allowed)
            self.assertIn("archived_document_count", self.env[model_name]._fields)

    def test_provider_evidence_document_is_restricted_and_readonly(self):
        field = self.env["b2c.provider.evidence"]._fields["archived_document_id"]
        self.assertTrue(field.readonly)
        self.assertEqual(field.ondelete, "restrict")
        self.assertEqual(field.groups, "usl_b2c.group_b2c_sensitive_evidence")

    def test_forms_have_one_contextual_documents_entry_point(self):
        for xmlid in (
            "usl_documents_b2c.view_b2c_order_documents",
            "usl_documents_b2c.view_b2c_payment_event_documents",
            "usl_documents_b2c.view_b2c_fulfilment_event_documents",
            "usl_documents_b2c.view_b2c_accounting_session_documents",
        ):
            arch = self.env.ref(xmlid).arch_db
            self.assertEqual(arch.count('name="action_open_documents_workspace"'), 2)
            self.assertIn('string="Upload"', arch)
            self.assertIn('string="Documents"', arch)
