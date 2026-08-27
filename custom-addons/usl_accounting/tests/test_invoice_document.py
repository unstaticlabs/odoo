import base64
import io

from odoo import fields
from odoo.exceptions import UserError
from odoo.tests import tagged
from odoo.tools import BinaryBytes
from odoo.tools.pdf import OdooPdfFileReader, OdooPdfFileWriter

from odoo.addons.account.tests.common import AccountTestInvoicingCommon
from odoo.addons.base.tests.files import PDF_RAW


PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwC"
    "AAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)


@tagged("post_install", "-at_install", "usl_accounting_documents")
class TestInvoiceDocument(AccountTestInvoicingCommon):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.company.write(
            {
                "country_id": cls.env.ref("base.fr").id,
                "street": "1 rue de la Paix",
                "zip": "75001",
                "city": "Paris",
                "logo": BinaryBytes(PNG),
                "company_registry": "983982950",
                "vat": "FR48983982950",
                "usl_document_legal_form": "SAS",
                "usl_document_share_capital": 1000,
                "usl_document_rcs_city": "Paris",
                "usl_invoice_late_penalty_text": "three times the legal interest rate",
                "usl_invoice_recovery_fee": 40,
            }
        )
        cls.partner_a.write(
            {
                "street": "20 avenue Victor Hugo",
                "zip": "69002",
                "city": "Lyon",
                "country_id": cls.env.ref("base.fr").id,
                "lang": "fr_FR",
                "vat": "FR23341987654",
                "is_company": True,
                "company_registry": "34198765400012",
            }
        )
        cls.template = cls.env.ref("usl_document_templates.template_invoice_v1")
        cls.product_a.type = "service"

    def _invoice(
        self,
        *,
        move_type="out_invoice",
        line_count=1,
        invoice_date=True,
        post=True,
    ):
        lines = [
            self._prepare_invoice_line(
                name=f"Professional service {index + 1}",
                product_id=self.product_a,
                account_id=self.company_data["default_account_revenue"],
                quantity=index + 1,
                price_unit=125.75,
                discount=10 if index == 0 else 0,
                tax_ids=self.tax_sale_a,
            )
            for index in range(line_count)
        ]
        invoice = self._create_invoice(
            move_type=move_type,
            partner_id=self.partner_a,
            company_id=self.company,
            currency_id=self.company.currency_id,
            invoice_date=fields.Date.today() if invoice_date else False,
            invoice_line_ids=lines,
        )
        if post:
            invoice.action_post()
        return invoice

    def test_invoice_and_credit_note_payloads_preserve_business_values(self):
        invoice = self._invoice(line_count=100)
        payload, assets = invoice._usl_document_render_payload(
            None, self.template, {}, "fr_FR"
        )
        self.assertEqual(payload["kind"], "invoice")
        self.assertEqual(len(payload["lines"]), 100)
        self.assertEqual(payload["lines"][0]["discount"], "10,00 %")
        self.assertIn(self.tax_sale_a.name, payload["lines"][0]["taxes"])
        self.assertIn("SIREN : 341987654", payload["customer"]["address_lines"])
        self.assertTrue(any("Nature" in item for item in payload["metadata"]))
        self.assertTrue(any("40" in mention for mention in payload["legal_mentions"]))
        self.assertFalse(
            any("Late-payment penalties" in mention for mention in payload["legal_mentions"])
        )
        self.assertIsInstance(assets, list)

        refund = self._invoice(move_type="out_refund")
        refund_payload, _assets = refund._usl_document_render_payload(
            None, self.template, {}, "fr_FR"
        )
        self.assertEqual(refund_payload["kind"], "credit_note")

    def test_proforma_can_render_before_invoice_date(self):
        invoice = self._invoice(invoice_date=False, post=False)
        payload, _assets = invoice._usl_document_render_payload(
            None, self.template, {"proforma": True}, "en_US"
        )
        self.assertEqual(payload["kind"], "proforma")
        self.assertIn("not an accounting invoice", payload["legal_mentions"][0])

    def test_english_official_invoice_keeps_payment_legal_mentions(self):
        invoice = self._invoice()
        payload, _assets = invoice._usl_document_render_payload(
            None, self.template, {}, "en_US"
        )
        self.assertTrue(
            any("Late-payment penalties" in mention for mention in payload["legal_mentions"])
        )
        self.assertTrue(
            any("recovery-cost indemnity" in mention for mention in payload["legal_mentions"])
        )

    def test_document_locale_is_independent_from_operator_language(self):
        invoice = self._invoice()

        english_payload, _assets = invoice.with_context(
            lang="fr_FR"
        )._usl_document_render_payload(None, self.template, {}, "en_US")
        french_payload, _assets = invoice.with_context(
            lang="en_US"
        )._usl_document_render_payload(None, self.template, {}, "fr_FR")

        self.assertEqual(english_payload["due_date_label"], "Due:")
        self.assertEqual(english_payload["totals"][0]["label"], "Untaxed amount")
        self.assertEqual(french_payload["due_date_label"], "Échéance :")
        self.assertEqual(french_payload["totals"][0]["label"], "Montant hors taxes")

    def test_official_invoice_rejects_incomplete_recipient_and_vendor_source(self):
        invoice = self._invoice()
        invoice.partner_id.street = False
        with self.assertRaisesRegex(UserError, "complete postal address"):
            invoice._usl_document_render_payload(None, self.template, {}, "en_US")

        vendor_bill = self._invoice(move_type="in_invoice", post=False)
        with self.assertRaisesRegex(UserError, "source passthrough"):
            vendor_bill._usl_document_render_payload(None, self.template, {}, "en_US")

    def test_draft_invoice_requires_proforma(self):
        invoice = self._invoice(post=False)
        with self.assertRaisesRegex(UserError, "Post the invoice or use Pro Forma"):
            invoice._usl_document_render_payload(None, self.template, {}, "en_US")

    def test_pdf_metadata_is_copied_to_attachment_provenance(self):
        writer = OdooPdfFileWriter()
        writer.clone_reader_document_root(
            OdooPdfFileReader(io.BytesIO(PDF_RAW), strict=False)
        )
        writer.add_metadata(
            {
                "/Subject": (
                    "Template invoice.v1@reviewed; payload sha256:"
                    f"{'b' * 64}; engine usl-document-renderer/1.0.0"
                )
            }
        )
        output = io.BytesIO()
        writer.write(output)
        values = {"raw": output.getvalue()}
        invoice = self._invoice()

        self.env["account.move.send"]._usl_invoice_attachment_provenance(
            invoice, values
        )

        self.assertEqual(values["usl_document_template_revision"], "reviewed")
        self.assertEqual(values["usl_document_payload_sha256"], "b" * 64)
        self.assertEqual(values["usl_document_company_id"], invoice.company_id.id)
