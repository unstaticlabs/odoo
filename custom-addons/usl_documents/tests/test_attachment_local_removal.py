"""Native HTTP removal of disposable files that were never archived."""
from io import BytesIO
from unittest.mock import patch

from odoo import Command
from odoo.exceptions import AccessError, UserError
from odoo.tests import HttpCase, JsonRpcException, tagged
from odoo.addons.mail.tests.common import mail_new_test_user
from odoo.addons.usl_documents.models.attachment_bridge import ORIGIN_CAPTURE_TOKEN
from odoo.addons.usl_documents.models.paperless_client import PaperlessClient


@tagged("post_install", "-at_install", "usl_documents", "document_local_removal")
class TestLocalAttachmentRemoval(HttpCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.editor = mail_new_test_user(
            cls.env, login="local-file-editor", password="local-file-editor",
            groups="usl_documents.group_documents_user,base.group_partner_manager",
        )
        cls.reader = mail_new_test_user(
            cls.env, login="local-file-reader", password="local-file-reader",
            groups="usl_documents.group_documents_accountant",
        )
        cls.irreversible_group = cls.env.ref(
            "usl_access_control.group_irreversible_actions", raise_if_not_found=False,
        )
        cls.remover = cls.editor
        cls.token_owner = cls.reader
        if cls.irreversible_group:
            cls.remover = mail_new_test_user(
                cls.env, login="authorized-local-file-remover", password="authorized-local-file-remover",
                groups="usl_documents.group_documents_user,base.group_partner_manager,usl_access_control.group_irreversible_actions",
            )
            cls.token_owner = mail_new_test_user(
                cls.env, login="authorized-local-file-owner", password="authorized-local-file-owner",
                groups="usl_documents.group_documents_accountant,usl_access_control.group_irreversible_actions",
            )
        cls.record = cls.env["res.partner"].create({
            "name": "Disposable local file record", "company_id": cls.env.company.id,
        })
        cls.env.ref("usl_pocketid.provider_pocketid")._usl_pocketid_environment_write(
            {"enabled": False},
        )

    def _attachment(self, record=None, message_type=None):
        record = record or self.record
        attachment = self.env["ir.attachment"].with_context(
            usl_documents_origin_token=ORIGIN_CAPTURE_TOKEN,
            usl_documents_attachment_origin="chatter",
        ).create({
            "name": "disposable-local-file.txt", "raw": b"Invented disposable QA file",
            "mimetype": "text/plain", "res_model": record._name, "res_id": record.id,
        })
        self.assertEqual(attachment.usl_documents_archive_mode, "on_request")
        self.assertEqual(attachment.usl_documents_ledger_state, "native_only_on_request")
        self.assertFalse(self.env["usl.document.operation"].search([
            ("source_attachment_id", "=", attachment.id),
        ]))
        if message_type:
            self.env["mail.message"].create({
                "model": record._name, "res_id": record.id,
                "message_type": message_type, "body": "Invented QA attachment message",
                "attachment_ids": [Command.link(attachment.id)],
            })
        return attachment

    def _delete(self, attachment, token=None):
        return self.make_jsonrpc_request("/mail/attachment/delete", {
            "attachment_id": attachment.id, "access_token": token,
        })

    def test_native_http_deletes_only_local_file_without_provider(self):
        for message_type in (None, "comment", "notification"):
            with self.subTest(message_type=message_type):
                attachment = self._attachment(message_type=message_type)
                survivor = self._attachment()
                checksum = survivor.checksum
                self.assertTrue(attachment.with_user(self.editor).has_access("write"))
                self.authenticate(self.remover.login, self.remover.login)
                with patch.object(PaperlessClient, "_request", side_effect=AssertionError(
                    "Local removal must not contact the archive provider",
                )):
                    self._delete(attachment)
                self.assertFalse(attachment.exists())
                self.assertEqual(bytes(survivor.raw), b"Invented disposable QA file")
                self.assertEqual(survivor.checksum, checksum)

    def test_current_native_readonly_authority_denies_without_token(self):
        attachment = self._attachment(self.env.company)
        self.assertTrue(attachment.with_user(self.reader).has_access("read"))
        self.assertFalse(attachment.with_user(self.reader).has_access("write"))
        self.authenticate(self.reader.login, self.reader.login)
        with self.assertRaises(JsonRpcException):
            self._delete(attachment)
        self.assertTrue(attachment.exists())

    def test_native_ownership_token_keeps_its_existing_authority(self):
        attachment = self._attachment(self.env.company)
        self.assertFalse(attachment.with_user(self.reader).has_access("write"))
        token = attachment._get_ownership_token()
        self.authenticate(self.token_owner.login, self.token_owner.login)
        self._delete(attachment, token)
        self.assertFalse(attachment.exists())

    def test_canonical_tracked_operation_rejects_stale_local_state(self):
        attachment = self._attachment()
        attachment.with_user(self.editor).action_keep_in_documents_from_ui()
        operation = self.env["usl.document.operation"].search([
            ("source_attachment_id", "=", attachment.id),
        ])
        self.assertTrue(operation)
        # A policy refresh can restore the local-looking ledger while an exact
        # operation still exists. The operation remains authoritative.
        attachment.sudo().with_context(usl_documents_attachment_policy_write=True).write({
            "usl_documents_ledger_state": "native_only_on_request",
        })
        self.authenticate(self.editor.login, self.editor.login)
        with self.assertRaises(JsonRpcException):
            self._delete(attachment)
        self.assertTrue(attachment.exists())
        self.assertTrue(operation.exists())

    def test_current_company_change_denies_after_local_details_were_read(self):
        attachment = self._attachment()
        self.authenticate(self.editor.login, self.editor.login)
        details = self.make_jsonrpc_request(
            "/web/dataset/call_kw/ir.attachment/get_keep_in_documents_details",
            {"model": "ir.attachment", "method": "get_keep_in_documents_details",
             "args": [[attachment.id]], "kwargs": {}},
        )
        self.assertEqual(details[str(attachment.id)]["state"], "available")
        other_company = self.env["res.company"].create({"name": "Isolated QA company"})
        self.record.write({"company_id": other_company.id})
        self.assertFalse(attachment.with_user(self.editor).has_access("write"))
        with self.assertRaises(JsonRpcException):
            self._delete(attachment)
        self.assertTrue(attachment.exists())

    def test_native_editor_without_documents_group_can_delete_local_file(self):
        editor = mail_new_test_user(
            self.env, login="native-file-editor", password="native-file-editor",
            groups="base.group_user,base.group_partner_manager",
        )
        attachment = self._attachment()
        self.assertFalse(editor.has_group("usl_documents.group_documents_user"))
        self.assertTrue(attachment.with_user(editor).has_access("write"))
        if self.irreversible_group:
            editor.write({"group_ids": [Command.link(self.irreversible_group.id)]})
        self.authenticate(editor.login, editor.login)
        self._delete(attachment)
        self.assertFalse(attachment.exists())

    def test_persistent_local_file_keeps_existing_irreversible_guard(self):
        if not self.irreversible_group:
            self.skipTest("The full product closure supplies the central irreversible guard")
        for message_type in (None, "comment", "notification"):
            with self.subTest(message_type=message_type):
                attachment = self._attachment(message_type=message_type)
                self.authenticate(self.editor.login, self.editor.login)
                with self.assertRaises(JsonRpcException):
                    self._delete(attachment)
                self.assertTrue(attachment.exists())

    def test_authorized_deletion_has_central_actor_audit(self):
        if not self.irreversible_group:
            self.skipTest("The full product closure supplies central actor audit")
        attachment = self._attachment(message_type="notification")
        attachment_id = attachment.id
        self.authenticate(self.remover.login, self.remover.login)
        self._delete(attachment)
        self.assertFalse(attachment.exists())
        audit = self.env["usl.audit.event"].search([
            ("actor_id", "=", self.remover.id),
            ("event_type", "=", "protected_action"),
            ("model_name", "=", "ir.attachment"),
            ("record_ids", "=", str([attachment_id])),
        ])
        # The pre-message authorization and final unlink each recheck the same
        # capability. These are two authorization events for one file deletion.
        self.assertEqual(len(audit), 2)
        self.assertTrue(all(audit.mapped("policy_digest")))
        with self.assertRaises(UserError):
            audit.with_user(self.remover).write({"action_name": "Changed"})
        with self.assertRaises(UserError):
            audit.with_user(self.remover).unlink()

    def test_agent_and_sudo_agent_cannot_delete_persistent_local_file(self):
        if not self.irreversible_group:
            self.skipTest("The full product closure supplies Agent and irreversible guards")
        agent = mail_new_test_user(
            self.env, login="local-file-agent", password="local-file-agent",
            groups="usl_documents.group_documents_user,base.group_partner_manager,usl_access_control.group_ai_agent",
        )
        attachment = self._attachment(message_type="notification")
        self.authenticate(agent.login, agent.login)
        with self.assertRaises(JsonRpcException):
            self._delete(attachment)
        self.assertTrue(attachment.exists())
        with self.assertRaises(AccessError):
            attachment.with_user(agent).sudo().unlink()
        self.assertTrue(attachment.exists())

    def test_unattached_native_upload_cleanup_remains_available(self):
        attachment = self.env["ir.attachment"].with_user(self.editor).create({
            "name": "disposable-composer-file.txt", "raw": b"Invented unsent file",
            "mimetype": "text/plain",
        })
        self.authenticate(self.editor.login, self.editor.login)
        self._delete(attachment)
        self.assertFalse(attachment.exists())

    def test_valid_native_token_does_not_require_extra_record_read_access(self):
        other_company = self.env["res.company"].create({"name": "Token QA company"})
        partner = self.env["res.partner"].create({
            "name": "Disposable token-owned file", "company_id": other_company.id,
        })
        attachment = self._attachment(partner)
        self.assertFalse(attachment.with_user(self.token_owner).has_access("read"))
        token = attachment._get_ownership_token()
        self.authenticate(self.token_owner.login, self.token_owner.login)
        self._delete(attachment, token)
        self.assertFalse(attachment.exists())

    def test_existing_protection_runs_before_touching_carrying_message(self):
        if not self.irreversible_group:
            self.skipTest("The full product closure supplies the central irreversible guard")
        attachment = self._attachment(message_type="notification")
        message = self.env["mail.message"].search([("attachment_ids", "in", attachment.ids)])
        writes = []
        message_class = type(message)
        original_write = message_class.write

        def record_write(records, values):
            if message.id in records.ids:
                writes.append(values)
            return original_write(records, values)

        self.authenticate(self.editor.login, self.editor.login)
        with patch.object(message_class, "write", record_write):
            with self.assertRaises(JsonRpcException):
                self._delete(attachment)
        self.assertFalse(writes, "Protected denial must precede native carrying-message writes")
        self.assertTrue(attachment.exists())

    def test_native_task_upload_can_be_removed_without_archiving(self):
        self.remover.write({"group_ids": [Command.link(
            self.env.ref("project.group_project_user").id,
        )]})
        project = self.env["project.project"].create({
            "name": "Disposable local-file project", "company_id": self.env.company.id,
            "privacy_visibility": "employees",
        })
        task = self.env["project.task"].create({
            "name": "Disposable attachment task", "project_id": project.id,
        })
        self.authenticate(self.remover.login, self.remover.login)
        with patch.object(PaperlessClient, "_request", side_effect=AssertionError(
            "Native local task upload/removal must not contact Paperless",
        )):
            response = self.url_open(
                "/mail/attachment/upload",
                data={"csrf_token": self.csrf_token(), "thread_model": "project.task",
                      "thread_id": task.id, "is_pending": "false"},
                files={"ufile": ("disposable-task-note.txt", BytesIO(b"Invented task note"), "text/plain")},
            )
            response.raise_for_status()
            payload = response.json()["data"]
            attachment = self.env["ir.attachment"].browse(payload["attachment_id"])
            self.assertEqual(attachment.usl_documents_archive_mode, "on_request")
            self.assertEqual(attachment.usl_documents_ledger_state, "native_only_on_request")
            self.assertFalse(self.env["usl.document.operation"].search([
                ("source_attachment_id", "=", attachment.id),
            ]))
            self._delete(attachment)
        self.assertFalse(attachment.exists())
        self.assertTrue(task.exists())
