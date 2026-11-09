import hashlib
import inspect
import base64
import io
import re
import uuid
from copy import deepcopy
from itertools import permutations
from unittest.mock import patch
from email.message import EmailMessage
from email.policy import SMTP

from reportlab.pdfgen import canvas

from odoo import Command
from odoo.exceptions import AccessError, UserError
from odoo.models import get_public_method
from odoo.tests import tagged
from odoo.tests.common import TransactionCase
from odoo.tools import BinaryBytes, file_open
from odoo.tools.pdf import OdooPdfFileReader, OdooPdfFileWriter
from odoo.tools.safe_eval import safe_eval
from lxml import etree

from odoo.addons.account.tests.common import AccountTestInvoicingCommon

from ..tools import purchase_mail_evidence as evidence


def text_pdf(lines):
    stream = io.BytesIO()
    document = canvas.Canvas(stream, invariant=1, pageCompression=0)
    for index, line in enumerate(lines):
        document.drawString(40, 800 - index * 22, line)
    document.showPage()
    document.save()
    return stream.getvalue()


def invoice_pdf(reference="QA-INV-001", amount="120.00"):
    return text_pdf([
        "FACTURE", "Fournisseur: Invented QA Supplier",
        "Adresse: 10 Rue Exemple, 75000 Ville QA",
        f"Numero de facture: {reference}", "Date de facture: 2026-10-01",
        f"Total TTC: {amount} EUR",
    ])


def receipt_pdf(reference="QA-INV-001", amount="120.00"):
    return text_pdf([
        "RECU DE PAIEMENT", "Beneficiaire: Invented QA Supplier",
        "Adresse: 10 Rue Exemple, 75000 Ville QA",
        f"Facture concernee: {reference}", "Date de paiement: 2026-10-02",
        f"Montant paye: {amount} EUR",
    ])


@tagged("post_install", "-at_install", "usl_purchase_mail_grouping")
class TestPurchaseMailGrouping(AccountTestInvoicingCommon):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.purchase_journal = cls.company_data["default_journal_purchase"]
        domain = cls.env["mail.alias.domain"].create({
            "name": f"qa-grouping-{uuid.uuid4().hex}.example.invalid",
            "company_ids": [Command.set(cls.env.company.ids)],
        })
        cls.purchase_journal.alias_id.write({
            "alias_name": f"qa-purchase-{uuid.uuid4().hex}",
            "alias_domain_id": domain.id, "alias_contact": "everyone",
        })

    def _receive(self, files, force_report_rendering=False, allowed_company_ids=None, inline_image=False, **headers):
        message = EmailMessage(policy=SMTP)
        message["From"] = "invented.supplier@example.invalid"
        message["To"] = self.purchase_journal.alias_id.alias_full_name
        message["Subject"] = "Invented purchase attachment grouping"
        message["Message-ID"] = f"<qa-grouping-{uuid.uuid4().hex}@example.invalid>"
        for key, value in headers.items():
            message[key] = value
        message.set_content("Disposable invented invoice evidence.")
        if inline_image:
            message.add_alternative('<p>Invented sender logo <img src="cid:qa-logo"/></p>', subtype="html")
            message.get_payload()[1].add_related(
                base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="),
                maintype="image", subtype="png", cid="<qa-logo>", filename="qa-logo.png",
            )
        for name, raw, mimetype in files:
            maintype, subtype = mimetype.split("/", 1)
            message.add_attachment(raw, maintype=maintype, subtype=subtype, filename=name)
        companies = allowed_company_ids or self.env.company.ids
        visible_moves = self.env["account.move"].with_context(allowed_company_ids=companies)
        before = visible_moves.search([])
        self.last_message_raw = message.as_bytes()
        self.last_message_id = message["Message-ID"]
        self.env["mail.thread"].sudo().with_context(
            allowed_company_ids=companies, mail_notify_force_send=False,
            force_report_rendering=force_report_rendering,
        ).message_process(None, message.as_bytes(), save_original=True)
        return visible_moves.search([]) - before

    def test_matching_invoice_and_payment_receipt_stay_together(self):
        invoice, receipt = invoice_pdf(), receipt_pdf()
        bills = self._receive([
            ("first-payment.pdf", receipt, "application/pdf"),
            ("second-invoice.pdf", invoice, "application/pdf"),
        ])
        self.assertEqual(len(bills), 1)
        self.assertEqual(bills.state, "draft")
        self.assertEqual(bills.company_id, self.env.company)
        self.assertEqual(bills.message_main_attachment_id.raw.content, invoice)
        self.assertEqual(
            {hashlib.sha256(a.raw.content).hexdigest() for a in bills.attachment_ids},
            {hashlib.sha256(raw).hexdigest() for raw in (invoice, receipt)},
        )

    def test_distinct_invoices_automatically_split(self):
        bills = self._receive([
            ("same-name.pdf", invoice_pdf("QA-INV-001"), "application/pdf"),
            ("same-name.pdf", invoice_pdf("QA-INV-002"), "application/pdf"),
        ])
        self.assertEqual(len(bills), 2)
        self.assertEqual(set(bills.mapped("state")), {"draft"})
        self.assertTrue(all(len(bill.attachment_ids) == 1 for bill in bills))
        self.assertTrue(all(bill.usl_mail_grouping_move_ids == bills.sorted("id") for bill in bills))

    def test_order_and_filenames_do_not_change_grouping(self):
        originals = [("receipt.pdf", receipt_pdf(), "application/pdf"),
                     ("invoice.pdf", invoice_pdf(), "application/pdf")]
        for ordered in permutations(originals):
            # Deliberately swap the apparently helpful names.
            files = [("invoice.pdf" if raw == receipt_pdf() else "receipt.pdf", raw, mime)
                     for _name, raw, mime in ordered]
            bill = self._receive(files)
            self.assertEqual(len(bill), 1)
            self.assertFalse(bill.usl_mail_grouping_needs_review)
            self.assertEqual(bill.message_main_attachment_id.raw.content, invoice_pdf())

    def test_receipts_alone_make_one_visible_uncertain_draft(self):
        bill = self._receive([
            ("invoice.pdf", receipt_pdf(), "application/pdf"),
            ("another-invoice.pdf", receipt_pdf("QA-INV-002"), "application/pdf"),
        ])
        self.assertEqual(len(bill), 1)
        self.assertTrue(bill.usl_mail_grouping_needs_review)
        self.assertFalse(bill.message_main_attachment_id)
        self.assertEqual(len(bill.attachment_ids), 2)
        self.assertFalse(bill.line_ids)

    def test_unknowns_do_not_create_holding_bills_or_hide_originals(self):
        bill = self._receive([
            ("invoice.pdf", text_pdf(["Invoice", "Insufficient identity"]), "application/pdf"),
            ("notes.txt", b"Unclassified invented original bytes", "text/plain"),
            ("corrupt.pdf", b"%PDF-not-valid", "application/pdf"),
        ])
        self.assertEqual(len(bill), 1)
        self.assertTrue(bill.usl_mail_grouping_needs_review)
        self.assertFalse(bill.message_main_attachment_id)
        self.assertEqual(set(bill.attachment_ids.mapped("name")), {"invoice.pdf", "notes.txt", "corrupt.pdf"})

    def test_unmatched_receipt_retained_with_first_of_two_invoices(self):
        bills = self._receive([
            ("one.pdf", invoice_pdf(), "application/pdf"),
            ("two.pdf", invoice_pdf("QA-INV-002"), "application/pdf"),
            ("payment.pdf", receipt_pdf("UNKNOWN-REFERENCE"), "application/pdf"),
        ])
        self.assertEqual(len(bills), 2)
        uncertain = bills.filtered("usl_mail_grouping_needs_review")
        self.assertEqual(len(uncertain), 1)
        self.assertEqual(len(uncertain.attachment_ids), 2)
        self.assertEqual(uncertain.message_main_attachment_id.raw.content, invoice_pdf())

    def test_conflicting_same_invoice_values_require_review_without_duplicate_bill(self):
        bill = self._receive([
            ("one.pdf", invoice_pdf(), "application/pdf"),
            ("two.pdf", invoice_pdf(amount="150.00"), "application/pdf"),
        ])
        self.assertEqual(len(bill), 1)
        self.assertTrue(bill.usl_mail_grouping_needs_review)
        self.assertFalse(bill.message_main_attachment_id)

    def test_native_message_id_replay_creates_no_bill(self):
        bill = self._receive([("invoice.pdf", invoice_pdf(), "application/pdf")])
        before = self.env["account.move"].search([])
        replay = self.env["mail.thread"].sudo().message_process(None, self.last_message_raw, save_original=True)
        self.assertFalse(replay)
        self.assertEqual(self.env["account.move"].search([]), before)
        self.assertEqual(bill.message_main_attachment_id.raw.content, invoice_pdf())

    def _uncertain_bill(self):
        return self._receive([("unknown.pdf", text_pdf(["Invented unknown document"]), "application/pdf")])

    def test_review_dialog_has_title_and_balanced_native_group(self):
        bill = self._uncertain_bill()
        action = bill.with_context(lang="en_US").action_review_mail_grouping()
        with self.subTest("action title"):
            self.assertEqual(action.get("name"), "Review attachments")
        view = self.env["usl.purchase.mail.grouping.review"].get_view(
            self.env.ref("usl_accounting.purchase_mail_grouping_review_form").id, "form")
        arch = etree.fromstring(view["arch"])
        self.assertEqual(arch.xpath("//field[@name='line_ids']/list/field[not(@column_invisible='1')]/@name"), ["raw", "use"])
        self.assertFalse(arch.xpath("//field[@name='attachment_ids' or @name='main_attachment_id']"))
        bill_view = self.env["account.move"].get_view(
            self.env.ref("account.view_move_form").id, "form")
        bill_arch = etree.fromstring(bill_view["arch"])
        with self.subTest("recorded review summary full row"):
            self.assertEqual(bill_arch.xpath("//field[@name='usl_mail_grouping_summary']")[0].get("colspan"), "2")

    def test_review_keeps_financial_state_and_uses_native_main_selection(self):
        bill = self._uncertain_bill()
        before = bill.read(["state", "line_ids", "amount_total", "review_state", "company_id"])
        action = bill.action_review_mail_grouping()
        wizard = self.env[action["res_model"]].browse(action["res_id"])
        wizard.main_attachment_id = bill.attachment_ids
        wizard.action_keep_together()
        self.assertFalse(bill.usl_mail_grouping_needs_review)
        self.assertEqual(bill.message_main_attachment_id, bill.attachment_ids)
        self.assertTrue(bill.is_manually_modified)
        self.assertEqual(bill.usl_mail_grouping_reviewed_by, self.env.user)
        self.assertTrue(bill.usl_mail_grouping_reviewed_at)
        self.assertEqual(bill.read(["state", "line_ids", "amount_total", "review_state", "company_id"]), before)

    def test_ordinary_invoicing_writer_can_confirm_under_native_rights(self):
        bill = self._receive([
            ("invoice.pdf", invoice_pdf(), "application/pdf"),
            ("unknown.pdf", text_pdf(["Invented unknown supporting document"]), "application/pdf"),
        ])
        before_manual = bill.is_manually_modified
        before_main = bill.message_main_attachment_id
        self.assertFalse(before_manual)
        self.assertTrue(before_main)
        writer = self.env["res.users"].with_context(no_reset_password=True).create({
            "name": "Invented QA Reviewer", "login": f"qa.writer.{uuid.uuid4().hex}@example.invalid",
            "company_id": self.env.company.id, "company_ids": [Command.set(self.env.company.ids)],
            "group_ids": [Command.set((self.env.ref("base.group_user") | self.env.ref("account.group_account_invoice")).ids)],
        })
        self.assertFalse(writer.has_group("base.group_system"))
        action = bill.with_user(writer).action_review_mail_grouping()
        wizard = self.env[action["res_model"]].with_user(writer).browse(action["res_id"])
        wizard.action_keep_together()
        self.assertFalse(bill.usl_mail_grouping_needs_review)
        self.assertEqual(bill.usl_mail_grouping_reviewed_by, writer)
        self.assertEqual(bill.state, "draft")
        self.assertEqual(bill.is_manually_modified, before_manual)
        self.assertEqual(bill.message_main_attachment_id, before_main)

    def test_actual_native_to_review_filter_includes_uncertain_drafts_only(self):
        uncertain = self._uncertain_bill()
        clear = self._receive([("invoice.pdf", invoice_pdf(), "application/pdf")])
        view = self.env["account.move"].get_view(self.env.ref("account.view_account_invoice_filter").id, "search")
        arch = etree.fromstring(view["arch"])
        domain = safe_eval(arch.xpath("//filter[@name='to_check']")[0].get("domain"))
        results = self.env["account.move"].search(domain)
        self.assertIn(uncertain, results)
        self.assertNotIn(clear, results)

    def test_grouping_review_does_not_gate_native_explicit_posting(self):
        self.env.ref("base.EUR").active = True
        with file_open("usl_accounting/tests/fixtures/mail_invoice.xml", "rb") as source:
            invoice = source.read()
        bill = self._receive([
            ("invoice.xml", invoice, "application/xml"),
            ("unknown.pdf", text_pdf(["Unclassified invented attachment"]), "application/pdf"),
        ])
        self.assertEqual(bill.state, "draft")
        self.assertTrue(bill.usl_mail_grouping_needs_review)
        # Complete the native required vendor: this classifier deliberately
        # does not replace the native partner-matching/manual entry workflow.
        bill.partner_id = self.partner_a
        bill.action_post()
        self.assertEqual(bill.state, "posted")
        self.assertTrue(bill.usl_mail_grouping_needs_review)
        self.assertFalse(bill.usl_mail_grouping_reviewed_by)

    def test_stale_review_does_not_acknowledge_changed_files(self):
        bill = self._uncertain_bill()
        action = bill.action_review_mail_grouping()
        wizard = self.env[action["res_model"]].browse(action["res_id"])
        self.env["ir.attachment"].create({
            "name": "new.txt", "raw": b"New invented evidence",
            "res_model": bill._name, "res_id": bill.id,
        })
        with self.assertRaisesRegex(UserError, "attachments changed"):
            wizard.action_keep_together()
        self.assertTrue(bill.usl_mail_grouping_needs_review)
        self.assertFalse(bill.usl_mail_grouping_reviewed_by)

    def test_foreign_attachment_cannot_be_selected_in_grouping_review(self):
        bill, other = self._uncertain_bill(), self._uncertain_bill()
        action = bill.action_review_mail_grouping()
        wizard = self.env[action["res_model"]].browse(action["res_id"])
        wizard.main_attachment_id = other.attachment_ids
        with self.assertRaisesRegex(UserError, "belonging to this bill"):
            wizard.action_keep_together()
        self.assertTrue(bill.usl_mail_grouping_needs_review)

    def test_native_readonly_accountant_cannot_confirm(self):
        bill = self._uncertain_bill()
        reader = self.env["res.users"].with_context(no_reset_password=True).create({
            "name": "Invented QA Reader", "login": f"qa.reader.{uuid.uuid4().hex}@example.invalid",
            "company_id": self.env.company.id, "company_ids": [Command.set(self.env.company.ids)],
            "group_ids": [Command.set((self.env.ref("base.group_user") | self.env.ref("account.group_account_readonly")).ids)],
        })
        bill.with_user(reader).check_access("read")
        with self.assertRaises(AccessError):
            bill.with_user(reader).action_review_mail_grouping()

    def test_native_ubl_and_matching_pdf_share_one_bill(self):
        with file_open("usl_accounting/tests/fixtures/mail_invoice.xml", "rb") as source:
            xml = source.read()
        pdf = text_pdf([
            "INVOICE", "Supplier: QA Mail Supplier",
            "Address: 10 QA Example Street, 75000 QA City",
            "Invoice number: QA-UBL-001", "Invoice date: 2026-10-01",
            "Invoice total: 120.00 EUR",
        ])
        bill = self._receive([
            ("misleading-payment.xml", xml, "application/xml"),
            ("misleading-payment.pdf", pdf, "application/pdf"),
        ])
        self.assertEqual(len(bill), 1)
        self.assertEqual(bill.ref, "QA-UBL-001")
        self.assertEqual(len(bill.invoice_line_ids), 1)
        self.assertEqual(bill.amount_total, 120)
        stored_xml = self.env["ir.attachment"].search([
            ("res_model", "=", "account.move"), ("res_id", "=", bill.id),
            ("res_field", "=", "ubl_cii_xml_file"),
        ])
        self.assertEqual(stored_xml.raw.content, xml)
        self.assertEqual(bill.message_main_attachment_id.raw.content, pdf)
        self.assertFalse(bill.usl_mail_grouping_needs_review)

    def test_two_distinct_native_ubl_invoices_split_and_import(self):
        with file_open("usl_accounting/tests/fixtures/mail_invoice.xml", "rb") as source:
            one = source.read()
        two = one.replace(b"QA-UBL-001", b"QA-UBL-002")
        bills = self._receive([
            ("invoice.xml", one, "application/xml"),
            ("invoice.xml", two, "application/xml"),
        ])
        self.assertEqual(len(bills), 2)
        self.assertEqual(set(bills.mapped("ref")), {"QA-UBL-001", "QA-UBL-002"})
        self.assertTrue(all(bill.amount_total == 120 and len(bill.invoice_line_ids) == 1 and bill.state == "draft" for bill in bills))
        stored_xml = self.env["ir.attachment"].search([
            ("res_model", "=", "account.move"), ("res_id", "in", bills.ids),
            ("res_field", "=", "ubl_cii_xml_file"),
        ])
        self.assertEqual({attachment.raw.content for attachment in stored_xml}, {one, two})

    def test_ubl_invoice_with_receipt_pdf_uses_native_invoice_pdf_as_main(self):
        with file_open("usl_accounting/tests/fixtures/mail_invoice.xml", "rb") as source:
            xml = source.read()
        receipt = text_pdf([
            "PAYMENT RECEIPT", "Payee: QA Mail Supplier",
            "Address: 10 QA Example Street, 75000 QA City",
            "Invoice reference: QA-UBL-001", "Payment date: 2026-10-02",
            "Amount paid: 120.00 EUR",
        ])
        # Follow upstream report tests: fixture only the external renderer.
        # The genuine non-test worker path qualifies wkhtmltopdf separately.
        with patch.object(type(self.env["ir.actions.report"]), "_run_wkhtmltopdf", return_value=text_pdf(["Invented generated invoice"])):
            bill = self._receive([
                ("payment.pdf", receipt, "application/pdf"),
                ("invoice.xml", xml, "application/xml"),
            ], force_report_rendering=True)
        self.assertEqual(len(bill), 1)
        self.assertEqual(bill.ref, "QA-UBL-001")
        self.assertEqual(bill.amount_total, 120)
        self.assertFalse(bill.usl_mail_grouping_needs_review)
        self.assertEqual(bill.message_main_attachment_id.mimetype, "application/pdf")
        self.assertNotEqual(bill.message_main_attachment_id.raw.content, receipt)
        self.assertIn("Generated by Odoo", bill.message_main_attachment_id.name)
        self.assertTrue(bill.attachment_ids.filtered(lambda attachment: attachment.raw.content == receipt))

    def test_receipt_pdf_with_embedded_invoice_keeps_native_generated_main(self):
        with file_open("usl_accounting/tests/fixtures/mail_invoice.xml", "rb") as source:
            xml = source.read()
        receipt = text_pdf([
            "PAYMENT RECEIPT", "Payee: QA Mail Supplier",
            "Address: 10 QA Example Street, 75000 QA City",
            "Invoice reference: QA-UBL-001", "Payment date: 2026-10-02",
            "Amount paid: 120.00 EUR",
        ])
        writer = OdooPdfFileWriter()
        writer.append_pages_from_reader(OdooPdfFileReader(io.BytesIO(receipt)))
        writer.add_attachment("invoice.xml", xml)
        stream = io.BytesIO()
        writer.write(stream)
        original = stream.getvalue()
        with patch.object(type(self.env["ir.actions.report"]), "_run_wkhtmltopdf", return_value=text_pdf(["Invented generated invoice"])):
            bill = self._receive([("original-receipt.pdf", original, "application/pdf")], force_report_rendering=True)
        self.assertEqual(len(bill), 1)
        self.assertEqual(bill.amount_total, 120)
        self.assertEqual(bill.ref, "QA-UBL-001")
        self.assertFalse(bill.usl_mail_grouping_needs_review)
        self.assertNotEqual(bill.message_main_attachment_id.raw.content, original)
        self.assertIn("Generated by Odoo", bill.message_main_attachment_id.name)
        attachments = self.env["ir.attachment"].search([
            ("res_model", "=", "account.move"), ("res_id", "=", bill.id),
            "|", ("res_field", "=", False), ("res_field", "=", "ubl_cii_xml_file"),
        ])
        original_attachment = attachments.filtered(lambda attachment: attachment.raw.content == original)
        self.assertEqual(len(original_attachment), 1)
        self.assertIn(("invoice.xml", xml), list(OdooPdfFileReader(io.BytesIO(original_attachment.raw.content)).get_attachments()))
        self.assertIn(hashlib.sha256(xml).hexdigest(), [item["sha256"] for item in bill.usl_mail_grouping["files"]])
        self.assertEqual({item["role"] for item in bill.usl_mail_grouping["files"]}, {"receipt", "invoice"})

    def test_delegated_billing_agent_uses_same_review_and_readonly_cannot_confirm(self):
        if "usl.agent" not in self.env:
            self.skipTest("Managed Agent is qualified in the full Distribution registry, not this standalone module registry.")
        owner = self.env["res.users"].with_context(no_reset_password=True, usl_governed_identity_provisioning=True).create({
            "name": "Invented QA Agent Owner", "login": f"qa.owner.{uuid.uuid4().hex}@example.invalid",
            "company_id": self.env.company.id, "company_ids": [Command.set(self.env.company.ids)],
            "group_ids": [Command.set((self.env.ref("base.group_user") | self.env.ref("base.group_system") | self.env.ref("account.group_account_manager")).ids)],
            "usl_identity_classification": "active",
        })
        for mode in ("read_write", "read_only"):
            agent = self.env["usl.agent"].with_user(owner).create({
                "name": "Invented QA Billing " + mode, "purpose": "Native shared grouping review qualification.",
                "owner_id": owner.id, "company_id": self.env.company.id,
                "company_ids": [Command.set(self.env.company.ids)],
                "delegated_group_ids": [Command.set(self.env.ref("account.group_account_invoice").ids)],
                "access_mode": mode,
            })
            bill = self._uncertain_bill()
            before_manual = bill.is_manually_modified
            self.assertFalse(before_manual)
            actor_bill = bill.with_user(agent.user_id)
            if mode == "read_only":
                with self.assertRaises(AccessError):
                    actor_bill.action_review_mail_grouping()
                self.assertTrue(bill.usl_mail_grouping_needs_review)
                self.assertFalse(bill.usl_mail_grouping_reviewed_by)
            else:
                action = actor_bill.action_review_mail_grouping()
                self.env[action["res_model"]].with_user(agent.user_id).browse(action["res_id"]).action_keep_together()
                self.assertFalse(bill.usl_mail_grouping_needs_review)
                self.assertEqual(bill.usl_mail_grouping_reviewed_by, agent.user_id)
                snapshot = actor_bill.get_purchase_attachment_context()
                self.assertIn("separate", snapshot["actions"])
                separate_action = actor_bill.action_review_mail_grouping()
                review = self.env[separate_action["res_model"]].with_user(agent.user_id).browse(separate_action["res_id"])
                review.line_ids.use = "separate"
                from odoo.addons.usl_access_control.controllers.json2 import UslAgentJson2Controller
                # Native computes use the same authenticated operation scope as
                # the JSON-2 entry point; this is test setup, never serialized.
                review = review.with_context(UslAgentJson2Controller._agent_call_context(
                    context=review.env.context, agent=agent, model_name=review._name,
                    method_name="action_create_draft_bill", access="write",
                ))
                created_id = review.action_create_draft_bill()["res_id"]
                created = self.env["account.move"].with_user(agent.user_id).browse(created_id)
                self.assertEqual(created.state, "draft")
                self.assertEqual(created.company_id, bill.company_id)
                self.assertEqual(created.attachment_ids.raw.content, bill.attachment_ids.raw.content)
                self.assertEqual(review.action_create_draft_bill()["res_id"], created_id)
            self.assertEqual(bill.state, "draft")
            self.assertEqual(bill.is_manually_modified, before_manual)

    def test_distinct_cii_invoices_split_and_import_with_native_decoder(self):
        with file_open("usl_accounting/tests/fixtures/mail_invoice_cii.xml", "rb") as source:
            first = source.read()
        second = first.replace(b"QA-CII-001", b"QA-CII-002")
        bills = self._receive([
            ("same-invoice.xml", first, "application/xml"),
            ("same-invoice.xml", second, "application/xml"),
        ])
        self.assertEqual(len(bills), 2)
        self.assertEqual(set(bills.mapped("ref")), {"QA-CII-001", "QA-CII-002"})
        self.assertTrue(all(bill.amount_total == 120 and len(bill.invoice_line_ids) == 1 and bill.state == "draft" for bill in bills))

    def test_mixed_ubl_invoice_and_cii_credit_note_keep_native_document_types(self):
        with file_open("usl_accounting/tests/fixtures/mail_invoice.xml", "rb") as source:
            invoice = source.read()
        with file_open("usl_accounting/tests/fixtures/mail_invoice_cii.xml", "rb") as source:
            credit_note = source.read().replace(b"<ram:TypeCode>380</ram:TypeCode>", b"<ram:TypeCode>381</ram:TypeCode>")
        bills = self._receive([
            ("document.xml", invoice, "application/xml"),
            ("document.xml", credit_note, "application/xml"),
        ])
        self.assertEqual(len(bills), 2)
        self.assertEqual(set(bills.mapped("move_type")), {"in_invoice", "in_refund"})
        self.assertTrue(all(bill.state == "draft" and bill.amount_total == 120 for bill in bills))

    def test_reply_to_existing_bill_does_not_regroup_or_reclassify(self):
        bill = self._uncertain_bill()
        initial = bill.usl_mail_grouping
        original_message = self.last_message_id
        created = self._receive([
            ("new-invoice.pdf", invoice_pdf(), "application/pdf"),
            ("other-invoice.pdf", invoice_pdf("QA-INV-002"), "application/pdf"),
        ], **{"In-Reply-To": original_message, "References": original_message})
        self.assertFalse(created)
        self.assertEqual(bill.usl_mail_grouping, initial)
        self.assertEqual(len(bill.attachment_ids), 3)

    def test_explicit_native_main_selection_remains_available(self):
        bill = self._uncertain_bill()
        bill.attachment_ids.register_as_main_attachment(force=True)
        self.assertEqual(bill.message_main_attachment_id, bill.attachment_ids)
        self.assertTrue(bill.usl_mail_grouping_needs_review)

    def test_forged_context_is_not_new_mail_scope(self):
        bill = self.env["account.move"].create({
            "move_type": "in_invoice", "journal_id": self.purchase_journal.id,
        })
        bill.with_context(usl_purchase_mail_created=True, usl_purchase_mail_plan={"groups": []}).message_post(
            body="Invented direct RPC context", attachments=[("invoice.pdf", invoice_pdf())],
        )
        self.assertFalse(bill.usl_mail_grouping)
        self.assertFalse(bill.usl_mail_grouping_needs_review)

    def test_review_cancel_is_readonly_and_stale_bytes_are_rejected(self):
        bill = self._uncertain_bill()
        action = bill.action_review_mail_grouping()
        wizard = self.env[action["res_model"]].browse(action["res_id"])
        wizard.unlink()  # Native Cancel discards only the transient draft.
        self.assertTrue(bill.usl_mail_grouping_needs_review)
        self.assertFalse(bill.usl_mail_grouping_reviewed_by)
        action = bill.action_review_mail_grouping()
        wizard = self.env[action["res_model"]].browse(action["res_id"])
        bill.attachment_ids.raw = BinaryBytes(text_pdf(["Changed invented bytes"]))
        with self.assertRaisesRegex(UserError, "attachments changed"):
            wizard.action_keep_together()

    def test_native_cii_identity_helpers_are_readonly(self):
        with file_open("usl_accounting/tests/fixtures/mail_invoice_cii.xml", "rb") as source:
            raw = source.read()
        attachment = self.env["ir.attachment"].create({"name": "receipt.xml", "raw": BinaryBytes(raw)})
        file = self.env["account.move"]._to_files_data(attachment)[0]
        before = {model: self.env[model].search_count([]) for model in ("account.move", "account.move.line", "res.partner")}
        observed = self.env["account.move"]._mail_structured_evidence(file)
        self.assertEqual(observed["role"], "invoice")
        self.assertEqual(observed["reference"], "qa-cii-001")
        self.assertEqual(observed["amount"], "120")
        self.assertEqual(observed["currency"], "EUR")
        self.assertEqual(observed["date"], "2026-10-01")
        self.assertEqual({model: self.env[model].search_count([]) for model in before}, before)

    def test_native_alias_sender_policy_still_applies(self):
        self.purchase_journal.alias_id.alias_contact = "partners"
        self.assertFalse(self.env["res.partner"].search([("email", "=", "invented.supplier@example.invalid")]))
        self.assertFalse(self._receive([("invoice.pdf", invoice_pdf(), "application/pdf")]))

    def test_native_target_company_survives_current_other_company(self):
        company_a = self.env.company
        company_b_data = self.setup_other_company()
        company_b = company_b_data["company"]
        self.purchase_journal = company_b_data["default_journal_purchase"]
        domain = self.env["mail.alias.domain"].create({
            "name": f"qa-company-b-{uuid.uuid4().hex[:8]}.example.invalid",
            "company_ids": [Command.set(company_b.ids)],
        })
        self.purchase_journal.alias_id.write({
            "alias_name": f"qa-company-b-{uuid.uuid4().hex[:8]}",
            "alias_domain_id": domain.id, "alias_contact": "everyone",
        })
        self.assertEqual(self.env.company, company_a)
        bill = self._receive([
            ("payment.pdf", receipt_pdf(), "application/pdf"),
            ("invoice.pdf", invoice_pdf(), "application/pdf"),
        ], allowed_company_ids=(company_a | company_b).ids)
        self.assertEqual(len(bill), 1)
        self.assertEqual(bill.company_id, company_b)
        self.assertEqual(bill.journal_id, self.purchase_journal)
        self.assertEqual(bill.message_main_attachment_id.raw.content, invoice_pdf())
        self.assertEqual(set(bill.attachment_ids.mapped("res_id")), {bill.id})
        restricted = self.env["res.users"].with_context(no_reset_password=True).create({
            "name": "Invented QA Company A", "login": f"qa.a.{uuid.uuid4().hex}@example.invalid",
            "company_id": company_a.id, "company_ids": [Command.set(company_a.ids)],
            "group_ids": [Command.set((self.env.ref("base.group_user") | self.env.ref("account.group_account_invoice")).ids)],
        })
        with self.assertRaises(AccessError):
            bill.with_user(restricted).with_context(allowed_company_ids=company_a.ids).action_review_mail_grouping()
        crafted = self.env["usl.purchase.mail.grouping.review"].with_user(restricted).with_context(
            allowed_company_ids=company_a.ids,
        ).create({"move_id": bill.id, "fingerprint": "untrusted input"})
        with self.assertRaises(AccessError):
            crafted.read(["summary"])

    def test_content_wins_over_misleading_declared_mime_and_extension(self):
        bill = self._receive([
            ("receipt.bin", invoice_pdf(), "application/octet-stream"),
            ("invoice.dat", receipt_pdf(), "application/octet-stream"),
        ])
        self.assertEqual(len(bill), 1)
        self.assertFalse(bill.usl_mail_grouping_needs_review)
        self.assertEqual(bill.message_main_attachment_id.raw.content, invoice_pdf())

    def test_inline_logo_retains_native_exclusion(self):
        bill = self._receive([("invoice.pdf", invoice_pdf(), "application/pdf")], inline_image=True)
        self.assertEqual(len(bill), 1)
        source_message = self.env["mail.message"].search([("message_id", "=", self.last_message_id)])
        self.assertFalse(bill.usl_mail_grouping_needs_review, {
            "source_image_paths": re.findall(r'src="([^"?]+)', str(source_message.body)),
            "files": bill.usl_mail_grouping,
        })
        self.assertEqual(bill.message_main_attachment_id.raw.content, invoice_pdf())
        self.assertEqual(len(bill.attachment_ids), 1)

    def test_standalone_image_is_retained_as_uncertain_evidence(self):
        raw = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=")
        bill = self._receive([("invoice.png", raw, "image/png")])
        self.assertEqual(len(bill), 1)
        self.assertTrue(bill.usl_mail_grouping_needs_review)
        self.assertFalse(bill.message_main_attachment_id)
        self.assertEqual(bill.attachment_ids.raw.content, raw)

    def test_message_file_limit_retains_one_explicit_uncertain_package(self):
        raw = invoice_pdf()
        bill = self._receive([(f"file-{index}.pdf", raw, "application/pdf") for index in range(evidence.MAX_FILES + 1)])
        self.assertEqual(len(bill), 1)
        self.assertTrue(bill.usl_mail_grouping_needs_review)
        self.assertFalse(bill.message_main_attachment_id)
        self.assertEqual(len(bill.attachment_ids), evidence.MAX_FILES + 1)
        self.assertEqual({item["reason"] for item in bill.usl_mail_grouping["files"]}, {"extraction_budget"})

    def test_embedded_ubl_and_original_pdf_remain_one_family(self):
        with file_open("usl_accounting/tests/fixtures/mail_invoice.xml", "rb") as source:
            xml = source.read()
        pdf = text_pdf([
            "INVOICE", "Supplier: QA Mail Supplier",
            "Address: 10 QA Example Street, 75000 QA City",
            "Invoice number: QA-UBL-001", "Invoice date: 2026-10-01",
            "Invoice total: 120.00 EUR",
        ])
        writer = OdooPdfFileWriter()
        writer.append_pages_from_reader(OdooPdfFileReader(io.BytesIO(pdf)))
        writer.add_attachment("invoice.xml", xml)
        stream = io.BytesIO()
        writer.write(stream)
        original = stream.getvalue()
        bill = self._receive([("original.pdf", original, "application/pdf")])
        self.assertEqual(len(bill), 1)
        self.assertEqual(bill.amount_total, 120)
        self.assertEqual(bill.ref, "QA-UBL-001")
        self.assertEqual(bill.message_main_attachment_id.raw.content, original)
        self.assertFalse(bill.usl_mail_grouping_needs_review)
        self.assertIn(hashlib.sha256(xml).hexdigest(), [item["sha256"] for item in bill.usl_mail_grouping["files"]])

    def test_manual_separation_creates_one_native_draft_and_preserves_source(self):
        bill = self._receive([
            ("invoice.pdf", invoice_pdf(), "application/pdf"),
            ("other.pdf", text_pdf(["Invented second invoice to review"]), "application/pdf"),
        ])
        before = bill.read(["state", "line_ids", "amount_total", "company_id", "journal_id", "ref"])
        originals = {a.id: (a.raw.content, a.res_id) for a in bill.attachment_ids}
        action = bill.action_review_mail_grouping()
        wizard = self.env[action["res_model"]].browse(action["res_id"])
        chosen = wizard.line_ids.filtered(lambda line: line.attachment_id.name == "other.pdf")
        chosen.use = "separate"
        with patch.object(type(bill), "_autopost_bill", side_effect=AssertionError("manual separation must remain draft")), \
             patch.object(type(bill), "_extend_with_attachments", side_effect=AssertionError("manual separation must not decode")):
            result = wizard.action_create_draft_bill()
        created = self.env["account.move"].browse(result["res_id"])
        self.assertNotEqual(created, bill)
        self.assertEqual(created.state, "draft")
        self.assertEqual(created.company_id, bill.company_id)
        self.assertEqual(created.journal_id, bill.journal_id)
        self.assertEqual(created.move_type, bill.move_type)
        self.assertFalse(created.invoice_line_ids)
        self.assertEqual(created.attachment_ids.raw.content, chosen.attachment_id.raw.content)
        self.assertNotEqual(created.attachment_ids, chosen.attachment_id)
        self.assertIn(created, bill.usl_mail_grouping_move_ids)
        self.assertIn(bill, created.usl_mail_grouping_move_ids)
        self.assertEqual(bill.read(["state", "line_ids", "amount_total", "company_id", "journal_id", "ref"]), before)
        self.assertEqual({a.id: (a.raw.content, a.res_id) for a in bill.attachment_ids}, originals)
        self.assertFalse(bill.usl_mail_grouping_needs_review)
        self.assertTrue(created.usl_mail_grouping_needs_review)
        self.assertEqual(wizard.action_create_draft_bill()["res_id"], created.id)
        again = bill.action_review_mail_grouping()
        retry = self.env[again["res_model"]].browse(again["res_id"])
        retry.line_ids.filtered(lambda line: line.attachment_id == chosen.attachment_id).use = "separate"
        self.assertEqual(retry.action_create_draft_bill()["res_id"], created.id)

    def test_manual_separation_checks_stale_bytes_and_empty_selection(self):
        bill = self._uncertain_bill()
        action = bill.action_review_mail_grouping()
        wizard = self.env[action["res_model"]].browse(action["res_id"])
        with self.assertRaisesRegex(UserError, "Choose.*another invoice"):
            wizard.action_create_draft_bill()
        wizard.line_ids.use = "separate"
        bill.attachment_ids.raw = BinaryBytes(text_pdf(["Changed invented document"]))
        before = self.env["account.move"].search_count([])
        with self.assertRaisesRegex(UserError, "attachments changed"):
            wizard.action_create_draft_bill()
        self.assertEqual(self.env["account.move"].search_count([]), before)

    def test_single_choice_review_confirms_main_without_changing_financials(self):
        bill = self._uncertain_bill()
        before = bill.read(["state", "line_ids", "amount_total", "company_id"])
        action = bill.action_review_mail_grouping()
        wizard = self.env[action["res_model"]].browse(action["res_id"])
        wizard.line_ids.use = "main"
        wizard.action_keep_together()
        self.assertEqual(bill.message_main_attachment_id, bill.attachment_ids)
        self.assertFalse(bill.usl_mail_grouping_needs_review)
        self.assertEqual(bill.read(["state", "line_ids", "amount_total", "company_id"]), before)

    def test_attachment_context_is_relevant_paginated_and_does_not_read_bytes(self):
        bill = self._receive([
            ("invoice.pdf", invoice_pdf(), "application/pdf"),
            ("unknown.pdf", text_pdf(["Invented unknown support"]), "application/pdf"),
        ])
        before = bill.read(["usl_mail_grouping", "usl_mail_grouping_reviewed_at"])
        bill.attachment_ids.invalidate_recordset(["raw"])
        with patch.object(type(bill), "_mail_grouping_fingerprint", side_effect=AssertionError("context must not hash attachment bytes")), \
             patch.object(type(self.env["ir.attachment"]), "_compute_raw", side_effect=AssertionError("context must not read attachment bytes")):
            first = bill.get_purchase_attachment_context(limit=1)
            second = bill.get_purchase_attachment_context(offset=1, limit=1)
        self.assertEqual(first["count"], 2)
        self.assertTrue(first["truncated"])
        self.assertEqual(first["next_offset"], 1)
        self.assertTrue(second["complete"])
        self.assertFalse(second["next_offset"])
        self.assertNotEqual(first["documents"][0]["id"], second["documents"][0]["id"])
        self.assertEqual(first["bill_id"], bill.id)
        self.assertIn("review", first["actions"])
        self.assertEqual(first["actions"]["review"]["ids"], bill.ids)
        self.assertNotIn("args", first["actions"]["review"])
        self.assertEqual(bill.read(["usl_mail_grouping", "usl_mail_grouping_reviewed_at"]), before)
        ordinary = self.env["account.move"].create({"move_type": "in_invoice"})
        self.assertEqual(ordinary.get_purchase_attachment_context(), {})
        bill.move_type = "out_invoice"
        self.assertEqual(bill.get_purchase_attachment_context(), {})



    def test_separation_of_posted_source_keeps_posted_lines_and_evidence(self):
        self.env.ref("base.EUR").active = True
        with file_open("usl_accounting/tests/fixtures/mail_invoice.xml", "rb") as source:
            xml = source.read()
        bill = self._receive([("invoice.xml", xml, "application/xml"),
                              ("other.pdf", text_pdf(["Invented separate invoice"]), "application/pdf")])
        bill.partner_id = self.partner_a
        bill.action_post()
        before = bill.line_ids.read(["debit", "credit", "balance", "amount_currency", "currency_id", "account_id", "tax_ids", "analytic_distribution", "amount_residual"])
        originals = {a.id: a.raw.content for a in bill.attachment_ids}
        action = bill.action_review_mail_grouping()
        wizard = self.env[action["res_model"]].browse(action["res_id"])
        wizard.line_ids.filtered(lambda line: line.attachment_id.name == "other.pdf").use = "separate"
        created = self.env["account.move"].browse(wizard.action_create_draft_bill()["res_id"])
        self.assertEqual(created.state, "draft")
        self.assertEqual(bill.state, "posted")
        self.assertEqual(bill.line_ids.read(["debit", "credit", "balance", "amount_currency", "currency_id", "account_id", "tax_ids", "analytic_distribution", "amount_residual"]), before)
        self.assertEqual({a.id: a.raw.content for a in bill.attachment_ids}, originals)

    def test_missing_or_inaccessible_separation_receipt_never_duplicates(self):
        bill = self._uncertain_bill()
        action = bill.action_review_mail_grouping()
        wizard = self.env[action["res_model"]].browse(action["res_id"])
        wizard.line_ids.use = "separate"
        created = self.env["account.move"].browse(wizard.action_create_draft_bill()["res_id"])
        before = self.env["account.move"].sudo().search_count([])
        writer = self.env["res.users"].with_context(no_reset_password=True).create({
            "name": "Invented QA Receipt Reviewer", "login": f"qa.receipt.{uuid.uuid4().hex}@example.invalid",
            "company_id": self.env.company.id, "company_ids": [Command.set(self.env.company.ids)],
            "group_ids": [Command.set((self.env.ref("base.group_user") | self.env.ref("account.group_account_invoice")).ids)],
        })
        retry_action = bill.with_user(writer).action_review_mail_grouping()
        restricted_retry = self.env[retry_action["res_model"]].with_user(writer).browse(retry_action["res_id"])
        restricted_retry.line_ids.use = "separate"
        self.env["ir.rule"].create({
            "name": "QA hide the previously created bill", "model_id": self.env["ir.model"]._get_id("account.move"),
            "domain_force": repr([("id", "!=", created.id)]),
            "perm_read": True, "perm_write": False, "perm_create": False, "perm_unlink": False,
        })
        with self.assertRaisesRegex(UserError, "no longer available"):
            restricted_retry.action_create_draft_bill()
        self.assertEqual(self.env["account.move"].sudo().search_count([]), before)
        created.sudo().unlink()
        another = bill.action_review_mail_grouping()
        retry = self.env[another["res_model"]].browse(another["res_id"])
        retry.line_ids.use = "separate"
        before = self.env["account.move"].sudo().search_count([])
        with self.assertRaisesRegex(UserError, "no longer available"):
            retry.action_create_draft_bill()
        self.assertEqual(self.env["account.move"].sudo().search_count([]), before)

    def test_crafted_foreign_file_and_company_cannot_be_separated(self):
        bill, other = self._uncertain_bill(), self._uncertain_bill()
        action = bill.action_review_mail_grouping()
        wizard = self.env[action["res_model"]].browse(action["res_id"])
        wizard.line_ids.write({"attachment_id": other.attachment_ids.id, "use": "separate"})
        with self.assertRaisesRegex(UserError, "belonging to this bill"):
            wizard.action_create_draft_bill()
        company_b = self.setup_other_company()["company"]
        writer = self.env["res.users"].with_context(no_reset_password=True).create({
            "name": "Invented QA Other Company", "login": f"qa.other.{uuid.uuid4().hex}@example.invalid",
            "company_id": company_b.id, "company_ids": [Command.set(company_b.ids)],
            "group_ids": [Command.set((self.env.ref("base.group_user") | self.env.ref("account.group_account_invoice")).ids)],
        })
        with self.assertRaises(AccessError):
            bill.with_user(writer).with_context(allowed_company_ids=company_b.ids).get_purchase_attachment_context()
        with self.assertRaises(AccessError):
            bill.with_user(writer).with_context(allowed_company_ids=company_b.ids).action_review_mail_grouping()

    def test_context_rejects_invalid_pagination_and_readonly_has_no_write_actions(self):
        bill = self._uncertain_bill()
        for values in ({"offset": -1}, {"offset": True}, {"limit": 0}, {"limit": 101}):
            with self.assertRaises(UserError):
                bill.get_purchase_attachment_context(**values)
        reader = self.env["res.users"].with_context(no_reset_password=True).create({
            "name": "Invented QA Context Reader", "login": f"qa.context.{uuid.uuid4().hex}@example.invalid",
            "company_id": self.env.company.id, "company_ids": [Command.set(self.env.company.ids)],
            "group_ids": [Command.set((self.env.ref("base.group_user") | self.env.ref("account.group_account_readonly")).ids)],
        })
        snapshot = bill.with_user(reader).get_purchase_attachment_context()
        self.assertTrue(snapshot["documents"])
        self.assertEqual(snapshot["actions"], {})


    def test_context_marks_truncated_metadata_without_losing_pagination(self):
        bill = self._uncertain_bill()
        bill.attachment_ids.name = "long-" + "x" * 4096 + ".pdf"
        metadata = dict(bill.usl_mail_grouping)
        metadata["files"][0]["reason"] = "e" * 4096
        bill.usl_mail_grouping = metadata
        snapshot = bill.get_purchase_attachment_context(limit=1)
        document = snapshot["documents"][0]
        self.assertEqual(len(document["name"]), 512)
        self.assertEqual(document["name_status"], "truncated")
        self.assertEqual(len(document["reason"]), 1024)
        self.assertEqual(document["reason_status"], "truncated")
        self.assertTrue(snapshot["complete"])
        self.assertEqual(snapshot["count"], 1)
        self.assertNotIn("usl_mail_grouping", snapshot)

    def test_second_review_honors_current_explicit_main_choice(self):
        bill = self._receive([(f"{name}.pdf", text_pdf([f"Invented unknown document {name}"]), "application/pdf")
                              for name in ("a", "b", "c")])
        files = bill.attachment_ids.sorted("name")
        bill._message_set_main_attachment_id(files[0], force=True, filter_xml=False)
        first = bill.action_review_mail_grouping()
        second = bill.action_review_mail_grouping()
        first_review = self.env[first["res_model"]].browse(first["res_id"])
        second_review = self.env[second["res_model"]].browse(second["res_id"])
        for review, main in ((first_review, files[1]), (second_review, files[2])):
            review.line_ids.use = "supporting"
            review.line_ids.filtered(lambda line: line.attachment_id == main).use = "main"
            review.action_keep_together()
            self.assertEqual(bill.message_main_attachment_id, main)

    def test_explicit_xml_main_selection_is_respected(self):
        with file_open("usl_accounting/tests/fixtures/mail_invoice.xml", "rb") as source:
            xml = source.read()
        bill = self._receive([("invoice.xml", xml, "application/xml")])
        action = bill.action_review_mail_grouping()
        review = self.env[action["res_model"]].browse(action["res_id"])
        xml_line = review.line_ids.filtered(lambda line: line.attachment_id.raw.content == xml)
        self.assertEqual(len(xml_line), 1)
        review.line_ids.use = "supporting"
        xml_line.use = "main"
        review.action_keep_together()
        self.assertEqual(bill.message_main_attachment_id, xml_line.attachment_id)
        self.assertFalse(bill.usl_mail_grouping_needs_review)

    def test_review_create_accepts_native_json2_vals_list(self):
        bill = self._uncertain_bill()
        fingerprint, attachments = bill._mail_grouping_fingerprint()
        model = self.env["usl.purchase.mail.grouping.review"]
        payload = {"vals_list": [{
            "move_id": bill.id, "fingerprint": fingerprint,
            "attachment_ids": [Command.set(attachments.ids)],
            "main_attachment_id": attachments[0].id,
        }]}
        method = get_public_method(model, "create")
        # Match native JSON-2: bind the published signature, then invoke its
        # actual callable with the canonical payload keyword.
        inspect.signature(method).bind(model, **payload)
        review = method(model, **payload)
        self.assertEqual(review.move_id, bill)
        self.assertEqual(review.main_attachment_id, attachments[0])
        self.assertTrue(review.legacy_main_explicit)
        self.assertEqual(bill.state, "draft")
        self.assertTrue(bill.usl_mail_grouping_needs_review)

    def test_review_write_accepts_native_json2_vals(self):
        bill = self._uncertain_bill()
        action = bill.action_review_mail_grouping()
        review = self.env[action["res_model"]].browse(action["res_id"])
        self.assertFalse(review.legacy_main_explicit)
        attachment = bill.attachment_ids[0]
        payload = {"vals": {"main_attachment_id": attachment.id}}
        method = get_public_method(review, "write")
        inspect.signature(method).bind(review, **payload)
        self.assertTrue(method(review, **payload))
        self.assertEqual(review.main_attachment_id, attachment)
        self.assertTrue(review.legacy_main_explicit)
        self.assertEqual(bill.state, "draft")
        self.assertTrue(bill.usl_mail_grouping_needs_review)

    def test_new_draft_discards_inherited_contact_and_financial_defaults(self):
        bill = self._uncertain_bill().with_context(
            default_partner_id=self.partner_a.id,
            default_invoice_line_ids=[Command.create({"name": "Unwanted inherited line", "quantity": 1, "price_unit": 900,
                                                     "account_id": self.company_data["default_account_expense"].id})],
            default_line_ids=[Command.create({"name": "Unwanted inherited accounting line", "debit": 300,
                                             "account_id": self.company_data["default_account_expense"].id})],
        )
        action = bill.action_review_mail_grouping()
        review = self.env[action["res_model"]].with_context(bill.env.context).browse(action["res_id"])
        review.line_ids.use = "separate"
        draft = self.env["account.move"].browse(review.action_create_draft_bill()["res_id"])
        self.assertFalse(draft.partner_id)
        self.assertFalse(draft.invoice_line_ids)
        self.assertFalse(draft.line_ids)
        self.assertEqual(draft.state, "draft")


@tagged("post_install", "-at_install", "usl_purchase_mail_grouping")
class TestPurchaseMailEvidence(TransactionCase):
    def test_generated_order_preserves_all_files_and_unique_receipt_pairing(self):
        def family(identifier, observed):
            return {"id": identifier, "evidence": observed}

        originals = [
            family("invoice-1", evidence.pdf_evidence(invoice_pdf())),
            family("invoice-2", evidence.pdf_evidence(invoice_pdf("QA-INV-002"))),
            family("receipt-1", evidence.pdf_evidence(receipt_pdf())),
            family("receipt-2", evidence.pdf_evidence(receipt_pdf("QA-INV-002"))),
            family("unknown", evidence.unknown(b"invented source", "unsupported_content")),
        ]
        # All 120 orders must conserve every source exactly once and attach
        # each uniquely referenced receipt to its own invoice. Unknown files
        # may remain with whichever invoice is the first native draft.
        for ordered in permutations(originals):
            groups = evidence.group_families(deepcopy(ordered))
            self.assertEqual(len(groups), 2)
            self.assertEqual(sorted(item["id"] for group in groups for item in group), sorted(item["id"] for item in originals))
            for group in groups:
                ids = {item["id"] for item in group}
                self.assertTrue({"invoice-1", "receipt-1"} <= ids or {"invoice-2", "receipt-2"} <= ids)

    def test_receipt_mismatch_dimensions_never_imply_payment_match(self):
        invoice = evidence.pdf_evidence(invoice_pdf())
        receipt = evidence.pdf_evidence(receipt_pdf())
        self.assertTrue(evidence.receipt_matches(receipt, invoice))
        for dimension, wrong in (("reference", "another"), ("issuer", "another supplier"),
                                 ("currency", "USD"), ("amount", "119"),
                                 ("date", "2026-09-30")):
            changed = {**receipt, dimension: wrong}
            self.assertFalse(evidence.receipt_matches(changed, invoice), dimension)
        self.assertTrue(evidence.receipt_matches({**receipt, "amount": "60", "partial": True}, invoice))
        self.assertFalse(evidence.receipt_matches({**receipt, "amount": "121", "partial": True}, invoice))

    def test_numeric_representation_does_not_create_false_conflict(self):
        one = evidence.pdf_evidence(invoice_pdf(amount="120.00"))
        two = evidence.pdf_evidence(invoice_pdf(amount="120,0"))
        groups = evidence.group_families([{"evidence": one}, {"evidence": two}])
        self.assertEqual(len(groups), 1)
        self.assertFalse(any(item.get("uncertain") for item in groups[0]))

    def test_real_pdf_page_and_byte_limits_produce_explicit_unknown(self):
        stream = io.BytesIO()
        document = canvas.Canvas(stream, invariant=1)
        for _page in range(evidence.MAX_PAGES + 1):
            document.drawString(40, 800, "Invented invoice")
            document.showPage()
        document.save()
        oversized_pages = evidence.pdf_evidence(stream.getvalue())
        self.assertEqual(oversized_pages["role"], "unknown")
        self.assertEqual(oversized_pages["reason"], "encrypted_or_page_limit")
        oversized_bytes = evidence.pdf_evidence(b"%PDF-" + b"x" * evidence.MAX_FILE_BYTES)
        self.assertEqual(oversized_bytes["role"], "unknown")
        self.assertEqual(oversized_bytes["reason"], "file_byte_limit")

    def test_missing_conflicting_or_scanned_text_identity_stays_unknown(self):
        for lines in (["FACTURE", "Invoice number: QA-1"], ["PAYMENT RECEIPT", "FACTURE"], [],
                      ["FACTURE", "Invoice number: QA-1", "Invoice number: QA-2"]):
            self.assertEqual(evidence.pdf_evidence(text_pdf(lines))["role"], "unknown")

    def test_partial_receipt_can_also_state_invoice_total_and_date(self):
        receipt = text_pdf([
            "PAYMENT RECEIPT", "Payee: Invented QA Supplier",
            "Address: 10 Rue Exemple, 75000 Ville QA",
            "Invoice reference: QA-INV-001", "Invoice date: 2026-10-01",
            "Payment date: 2026-10-02", "Invoice total: 120.00 EUR",
            "Amount paid: 60.00 EUR", "Partial payment",
        ])
        observed = evidence.pdf_evidence(receipt)
        self.assertEqual(observed["role"], "receipt")
        self.assertTrue(evidence.receipt_matches(observed, evidence.pdf_evidence(invoice_pdf())))

    def test_french_day_first_and_euro_symbol_are_explicit_supported_evidence(self):
        original = text_pdf([
            "FACTURE", "Fournisseur: Invented QA Supplier",
            "Adresse: 10 Rue Exemple, 75000 Ville QA",
            "Numero de facture: QA-INV-001", "Date de facture: 01/10/2026",
            "Total TTC: 120,00 €",
        ])
        observed = evidence.pdf_evidence(original)
        self.assertEqual(observed["role"], "invoice")
        self.assertEqual(observed["date"], "2026-10-01")
        self.assertEqual(observed["currency"], "EUR")
        self.assertTrue(evidence.receipt_matches(evidence.pdf_evidence(receipt_pdf()), observed))
        ambiguous = evidence.text_evidence(
            "INVOICE\nSupplier: Invented QA Supplier\nAddress: 10 Rue Exemple, 75000 Ville QA\nInvoice number: QA-INV-001\nInvoice date: 01/10/2026\nInvoice total: 120 EUR", b"invented english source",
        )
        self.assertEqual(ambiguous["role"], "unknown")
