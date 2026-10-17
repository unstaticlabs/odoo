"""Linked invoice originals reach their exact supplier bill through native mail."""
from email.message import EmailMessage
from unittest.mock import patch

from odoo.addons.hr_expense.tests.common import TestExpenseCommon
from odoo.tests import tagged


@tagged("post_install", "-at_install", "usl_vendor_linked_receipt")
class TestVendorLinkedReceipt(TestExpenseCommon):
    def _email(self, suffix="discovery"):
        message = EmailMessage()
        message["From"] = "Synthetic supplier <supplier@example.invalid>"
        message["To"] = "purchases@example.invalid"
        message["Subject"] = "[QA linked invoice] Original supplier document"
        message["Message-ID"] = f"<qa-vendor-{suffix}@example.invalid>"
        message.set_content("Download the original invoice.")
        message.add_alternative(
            '<p>Your invoice is ready.</p><a href="https://receipts.example.com/invoices/qa/download?token=synthetic-only">Download PDF invoice</a>',
            subtype="html",
        )
        return message.as_bytes()

    def _ingest(self, suffix="discovery"):
        with patch.dict("os.environ", {"USL_LINKED_PDF_DOWNLOAD_ENABLED": "1"}):
            return self.env["mail.thread"].message_process(
                "account.move", self._email(suffix), custom_values={
                    "move_type": "in_invoice",
                    "journal_id": self.company_data["default_journal_purchase"].id,
                    "company_id": self.env.company.id,
                },
            )

    def test_supplier_email_without_file_discovers_link_on_exact_bill(self):
        bill = self._ingest()
        self.assertEqual(bill.move_type, "in_invoice")
        self.assertFalse(bill.message_main_attachment_id)
        retrieval = self.env["usl.mail.pdf.retrieval"].sudo().search([("move_id", "=", bill.id)])
        self.assertEqual(len(retrieval), 1)
        self.assertFalse(retrieval.expense_id)
        self.assertEqual(retrieval.company_id, bill.company_id)
        self.assertEqual(retrieval.source_message_id.model, "account.move")
        self.assertEqual(retrieval.source_message_id.res_id, bill.id)
        self.assertEqual(retrieval.state, "selection_required")

    def _request(self, bill):
        with patch.dict("os.environ", {"USL_LINKED_PDF_DOWNLOAD_ENABLED": "1"}):
            bill.action_accept_linked_receipt()
        return self.env["usl.mail.pdf.retrieval"].sudo().search([("move_id", "=", bill.id)])

    def _complete(self, retrieval, callback=None):
        from hashlib import sha256
        from odoo.addons.base.tests.files import PDF_RAW
        def fetch(*args):
            if callback:
                callback()
            return PDF_RAW, "Synthetic original invoice.pdf", sha256(PDF_RAW).hexdigest(), {"fetch_mode": "http", "redirect_hosts": "[]"}
        with patch.object(type(retrieval), "_feature_enabled", return_value=True), patch.object(
            type(retrieval), "_fetcher_request", side_effect=fetch,
        ):
            retrieval._job_fetch_receipt()
        return PDF_RAW

    def test_native_purchase_email_to_original_pdf_main_and_history(self):
        from hashlib import sha256
        bill = self._ingest("complete")
        # Find by the existing source relation so the original code fails for
        # the missing business outcome rather than for an adapter field name.
        requests = self.env["usl.mail.pdf.retrieval"].sudo().search([
            ("source_message_id", "in", bill.message_ids.ids),
        ])
        self.assertEqual(len(requests), 1, "A link-only supplier email must offer receipt recovery on its bill")
        before = (bill.state, bill.amount_total, bill.amount_residual, bill.line_ids.ids)
        retrieval = self._request(bill)
        self.assertEqual(retrieval.state, "queued")
        content = self._complete(retrieval)
        self.assertEqual(retrieval.state, "succeeded")
        self.assertEqual(bytes(bill.message_main_attachment_id.raw), content)
        self.assertEqual(bill.message_main_attachment_id.res_model, "account.move")
        self.assertEqual(bill.message_main_attachment_id.res_id, bill.id)
        self.assertEqual(retrieval.sha256, sha256(content).hexdigest())
        self.assertEqual(bill.message_main_attachment_id.name, "Synthetic original invoice.pdf")
        self.assertTrue(retrieval.source_message_id.exists())
        self.assertTrue(any("linked PDF receipt was downloaded" in str(m.body) for m in bill.message_ids))
        self.assertEqual((bill.state, bill.amount_total, bill.amount_residual, bill.line_ids.ids), before)
        attachment = bill.message_main_attachment_id
        self._complete(retrieval)
        self.assertEqual(bill.message_main_attachment_id, attachment)
        self.assertEqual(len(bill.attachment_ids), 1)

    def test_same_name_bills_keep_their_own_original(self):
        first, second = self._ingest("same1"), self._ingest("same2")
        self.assertEqual(first.name, second.name)
        self._complete(self._request(first))
        self.assertTrue(first.message_main_attachment_id)
        self.assertFalse(second.message_main_attachment_id)

    def test_changed_source_during_fetch_cannot_attach_original_from_old_link(self):
        bill = self._ingest("source-change")
        retrieval = self._request(bill)
        self._complete(retrieval, lambda: retrieval.source_message_id.write({"body": "The supplier replaced this download link."}))
        self.assertEqual(retrieval.state, "superseded")
        self.assertFalse(bill.message_main_attachment_id)
        self.assertFalse(bill.attachment_ids)

    def test_manual_original_during_fetch_wins(self):
        bill = self._ingest("manual-change")
        retrieval = self._request(bill)
        def attach():
            original = self.env["ir.attachment"].create({
                "name": "Synthetic manually supplied invoice.pdf", "raw": b"original manual bytes",
                "mimetype": "application/pdf", "res_model": "account.move", "res_id": bill.id,
            })
            bill.message_main_attachment_id = original
        self._complete(retrieval, attach)
        self.assertEqual(retrieval.state, "superseded")
        self.assertEqual(bytes(bill.message_main_attachment_id.raw), b"original manual bytes")
        self.assertEqual(len(bill.attachment_ids), 1)

    def test_cancelled_bill_supersedes_pending_work(self):
        bill = self._ingest("cancelled")
        retrieval = self._request(bill)
        bill.button_cancel()
        with patch.object(type(retrieval), "_fetcher_request") as fetch, patch.object(type(retrieval), "_feature_enabled", return_value=True):
            retrieval._job_fetch_receipt()
        fetch.assert_not_called()
        self.assertEqual(retrieval.state, "superseded")
        self.assertFalse(bill.message_main_attachment_id)

    def test_manual_file_skips_historical_scan(self):
        bill = self._ingest("rescan")
        retrieval = self.env["usl.mail.pdf.retrieval"].sudo().search([("move_id", "=", bill.id)])
        retrieval.unlink()
        bill.message_post(body="Synthetic manual evidence", attachments=[("manual.pdf", b"synthetic evidence")])
        with patch.object(type(retrieval), "_feature_enabled", return_value=True):
            result = bill.action_scan_existing_receipt_emails()
        self.assertIn("0", result["params"]["message"])
        self.assertFalse(self.env["usl.mail.pdf.retrieval"].sudo().search([("move_id", "=", bill.id)]))

    def _writer(self, name="writer", readonly=False):
        from odoo import Command
        group = self.env.ref("account.group_account_readonly" if readonly else "account.group_account_user")
        return self.env["res.users"].with_context(no_reset_password=True).create({
            "name": "[QA linked bill] " + name, "login": "qa.linked.bill." + name,
            "email": name + "@example.invalid", "company_id": self.env.company.id,
            "company_ids": [Command.set(self.env.company.ids)],
            "group_ids": [Command.set((self.env.ref("base.group_user") | group).ids)],
            "usl_identity_classification": "active",
        })

    def test_native_writer_can_request_but_readonly_user_cannot(self):
        from odoo.exceptions import AccessError
        bill = self._ingest("native-authority")
        writer, reader = self._writer(), self._writer("reader", readonly=True)
        retrieval = self.env["usl.mail.pdf.retrieval"].sudo().search([("move_id", "=", bill.id)])
        with self.assertRaises(AccessError):
            retrieval.with_user(reader).action_accept_suggestion()
        with patch.object(type(retrieval), "_feature_enabled", return_value=True):
            bill.with_user(writer).action_accept_linked_receipt()
        self.assertEqual(retrieval.bill_request_user_id, writer)
        self._complete(retrieval)
        self.assertEqual(retrieval.state, "succeeded")

    def test_requester_authority_loss_during_fetch_does_not_publish(self):
        from odoo import Command
        bill = self._ingest("authority-change")
        writer = self._writer()
        retrieval = self._request(bill.with_user(writer))
        self._complete(retrieval, lambda: writer.write({"group_ids": [Command.set(self.env.ref("base.group_user").ids)]}))
        self.assertEqual(retrieval.state, "needs_attention")
        self.assertEqual(retrieval.failure_code, "target_access_changed")
        self.assertFalse(bill.message_main_attachment_id)

    def test_foreign_selected_company_cannot_accept_or_open(self):
        from odoo.exceptions import AccessError
        bill = self._ingest("foreign-company")
        writer = self._writer()
        retrieval = self.env["usl.mail.pdf.retrieval"].sudo().search([("move_id", "=", bill.id)])
        foreign = retrieval.with_user(writer).with_context(allowed_company_ids=self.company_data_2["company"].ids)
        with self.assertRaises(AccessError):
            foreign.action_accept_suggestion()
        with self.assertRaises(AccessError):
            foreign._check_can_open_handoff()
        self.assertEqual(retrieval.state, "selection_required")

    def test_bill_company_changed_before_worker_supersedes_without_fetch(self):
        bill = self._ingest("company-change")
        retrieval = self._request(bill)
        bill.write({"company_id": self.company_data_2["company"].id,
                    "journal_id": self.company_data_2["default_journal_purchase"].id})
        with patch.object(type(retrieval), "_feature_enabled", return_value=True), patch.object(type(retrieval), "_fetcher_request") as fetch:
            retrieval._job_fetch_receipt()
        fetch.assert_not_called()
        self.assertEqual(retrieval.state, "superseded")
        self.assertFalse(bill.message_main_attachment_id)

    def test_source_changed_while_queued_is_not_fetched(self):
        bill = self._ingest("queued-source")
        retrieval = self._request(bill)
        retrieval.source_message_id.write({"body": "A newer supplier message replaced this source."})
        with patch.object(type(retrieval), "_feature_enabled", return_value=True), patch.object(type(retrieval), "_fetcher_request") as fetch:
            retrieval._job_fetch_receipt()
        fetch.assert_not_called()
        self.assertEqual(retrieval.state, "superseded")
        self.assertTrue(bill.linked_receipt_message)

    def test_posted_bill_gains_original_without_changing_financial_lines(self):
        from odoo import Command, fields
        bill = self._ingest("posted")
        bill.write({"partner_id": self.partner_a.id, "invoice_date": fields.Date.today(),
                    "invoice_line_ids": [Command.create({"name": "Synthetic service", "account_id": self.company_data["default_account_expense"].id,
                                                         "quantity": 1, "price_unit": 42, "tax_ids": [Command.clear()]})]})
        bill.action_post()
        before = bill.line_ids.read(["account_id", "debit", "credit", "amount_currency", "currency_id", "amount_residual", "full_reconcile_id"])
        retrieval = self._request(bill)
        self._complete(retrieval)
        self.assertEqual(retrieval.state, "succeeded")
        self.assertEqual(bill.state, "posted")
        self.assertEqual(bill.line_ids.read(["account_id", "debit", "credit", "amount_currency", "currency_id", "amount_residual", "full_reconcile_id"]), before)

    def test_cancel_and_retry_keep_source_and_existing_expense_target(self):
        bill = self._ingest("dismiss")
        retrieval = self.env["usl.mail.pdf.retrieval"].sudo().search([("move_id", "=", bill.id)])
        source = retrieval.source_message_id
        bill.action_dismiss_linked_receipt()
        self.assertEqual(retrieval.state, "dismissed")
        self.assertTrue(source.exists())
        self.assertFalse(retrieval.expense_id)

    def test_file_without_main_added_during_fetch_is_preserved(self):
        bill = self._ingest("unselected-file")
        retrieval = self._request(bill)
        def attach():
            self.env["ir.attachment"].create({"name": "Manual original without selected main.pdf", "raw": b"manual exact bytes",
                                               "mimetype": "application/pdf", "res_model": "account.move", "res_id": bill.id})
        self._complete(retrieval, attach)
        self.assertEqual(retrieval.state, "superseded")
        self.assertFalse(bill.message_main_attachment_id)
        self.assertEqual(len(bill.attachment_ids), 1)
        self.assertEqual(bytes(bill.attachment_ids.raw), b"manual exact bytes")

    def test_delegated_writer_uses_native_bill_actions_and_readonly_cannot(self):
        from odoo import Command
        from odoo.exceptions import AccessError
        from odoo.addons.usl_access_control.controllers.json2 import UslAgentJson2Controller
        bill = self._ingest("agent")
        writer = self._writer()
        group = self.env.ref("account.group_account_invoice")
        writer.group_ids = [Command.set((self.env.ref("base.group_user") | group).ids)]
        self.assertFalse(writer.has_group("account.group_account_readonly"))
        agent = self.env["usl.agent"].with_user(writer).create({
            "name": "[QA linked bill] Collaborator", "purpose": "Attach original supplier evidence.",
            "owner_id": writer.id, "company_id": self.env.company.id,
            "company_ids": [Command.set(self.env.company.ids)],
            "delegated_group_ids": [Command.set(group.ids)], "read_only_group_ids": [Command.clear()],
            "access_mode": "read_write",
        })
        context = UslAgentJson2Controller._agent_call_context(
            context={"allowed_company_ids": self.env.company.ids}, agent=agent,
            model_name="account.move", method_name="action_accept_linked_receipt", access="write",
        )
        retrieval = self.env["usl.mail.pdf.retrieval"].sudo().search([("move_id", "=", bill.id)])
        with patch.object(type(retrieval), "_feature_enabled", return_value=True):
            bill.with_user(agent.user_id).with_context(context).action_accept_linked_receipt()
        self.assertEqual(retrieval.bill_request_user_id, agent.user_id)
        agent.with_user(writer).write({"read_only_group_ids": [Command.set(group.ids)]})
        with self.assertRaises(AccessError):
            bill.with_user(agent.user_id).action_dismiss_linked_receipt()
        self._complete(retrieval)
        self.assertEqual(retrieval.state, "needs_attention")
        self.assertEqual(retrieval.failure_code, "target_access_changed")
        self.assertFalse(bill.message_main_attachment_id)

    def test_delegated_worker_retains_native_request_after_rpc_context_ends(self):
        from odoo import Command
        from odoo.addons.usl_access_control.controllers.json2 import UslAgentJson2Controller
        bill = self._ingest("agent-worker")
        writer = self._writer()
        group = self.env.ref("account.group_account_user")
        agent = self.env["usl.agent"].with_user(writer).create({
            "name": "[QA linked bill] Background collaborator", "purpose": "Retrieve original evidence.",
            "owner_id": writer.id, "company_id": self.env.company.id,
            "company_ids": [Command.set(self.env.company.ids)],
            "delegated_group_ids": [Command.set(group.ids)], "read_only_group_ids": [Command.clear()],
            "access_mode": "read_write",
        })
        context = UslAgentJson2Controller._agent_call_context(
            context={"allowed_company_ids": self.env.company.ids}, agent=agent,
            model_name="account.move", method_name="action_accept_linked_receipt", access="write",
        )
        retrieval = self.env["usl.mail.pdf.retrieval"].sudo().search([("move_id", "=", bill.id)])
        with patch.object(type(retrieval), "_feature_enabled", return_value=True):
            bill.with_user(agent.user_id).with_context(context).action_accept_linked_receipt()
        # OCA serializes a small native context, not the request's in-process
        # authorization objects. The worker must complete under the same actor.
        worker = retrieval.with_user(agent.user_id).with_context(allowed_company_ids=self.env.company.ids)
        job = self.env["queue.job"].sudo().search([
            ("identity_key", "=", f"receipt-fetch:{retrieval.id}:{retrieval.generation}"),
        ], limit=1)
        self.assertTrue(job)
        worker = worker.with_context(job_uuid=job.uuid)
        prepared = worker._bill_worker_recordset()
        self.assertTrue(prepared)
        self.assertEqual(prepared.env.uid, agent.user_id.id)
        # This native technical write previously failed after context loss.
        prepared.sudo().write({"state": "running"})
        self.assertEqual(retrieval.state, "running")
        self.assertTrue(prepared._bill_request_is_authorized())
        forged = worker.with_context(job_uuid="not-a-stored-job")._bill_worker_recordset()
        self.assertFalse(forged)
        self.assertFalse(worker.with_context(job_uuid=False)._bill_worker_recordset())
        editable_job = job.with_context(_job_edit_sentinel=job.EDIT_SENTINEL)
        for field, invalid in (("method_name", "action_retry"),
                               ("identity_key", f"receipt-fetch:{retrieval.id}:999"),
                               ("company_id", self.company_data_2["company"].id),
                               ("records", self.env["usl.mail.pdf.retrieval"].browse()),
                               ("user_id", self.env.user.id)):
            original = job[field]
            with self.subTest(queue_identity_field=field):
                try:
                    editable_job.write({field: invalid})
                    self.assertFalse(worker._bill_worker_recordset())
                finally:
                    editable_job.write({field: original.id if field in ("company_id", "user_id") else original})

    def test_invoicing_only_writer_uses_native_bill_visibility(self):
        from odoo import Command
        bill = self._ingest("invoicing-only")
        writer = self._writer("invoicing-only")
        writer.group_ids = [Command.set((self.env.ref("base.group_user")
                                        | self.env.ref("account.group_account_invoice")).ids)]
        self.assertFalse(writer.has_group("account.group_account_readonly"))
        self.assertTrue(bill.with_user(writer).has_access("write"))
        with patch.object(type(self.env["usl.mail.pdf.retrieval"]), "_feature_enabled", return_value=True):
            bill.with_user(writer).action_accept_linked_receipt()
        retrieval = bill.with_user(writer)._latest_linked_receipt()
        self.assertEqual(retrieval.state, "queued")
        self.assertEqual(retrieval.bill_request_user_id, writer)
        employee_only = self._writer("employee-only")
        employee_only.group_ids = [Command.set(self.env.ref("base.group_user").ids)]
        self.assertFalse(self.env["usl.mail.pdf.retrieval"].with_user(employee_only).search([
            ("move_id", "=", bill.id),
        ]))

    def test_native_upload_widget_callback_selects_original_and_preserves_files(self):
        from odoo.addons.base.tests.files import PDF_RAW
        from odoo.exceptions import UserError
        bill, other = self._ingest("widget"), self._ingest("widget-other")
        retrieval = self._request(bill)
        attachment = self.env["ir.attachment"].create({
            "name": "Synthetic manually downloaded invoice.pdf", "raw": PDF_RAW,
            "res_model": "account.move", "res_id": bill.id,
        })
        with self.assertRaises(UserError):
            other.action_attach_linked_receipt(attachment_ids=attachment.ids)
        # Exact RPC signature used by AttachDocumentWidget.onFileUploaded.
        self.assertTrue(bill.action_attach_linked_receipt(attachment_ids=attachment.ids))
        self.assertEqual(bill.message_main_attachment_id, attachment)
        self.assertEqual(retrieval.state, "superseded")
        self.assertEqual(bytes(attachment.raw), PDF_RAW)
        later = attachment.copy({"name": "Synthetic additional receipt.pdf"})
        bill.action_attach_linked_receipt(attachment_ids=later.ids)
        self.assertEqual(bill.message_main_attachment_id, attachment)
        self.assertTrue(later.exists())

    def test_selected_company_bill_does_not_have_to_be_current_company(self):
        from markupsafe import Markup
        from odoo import Command
        first, second = self.env.company, self.company_data_2["company"]
        writer = self._writer("both-companies")
        writer.company_ids = [Command.set((first | second).ids)]
        bill = self.env["account.move"].with_company(second).create({
            "move_type": "in_invoice", "company_id": second.id,
            "journal_id": self.company_data_2["default_journal_purchase"].id,
        })
        with patch.object(type(self.env["usl.mail.pdf.retrieval"]), "_feature_enabled", return_value=True):
            bill.message_post(body=Markup('<a href="https://receipts.example.com/invoices/both/download">Download invoice PDF</a>'), message_type="email")
            native = bill.with_user(writer).with_context(allowed_company_ids=[first.id, second.id])
            self.assertEqual(native.env.company, first)
            native.action_accept_linked_receipt()
        retrieval = native._latest_linked_receipt()
        job = self.env["queue.job"].sudo().search([
            ("identity_key", "=", f"receipt-fetch:{retrieval.id}:{retrieval.generation}"),
        ], limit=1)
        self.assertEqual(job.company_id, second)
        self.assertEqual(set(job.records.env.companies.ids), {first.id, second.id})
        self.assertTrue(retrieval.with_user(writer).with_context(
            allowed_company_ids=job.records.env.companies.ids, job_uuid=job.uuid,
        )._bill_worker_recordset())

    def test_french_status_translation_on_bill_and_existing_expense(self):
        from markupsafe import Markup
        bill = self._ingest("french-status")
        expense = self.create_expenses({"name": "[QA] French existing expense status"})
        with patch.object(type(self.env["usl.mail.pdf.retrieval"]), "_feature_enabled", return_value=True):
            expense.message_post(body=Markup('<a href="https://receipts.example.com/invoices/french/download">Download receipt PDF</a>'), message_type="email")
        requests = self.env["usl.mail.pdf.retrieval"].sudo().search([
            "|", ("move_id", "=", bill.id), ("expense_id", "=", expense.id),
        ])
        self.assertEqual(len(requests), 2)
        self.assertTrue(bill.with_context(lang="fr_FR").linked_receipt_message.startswith("Odoo a retenu "))
        self.assertTrue(expense.with_context(lang="fr_FR").linked_receipt_message.startswith("Odoo a retenu "))
        requests.write({"state": "queued"})
        # The user reloads the bill after a public recovery action. In this
        # single test transaction, explicitly refresh the non-stored status.
        bill.invalidate_recordset()
        expense.invalidate_recordset()
        expected = "Le téléchargement des justificatifs est suspendu dans cet environnement. Joignez le PDF manuellement ou demandez à un opérateur de rétablir le traitement."
        with patch.object(type(requests), "_queue_runtime_status", return_value="paused"):
            self.assertEqual(bill.with_context(lang="fr_FR").linked_receipt_message, expected)
            self.assertEqual(expense.with_context(lang="fr_FR").linked_receipt_message, expected)


    def _picker_bill(self, suffix, labels):
        message = EmailMessage()
        message["From"] = "Synthetic supplier <supplier@example.invalid>"
        message["To"] = "purchases@example.invalid"
        message["Subject"] = "[QA] Original invoice documents"
        message["Message-ID"] = f"<qa-picker-{suffix}@example.invalid>"
        message.set_content("Your invoice documents are available.")
        message.add_alternative("<p>Invoice PDF documents</p>" + "".join(
            f'<a href="https://picker.example.com/invoices/{index}/download?token=synthetic-private">{label}</a>'
            for index, label in enumerate(labels)
        ), subtype="html")
        with patch.object(type(self), "_email", return_value=message.as_bytes()):
            return self._ingest(suffix)

    def test_bill_picker_uses_distinct_source_copy_and_exact_original_choice(self):
        import hashlib
        import json
        bill = self._picker_bill("distinct", ["Invoice PDF", "Detailed invoice PDF"])
        retrieval = bill._latest_linked_receipt()
        self.assertEqual(retrieval.state, "selection_required")
        before = json.dumps(retrieval.candidate_features, sort_keys=True)
        with patch.dict("os.environ", {"USL_LINKED_PDF_DOWNLOAD_ENABLED": "1"}):
            action = bill.action_review_linked_receipt()
            wizard = self.env["usl.mail.pdf.candidate.wizard"].browse(action["res_id"])
            self.assertEqual(set(wizard.candidate_ids.mapped("label")), {"Invoice PDF", "Detailed invoice PDF"})
            line = wizard.candidate_ids.filtered(lambda row: row.label == "Detailed invoice PDF")
            expected = hashlib.sha256(b"https://picker.example.com/invoices/1/download?token=synthetic-private").hexdigest()
            self.assertEqual(line.fingerprint, expected)
            self.assertEqual(json.dumps(retrieval.candidate_features, sort_keys=True), before)
            line.action_choose()
            self.assertEqual(retrieval.selected_fingerprint, expected)
            self.assertNotIn("Detailed", json.dumps(retrieval.candidate_features))
            self.assertNotIn("Detailed", retrieval.pattern_id.label_tokens or "")
        self._complete(retrieval)
        self.assertTrue(bill.message_main_attachment_id)
        self.assertEqual(bill.message_main_attachment_id, retrieval.attachment_id)

    def test_bill_picker_ambiguous_or_url_labels_use_honest_source_order(self):
        for suffix, labels in (("identical", ["Invoice PDF", "Invoice PDF"]), ("empty", ["", "https://picker.example.com/invoices/1/download?token=synthetic-private"])):
            bill = self._picker_bill(suffix, labels)
            action = bill.action_review_linked_receipt()
            wizard = self.env["usl.mail.pdf.candidate.wizard"].browse(action["res_id"])
            shown = wizard.candidate_ids.mapped("label")
            self.assertEqual(len(shown), 2)
            self.assertTrue(all("source email" in label for label in shown))
            self.assertTrue(any("1" in label for label in shown))
            self.assertTrue(any("2" in label for label in shown))
            self.assertFalse(any("https" in label.casefold() or "never-display" in label for label in shown))

    def test_existing_expense_picker_keeps_its_semantic_labels(self):
        from markupsafe import Markup
        expense = self.create_expenses({"name": "[QA] Existing expense picker"})
        with patch.object(type(self.env["usl.mail.pdf.retrieval"]), "_feature_enabled", return_value=True):
            expense.message_post(body=Markup('<a href="https://picker.example.com/invoices/expense/download">Detailed invoice PDF</a>'), message_type="email")
        action = expense.action_review_linked_receipt()
        wizard = self.env["usl.mail.pdf.candidate.wizard"].browse(action["res_id"])
        self.assertEqual(wizard.candidate_ids.mapped("label"), ["Invoice PDF"])

    def test_french_picker_view_fields_and_fallback_are_translated(self):
        from lxml import etree
        Wizard = self.env["usl.mail.pdf.candidate.wizard"].with_context(lang="fr_FR")
        arch = Wizard.get_view(self.env.ref("usl_accounting.view_usl_mail_pdf_candidate_wizard_form").id, "form")["arch"]
        root = etree.fromstring(arch.encode())
        self.assertTrue("Choisissez le lien qui télécharge" in " ".join(root.itertext()))
        self.assertEqual(set(root.xpath("//button[@name='action_choose']/@string")), {"Utiliser ce lien"})
        self.assertEqual(root.xpath("//button[@special='cancel']/@string"), ["Annuler"])
        info = self.env["usl.mail.pdf.candidate.wizard.line"].with_context(lang="fr_FR").fields_get(["label", "hostname"])
        self.assertEqual(info["label"]["string"], "Libellé")
        self.assertEqual(info["hostname"]["string"], "Nom d’hôte")
        bill = self._picker_bill("french-empty", ["", ""])
        candidates = bill._latest_linked_receipt()._extract_candidates(bill._latest_linked_receipt().source_message_id)
        self.assertEqual(set(Wizard._bill_display_labels(bill._latest_linked_receipt(), candidates).values()), {"Lien 1 dans l’e-mail source", "Lien 2 dans l’e-mail source"})
