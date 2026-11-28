"""Receipt email identity, incidental terms and the native expense workflow."""

from email.message import EmailMessage
import hashlib
import io
from unittest.mock import patch

from reportlab.pdfgen import canvas

from odoo.addons.hr_expense.tests.common import TestExpenseCommon
from odoo.tests import TransactionCase, tagged
from odoo import Command
from odoo.exceptions import AccessError, UserError
from odoo.tools.binary import BinaryBytes
from markupsafe import Markup


TERMS = b"""<!doctype html><html lang="fr"><head><title>Conditions d'utilisation Google Play</title></head>
<body><h1>Conditions d'utilisation Google Play</h1><p>Fictional generic legal terms for receipt QA only.</p></body></html>"""
REFERENCES = (
    "GPA.0000-0000-0000-00001",
    "GPA.0000-0000-0000-00001..0",
    "GPA.0000-0000-0000-00001..1",
)


def _pdf(label):
    stream = io.BytesIO()
    document = canvas.Canvas(stream, invariant=1, pageCompression=0)
    document.drawString(40, 800, label)
    document.showPage()
    document.save()
    return stream.getvalue()


@tagged("post_install", "-at_install", "usl_expense_email_receipt", "usl_accounting_unit")
class TestExpenseEmailReceipt(TestExpenseCommon):
    def _email(self, reference, *, token="receipt", french=False):
        message = EmailMessage()
        message["From"] = self.expense_user_employee.email
        message["To"] = "expenses@example.invalid"
        message["Subject"] = f"{self.product_c.default_code} Atelier Lune EUR 12.34"
        message["Message-ID"] = f"<{token}@example.invalid>"
        label = "Numéro de commande" if french else "Order number"
        body = f"<html><body><h1>Google Play</h1><p>{label}: {reference}</p><p>Atelier Lune — subscription</p><p>2026-03-04 — EUR 12.34</p><p>Synthetic receipt fixture</p></body></html>"
        message.set_content(f"Google Play\n{label}: {reference}\nAtelier Lune\nSynthetic receipt fixture")
        message.add_alternative(body, subtype="html")
        # Filename is deliberately not a classification signal. The original
        # problem's HTML arrived with this native MIME type too.
        message.add_attachment(TERMS, maintype="text", subtype="plain", filename="source-details.dat")
        return message.as_bytes()

    def _ingest(self, reference, *, token="receipt", french=False):
        def renderer(bodies, *args, **kwargs):
            content = "\n".join(body.decode() if isinstance(body, bytes) else str(body) for body in bodies)
            return _pdf("Synthetic rendered email " + hashlib.sha256(content.encode()).hexdigest())
        # Keep the native MIME/parser/QWeb/attachment path. Only the external
        # rendering executable and outbound acknowledgment are isolated here;
        # genuine wkhtmltopdf and UI qualification are separate evidence.
        with patch.object(type(self.env["ir.actions.report"]), "_run_wkhtmltopdf", side_effect=renderer), patch.object(
            type(self.env["hr.expense"]), "_send_expense_success_mail", return_value=None
        ):
            return self.env["mail.thread"].with_context(force_report_rendering=True).message_process(
                "hr.expense", self._email(reference, token=token, french=french)
            )

    def test_receipt_email_becomes_main_and_original_terms_survive(self):
        expense = self._ingest(REFERENCES[0])
        self.assertEqual(expense.message_main_attachment_id.mimetype, "application/pdf")
        terms = expense.attachment_ids.filtered(lambda attachment: attachment.raw.content == TERMS)
        self.assertEqual(len(terms), 1)
        self.assertNotEqual(terms, expense.message_main_attachment_id)
        source = expense.message_ids.filtered(lambda message: message.message_type == "email")
        self.assertIn(expense.message_main_attachment_id, source.attachment_ids)
        self.assertTrue(any(REFERENCES[0] in str(message.body) for message in source))
        owner = expense.with_user(self.expense_user_employee)
        self.assertIn(expense.message_main_attachment_id, owner._get_mail_thread_data_attachments())

    def test_renderer_fallback_never_becomes_a_false_pdf_receipt(self):
        for index, rendered in enumerate(((b"<html>fallback</html>", "html"), (b"not a PDF", "pdf"))):
            with self.subTest(format=rendered[1]), patch.object(
                type(self.env["ir.actions.report"]), "_render_qweb_pdf", return_value=rendered
            ), patch.object(type(self.env["hr.expense"]), "_send_expense_success_mail", return_value=None):
                expense = self.env["mail.thread"].message_process(
                    "hr.expense", self._email(REFERENCES[0], token=f"fallback-{index}")
                )
            source = self._message(expense)
            self.assertFalse(expense.message_main_attachment_id)
            self.assertFalse(source.attachment_ids.filtered(lambda attachment: attachment.mimetype == "application/pdf"))
            self.assertEqual(expense.attachment_ids.raw.content, TERMS)
            self.assertTrue(expense.receipt_email_url)

    def test_pdf_engine_unavailable_keeps_the_received_email_and_original_files(self):
        for index, error in enumerate((UserError("Synthetic renderer unavailable"), FileNotFoundError("Synthetic renderer missing"))):
            with self.subTest(error=type(error).__name__), patch.object(
                type(self.env["ir.actions.report"]), "_render_qweb_pdf", side_effect=error
            ):
                expense = self._ingest(REFERENCES[0], token=f"unavailable-{index}")
            self.assertFalse(expense.message_main_attachment_id)
            self.assertEqual(expense.attachment_ids.raw.content, TERMS)
            self.assertTrue(self._message(expense))
            self.assertIn("/report/html/usl_accounting.expense_receipt_email/", expense.receipt_email_url)
            self.assertFalse(expense.account_move_id)

    def test_legacy_attachment_without_mimetype_survives_receipt_email_ingestion(self):
        expense = self.create_expenses({"name": "Atelier Lune legacy file"})
        legacy = self.env["ir.attachment"].create({
            "name": "Original legacy file", "raw": BinaryBytes(b"Synthetic legacy source"),
            "res_model": "hr.expense", "res_id": expense.id,
        })
        # Simulate an old nullable native column; ordinary creation derives a
        # MIME type from bytes. This is disposable test setup, not product SQL.
        self.env.cr.execute("UPDATE ir_attachment SET mimetype = NULL WHERE id = %s", [legacy.id])
        legacy.invalidate_recordset(["mimetype"])
        self.assertFalse(legacy.mimetype)
        with patch.object(type(self.env["ir.actions.report"]), "_run_wkhtmltopdf", return_value=_pdf("Synthetic receipt")), patch.object(
            type(self.env["hr.expense"]), "_send_expense_success_mail", return_value=None
        ):
            self.env["mail.thread"].with_context(force_report_rendering=True).message_process(
                "hr.expense", self._email(REFERENCES[0], token="legacy-mime"), thread_id=expense.id
            )
        self.assertEqual(expense.message_main_attachment_id.mimetype, "application/pdf")
        self.assertEqual(legacy.raw.content, b"Synthetic legacy source")
        self.assertFalse(legacy.mimetype)

    def test_three_full_references_are_distinct_and_a_second_email_is_a_true_duplicate(self):
        expenses = [self._ingest(reference, token=f"receipt-{index}", french=bool(index % 2)) for index, reference in enumerate(REFERENCES)]
        duplicate = self._ingest(REFERENCES[1], token="second-transport", french=True)
        self.assertFalse(expenses[0].same_receipt_expense_ids)
        self.assertEqual(expenses[1].same_receipt_expense_ids, duplicate)
        self.assertFalse(expenses[2].same_receipt_expense_ids)
        self.assertEqual(duplicate.same_receipt_expense_ids, expenses[1])

    def _message(self, expense):
        return expense.message_ids.filtered(lambda message: message.message_type == "email").sorted("id")[:1]

    def _manual_pdf(self, expense, content):
        return self.env["ir.attachment"].create({
            "name": "Manual receipt.pdf", "mimetype": "application/pdf", "raw": BinaryBytes(content),
            "res_model": "hr.expense", "res_id": expense.id, "company_id": expense.company_id.id,
        })

    def test_manual_correction_supersedes_old_email_identity_and_true_file_duplicate_survives(self):
        expense = self._ingest(REFERENCES[0], token="manual-original")
        peer = self._ingest(REFERENCES[0], token="manual-peer")
        original = expense.message_main_attachment_id
        content = _pdf("Synthetic manually corrected receipt")
        manual = self._manual_pdf(expense, content)
        expense._message_set_main_attachment_id(manual, force=True)
        self.assertFalse(expense._receipt_email_identity())
        self.assertFalse(expense.same_receipt_expense_ids)
        self.assertFalse(peer.same_receipt_expense_ids)
        self.assertIn(original, self._message(expense).attachment_ids)
        other = self.create_expenses({"name": "Atelier Lune manual copy"})
        copied = self._manual_pdf(other, content)
        other._message_set_main_attachment_id(copied, force=True)
        (expense | other).invalidate_recordset(["same_receipt_expense_ids"])
        self.assertEqual(expense.same_receipt_expense_ids, other)
        self.assertEqual(other.same_receipt_expense_ids, expense)
        self.assertEqual(expense.message_main_attachment_id, manual)

    def test_new_receipt_email_supersedes_pending_link_retrieval_generation(self):
        from .test_linked_receipt import TestLinkedReceipt
        linked_email = TestLinkedReceipt._email(self, token="email-copy-pending")
        with patch.dict("os.environ", {"USL_LINKED_PDF_DOWNLOAD_ENABLED": "1"}), patch.object(
            type(self.env["hr.expense"]), "_send_expense_success_mail", return_value=None
        ):
            expense = self.env["mail.thread"].message_process("hr.expense", linked_email)
            retrieval = self.env["usl.mail.pdf.retrieval"].sudo().search([("expense_id", "=", expense.id)])
            self.assertEqual(len(retrieval), 1)
            self.assertIn(retrieval.state, ("queued", "selection_required"))
            generation = retrieval.generation
            with patch.object(type(self.env["ir.actions.report"]), "_run_wkhtmltopdf", return_value=_pdf("Synthetic source email")):
                self.env["mail.thread"].with_context(force_report_rendering=True).message_process(
                    "hr.expense", self._email(REFERENCES[0], token="email-copy-arrived"), thread_id=expense.id
                )
        self.assertEqual(expense.message_main_attachment_id.mimetype, "application/pdf")
        self.assertEqual(retrieval.state, "superseded")
        self.assertEqual(retrieval.generation, generation + 1)
        self.assertFalse(expense.account_move_id)

    def test_approved_email_is_readable_without_new_attachment_or_financial_change(self):
        expense = self.create_expenses({"name": "Atelier Lune historical receipt", "total_amount_currency": 12.34})
        terms = self.env["ir.attachment"].create({
            "name": "source-details.dat", "mimetype": "text/plain", "raw": BinaryBytes(TERMS),
            "res_model": "hr.expense", "res_id": expense.id, "company_id": expense.company_id.id,
        })
        expense._message_set_main_attachment_id(terms, force=True)
        expense.with_user(self.expense_user_employee).action_submit()
        expense.with_user(self.expense_user_manager).action_approve()
        self.assertEqual(expense.state, "approved")
        fields = ["state", "approval_state", "account_move_id", "company_id", "employee_id", "currency_id", "total_amount_currency", "total_amount", "tax_ids", "account_id", "analytic_distribution", "payment_mode", "message_main_attachment_id"]
        before = expense.read(fields)
        attachment_count = self.env["ir.attachment"].search_count([])
        source = expense.message_post(
            body=Markup("<h1>Google Play</h1><p>Order number: %s</p><p>Atelier Lune</p>") % REFERENCES[0],
            message_type="email", subtype_xmlid="mail.mt_comment",
            partner_ids=[], notify_skip_followers=True,
        )
        self.assertEqual(self.env["ir.attachment"].search_count([]), attachment_count)
        self.assertEqual(expense.read(fields), before)
        self.assertEqual(terms.raw.content, TERMS)
        owner = expense.with_user(self.expense_user_employee)
        self.assertTrue(owner.receipt_email_url)
        values = owner._receipt_email_report_sources()
        self.assertEqual(values[0]["message"], source)
        self.assertEqual(expense.read(fields), before)
        self.assertFalse(expense.account_move_id)

    def test_company_correction_keeps_source_visibility_and_does_not_share_private_email(self):
        expense = self._ingest(REFERENCES[0], token="company-source")
        source = self._message(expense)
        pdf = expense.message_main_attachment_id
        self.assertEqual(pdf.res_model, "mail.message")
        self.assertEqual(pdf.res_id, source.id)
        self.assertFalse(pdf.company_id)
        self.assertFalse(pdf.public or pdf.access_token)
        company = self.company_data_2["company"]
        companies = expense.company_id | company
        for user in self.expense_user_employee | self.expense_user_manager | self.env.user:
            user.company_ids |= companies
        target = self.env["hr.employee"].sudo().create({
            "name": "Camille Dune", "company_id": company.id,
            "user_id": self.expense_user_employee.id,
            "expense_manager_id": self.expense_user_manager.id,
        })
        before_gross = expense.total_amount_currency
        expense.with_context(allowed_company_ids=companies.ids).configure_draft_expense({
            "company_id": company.id, "employee_id": target.id,
            "product_id": False, "account_id": False, "tax_ids": [], "analytic_distribution": {},
        })
        self.assertEqual(expense.company_id, company)
        self.assertEqual(expense.message_main_attachment_id, pdf)
        self.assertFalse(pdf.company_id)
        self.assertEqual(expense.total_amount_currency, before_gross)
        reader = self.expense_user_manager
        scoped = expense.with_user(reader).with_context(allowed_company_ids=companies.ids)
        self.assertTrue(scoped.has_access("read"))
        self.assertTrue(source.with_env(scoped.env).has_access("read"))
        self.assertTrue(pdf.with_env(scoped.env).has_access("read"))
        self.assertIn(pdf, scoped.sudo()._get_mail_thread_data_attachments())
        self.env["ir.rule"].sudo().create({
            "name": "Synthetic private source message", "model_id": self.env["ir.model"]._get_id("mail.message"),
            "domain_force": repr([("id", "!=", source.id)]),
            "perm_read": True, "perm_create": False, "perm_write": False, "perm_unlink": False,
        })
        self.env.invalidate_all()
        self.assertTrue(scoped.has_access("read"))
        self.assertFalse(source.with_env(scoped.env).has_access("read"))
        self.assertFalse(pdf.with_env(scoped.env).has_access("read"))
        self.assertNotIn(pdf, scoped.sudo()._get_mail_thread_data_attachments())
        with self.assertRaises(AccessError):
            pdf.with_env(scoped.env).read(["raw"])
        self.assertFalse(scoped.receipt_email_url)
        with self.assertRaises(UserError):
            scoped._receipt_email_report_sources()
        metadata = scoped.read(["message_main_attachment_id"])
        self.assertNotIn(REFERENCES[0], str(metadata))
        self.assertEqual(pdf.name, "Receipt email.pdf")

    def test_duplicate_source_is_scoped_to_native_company_visibility(self):
        expense = self._ingest(REFERENCES[0], token="company-visible")
        other_company = self.company_data_2["company"]
        employee = self.env["hr.employee"].sudo().create({"name": "Alex Corail", "company_id": other_company.id})
        self.env.user.company_ids |= other_company
        foreign = self.env["hr.expense"].with_company(other_company).create({
            "name": "Atelier Corail", "company_id": other_company.id,
            "employee_id": employee.id, "product_id": False, "account_id": False,
            "currency_id": other_company.currency_id.id, "date": "2026-03-04",
            "total_amount_currency": 12.34, "tax_ids": [Command.clear()],
        })
        self.env["mail.message"].create({
            "model": "hr.expense", "res_id": foreign.id, "message_type": "email",
            "body": Markup("<h1>Google Play</h1><p>Order number: %s</p>") % REFERENCES[0],
        })
        reader = self.expense_user_manager
        reader.company_ids |= expense.company_id | other_company
        selected = expense.with_user(reader).with_context(allowed_company_ids=expense.company_id.ids)
        self.assertFalse(foreign.with_env(selected.env).has_access("read"))
        self.assertFalse(selected.same_receipt_expense_ids)
        with self.assertRaises(AccessError):
            foreign.with_env(selected.env)._receipt_email_report_sources()
        both = expense.with_user(reader).with_context(allowed_company_ids=(expense.company_id | other_company).ids)
        self.assertEqual(both.same_receipt_expense_ids, foreign)
        self.assertEqual(foreign.with_env(both.env).same_receipt_expense_ids, expense)
        # Defensive native-context unit test only: no crafted HTTP request or
        # attempt to obtain the inaccessible fixture's contents.
        reports = self.env["ir.actions.report"].with_env(selected.env)
        report = reports._get_report("usl_accounting.expense_receipt_email")
        context = reports._get_rendering_context(report, [expense.id], {
            "doc_ids": foreign.ids, "doc_model": "res.users", "docs": foreign,
        })
        self.assertEqual(context["doc_ids"], expense.ids)
        self.assertEqual(context["doc_model"], "hr.expense")
        self.assertEqual(context["docs"], selected)
        values = context["docs"]._receipt_email_report_sources()
        self.assertEqual(values[0]["message"], self._message(expense))

    def test_native_split_keeps_original_email_available_without_duplicate_warning(self):
        expense = self._ingest(REFERENCES[0], token="split-source")
        source = self._message(expense)
        main = expense.message_main_attachment_id
        gross = expense.total_amount_currency
        first = expense.currency_id.round(gross / 2)
        result = expense.split_draft_expense([
            {"name": "Atelier Lune first part", "total_amount_currency": first},
            {"name": "Atelier Lune second part", "total_amount_currency": gross - first},
        ])
        family = self.env["hr.expense"].search([("split_expense_origin_id", "=", result["origin_id"])])
        self.assertEqual(len(family), 2)
        self.assertEqual(sum(family.mapped("total_amount_currency")), gross)
        self.assertFalse(family.same_receipt_expense_ids)
        self.assertEqual(expense.message_main_attachment_id, main)
        self.assertIn(main, source.attachment_ids)
        for part in family:
            self.assertTrue(part.receipt_email_url)
            values = part._receipt_email_report_sources()
            self.assertEqual(values[0]["message"], source)


@tagged("post_install", "-at_install", "usl_expense_email_receipt", "usl_accounting_unit")
class TestExpenseReceiptEmailEvidence(TransactionCase):
    def test_complete_reference_property_and_private_markup_boundaries(self):
        import random
        from odoo.addons.usl_accounting.tools.expense_receipt_email import receipt_email, incidental_terms
        random_source = random.Random(3796)
        for index in range(40):
            digits = ["".join(str(random_source.randrange(10)) for _ in range(size)) for size in (4, 4, 4, 5)]
            reference = "GPA." + "-".join(digits) + (".." + str(index) if index % 2 else "")
            label = "Numéro de commande" if index % 3 else "Order number"
            body = f"<h1>Google Play</h1><p>{label}: {reference}</p>"
            self.assertEqual(receipt_email(body)["reference"], reference)
            self.assertIsNone(receipt_email(body + f"<p>{REFERENCES[1]}</p>"))
            self.assertIsNone(receipt_email(f'<h1>Google Play</h1><p>{label}:</p><script>{reference}</script>'))
            self.assertIsNone(receipt_email(f'<h1>Google Play</h1><p>{label}:</p><a href="https://example.invalid/{reference}">Open</a>'))
        self.assertTrue(incidental_terms(TERMS))
        self.assertTrue(incidental_terms(TERMS.decode().replace("d'utilisation", "d’utilisation").encode()))
        self.assertFalse(incidental_terms(_pdf("Synthetic genuine receipt with Google Play terms of service")))
        for reference in REFERENCES:
            self.assertEqual(receipt_email(f"Google Play Order number: {reference}")["reference"], reference)
        self.assertIsNone(receipt_email("Google Play Order number: " + REFERENCES[1] + "..1"))

    def test_native_report_labels_have_exact_french_translation_terms(self):
        import polib
        from lxml import etree
        from odoo.tools import file_open
        from odoo.tools.translate import xml_translate
        with file_open("usl_accounting/views/expense_receipt_email_views.xml", "rb") as source:
            template = etree.parse(source).find("template")
        with file_open("usl_accounting/i18n/fr.po") as source:
            catalogue = {term.msgid: term.msgstr for term in polib.pofile(source.read())}
        translated = xml_translate(lambda term: catalogue.get(term, term), etree.tostring(template, encoding="unicode"))
        for label in ("Objet :", "Date :", "Numéro de commande :"):
            self.assertIn("<strong>" + label + "</strong>", translated)
