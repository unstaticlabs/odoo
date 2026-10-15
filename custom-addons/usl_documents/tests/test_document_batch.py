"""Synthetic HTTP boundary tests; no live archive or privileged actor bypass."""

import importlib
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from odoo import Command
from odoo.exceptions import AccessError, ValidationError
from odoo.tests import TransactionCase, tagged

from odoo.addons.mail.tests.common import mail_new_test_user


@tagged("post_install", "-at_install", "usl_documents", "usl_document_batch")
class TestDocumentBatch(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.editor = mail_new_test_user(
            cls.env,
            login="qa-batch-editor",
            groups="usl_documents.group_documents_user",
        )
        cls.viewer = mail_new_test_user(
            cls.env,
            login="qa-batch-viewer",
            groups="usl_documents.group_documents_accountant",
        )
        cls.other_company = cls.env["res.company"].create(
            {"name": "[QA] Other archive company"},
        )

    def setUp(self):
        super().setUp()
        self.remote = {
            "id": 92001,
            "title": "[QA] Batch document",
            "tags": [92999],
            "correspondent": None,
            "document_type": None,
        }
        self.requests = []
        self.failure = None
        self.ignore_patch = False
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def do_GET(self):
                owner.requests.append(("GET", self.path))
                self.send_response(503 if owner.failure == "before" else 200)
                self.end_headers()
                self.wfile.write(json.dumps(owner.remote).encode())

            def do_PATCH(self):
                values = json.loads(
                    self.rfile.read(int(self.headers["Content-Length"])),
                )
                owner.requests.append(("PATCH", values))
                if not owner.ignore_patch:
                    owner.remote.update(values)
                self.send_response(503 if owner.failure == "after" else 200)
                self.end_headers()
                self.wfile.write(json.dumps(owner.remote).encode())

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        params = self.env["ir.config_parameter"].sudo()
        params.set_str(
            "usl_documents.paperless_url", f"http://127.0.0.1:{self.server.server_port}",
        )
        params.set_str("usl_documents.paperless_token", "disposable-http-fixture")
        self.document = self.env["usl.document"].create(
            {
                "name": self.remote["title"],
                "paperless_id": 92001,
                "company_id": self.env.company.id,
                "confidentiality": "internal",
                "review_state": "classified",
                "permission_sync_state": "synchronized",
            },
        )
        self.tag = (
            self.env["usl.paperless.tag"]
            .with_context(usl_documents_cache_write=True)
            .create({"name": "[QA] Added", "paperless_id": 92002})
        )
        self.correspondent = (
            self.env["usl.paperless.correspondent"]
            .with_context(usl_documents_cache_write=True)
            .create({"name": "[QA] Correspondent", "paperless_id": 92003})
        )
        self.kind = (
            self.env["usl.paperless.document.type"]
            .with_context(usl_documents_cache_write=True)
            .create({"name": "[QA] Type", "paperless_id": 92004})
        )

    def apply(self, operation="add_tags", ids=None, actor=None):
        return self.document.with_user(
            actor or self.editor,
        ).apply_batch_archive_metadata(operation, ids or self.tag.ids)

    def test_additive_remote_tags_and_repeated_apply(self):
        self.assertEqual(self.apply()["status"], "completed")
        self.assertEqual(set(self.remote["tags"]), {92999, 92002})
        self.assertEqual(self.apply()["status"], "completed")
        self.assertEqual(len([item for item in self.requests if item[0] == "PATCH"]), 1)

    def test_explicit_correspondent_and_type_replace_only_selected_field(self):
        self.assertEqual(
            self.apply("set_correspondent", self.correspondent.ids)["status"],
            "completed",
        )
        self.assertEqual(
            self.apply("set_document_type", self.kind.ids)["status"], "completed",
        )
        self.assertEqual(self.remote["correspondent"], 92003)
        self.assertEqual(self.remote["document_type"], 92004)
        self.assertEqual(self.document.write_uid, self.editor)
        self.assertEqual(self.remote["tags"], [92999])

    def test_before_request_failure_sends_no_change(self):
        self.failure = "before"
        self.assertEqual(self.apply()["status"], "failed")
        self.assertEqual(self.remote["tags"], [92999])
        self.assertFalse(any(item[0] == "PATCH" for item in self.requests))

    def test_after_write_failure_retry_confirms_without_repeating_write(self):
        self.failure = "after"
        self.assertEqual(self.apply()["status"], "uncertain")
        self.assertEqual(set(self.remote["tags"]), {92999, 92002})
        self.failure = None
        self.assertEqual(self.apply()["status"], "completed")
        self.assertEqual(len([item for item in self.requests if item[0] == "PATCH"]), 1)

    def test_viewer_and_current_company_denied_before_http(self):
        with self.assertRaises(AccessError):
            self.apply(actor=self.viewer)
        self.document.with_context(usl_documents_cache_write=True).write(
            {"company_id": self.other_company.id},
        )
        with self.assertRaises(AccessError) as refused:
            self.apply()
        self.assertNotIn(self.document.name, str(refused.exception))
        self.assertFalse(self.requests)

    def test_unavailable_metadata_and_crafted_operations_denied_before_http(self):
        self.tag.with_context(usl_documents_cache_write=True).write({"active": False})
        for operation, ids in (
            ("add_tags", self.tag.ids),
            ("set_company", [1]),
            ("set_correspondent", [999999]),
            ("set_document_type", [True]),
            ("add_tags", [1, 1]),
        ):
            with (
                self.subTest(operation=operation, ids=ids),
                self.assertRaises(ValidationError),
            ):
                self.apply(operation, ids)
        self.assertFalse(self.requests)

    def test_acknowledged_but_unapplied_write_is_not_reported_completed(self):
        self.ignore_patch = True
        result = self.apply()
        self.assertEqual(result["status"], "uncertain")
        self.assertEqual(self.remote["tags"], [92999])

    def test_wrong_remote_identity_refused_before_write_or_cache_rebinding(self):
        self.remote["id"] = 92901
        result = self.apply()
        self.assertEqual(result["status"], "failed")
        self.assertEqual(self.document.paperless_id, 92001)
        self.assertFalse(any(item[0] == "PATCH" for item in self.requests))

    def test_stale_cached_correspondent_does_not_skip_authoritative_change(self):
        self.document.with_context(usl_documents_cache_write=True).write({
            "correspondent_id": self.correspondent.id,
        })
        self.assertIsNone(self.remote["correspondent"])
        result = self.apply("set_correspondent", self.correspondent.ids)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(self.remote["correspondent"], 92003)

    def test_managed_agent_write_and_readonly_scope_use_the_same_native_boundaries(self):
        if "usl.agent" not in self.env:
            self.skipTest("Managed Agent extension is not installed in the focused Documents closure")
        group = self.env.ref("usl_documents.group_documents_user")
        agent = self.env["usl.agent"].with_user(self.editor).create({
            "name": "[QA] Document batch helper",
            "purpose": "Exercise fixed synthetic archive metadata editing.",
            "owner_id": self.editor.id,
            "company_id": self.env.company.id,
            "company_ids": [Command.set(self.editor.company_ids.ids)],
            "delegated_group_ids": [Command.set(group.ids)],
            "access_mode": "read_write",
        })
        controller = importlib.import_module(
            "odoo.addons.usl_access_control.controllers.json2",
        ).UslAgentJson2Controller

        access = controller._check_agent_call(
            agent=agent, model_name="usl.document", method_name="apply_batch_archive_metadata",
            kwargs={"ids": self.document.ids, "operation": "add_tags", "metadata_ids": self.tag.ids},
        )
        self.assertEqual(access, "write")
        context = controller._agent_call_context(
            context={}, agent=agent, model_name="usl.document",
            method_name="apply_batch_archive_metadata", access=access,
        )
        self.assertEqual(
            self.document.with_user(agent.user_id).with_context(context).apply_batch_archive_metadata("add_tags", self.tag.ids)["status"],
            "completed",
        )
        agent.with_user(self.editor).write({
            "read_only_group_ids": [Command.set(group.ids)],
        })
        self.requests.clear()
        self.assertFalse(self.env["usl.document"].with_user(agent.user_id).workspace_data()["can_edit_metadata"])
        with self.assertRaises(AccessError):
            controller._check_agent_call(
                agent=agent, model_name="usl.document", method_name="apply_batch_archive_metadata", kwargs={},
            )
        with self.assertRaises(AccessError):
            self.apply(actor=agent.user_id)
        with self.assertRaises(AccessError):
            self.document.with_user(agent.user_id).sudo().apply_batch_archive_metadata("add_tags", self.tag.ids)
        self.assertFalse(self.requests)

    def test_satisfied_change_does_not_rebind_binary_or_lifecycle_cache(self):
        self.remote["tags"] = [92999, 92002]
        self.remote["checksum"] = "f" * 64
        self.remote["original_file_name"] = "different-remote-version.pdf"
        self.document.with_context(usl_documents_cache_write=True).write({
            "checksum": "0" * 64, "original_filename": "retained-original.pdf",
        })
        self.assertEqual(self.apply()["status"], "completed")
        self.assertEqual(self.document.checksum, "0" * 64)
        self.assertEqual(self.document.original_filename, "retained-original.pdf")
        self.assertFalse(any(item[0] == "PATCH" for item in self.requests))

    def test_workspace_reports_native_metadata_write_access_for_review_and_edit(self):
        editor_view = self.env["usl.document"].with_user(self.editor).workspace_data()
        viewer_view = self.env["usl.document"].with_user(self.viewer).workspace_data()
        self.assertTrue(editor_view["can_edit_metadata"])
        self.assertFalse(viewer_view["can_edit_metadata"])
        self.assertFalse(self.requests)
