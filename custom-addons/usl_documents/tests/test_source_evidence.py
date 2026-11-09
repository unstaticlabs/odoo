import base64
import hashlib
import io
from unittest.mock import patch

from PIL import Image

from odoo import Command
from odoo.exceptions import AccessError, ValidationError
from odoo.tests import tagged
from odoo.addons.hr_expense.tests.common import TestExpenseCommon
from odoo.addons.mail.tests.common import mail_new_test_user
from odoo.addons.usl_documents.models.paperless_client import PaperlessClient


@tagged("post_install", "-at_install", "usl_source_evidence")
class TestSourceEvidence(TestExpenseCommon):
    def _expense(self):
        return self.env["hr.expense"].with_user(self.expense_user_employee).create({
            "name": "[QA] Source evidence expense", "employee_id": self.expense_employee.id,
            "product_id": self.product_c.id, "total_amount_currency": 54,
            "tax_ids": [Command.clear()], "date": "2026-10-14",
        })

    def _payload(self, content=b"%PDF-1.4\n[QA] Exact source evidence\n%%EOF\n", **values):
        return {
            "filename": "qa-source-evidence.pdf", "mimetype": "application/pdf",
            "data_base64": base64.b64encode(content).decode(),
            "sha256": hashlib.sha256(content).hexdigest(), "byte_size": len(content), **values,
        }

    def test_exact_expense_file_preserves_financial_values_and_false_main(self):
        expense = self._expense()
        fields = ["name", "company_id", "employee_id", "currency_id", "date", "payment_mode", "state", "total_amount_currency", "account_id", "tax_ids"]
        before = expense.read(fields)
        result = expense.attach_source_evidence(**self._payload())
        attachment = self.env["ir.attachment"].browse(result["attachment_id"])
        self.assertEqual(bytes(attachment.raw), base64.b64decode(self._payload()["data_base64"]))
        self.assertEqual(attachment.name, "qa-source-evidence.pdf")
        self.assertEqual(attachment.mimetype, "application/pdf")
        self.assertEqual(attachment.company_id, expense.company_id)
        self.assertEqual(attachment.res_model, "hr.expense")
        self.assertEqual(attachment.res_id, expense.id)
        self.assertEqual(attachment.create_uid, self.expense_user_employee)
        self.assertEqual(expense.read(fields), before)
        self.assertFalse(expense.message_main_attachment_id)
        self.assertEqual(result["archive_state"], "pending")
        self.assertIn("qa-source-evidence.pdf", expense.message_ids[0].body)
        # Inspect the technical queue as a fixture assertion, not as the uploader.
        operation = self.env["usl.document.operation"].sudo().search([("source_attachment_id", "=", attachment.id)])
        self.assertEqual(len(operation), 1)
        self.assertEqual(operation.res_model, "hr.expense")
        self.assertEqual(operation.res_id, expense.id)
        self.assertEqual(operation.source_attachment_checksum, attachment.checksum)
        self.assertEqual(attachment.usl_documents_origin, "direct_record")
        self.assertEqual(attachment.usl_documents_document_role, "evidence")

    def test_repeat_reuses_owned_attachment_and_does_not_repeat_audit(self):
        expense = self._expense()
        first = expense.attach_source_evidence(**self._payload())
        messages = expense.message_ids.ids
        again = expense.attach_source_evidence(**self._payload())
        self.assertEqual(first["attachment_id"], again["attachment_id"])
        self.assertTrue(again["reused"])
        self.assertEqual(expense.message_ids.ids, messages)

    def test_missing_or_generic_mime_uses_native_inference_without_changing_bytes(self):
        content = b"%PDF-1.4\n[QA] Exact source evidence\n%%EOF\n"
        for declared in (False, None, "application/octet-stream"):
            expense = self._expense()
            result = expense.attach_source_evidence(**self._payload(content, mimetype=declared))
            attachment = self.env["ir.attachment"].browse(result["attachment_id"])
            self.assertTrue(result["mime_inferred"])
            self.assertEqual(result["mimetype"], "application/pdf")
            self.assertEqual(bytes(attachment.raw), content)
        result = self._expense().attach_source_evidence(**self._payload())
        self.assertFalse(result["mime_inferred"])

    def test_explicit_main_selection_preserves_other_evidence(self):
        expense = self._expense()
        first = expense.attach_source_evidence(**self._payload(set_main_attachment=True))
        second = expense.attach_source_evidence(**self._payload(b"%PDF-1.4\n[QA] Second evidence\n%%EOF", filename="qa-second.pdf"))
        self.assertEqual(expense.message_main_attachment_id.id, first["attachment_id"])
        expense.attach_source_evidence(**self._payload(b"%PDF-1.4\n[QA] Second evidence\n%%EOF", filename="qa-second.pdf", set_main_attachment=True))
        self.assertEqual(expense.message_main_attachment_id.id, second["attachment_id"])
        self.assertIn("selected as main attachment", expense.message_ids[0].body)
        self.assertTrue(self.env["ir.attachment"].browse(first["attachment_id"]).exists())

    def test_same_content_on_two_records_has_owned_rows_and_one_binary(self):
        first = self._expense().attach_source_evidence(**self._payload())
        second = self._expense().attach_source_evidence(**self._payload())
        attachments = self.env["ir.attachment"].browse([first["attachment_id"], second["attachment_id"]])
        self.assertNotEqual(first["attachment_id"], second["attachment_id"])
        self.assertEqual(len(set(attachments.mapped("checksum"))), 1)
        self.assertEqual(len(set(attachments.mapped("store_fname"))), 1)

    def test_malformed_or_changed_source_does_not_attach_or_append_history(self):
        expense = self._expense()
        messages = expense.message_ids.ids
        for change in [{"sha256": "0" * 64}, {"byte_size": 2}, {"byte_size": True},
                       {"data_base64": "not binary"}, {"set_main_attachment": 1},
                       {"filename": "private/folder/file.pdf"}]:
            with self.subTest(change=change), self.assertRaises(ValidationError):
                expense.attach_source_evidence(**self._payload(**change))
        self.assertFalse(expense.attachment_ids)
        self.assertEqual(expense.message_ids.ids, messages)

    def test_mime_transformation_rolls_back_the_new_evidence(self):
        expense = self._expense()
        content = b"<html><body>[QA] Source</body></html>"
        with self.assertRaisesRegex(ValidationError, "exact content and MIME"):
            expense.attach_source_evidence(**self._payload(content, filename="qa-source.html", mimetype="text/html"))
        self.assertFalse(expense.attachment_ids)

    def test_late_audit_failure_rolls_back_attachment_and_main_selection(self):
        expense = self._expense()
        # Capture an existing persisted target, as in a separate native RPC.
        # Pending creation computations are outside the handoff's savepoint.
        expense.flush_recordset()
        expense.invalidate_recordset()
        before = expense.read(["write_date", "write_uid", "message_main_attachment_id"])
        with patch.object(type(expense), "message_post", side_effect=ValidationError("[QA] Audit unavailable")):
            with self.assertRaises(ValidationError):
                expense.attach_source_evidence(**self._payload(set_main_attachment=True))
        self.assertFalse(expense.message_main_attachment_id)
        self.assertFalse(expense.attachment_ids)
        self.assertEqual(expense.read(["write_date", "write_uid", "message_main_attachment_id"]), before)

    def test_accounting_entry_accepts_evidence_without_financial_transition(self):
        data = self.company_data
        move = self.env["account.move"].create({
            "move_type": "entry", "journal_id": data["default_journal_misc"].id,
            "line_ids": [Command.create({"name": "[QA] Debit", "account_id": data["default_account_expense"].id, "debit": 54}),
                         Command.create({"name": "[QA] Credit", "account_id": data["default_account_revenue"].id, "credit": 54})],
        })
        move.action_post()
        before = move.line_ids.read(["account_id", "debit", "credit", "balance", "currency_id", "amount_currency", "amount_residual", "full_reconcile_id"])
        original_name = move.name
        move.attach_source_evidence(**self._payload(set_main_attachment=True))
        self.assertEqual(move.state, "posted")
        self.assertEqual(move.name, original_name)
        self.assertEqual(move.line_ids.read(["account_id", "debit", "credit", "balance", "currency_id", "amount_currency", "amount_residual", "full_reconcile_id"]), before)
        self.assertTrue(move.message_main_attachment_id)

    def test_unselected_company_and_other_employee_are_denied(self):
        expense = self._expense()
        with self.assertRaises(AccessError):
            expense.with_user(mail_new_test_user(self.env, login="qa-evidence-unrelated", groups="base.group_user")).attach_source_evidence(**self._payload())
        with self.assertRaises(AccessError):
            expense.with_context(allowed_company_ids=self.company_data_2["company"].ids).attach_source_evidence(**self._payload())
        self.assertFalse(expense.attachment_ids)

    def test_large_image_retains_exact_source_resolution_and_bytes(self):
        stream = io.BytesIO()
        Image.new("RGB", (2200, 2000), color=(47, 83, 129)).save(stream, format="PNG")
        content = stream.getvalue()
        expense = self._expense()
        result = expense.attach_source_evidence(**self._payload(content, filename="qa-source-image.png", mimetype="image/png"))
        attachment = self.env["ir.attachment"].browse(result["attachment_id"])
        self.assertEqual(bytes(attachment.raw), content)
        self.assertEqual(Image.open(io.BytesIO(bytes(attachment.raw))).size, (2200, 2000))

    def test_selected_secondary_company_retains_its_owned_evidence(self):
        company = self.company_data_2["company"]
        self.expense_user_manager.company_ids = [Command.link(company.id)]
        employee = self.env["hr.employee"].sudo().create({"name": "[QA] Secondary evidence employee", "company_id": company.id})
        expense = self.env["hr.expense"].with_company(company).create({
            "name": "[QA] Secondary source evidence", "company_id": company.id, "employee_id": employee.id,
            "total_amount_currency": 37, "tax_ids": [Command.clear()],
        }).with_user(self.expense_user_manager).with_context(allowed_company_ids=company.ids)
        result = expense.attach_source_evidence(**self._payload())
        attachment = self.env["ir.attachment"].browse(result["attachment_id"])
        self.assertEqual(attachment.company_id, company)
        self.assertEqual(expense.total_amount_currency, 37)

    def test_managed_agent_uses_native_source_authority_without_generic_create(self):
        if "usl.agent" not in self.env:
            self.skipTest("Managed Agent is qualified in the complete runtime registry.")
        owner = self.expense_user_manager
        for mode in ("read_only", "read_write"):
            expense = self._expense()
            agent = self.env["usl.agent"].with_user(owner).create({
                "name": "[QA] Evidence assistant", "purpose": "[QA] Exact source handoff",
                "owner_id": owner.id, "company_id": self.env.company.id,
                "company_ids": [Command.set(self.env.company.ids)],
                "delegated_group_ids": [Command.set(self.env.ref("hr_expense.group_hr_expense_manager").ids)],
                "access_mode": mode,
            })
            record = expense.with_user(agent.user_id)
            with self.assertRaises(AccessError), self.env.cr.savepoint():
                record.with_context(usl_agent_collaboration_token=True).env["ir.attachment"].create({
                    "name": "qa-forged.pdf", "raw": b"[QA] Forged generic create", "res_model": "hr.expense", "res_id": expense.id,
                })
            if mode == "read_only":
                with self.assertRaises(AccessError):
                    record.with_context(usl_agent_collaboration_token=True).attach_source_evidence(**self._payload())
                self.assertFalse(expense.attachment_ids)
            else:
                result = record.attach_source_evidence(**self._payload(set_main_attachment=True))
                self.assertEqual(expense.message_main_attachment_id.id, result["attachment_id"])
                self.assertEqual(self.env["ir.attachment"].browse(result["attachment_id"]).create_uid, agent.user_id)

    def test_portal_and_readonly_record_cannot_upload(self):
        expense = self._expense()
        portal = mail_new_test_user(self.env, login="qa-evidence-portal", groups="base.group_portal")
        with self.assertRaises(AccessError):
            expense.with_user(portal).attach_source_evidence(**self._payload())
        self.assertFalse(expense.attachment_ids)

    def test_forged_attachment_defaults_cannot_publish_or_rebind_evidence(self):
        expense = self._expense().with_context(
            default_public=True, default_access_token="[QA] Forged bearer",
            default_res_model="res.users", default_res_id=self.env.user.id,
            default_res_field="image_1920", default_url="https://example.test/qa-secret",
        )
        result = expense.attach_source_evidence(**self._payload())
        attachment = self.env["ir.attachment"].browse(result["attachment_id"])
        self.assertFalse(attachment.public)
        self.assertFalse(attachment.access_token)
        self.assertFalse(attachment.res_field)
        self.assertFalse(attachment.url)
        self.assertEqual((attachment.res_model, attachment.res_id), ("hr.expense", expense.id))

    def test_settled_expense_keeps_native_attachment_rule_and_accounting_recovery(self):
        for mode in ("own_account", "company_account"):
            expense = self._expense().with_user(self.expense_user_manager)
            expense.attach_source_evidence(**self._payload(
                b"%PDF-1.4\n[QA] Original settled receipt\n%%EOF\n",
                filename="qa-original-receipt.pdf", set_main_attachment=True,
            ))
            expense.payment_mode = mode
            expense.action_submit()
            expense._do_approve()
            if mode == "own_account":
                self.post_expenses_with_wizard(expense.with_user(self.env.user))
            else:
                expense.with_user(self.env.user).action_post()
            self.assertEqual(expense.state, "posted" if mode == "own_account" else "paid")
            move = expense.account_move_id
            before = move.line_ids.read(["account_id", "debit", "credit", "currency_id", "amount_currency", "amount_residual", "full_reconcile_id"])
            old_receipt = expense.message_main_attachment_id
            old_checksum = old_receipt.checksum
            state = expense.state
            with self.assertRaises(AccessError), self.env.cr.savepoint():
                self.env["ir.attachment"].with_user(self.expense_user_manager).create({
                    "name": "qa-native-approved.pdf", "raw": b"[QA] Native approved receipt",
                    "res_model": "hr.expense", "res_id": expense.id,
                })
            with self.assertRaisesRegex(AccessError, "accounting entry"), self.env.cr.savepoint():
                expense.attach_source_evidence(**self._payload(set_main_attachment=True))
            accountant = mail_new_test_user(
                self.env, login=f"qa-evidence-accountant-{mode}", groups="account.group_account_user",
                company_id=expense.company_id.id, company_ids=[Command.set(expense.company_id.ids)],
            )
            result = move.with_user(accountant).attach_source_evidence(**self._payload())
            self.assertEqual(result["target_id"], move.id)
            self.assertEqual(expense.state, state)
            self.assertEqual(move.line_ids.read(["account_id", "debit", "credit", "currency_id", "amount_currency", "amount_residual", "full_reconcile_id"]), before)
            self.assertTrue(expense.message_main_attachment_id)
            self.assertEqual(old_receipt.checksum, old_checksum)

    def test_existing_attachment_copy_retains_source_and_deduplicates_destination(self):
        source_expense, target = self._expense(), self._expense()
        source_id = source_expense.attach_source_evidence(**self._payload(set_main_attachment=True))["attachment_id"]
        source = self.env["ir.attachment"].browse(source_id)
        fields = ["name", "res_model", "res_id", "company_id", "checksum", "raw", "create_uid", "create_date"]
        before = source.read(fields)
        result = target.attach_source_evidence(source_attachment_id=source.id, set_main_attachment=True)
        copied = self.env["ir.attachment"].browse(result["attachment_id"])
        self.assertNotEqual(copied, source)
        self.assertEqual(source.read(fields), before)
        self.assertEqual(bytes(copied.raw), bytes(source.raw))
        self.assertEqual(copied.store_fname, source.store_fname)
        self.assertEqual(target.message_main_attachment_id, copied)
        self.assertIn(f"attachment #{source.id}", target.message_ids[0].body)
        messages = target.message_ids.ids
        repeated = target.attach_source_evidence(source_attachment_id=source.id, set_main_attachment=True)
        self.assertTrue(repeated["reused"])
        self.assertEqual(result["attachment_id"], repeated["attachment_id"])
        self.assertEqual(target.message_ids.ids, messages)

    def test_existing_source_requires_native_read_access_before_target_changes(self):
        target = self._expense()
        outsider = mail_new_test_user(self.env, login="qa-evidence-source-owner", groups="base.group_user")
        private = self.env["ir.attachment"].with_user(outsider).create({
            "name": "qa-private.pdf", "raw": b"%PDF-1.4 [QA] Private original", "mimetype": "application/pdf",
        })
        before = target.read(["attachment_ids", "message_ids", "message_main_attachment_id", "write_date"])
        with self.assertRaises(AccessError):
            target.attach_source_evidence(source_attachment_id=private.id)
        self.assertEqual(target.read(["attachment_ids", "message_ids", "message_main_attachment_id", "write_date"]), before)

    def test_existing_source_rejects_ambiguous_references(self):
        target = self._expense()
        for values in [dict(source_attachment_id=True), dict(source_attachment_id=1, source_document_id=2),
                       dict(source_attachment_id=1, **self._payload())]:
            with self.assertRaises(ValidationError):
                target.attach_source_evidence(**values)
        self.assertFalse(target.attachment_ids)

    def test_archived_original_copy_uses_native_binary_access_and_verifies_checksum(self):
        target = self._expense()
        user = self.expense_user_employee
        user.sudo().group_ids = [Command.link(self.env.ref("usl_documents.group_documents_user").id)]
        content = b"%PDF-1.4\n[QA] Archived exact source\n%%EOF\n"
        document = self.env["usl.document"].sudo().create({
            "name": "[QA] Archived source", "paperless_id": 78103,
            "company_id": target.company_id.id, "confidentiality": "internal",
            "review_state": "classified", "availability_state": "available",
            "permission_sync_state": "synchronized", "original_filename": "qa-archived-original.pdf",
            "mime_type": "application/pdf",
        })
        version = self.env["usl.document.version"].sudo().create({
            "document_id": document.id, "paperless_version_id": "qa-original-v1", "label": "[QA] Original",
            "original_filename": "qa-archived-original.pdf", "mime_type": "application/pdf",
            "checksum": hashlib.md5(content).hexdigest(), "is_current": True,
        })
        class OriginalStream:
            status = 200
            def iter_chunks(self):
                yield content[:11]
                yield content[11:]
            def close(self):
                pass
        before = document.read(["name", "version_ids", "company_id", "link_ids"])
        with patch.object(PaperlessClient, "open_download", return_value=OriginalStream()) as download:
            result = target.attach_source_evidence(source_document_id=document.id, set_main_attachment=True)
        download.assert_called_once_with(78103, version_id="qa-original-v1", original=True)
        copied = self.env["ir.attachment"].browse(result["attachment_id"])
        self.assertEqual(bytes(copied.raw), content)
        self.assertEqual(document.read(["name", "version_ids", "company_id", "link_ids"]), before)
        self.assertEqual(result["source_document_id"], document.id)
        self.assertIn(f"archived document #{document.id}", target.message_ids[0].body)
        other = self._expense()
        version.sudo().checksum = "0" * 32
        with patch.object(PaperlessClient, "open_download", return_value=OriginalStream()):
            with self.assertRaisesRegex(ValidationError, "checksum changed"):
                other.attach_source_evidence(source_document_id=document.id)
        self.assertFalse(other.attachment_ids)
        version.sudo().checksum = hashlib.md5(content).hexdigest()
        document.sudo().with_context(usl_documents_cache_write=True).write({"permission_sync_state": "pending"})
        with patch.object(PaperlessClient, "open_download") as download:
            with self.assertRaises(AccessError):
                other.attach_source_evidence(source_document_id=document.id)
        download.assert_not_called()
