from odoo import fields
from odoo.tests import tagged

from odoo.addons.account.tests.common import AccountTestInvoicingCommon


@tagged("post_install", "-at_install", "usl_bill_terms")
class TestBillTerms(AccountTestInvoicingCommon):
    def test_printable_field_help_and_native_definition(self):
        metadata = self.env["account.move"].with_context(lang="en_US").fields_get(
            ["narration"], attributes=["string", "help", "type", "readonly", "store"],
        )["narration"]
        self.assertIn("printed", metadata.get("help", ""))
        self.assertIn("Chatter", metadata["help"])
        self.assertIn("payment_reference", metadata["help"])
        self.assertEqual(metadata["string"], "Terms and Conditions")
        self.assertEqual(metadata["type"], "html")
        self.assertTrue(metadata["store"])
        self.assertFalse(metadata["readonly"])
        field = self.env["account.move"]._fields["narration"]
        self.assertEqual(field.compute, "_compute_narration")
        self.assertEqual(field.inverse, "_inverse_narration")

    def test_draft_configuration_preserves_legal_terms_and_omission(self):
        bill = self.init_invoice("in_invoice", invoice_date=fields.Date.today(), amounts=[10])
        terms = "<p>[QA] The remaining balance is payable within 30 days.</p>"
        bill.configure_draft_vendor_bill(header_values={"narration": terms})
        self.assertEqual(bill.narration, terms)
        bill.configure_draft_vendor_bill(header_values={"ref": "QA-SUPPLIER-REFERENCE"})
        self.assertEqual(bill.narration, terms)
        bill.configure_draft_vendor_bill(header_values={"narration": ""})
        self.assertFalse(bill.narration)

    def test_posted_native_terms_and_internal_note_have_distinct_destinations(self):
        bill = self.init_invoice("in_invoice", invoice_date=fields.Date.today(), amounts=[10], post=True)
        ledger = bill.line_ids.read(["balance", "amount_currency", "amount_residual", "account_id", "tax_ids"])
        terms = "<p>[QA] Contractual audit rights remain with the parties.</p>"
        bill.write({"narration": terms})
        message = bill.with_context(mail_post_autofollow=False).message_post(
            body="[QA] INTERNAL-MEMO-ONLY", subtype_xmlid="mail.mt_note",
            partner_ids=[], notify_skip_followers=True,
        )
        self.assertEqual(message.subtype_id, self.env.ref("mail.mt_note"))
        self.assertFalse(message.notification_ids)
        self.assertEqual(bill.narration, terms)
        self.assertEqual(bill.line_ids.read(["balance", "amount_currency", "amount_residual", "account_id", "tax_ids"]), ledger)
        rendered, _kind = self.env["ir.actions.report"]._render_qweb_html(
            "account.account_invoices", bill.ids,
        )
        self.assertIn("Contractual audit rights", str(rendered))
        self.assertNotIn("INTERNAL-MEMO-ONLY", str(rendered))
