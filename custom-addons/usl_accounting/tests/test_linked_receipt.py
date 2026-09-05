from email.message import EmailMessage
import hashlib
import json
from unittest.mock import patch

from odoo import Command
from odoo.addons.queue_job.exception import RetryableJobError
from odoo.addons.queue_job.job import Job
from odoo.exceptions import AccessError, UserError
from odoo.tests import tagged

from odoo.addons.hr_expense.tests.common import TestExpenseCommon

from ..models.linked_receipt import (
    ReceiptFetchError,
    _LINKED_RECEIPT_INTERNAL,
    _safe_fetch_failure_message,
    _safe_filename,
    _safe_redirect_evidence,
)


@tagged(
    "post_install",
    "-at_install",
    "usl_accounting_linked_receipt",
    "usl_accounting_unit",
)
class TestLinkedReceipt(TestExpenseCommon):
    def _email(
        self,
        *,
        token="first-secret-token-value",
        extra_link="",
        attachment=False,
        url=None,
    ):
        message = EmailMessage()
        message["From"] = self.expense_user_employee.email
        message["To"] = "expenses@example.invalid"
        message["Subject"] = f"{self.product_c.default_code} Uber trip EUR 24.50"
        message["Message-ID"] = f"<{token}@example.invalid>"
        message.set_content("Download receipt")
        message.add_alternative(
            f"""
            <html><body>
              <p>Your ride is complete.</p>
              <a href="{url or f'https://receipts.example.com/trips/9f4d9a829c45a18f/download?token={token}&amp;locale=en'}">
                Download PDF receipt
              </a>
              {extra_link}
            </body></html>
            """,
            subtype="html",
        )
        if attachment:
            message.add_attachment(
                b"%PDF-1.4\nattached\n%%EOF\n",
                maintype="application",
                subtype="pdf",
                filename="attached.pdf",
            )
        return message.as_bytes()

    def _ingest(self, **values):
        with patch.dict(
            "os.environ",
            {"USL_LINKED_PDF_DOWNLOAD_ENABLED": "1"},
        ):
            return self.env["mail.thread"].message_process(
                "hr.expense",
                self._email(**values),
            )

    def _authentication_retrieval(self, *, token="authentication-handoff"):
        expense = self._ingest(token=token)
        retrieval = self.env["usl.mail.pdf.retrieval"].sudo().search(
            [("expense_id", "=", expense.id)],
        )
        candidate = retrieval._extract_candidates(retrieval.source_message_id)[0]
        retrieval._select_candidate(candidate["fingerprint"], teach=True)
        retrieval.write(
            {
                "state": "needs_attention",
                "failure_code": "authentication_required",
                "failure_message": "The receipt page requires authentication.",
            }
        )
        return expense, retrieval, candidate

    def test_disabled_feature_does_not_create_user_visible_work(self):
        with patch.dict(
            "os.environ",
            {"USL_LINKED_PDF_DOWNLOAD_ENABLED": "0"},
        ):
            expense = self.env["mail.thread"].message_process(
                "hr.expense",
                self._email(token="disabled-feature-secret"),
            )

        self.assertFalse(
            self.env["usl.mail.pdf.retrieval"].sudo().search(
                [("expense_id", "=", expense.id)]
            )
        )

    def test_full_mime_ingestion_creates_recoverable_expense_and_private_snapshot(self):
        expense = self._ingest()

        retrieval = self.env["usl.mail.pdf.retrieval"].sudo().search(
            [("expense_id", "=", expense.id)],
        )
        self.assertEqual(retrieval.state, "selection_required")
        self.assertEqual(len(retrieval.candidate_features), 1)
        serialized = str(retrieval.candidate_features)
        self.assertNotIn("first-secret-token-value", serialized)
        self.assertNotIn("9f4d9a829c45a18f", serialized)
        self.assertIn("{id}", serialized)
        self.assertFalse(retrieval.selected_fingerprint)

    def _historical_expense(self, **values):
        with patch.dict("os.environ", {"USL_LINKED_PDF_DOWNLOAD_ENABLED": "0"}):
            return self.env["mail.thread"].message_process("hr.expense", self._email(**values))

    def test_historical_scan_is_idempotent_and_preserves_expense(self):
        expense = self._historical_expense(token="historical-scan")
        before = (expense.state, expense.total_amount, expense.company_id)
        with patch.dict("os.environ", {"USL_LINKED_PDF_DOWNLOAD_ENABLED": "1"}):
            expense.with_user(self.expense_user_employee).action_scan_existing_receipt_emails()
            expense.with_user(self.expense_user_employee).action_scan_existing_receipt_emails()
        retrievals = self.env["usl.mail.pdf.retrieval"].sudo().search([("expense_id", "=", expense.id)])
        self.assertEqual(len(retrievals), 1)
        self.assertEqual(retrievals.state, "selection_required")
        self.assertEqual((expense.state, expense.total_amount, expense.company_id), before)

    def test_historical_scan_skips_receipts_and_non_drafts(self):
        attached = self._historical_expense(token="historical-attached", attachment=True)
        refused = self._historical_expense(token="historical-refused")
        refused.state = "refused"
        with patch.dict("os.environ", {"USL_LINKED_PDF_DOWNLOAD_ENABLED": "1"}):
            (attached | refused).with_user(self.expense_user_employee).action_scan_existing_receipt_emails()
        self.assertFalse(self.env["usl.mail.pdf.retrieval"].sudo().search([
            ("expense_id", "in", (attached | refused).ids),
        ]))

    def test_historical_scan_checks_authority_and_feature_gate(self):
        expense = self._historical_expense(token="historical-authority")
        with patch.dict("os.environ", {"USL_LINKED_PDF_DOWNLOAD_ENABLED": "0"}):
            with self.assertRaises(UserError):
                expense.with_user(self.expense_user_employee).action_scan_existing_receipt_emails()
        with patch.dict("os.environ", {"USL_LINKED_PDF_DOWNLOAD_ENABLED": "1"}):
            with self.assertRaises(AccessError):
                expense.with_user(self.expense_user_manager_2).action_scan_existing_receipt_emails()

    def test_historical_scan_skips_chatter_receipt_and_empty_email(self):
        attached = self._historical_expense(token="historical-chatter")
        attached.message_post(attachments=[("receipt.pdf", b"%PDF-1.4\nreceipt\n%%EOF\n")])
        empty = self._historical_expense(token="historical-empty")
        empty.message_ids.filtered(lambda message: message.message_type == "email").body = "No links here."
        with patch.dict("os.environ", {"USL_LINKED_PDF_DOWNLOAD_ENABLED": "1"}):
            action = (attached | empty).with_user(self.expense_user_employee).action_scan_existing_receipt_emails()
        self.assertEqual(action["params"]["type"], "info")
        self.assertFalse(self.env["usl.mail.pdf.retrieval"].sudo().search([
            ("expense_id", "in", (attached | empty).ids),
        ]))

    def test_historical_scan_rejects_other_company_record(self):
        expense = self._historical_expense(token="historical-company")
        other_company = self.env["res.company"].create({"name": "Receipt scan other company"})
        other_user = self.env["res.users"].sudo().create({
            "name": "Other company receipt manager", "login": "other-company-receipt-manager",
            "company_id": other_company.id, "company_ids": [Command.set(other_company.ids)],
            "group_ids": [Command.set(self.env.ref("account.group_account_manager").ids)],
        })
        with patch.dict("os.environ", {"USL_LINKED_PDF_DOWNLOAD_ENABLED": "1"}):
            with self.assertRaises(AccessError):
                expense.with_user(other_user).with_context(
                    allowed_company_ids=other_company.ids,
                ).action_scan_existing_receipt_emails()

    def test_user_choice_teaches_positive_and_bounded_negative_examples(self):
        expense = self._ingest(
            extra_link='<a href="https://files.example.com/invoice.pdf?signature=other-secret">Invoice PDF</a>',
        )
        retrieval = self.env["usl.mail.pdf.retrieval"].sudo().search(
            [("expense_id", "=", expense.id)],
        )
        candidates = retrieval._extract_candidates(retrieval.source_message_id)

        retrieval.with_user(self.expense_user_employee)._select_candidate(
            candidates[0]["fingerprint"],
            teach=True,
        )

        self.assertEqual(retrieval.pattern_id.positive_count, 1)
        rejected = self.env["usl.mail.pdf.pattern"].sudo().search(
            [("signature", "=", candidates[1]["signature"])],
        )
        self.assertEqual(rejected.negative_count, 1)
        self.assertEqual(
            self.env["usl.mail.pdf.host"].sudo().search(
                [("hostname", "=", candidates[0]["hostname"])],
            ).state,
            "provisional",
        )

    def test_candidate_picker_persists_lines_before_row_action(self):
        expense = self._ingest()
        retrieval = self.env["usl.mail.pdf.retrieval"].sudo().search(
            [("expense_id", "=", expense.id)]
        )

        with patch.object(type(retrieval), "_enqueue") as enqueue:
            action = expense.with_user(self.expense_user_employee).action_review_linked_receipt()
            wizard = self.env["usl.mail.pdf.candidate.wizard"].browse(action["res_id"])
            self.assertTrue(wizard.exists())
            self.assertTrue(wizard.candidate_ids.ids)
            self.assertEqual(wizard.candidate_ids[0].label, "Download PDF receipt")
            wizard.candidate_ids[0].with_user(self.expense_user_employee).action_choose()

        enqueue.assert_called_once()
        self.env.flush_all()
        retrieval.invalidate_recordset()
        self.assertTrue(retrieval.selected_fingerprint)

    def test_extraction_supports_plaintext_caps_candidates_and_drops_negative_links(self):
        anchors = []
        for index in range(12):
            button_attributes = ' class="btn" role="button"' if index == 0 else ""
            anchors.append(
                f'<a{button_attributes} href="https://files.example.com/receipt/'
                f'{100000 + index}.pdf?signature=token-{index}">'
                f"Receipt PDF {index}</a>"
            )
        body = "\n".join(anchors)
        body += '<a href="https://example.com/unsubscribe">Unsubscribe</a>'
        message = self.env["mail.message"].sudo().create(
            {
                "subject": "Reçu du trajet 123456789",
                "email_from": "factures@provider.example",
                "body": body,
                "message_type": "email",
            }
        )

        Retrieval = self.env["usl.mail.pdf.retrieval"]
        candidates = Retrieval._extract_candidates(message)

        self.assertEqual(len(candidates), 10)
        self.assertEqual(candidates[0]["role"], "button")
        serialized = str(
            [Retrieval._safe_candidate_snapshot(candidate) for candidate in candidates]
        )
        self.assertNotIn("token-", serialized)
        self.assertNotIn("123456789", serialized)
        self.assertFalse(any("unsubscribe" in candidate["path_template"] for candidate in candidates))

        plaintext = self.env["mail.message"].sudo().create(
            {
                "subject": "Votre trajet",
                "email_from": "factures@provider.example",
                "body": "Télécharger le reçu https://receipts.example.com/r/123456789/receipt.pdf?token=secret",
                "message_type": "email",
            }
        )
        plain_candidates = Retrieval._extract_candidates(plaintext)
        self.assertEqual(len(plain_candidates), 1)
        self.assertEqual(plain_candidates[0]["path_template"], "/r/{id}/receipt.pdf")

    def test_positive_tracking_wrapper_is_kept_without_opaque_feature_values(self):
        message = self.env["mail.message"].sudo().create(
            {
                "subject": "Votre reçu 123456789",
                "email_from": "factures@provider.example",
                "body": (
                    '<a href="https://tracking.example/click/abcdef1234567890'
                    '?abcdef1234567890=secret&amp;token=private">'
                    "Download PDF receipt abcdef1234567890</a>"
                ),
                "message_type": "email",
            }
        )

        candidate = self.env["usl.mail.pdf.retrieval"]._extract_candidates(message)[0]
        snapshot = str(
            self.env["usl.mail.pdf.retrieval"]._safe_candidate_snapshot(candidate)
        )

        self.assertEqual(candidate["label"], "download pdf receipt")
        self.assertEqual(candidate["query_keys"], ["token"])
        self.assertNotIn("abcdef1234567890", snapshot)
        self.assertNotIn("secret", snapshot)

    def test_image_only_cta_uses_bounded_preceding_semantics(self):
        message = self.env["mail.message"].sudo().create(
            {
                "subject": "Your trip",
                "email_from": "employee@company.example",
                "body": (
                    "<section><p>Your trip receipt for Valentin-Viennot is ready.</p>"
                    '<a href="https://click.provider.example/r/receipt-token"></a>'
                    "</section><footer>"
                    '<a href="https://click.provider.example/r/account-token">Account</a>'
                    '<a href="https://click.provider.example/r/privacy-token">Privacy</a>'
                    '<a href="https://click.provider.example/r/social-token"><img src="logo.png"/></a>'
                    "</footer>"
                ),
                "message_type": "email",
            }
        )

        candidates = self.env["usl.mail.pdf.retrieval"]._extract_candidates(message)

        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["position"], 0)
        self.assertIn("receipt", candidates[0]["label_tokens"])
        self.assertEqual(candidates[0]["label"], "receipt trip")
        self.assertNotIn("valentin", str(candidates[0]).casefold())

    def test_unlabelled_links_do_not_inherit_an_earlier_receipt_cta(self):
        message = self.env["mail.message"].sudo().create(
            {
                "subject": "Your ride",
                "email_from": "employee@company.example",
                "body": (
                    '<a href="https://click.provider.example/r/pdf-token">'
                    "Download PDF invoice</a>"
                    '<a href="https://click.provider.example/r/navigation-token"></a>'
                    '<a href="https://click.provider.example/r/social-token">Trips</a>'
                ),
                "message_type": "email",
            }
        )

        candidates = self.env["usl.mail.pdf.retrieval"]._extract_candidates(message)

        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["position"], 0)

    def test_learning_snapshot_redacts_arbitrary_subject_label_and_path_text(self):
        personal = "Valentin-Viennot"
        message = self.env["mail.message"].sudo().create(
            {
                "subject": f"Receipt for {personal} at 18 rue privée order 42",
                "email_from": "employee@company.example",
                "body": (
                    '<a href="https://receipts.example.com/users/'
                    f'{personal}/orders/customer-name.pdf?token=secret">'
                    f"Download receipt for {personal}</a>"
                ),
                "message_type": "email",
            }
        )

        Retrieval = self.env["usl.mail.pdf.retrieval"]
        candidate = Retrieval._extract_candidates(message)[0]
        snapshot = str(Retrieval._safe_candidate_snapshot(candidate)).casefold()

        self.assertNotIn(personal.casefold(), snapshot)
        self.assertNotIn("customer-name", snapshot)
        self.assertNotIn("rue privée", snapshot)
        self.assertEqual(candidate["label"], "download receipt")
        self.assertEqual(
            candidate["path_template"],
            "/{segment}/{segment}/{segment}/{id}.pdf",
        )

    def test_candidate_cap_keeps_best_links_not_first_links(self):
        navigation = "".join(
            f'<a href="https://example.com/page/{index}.pdf">Navigation {index}</a>'
            for index in range(15)
        )
        message = self.env["mail.message"].sudo().create(
            {
                "subject": "Your receipt",
                "email_from": "employee@company.example",
                "body": (
                    navigation
                    + '<a href="https://receipts.example.com/trips/123456/receipt.pdf">'
                    "Download PDF receipt</a>"
                ),
                "message_type": "email",
            }
        )

        candidates = self.env["usl.mail.pdf.retrieval"]._extract_candidates(message)

        self.assertEqual(len(candidates), 10)
        self.assertEqual(candidates[0]["hostname"], "receipts.example.com")
        self.assertEqual(candidates[0]["score"], 20)

    def test_selected_link_recovery_is_not_limited_by_picker_cap(self):
        expense = self._ingest(token="candidate-recovery")
        retrieval = self.env["usl.mail.pdf.retrieval"].sudo().search(
            [("expense_id", "=", expense.id)]
        )
        stronger = "".join(
            f'<a href="https://receipts.example.com/files/{index}.pdf">'
            "Download PDF receipt</a>"
            for index in range(10)
        )
        selected_url = "https://receipts.example.com/archive/chosen-receipt"
        retrieval.source_message_id.body = (
            stronger + f'<a href="{selected_url}">Receipt</a>'
        )

        picker_candidates = retrieval._extract_candidates(
            retrieval.source_message_id
        )
        fingerprint = hashlib.sha256(selected_url.encode()).hexdigest()

        self.assertEqual(len(picker_candidates), 10)
        self.assertFalse(
            any(item["fingerprint"] == fingerprint for item in picker_candidates)
        )
        recovered = retrieval._candidate_by_fingerprint(fingerprint)
        self.assertEqual(recovered["position"], 10)
        self.assertEqual(recovered["hostname"], "receipts.example.com")

    def test_receipt_job_function_allows_guarded_progress_commits(self):
        function = self.env.ref(
            "usl_accounting.queue_job_function_linked_receipt_fetch"
        ).sudo()
        self.assertTrue(function.allow_commit)

    def test_attached_pdf_prevents_linked_retrieval(self):
        expense = self._ingest(attachment=True)
        self.assertFalse(
            self.env["usl.mail.pdf.retrieval"].sudo().search(
                [("expense_id", "=", expense.id)]
            )
        )

    def test_sidecar_metadata_is_defensively_sanitized(self):
        token = "abcdef1234567890abcdef1234567890"
        evidence = _safe_redirect_evidence(
            json.dumps(
                [
                    {
                        "host": "RÉCUS.example",
                        "path": f"/receipt/{token}?secret=value",
                    }
                ]
            )
        )
        self.assertNotIn(token, evidence)
        self.assertNotIn("secret", evidence)
        self.assertEqual(_safe_filename(f"receipt-{token}.pdf"), "receipt.pdf")
        self.assertNotIn(
            "https://",
            str(_safe_fetch_failure_message("https://private.example/token")),
        )

    def test_other_employee_cannot_teach_from_expense(self):
        expense = self._ingest()
        retrieval = self.env["usl.mail.pdf.retrieval"].sudo().search(
            [("expense_id", "=", expense.id)],
        )
        outsider = self.expense_user_manager_2
        outsider.group_ids = [Command.unlink(self.env.ref("account.group_account_manager").id)]
        candidate = retrieval._extract_candidates(retrieval.source_message_id)[0]

        with self.assertRaises(AccessError):
            retrieval.with_user(outsider).action_select_candidate(candidate["fingerprint"])

    def test_authenticated_handoff_action_exposes_only_an_internal_url(self):
        expense, retrieval, _candidate = self._authentication_retrieval()

        with patch.object(type(retrieval), "_feature_enabled", return_value=True):
            action = expense.with_user(
                self.expense_user_employee
            ).action_open_linked_receipt_website()

        self.assertEqual(action["type"], "ir.actions.act_url")
        self.assertEqual(action["target"], "new")
        self.assertEqual(
            action["url"],
            f"/usl/expenses/linked-receipt/{retrieval.id}/open",
        )
        self.assertNotIn("https://", str(action))
        self.assertNotIn("authentication-handoff", str(action))

    def test_only_expense_owner_can_consume_authenticated_handoff(self):
        _expense, retrieval, candidate = self._authentication_retrieval(
            token="private-handoff-token",
        )
        manager = self.expense_user_manager
        manager.group_ids = [
            Command.link(self.env.ref("account.group_account_manager").id)
        ]
        outsider = self.expense_user_manager_2
        outsider.group_ids = [
            Command.unlink(self.env.ref("account.group_account_manager").id)
        ]

        with patch.object(type(retrieval), "_feature_enabled", return_value=True):
            for user in (manager, outsider):
                with self.assertRaises(AccessError):
                    retrieval.with_user(user)._consume_handoff(
                        expected_generation=retrieval.generation,
                    )
            url = retrieval.with_user(
                self.expense_user_employee
            )._consume_handoff(expected_generation=retrieval.generation)

        self.assertEqual(url, candidate["_url"])
        self.assertEqual(retrieval.handoff_open_count, 1)
        self.assertEqual(retrieval.last_handoff_user_id, self.expense_user_employee)
        self.assertTrue(retrieval.last_handoff_at)
        serialized = str(
            (
                retrieval.handoff_open_count,
                retrieval.last_handoff_at,
                retrieval.last_handoff_user_id.id,
            )
        )
        self.assertNotIn("private-handoff-token", serialized)

    def test_authenticated_handoff_rechecks_generation_host_and_attachment(self):
        expense, retrieval, _candidate = self._authentication_retrieval(
            token="stale-handoff",
        )
        employee_retrieval = retrieval.with_user(self.expense_user_employee)
        with patch.object(type(retrieval), "_feature_enabled", return_value=True):
            with self.assertRaises(UserError):
                employee_retrieval._consume_handoff(
                    expected_generation=retrieval.generation + 1,
                )
            host = self.env["usl.mail.pdf.host"].sudo().search(
                [("hostname", "=", retrieval.starting_host)],
            )
            host.with_context(linked_receipt_internal=_LINKED_RECEIPT_INTERNAL).write(
                {"state": "blocked"}
            )
            with self.assertRaises(UserError):
                employee_retrieval._consume_handoff(
                    expected_generation=retrieval.generation,
                )
            host.with_context(linked_receipt_internal=_LINKED_RECEIPT_INTERNAL).write(
                {"state": "provisional"}
            )
            attachment = self.env["ir.attachment"].sudo().create(
                {
                    "name": "manual.pdf",
                    "raw": b"%PDF-1.4\nmanual\n%%EOF\n",
                    "mimetype": "application/pdf",
                    "res_model": "hr.expense",
                    "res_id": expense.id,
                    "company_id": expense.company_id.id,
                }
            )
            expense.sudo()._message_set_main_attachment_id(attachment, force=True)
            with self.assertRaises(UserError):
                employee_retrieval._consume_handoff(
                    expected_generation=retrieval.generation,
                )

        self.assertFalse(retrieval.handoff_open_count)

    def test_authenticated_handoff_remains_available_when_automation_is_paused(self):
        _expense, retrieval, candidate = self._authentication_retrieval(
            token="paused-automation-handoff",
        )
        retrieval.pattern_id.action_pause()

        with patch.object(type(retrieval), "_feature_enabled", return_value=True):
            url = retrieval.with_user(
                self.expense_user_employee
            )._consume_handoff(expected_generation=retrieval.generation)

        self.assertEqual(url, candidate["_url"])
        self.assertEqual(retrieval.pattern_id.state, "paused")
        self.assertEqual(retrieval.handoff_open_count, 1)

    def test_native_manual_upload_immediately_supersedes_handoff(self):
        expense, retrieval, _candidate = self._authentication_retrieval(
            token="manual-handoff-upload",
        )
        attachment = self.env["ir.attachment"].sudo().create(
            {
                "name": "manual.pdf",
                "raw": b"%PDF-1.4\nmanual\n%%EOF\n",
                "mimetype": "application/pdf",
                "res_model": "hr.expense",
                "res_id": expense.id,
                "company_id": expense.company_id.id,
            }
        )

        expense.with_user(self.expense_user_employee).attach_document(
            attachment_ids=[attachment.id],
        )

        self.assertEqual(expense.message_main_attachment_id, attachment)
        self.assertEqual(retrieval.state, "superseded")
        self.assertEqual(retrieval.generation, 2)

    def test_queue_identity_contains_only_the_retrieval_generation(self):
        expense = self._ingest()
        retrieval = self.env["usl.mail.pdf.retrieval"].sudo().search(
            [("expense_id", "=", expense.id)]
        )
        candidate = retrieval._extract_candidates(retrieval.source_message_id)[0]
        retrieval._select_candidate(candidate["fingerprint"], teach=True)

        with patch.object(type(retrieval), "_feature_enabled", return_value=True):
            retrieval._enqueue()

        job = self.env["queue.job"].sudo().search(
            [("identity_key", "=", f"receipt-fetch:{retrieval.id}:{retrieval.generation}")]
        )
        self.assertTrue(job)
        self.assertEqual(job.args, [])
        self.assertEqual(job.kwargs, {})
        serialized = " ".join(
            str(value)
            for value in (job.name, job.func_string, job.args, job.kwargs, job.identity_key)
        )
        self.assertNotIn("https://", serialized)
        self.assertNotIn("first-secret-token-value", serialized)

    def test_active_learning_matches_instance_wide_without_company_scope(self):
        expense = self._ingest()
        retrieval = self.env["usl.mail.pdf.retrieval"].sudo().search(
            [("expense_id", "=", expense.id)],
        )
        candidate = retrieval._extract_candidates(retrieval.source_message_id)[0]
        retrieval._select_candidate(candidate["fingerprint"], teach=True)
        retrieval.pattern_id._register_success({"fetch_mode": "http"})
        host = self.env["usl.mail.pdf.host"].sudo().search(
            [("hostname", "=", candidate["hostname"])],
        )
        host.with_context(linked_receipt_internal=_LINKED_RECEIPT_INTERNAL).write({"state": "active"})
        other_message = self.env["mail.message"].sudo().create(
            {
                "subject": retrieval.source_message_id.subject,
                "email_from": retrieval.source_message_id.email_from,
                "body": '<a href="https://receipts.example.com/trips/another-opaque-token-12345/download?token=new-secret">Download PDF receipt</a>',
                "message_type": "email",
            },
        )

        matched = retrieval._extract_candidates(other_message)[0]

        self.assertEqual(matched["pattern_id"], retrieval.pattern_id.id)
        self.assertTrue(matched["host_active"])
        self.assertNotIn("new-secret", str(retrieval._safe_candidate_snapshot(matched)))

    def test_active_host_auto_selects_only_an_unambiguous_generic_pdf_signature(self):
        self.env["usl.mail.pdf.host"].sudo().create(
            {"hostname": "receipts.example.com", "state": "active"}
        )
        Retrieval = self.env["usl.mail.pdf.retrieval"]

        with patch.object(type(Retrieval), "_feature_enabled", return_value=True):
            expense = self._ingest(
                url=(
                    "https://receipts.example.com/receipt/"
                    "9f4d9a829c45a18f.pdf?token=generic-secret"
                )
            )

        retrieval = Retrieval.sudo().search([("expense_id", "=", expense.id)])
        self.assertEqual(retrieval.state, "queued")
        self.assertTrue(retrieval.selected_fingerprint)
        self.assertFalse(retrieval.pattern_id)

    def test_manager_can_govern_hosts_and_patterns_but_employee_cannot_edit_statistics(self):
        expense = self._ingest()
        retrieval = self.env["usl.mail.pdf.retrieval"].sudo().search(
            [("expense_id", "=", expense.id)],
        )
        candidate = retrieval._extract_candidates(retrieval.source_message_id)[0]
        retrieval._select_candidate(candidate["fingerprint"], teach=True)
        manager = self.expense_user_manager
        manager.group_ids = [
            Command.link(self.env.ref("account.group_account_manager").id)
        ]
        host = self.env["usl.mail.pdf.host"].sudo().search(
            [("hostname", "=", candidate["hostname"])],
        )
        retrieval.pattern_id._register_success({"fetch_mode": "http"})
        host.with_context(linked_receipt_internal=_LINKED_RECEIPT_INTERNAL).write(
            {"state": "active", "success_count": 1}
        )

        host.with_user(manager).action_block()
        retrieval.pattern_id.with_user(manager).action_block()
        self.assertEqual(host.state, "blocked")
        self.assertEqual(retrieval.pattern_id.state, "blocked")
        host.with_user(manager).action_activate()
        retrieval.pattern_id.with_user(manager).action_activate()
        self.assertEqual(host.state, "active")
        self.assertEqual(retrieval.pattern_id.state, "active")

        with self.assertRaises(AccessError):
            retrieval.pattern_id.with_user(self.expense_user_employee).write(
                {"success_count": 999}
            )
        with self.assertRaises(AccessError):
            host.with_user(manager).write({"state": "active"})

    def test_manager_cannot_forge_internal_context_to_edit_learning_evidence(self):
        expense = self._ingest(token="forged-governance-context")
        retrieval = self.env["usl.mail.pdf.retrieval"].sudo().search(
            [("expense_id", "=", expense.id)]
        )
        candidate = retrieval._extract_candidates(retrieval.source_message_id)[0]
        retrieval._select_candidate(candidate["fingerprint"], teach=True)
        manager = self.expense_user_manager
        manager.group_ids = [
            Command.link(self.env.ref("account.group_account_manager").id)
        ]
        host = self.env["usl.mail.pdf.host"].sudo().search(
            [("hostname", "=", candidate["hostname"])]
        )
        pattern = retrieval.pattern_id
        for record in (host, pattern):
            original_count = record.success_count
            original_state = record.state
            for forged in (True, 1, "internal"):
                with self.subTest(model=record._name, context=forged):
                    with self.assertRaises(AccessError):
                        record.with_user(manager).with_context(
                            linked_receipt_internal=forged,
                        ).write({"success_count": 999, "state": "active"})
                    self.assertEqual(record.success_count, original_count)
                    self.assertEqual(record.state, original_state)
            # A valid workflow must retain the native ACL boundary even if
            # the in-process caller holds the private identity sentinel.
            with self.assertRaises(AccessError):
                record.with_user(self.expense_user_employee).with_context(
                    linked_receipt_internal=_LINKED_RECEIPT_INTERNAL,
                ).write({"success_count": 999})
            record.with_user(manager).action_block()
            self.assertEqual(record.state, "blocked")
            record.with_user(manager).action_activate()
        self.assertEqual(host.state, "provisional")
        self.assertEqual(pattern.state, "learning")

    def test_queue_job_authority_requires_capability_but_worker_storage_does_not(self):
        manager = self.expense_user_manager
        manager.group_ids = [
            Command.link(self.env.ref("queue_job.group_queue_job_manager").id),
            Command.unlink(self.env.ref("usl_access_control.group_irreversible_actions").id),
        ]
        job = Job(
            self.env["res.partner"].with_user(self.expense_user_employee).get_base_url,
        )
        job.store()
        record = job.db_record()
        original_user = record.user_id
        original_company = record.company_id
        for values in (
            {"user_id": manager.id},
            {"company_id": False},
        ):
            for forged in (None, True, "internal"):
                with self.subTest(values=values, context=forged):
                    with self.assertRaises(AccessError):
                        record.with_user(manager).with_context(
                            _job_edit_sentinel=forged,
                        ).write(values)
        self.assertEqual(record.user_id, original_user)
        self.assertEqual(record.company_id, original_company)
        # Normal non-authority queue operation and subsequent OCA persistence
        # must not require an irreversible capability.
        record.with_user(manager).write({"priority": 25})
        job.priority = 30
        job.store()
        self.assertEqual(record.priority, 30)
        manager.group_ids = [
            Command.link(self.env.ref("usl_access_control.group_irreversible_actions").id),
        ]
        target_user = self.env.user
        record.with_user(manager).write({"user_id": target_user.id})
        self.assertEqual(record.user_id, target_user)
        self.assertEqual(record.records.env.uid, target_user.id)
        audit = self.env["usl.audit.event"].sudo().search(
            [("action_key", "=", "guard:queue_job_authority"),
             ("actor_id", "=", manager.id), ("event_type", "=", "protected_action")],
            limit=1,
        )
        self.assertTrue(audit)
        with self.assertRaises(UserError):
            audit.with_user(manager).write({"action_name": "Altered queue evidence"})
        manager.group_ids = [
            Command.unlink(self.env.ref("usl_access_control.group_irreversible_actions").id),
            Command.link(self.env.ref("usl_access_control.group_ai_agent").id),
        ]
        for actor_record in (record.with_user(manager), record.with_user(manager).sudo()):
            with self.assertRaises(AccessError):
                actor_record.write({"user_id": self.expense_user_employee.id})

    def test_unvalidated_governance_restore_does_not_activate_learning(self):
        expense = self._ingest(token="unvalidated-governance")
        retrieval = self.env["usl.mail.pdf.retrieval"].sudo().search(
            [("expense_id", "=", expense.id)]
        )
        candidate = retrieval._extract_candidates(retrieval.source_message_id)[0]
        retrieval._select_candidate(candidate["fingerprint"], teach=True)
        host = self.env["usl.mail.pdf.host"].sudo().search(
            [("hostname", "=", candidate["hostname"])]
        )

        host.action_block()
        retrieval.pattern_id.action_block()
        host.action_activate()
        retrieval.pattern_id.action_activate()

        self.assertEqual(host.state, "provisional")
        self.assertEqual(retrieval.pattern_id.state, "learning")

    def test_neutralized_database_disables_existing_and_future_jobs(self):
        parameter = self.env["ir.config_parameter"].sudo()
        parameter.set_bool("database.is_neutralized", True)

        with patch.dict(
            "os.environ",
            {
                "USL_LINKED_PDF_DOWNLOAD_ENABLED": "1",
                "USL_LINKED_PDF_DOWNLOAD_ADMITTED": "1",
            },
        ):
            self.assertFalse(self.env["usl.mail.pdf.retrieval"]._feature_enabled())

    def test_disabled_job_does_not_penalize_learned_pattern(self):
        expense = self._ingest(token="disabled-running-job")
        retrieval = self.env["usl.mail.pdf.retrieval"].sudo().search(
            [("expense_id", "=", expense.id)]
        )
        candidate = retrieval._extract_candidates(retrieval.source_message_id)[0]
        retrieval._select_candidate(candidate["fingerprint"], teach=True)

        with patch.object(type(retrieval), "_feature_enabled", return_value=False):
            retrieval._job_fetch_receipt()

        self.assertEqual(retrieval.state, "needs_attention")
        self.assertEqual(retrieval.failure_code, "feature_disabled")
        self.assertEqual(retrieval.pattern_id.failure_count, 0)

    def test_blocked_starting_host_stops_job_before_network_access(self):
        expense = self._ingest(token="blocked-before-fetch")
        retrieval = self.env["usl.mail.pdf.retrieval"].sudo().search(
            [("expense_id", "=", expense.id)]
        )
        candidate = retrieval._extract_candidates(retrieval.source_message_id)[0]
        retrieval._select_candidate(candidate["fingerprint"], teach=True)
        self.env["usl.mail.pdf.host"].sudo().search(
            [("hostname", "=", retrieval.starting_host)]
        ).action_block()

        with patch.object(
            type(retrieval), "_feature_enabled", return_value=True
        ), patch.object(type(retrieval), "_fetcher_request") as fetch:
            retrieval._job_fetch_receipt()

        fetch.assert_not_called()
        self.assertEqual(retrieval.state, "needs_attention")
        self.assertEqual(retrieval.failure_code, "egress_denied")
        self.assertEqual(retrieval.pattern_id.failure_count, 0)

    def test_paused_pattern_stops_job_before_network_access(self):
        expense = self._ingest(token="paused-before-fetch")
        retrieval = self.env["usl.mail.pdf.retrieval"].sudo().search(
            [("expense_id", "=", expense.id)]
        )
        candidate = retrieval._extract_candidates(retrieval.source_message_id)[0]
        retrieval._select_candidate(candidate["fingerprint"], teach=True)
        retrieval.pattern_id.action_pause()

        with patch.object(
            type(retrieval), "_feature_enabled", return_value=True
        ), patch.object(type(retrieval), "_fetcher_request") as fetch:
            retrieval._job_fetch_receipt()

        fetch.assert_not_called()
        self.assertEqual(retrieval.state, "needs_attention")
        self.assertEqual(retrieval.failure_code, "pattern_unavailable")
        self.assertEqual(retrieval.pattern_id.failure_count, 0)

    def test_low_confidence_pattern_requires_teaching_again(self):
        expense = self._ingest()
        retrieval = self.env["usl.mail.pdf.retrieval"].sudo().search(
            [("expense_id", "=", expense.id)],
        )
        candidate = retrieval._extract_candidates(retrieval.source_message_id)[0]
        retrieval._select_candidate(candidate["fingerprint"], teach=True)
        retrieval.pattern_id._register_success({"fetch_mode": "http"})
        retrieval.pattern_id.with_context(linked_receipt_internal=_LINKED_RECEIPT_INTERNAL).write(
            {"negative_count": 2}
        )

        rematch = retrieval._extract_candidates(retrieval.source_message_id)[0]

        self.assertLess(retrieval.pattern_id.confidence, 0.60)
        self.assertFalse(rematch["pattern_id"])

    def test_two_terminal_failures_pause_pattern(self):
        expense = self._ingest()
        retrieval = self.env["usl.mail.pdf.retrieval"].sudo().search(
            [("expense_id", "=", expense.id)],
        )
        candidate = retrieval._extract_candidates(retrieval.source_message_id)[0]
        retrieval._select_candidate(candidate["fingerprint"], teach=True)

        retrieval._register_terminal_failure(ReceiptFetchError("invalid_pdf", "Invalid PDF"))
        self.assertEqual(retrieval.pattern_id.state, "learning")
        retrieval._register_terminal_failure(ReceiptFetchError("invalid_pdf", "Invalid PDF"))
        self.assertEqual(retrieval.pattern_id.state, "paused")

    def test_explicit_teaching_resumes_paused_pattern_but_not_blocked_pattern(self):
        expense = self._ingest(token="resume-paused-pattern")
        retrieval = self.env["usl.mail.pdf.retrieval"].sudo().search(
            [("expense_id", "=", expense.id)]
        )
        candidate = retrieval._extract_candidates(retrieval.source_message_id)[0]
        retrieval._select_candidate(candidate["fingerprint"], teach=True)
        retrieval.pattern_id.action_pause()

        retrieval._select_candidate(candidate["fingerprint"], teach=True)

        self.assertEqual(retrieval.pattern_id.state, "learning")
        retrieval.pattern_id.action_block()
        positive_count = retrieval.pattern_id.positive_count
        with self.assertRaises(UserError):
            retrieval._select_candidate(candidate["fingerprint"], teach=True)
        self.assertEqual(retrieval.pattern_id.positive_count, positive_count)

    def test_explicit_retry_resumes_paused_pattern_but_not_blocked_host(self):
        expense = self._ingest(token="retry-paused-pattern")
        retrieval = self.env["usl.mail.pdf.retrieval"].sudo().search(
            [("expense_id", "=", expense.id)]
        )
        candidate = retrieval._extract_candidates(retrieval.source_message_id)[0]
        retrieval._select_candidate(candidate["fingerprint"], teach=True)
        retrieval.sudo().write({"state": "needs_attention"})
        retrieval.pattern_id.action_pause()
        generation = retrieval.generation
        employee_retrieval = self.env["usl.mail.pdf.retrieval"].with_user(
            self.expense_user_employee
        ).browse(retrieval.id)

        with patch.object(type(retrieval), "_enqueue") as enqueue:
            employee_retrieval.action_retry()

        enqueue.assert_called_once()
        self.assertEqual(retrieval.pattern_id.state, "learning")
        self.assertEqual(retrieval.generation, generation + 1)

        retrieval.sudo().write({"state": "needs_attention"})
        self.env["usl.mail.pdf.host"].sudo().search(
            [("hostname", "=", retrieval.starting_host)]
        ).action_block()
        with self.assertRaises(UserError), self.env.cr.savepoint():
            employee_retrieval.action_retry()

    def test_success_attaches_exactly_one_main_receipt(self):
        expense = self._ingest()
        retrieval = self.env["usl.mail.pdf.retrieval"].sudo().search(
            [("expense_id", "=", expense.id)],
        )
        candidate = retrieval._extract_candidates(retrieval.source_message_id)[0]
        retrieval._select_candidate(candidate["fingerprint"], teach=True)
        content = b"%PDF-1.4\nlinked receipt fixture\n%%EOF\n"
        digest = __import__("hashlib").sha256(content).hexdigest()

        with patch.object(type(retrieval), "_feature_enabled", return_value=True), patch.object(
            type(retrieval),
            "_fetcher_request",
            return_value=(
                content,
                "receipt.pdf",
                digest,
                {
                    "fetch_mode": "http",
                    "redirect_hosts": (
                        '[{"host":"links.example","path":"/go/{id}"},'
                        '{"host":"cdn.example","path":"/receipt/{id}.pdf"}]'
                    ),
                },
            ),
        ), patch.object(type(retrieval), "_persist_job_state") as persist:
            retrieval._job_fetch_receipt()

        persist.assert_not_called()
        self.assertEqual(retrieval.state, "succeeded")
        self.assertEqual(expense.message_main_attachment_id, retrieval.attachment_id)
        self.assertEqual(retrieval.pattern_id.observed_final_host, "cdn.example")
        self.assertEqual(
            retrieval.pattern_id.observed_final_path_template,
            "/receipt/{id}.pdf",
        )
        final_host = self.env["usl.mail.pdf.host"].sudo().search(
            [("hostname", "=", "cdn.example")],
        )
        self.assertEqual(final_host.state, "active")
        self.assertEqual(final_host.validated_pattern_id, retrieval.pattern_id)
        self.assertEqual(final_host.success_count, 1)
        self.assertEqual(bytes(retrieval.attachment_id.raw), content)
        self.assertEqual(
            self.env["ir.attachment"].sudo().search_count(
                [
                    ("res_model", "=", "hr.expense"),
                    ("res_id", "=", expense.id),
                    ("mimetype", "=", "application/pdf"),
                ],
            ),
            1,
        )

    def test_blocked_redirect_chain_is_rejected_before_attachment(self):
        expense = self._ingest()
        retrieval = self.env["usl.mail.pdf.retrieval"].sudo().search(
            [("expense_id", "=", expense.id)],
        )
        candidate = retrieval._extract_candidates(retrieval.source_message_id)[0]
        retrieval._select_candidate(candidate["fingerprint"], teach=True)
        self.env["usl.mail.pdf.host"].sudo().create(
            {"hostname": "blocked.example", "state": "blocked"}
        )
        content = b"%PDF-1.4\nblocked redirect fixture\n%%EOF\n"
        digest = __import__("hashlib").sha256(content).hexdigest()

        with patch.object(type(retrieval), "_feature_enabled", return_value=True), patch.object(
            type(retrieval),
            "_fetcher_request",
            return_value=(
                content,
                "receipt.pdf",
                digest,
                {
                    "fetch_mode": "http",
                    "redirect_hosts": (
                        '[{"host":"links.example","path":"/go/{id}"},'
                        '{"host":"blocked.example","path":"/receipt/{id}.pdf"}]'
                    ),
                },
            ),
        ):
            retrieval._job_fetch_receipt()

        self.assertEqual(retrieval.state, "needs_attention")
        self.assertEqual(retrieval.failure_code, "egress_denied")
        self.assertFalse(retrieval.attachment_id)
        self.assertFalse(expense.message_main_attachment_id)

    def test_manual_main_receipt_supersedes_download(self):
        expense = self._ingest()
        retrieval = self.env["usl.mail.pdf.retrieval"].sudo().search(
            [("expense_id", "=", expense.id)],
        )
        candidate = retrieval._extract_candidates(retrieval.source_message_id)[0]
        retrieval._select_candidate(candidate["fingerprint"], teach=True)
        attachment = self.env["ir.attachment"].sudo().create(
            {
                "name": "manual.pdf",
                "raw": b"manual",
                "mimetype": "application/pdf",
                "res_model": "hr.expense",
                "res_id": expense.id,
                "company_id": expense.company_id.id,
            },
        )
        expense.sudo()._message_set_main_attachment_id(attachment, force=True)

        with patch.object(type(retrieval), "_feature_enabled", return_value=True):
            retrieval._job_fetch_receipt()

        self.assertEqual(retrieval.state, "superseded")

    def test_non_draft_expense_is_superseded_without_fetching(self):
        expense = self._ingest(token="submitted-before-fetch")
        retrieval = self.env["usl.mail.pdf.retrieval"].sudo().search(
            [("expense_id", "=", expense.id)]
        )
        candidate = retrieval._extract_candidates(retrieval.source_message_id)[0]
        retrieval._select_candidate(candidate["fingerprint"], teach=True)
        expense.approval_state = "submitted"

        with patch.object(
            type(retrieval), "_feature_enabled", return_value=True
        ), patch.object(type(retrieval), "_fetcher_request") as fetch:
            retrieval._job_fetch_receipt()

        self.assertEqual(retrieval.state, "superseded")
        fetch.assert_not_called()

    def test_unexpected_fetch_error_retries_without_exposing_its_url(self):
        expense = self._ingest(token="unexpected-fetch-error")
        retrieval = self.env["usl.mail.pdf.retrieval"].sudo().search(
            [("expense_id", "=", expense.id)]
        )
        candidate = retrieval._extract_candidates(retrieval.source_message_id)[0]
        retrieval._select_candidate(candidate["fingerprint"], teach=True)
        leaked_url = "https://receipts.example/private?token=never-log-this"

        with patch.object(
            type(retrieval), "_feature_enabled", return_value=True
        ), patch.object(
            type(retrieval),
            "_fetcher_request",
            side_effect=RuntimeError(leaked_url),
        ), patch.object(type(retrieval), "_persist_job_state") as persist:
            with self.assertRaises(RetryableJobError) as raised:
                retrieval._job_fetch_receipt()

        self.assertNotIn(leaked_url, str(raised.exception))
        self.assertEqual(persist.call_args_list[-1].args[0]["state"], "retrying")
        self.assertEqual(
            persist.call_args_list[-1].args[0]["failure_code"],
            "fetch_failed",
        )

    def test_stale_generation_after_fetch_cannot_attach_or_update_learning(self):
        expense = self._ingest()
        retrieval = self.env["usl.mail.pdf.retrieval"].sudo().search(
            [("expense_id", "=", expense.id)],
        )
        candidate = retrieval._extract_candidates(retrieval.source_message_id)[0]
        retrieval._select_candidate(candidate["fingerprint"], teach=True)
        content = b"%PDF-1.4\nstale generation fixture\n%%EOF\n"
        digest = __import__("hashlib").sha256(content).hexdigest()

        def supersede_while_fetching(_url, _candidate):
            retrieval.write(
                {
                    "state": "superseded",
                    "generation": retrieval.generation + 1,
                }
            )
            return content, "receipt.pdf", digest, {"fetch_mode": "http"}

        with patch.object(type(retrieval), "_feature_enabled", return_value=True), patch.object(
            type(retrieval),
            "_fetcher_request",
            side_effect=supersede_while_fetching,
        ):
            retrieval._job_fetch_receipt()

        self.assertEqual(retrieval.state, "superseded")
        self.assertFalse(retrieval.attachment_id)
        self.assertFalse(expense.message_main_attachment_id)
        self.assertEqual(retrieval.pattern_id.success_count, 0)
