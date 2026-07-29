import base64
from unittest.mock import patch

from odoo import Command, fields
from odoo.exceptions import AccessError, ValidationError
from odoo.tests import TransactionCase, tagged
from odoo.addons.mail.tests.common import mail_new_test_user

from ..models.paperless_client import (
    PaperlessClient,
    PaperlessCompatibilityError,
    PaperlessUnavailable,
)


@tagged("post_install", "-at_install", "usl_documents")
class TestDocuments(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company_a = cls.env.company
        cls.company_b = cls.env["res.company"].create({"name": "Restricted Company"})
        cls.manager = cls.env.ref("base.user_admin")
        cls.user = mail_new_test_user(
            cls.env,
            login="documents-user",
            name="Documents User",
            company_id=cls.company_a.id,
            company_ids=[Command.set(cls.company_a.ids)],
            groups="usl_documents.group_documents_user",
        )
        cls.accountant = mail_new_test_user(
            cls.env,
            login="documents-accountant",
            name="Evidence Accountant",
            company_id=cls.company_a.id,
            company_ids=[Command.set(cls.company_a.ids)],
            groups="usl_documents.group_documents_accountant",
        )
        cls.partner_a = cls.env["res.partner"].create({
            "name": "Archive Partner A",
            "company_id": cls.company_a.id,
        })
        cls.partner_b = cls.env["res.partner"].create({
            "name": "Archive Partner B",
            "company_id": cls.company_a.id,
        })

    def _document(self, paperless_id, **values):
        return self.env["usl.document"].create({
            "name": values.pop("name", f"Document {paperless_id}"),
            "paperless_id": paperless_id,
            "company_id": values.pop("company_id", self.company_a.id),
            "confidentiality": values.pop("confidentiality", "internal"),
            "review_state": values.pop("review_state", "classified"),
            "permission_sync_state": values.pop(
                "permission_sync_state", "synchronized"
            ),
            **values,
        })

    def test_one_archive_document_links_to_multiple_records(self):
        document = self._document(101, checksum="a" * 64)
        first = document.link_to_record("res.partner", self.partner_a.id)
        second = document.link_to_record("res.partner", self.partner_b.id)

        self.assertEqual(document.link_count, 2)
        self.assertEqual(first.document_id, second.document_id)
        first.unlink()
        self.assertTrue(document.exists())
        self.assertEqual(document.link_ids, second)

    def test_duplicate_checksum_reuses_archive_without_upload(self):
        content = b"identical supplier evidence"
        checksum = __import__("hashlib").sha256(content).hexdigest()
        existing = self._document(102, checksum=checksum)
        operation_count = self.env["usl.document.operation"].search_count([])
        result = (
            self.env["usl.document"]
            .with_user(self.user)
            .upload_from_odoo(
                "supplier.pdf",
                base64.b64encode(content).decode(),
                "application/pdf",
                res_model="res.partner",
                res_id=self.partner_a.id,
                company_id=self.company_a.id,
            )
        )
        self.assertEqual(result["state"], "duplicate")
        self.assertEqual(result["document_id"], existing.id)
        self.assertEqual(existing.link_count, 1)
        self.assertEqual(
            self.env["usl.document"].search_count([("checksum", "=", checksum)]), 1
        )
        self.assertEqual(
            self.env["usl.document.operation"].search_count([]), operation_count
        )

    def test_unfiltered_remote_checksum_response_does_not_false_duplicate(self):
        content = b"genuinely new evidence"
        with (
            patch.object(
                PaperlessClient,
                "search",
                return_value={
                    "results": [{
                        "id": 999,
                        "versions": [{"checksum": "e" * 64}],
                    }]
                },
            ),
            patch.object(
                PaperlessClient, "upload_multipart", return_value="task-new"
            ) as upload,
        ):
            result = self.env["usl.document"].upload_from_odoo(
                "new.pdf",
                base64.b64encode(content).decode(),
                "application/pdf",
            )
        self.assertEqual(result["state"], "processing")
        upload.assert_called_once()

    def test_company_and_accountant_permissions_do_not_leak_metadata(self):
        internal = self._document(103)
        evidence = self._document(
            104, confidentiality="accounting", accounting_evidence=True
        )
        restricted = self._document(105, company_id=self.company_b.id)
        hr_document = self._document(106, confidentiality="hr")

        user_documents = self.env["usl.document"].with_user(self.user).search([])
        self.assertIn(internal, user_documents)
        self.assertIn(evidence, user_documents)
        self.assertNotIn(restricted, user_documents)
        self.assertNotIn(hr_document, user_documents)

        accountant_documents = (
            self.env["usl.document"].with_user(self.accountant).search([])
        )
        self.assertEqual(accountant_documents, evidence)
        with self.assertRaises(AccessError):
            internal.with_user(self.accountant).check_access("read")

        internal_link = internal.link_to_record("res.partner", self.partner_a.id)
        evidence_link = evidence.link_to_record("res.partner", self.partner_b.id)
        accountant_links = self.env["usl.document.link"].with_user(
            self.accountant
        ).search([])
        self.assertEqual(accountant_links, evidence_link)
        self.assertNotIn(internal_link, accountant_links)

    def test_cross_company_relationship_is_rejected(self):
        document = self._document(107, company_id=self.company_a.id)
        partner = self.env["res.partner"].create({
            "name": "Restricted target",
            "company_id": self.company_b.id,
        })
        with self.assertRaises(ValidationError):
            document.link_to_record("res.partner", partner.id)

    def test_incremental_sync_is_idempotent_and_preserves_relationships(self):
        payload = {
            "count": 1,
            "next": None,
            "results": [{
                "id": 108,
                "title": "Externally ingested contract",
                "created": "2026-07-28",
                "added": "2026-07-28T10:00:00Z",
                "modified": "2026-07-28T10:05:00Z",
                "checksum": "b" * 64,
                "original_file_name": "contract.pdf",
                "mime_type": "application/pdf",
                "tags": [],
                "custom_fields": [],
                "versions": [{"id": 1, "version_label": "Received original"}],
            }],
        }
        with (
            patch.object(PaperlessClient, "compatibility", return_value={"ok": True}),
            patch.object(PaperlessClient, "list_documents", return_value=payload),
        ):
            first = (
                self.env["usl.document"]
                .with_user(self.manager)
                .sync_from_paperless()
            )
            document = self.env["usl.document"].search(
                [("paperless_id", "=", 108)]
            )
            document.link_to_record("res.partner", self.partner_a.id)
            second = (
                self.env["usl.document"]
                .with_user(self.manager)
                .sync_from_paperless()
            )
        self.assertEqual(first["synchronized"], 1)
        self.assertEqual(second["synchronized"], 1)
        self.assertEqual(
            self.env["usl.document"].search_count([("paperless_id", "=", 108)]), 1
        )
        self.assertEqual(document.link_count, 1)
        self.assertEqual(document.review_state, "classified")
        self.assertEqual(document.company_id, self.company_a)

    def test_sync_hydrates_integer_paperless_metadata_identifiers(self):
        payload = {
            "count": 1,
            "next": None,
            "results": [{
                "id": 179,
                "title": "Typed supplier invoice",
                "correspondent": 7,
                "document_type": 9,
                "tags": [11, 12],
                "modified": "2026-07-29T10:00:00Z",
                "versions": [],
            }],
        }
        catalog = {
            "correspondents": {7: "Example Supplier"},
            "document_types": {9: "Invoice"},
            "tags": {11: "Accounting", 12: "Reviewed"},
        }
        with (
            patch.object(PaperlessClient, "compatibility", return_value={"ok": True}),
            patch.object(PaperlessClient, "list_documents", return_value=payload),
            patch.object(
                PaperlessClient, "metadata_catalog", return_value=catalog
            ) as metadata_catalog,
        ):
            self.env["usl.document"].with_user(self.manager).sync_from_paperless()
        document = self.env["usl.document"].search(
            [("paperless_id", "=", 179)]
        )
        self.assertEqual(document.correspondent_name, "Example Supplier")
        self.assertEqual(document.document_type_name, "Invoice")
        self.assertEqual(document.tag_names, "Accounting, Reviewed")
        metadata_catalog.assert_called_once()

    def test_supported_paperless_metadata_write_contract(self):
        client = PaperlessClient(self.env)
        client.owner_user_id = 3
        with patch.object(
            client,
            "_request",
            side_effect=[
                ({"results": []}, {}),
                ({"id": 9, "name": "Contract"}, {}),
                ({"id": 179, "document_type": 9}, {}),
            ],
        ) as request:
            document_type = client.ensure_document_type("Contract")
            result = client.update_document_metadata(
                179, {"document_type": document_type["id"]}
            )
        self.assertEqual(document_type["id"], 9)
        self.assertEqual(result["document_type"], 9)
        self.assertEqual(
            request.call_args_list[1].args[:2],
            ("POST", "/api/document_types/"),
        )
        self.assertEqual(
            request.call_args_list[2].args[:2], ("PATCH", "/api/documents/179/")
        )

    def test_interrupted_incremental_sync_resumes_from_saved_checkpoint(self):
        page_one = {
            "next": "http://paperless/api/documents/?page=2",
            "results": [
                {
                    "id": 180,
                    "title": "First page",
                    "modified": "2026-07-29T10:00:00Z",
                    "versions": [],
                }
            ],
        }
        page_two = {
            "next": None,
            "results": [
                {
                    "id": 181,
                    "title": "Second page",
                    "modified": "2026-07-29T10:01:00Z",
                    "versions": [],
                }
            ],
        }
        with (
            patch.object(PaperlessClient, "compatibility", return_value={"ok": True}),
            patch.object(
                PaperlessClient,
                "list_documents",
                side_effect=[page_one, page_two],
            ) as list_documents,
        ):
            previous_sync = (
                self.env["ir.config_parameter"]
                .sudo()
                .get_str("usl_documents.last_sync")
            )
            first = self.env["usl.document"].with_user(self.manager).sync_from_paperless(
                limit_pages=1
            )
            self.assertFalse(first["complete"])
            self.assertEqual(
                self.env["ir.config_parameter"]
                .sudo()
                .get_str("usl_documents.sync_cursor_page"),
                "2",
            )
            self.assertEqual(
                self.env["ir.config_parameter"]
                .sudo()
                .get_str("usl_documents.last_sync"),
                previous_sync,
            )
            second = self.env["usl.document"].with_user(
                self.manager
            ).sync_from_paperless(limit_pages=1)
        self.assertTrue(second["complete"])
        self.assertEqual(
            self.env["usl.document"].search_count(
                [("paperless_id", "in", [180, 181])]
            ),
            2,
        )
        self.assertEqual(list_documents.call_args_list[1].kwargs["page"], 2)
        self.assertEqual(
            list_documents.call_args_list[0].kwargs["modified_before"],
            list_documents.call_args_list[1].kwargs["modified_before"],
        )

    def test_search_collects_all_paperless_pages_before_odoo_pagination(self):
        first = self._document(182, name="First OCR match")
        second = self._document(183, name="Second OCR match")
        with patch.object(
            PaperlessClient,
            "search",
            side_effect=[
                {
                    "count": 2,
                    "next": "http://paperless/api/documents/?page=2",
                    "results": [{"id": first.paperless_id}],
                },
                {
                    "count": 2,
                    "next": None,
                    "results": [{"id": second.paperless_id}],
                },
            ],
        ) as search:
            result = self.env["usl.document"].workspace_data(
                query="OCR-only phrase", page=1, page_size=1
            )
        self.assertEqual(result["count"], 2)
        self.assertEqual(len(result["documents"]), 1)
        self.assertEqual(search.call_count, 2)

    def test_async_success_creates_relationship_only_after_confirmation(self):
        operation = self.env["usl.document.operation"].with_user(self.user).create({
            "name": "pending.pdf",
            "state": "processing",
            "checksum": "c" * 64,
            "mime_type": "application/pdf",
            "company_id": self.company_a.id,
            "paperless_task_id": "task-1",
            "res_model": "res.partner",
            "res_id": self.partner_a.id,
            "source": "odoo_upload",
        })
        payload = {
            "id": 109,
            "title": "Confirmed archive",
            "created": "2026-07-29",
            "added": "2026-07-29T10:00:00Z",
            "modified": "2026-07-29T10:00:00Z",
            "original_file_name": "pending.pdf",
            "mime_type": "application/pdf",
            "tags": [],
            "custom_fields": [],
            "versions": [],
        }
        self.assertFalse(operation.document_id)
        with (
            patch.object(
                PaperlessClient,
                "task",
                return_value={"status": "success", "related_document_ids": [109]},
            ),
            patch.object(PaperlessClient, "get_document", return_value=payload),
        ):
            status = operation.with_user(self.user).poll()
        self.assertEqual(status[operation.id]["state"], "archived")
        self.assertTrue(operation.document_id)
        self.assertEqual(operation.document_id.link_count, 1)
        self.assertEqual(operation.document_id.checksum, "c" * 64)

    def test_structured_versions_are_stable_and_version_downloads_are_scoped(self):
        document = self._document(184)
        document._synchronize_versions(
            [
                {
                    "id": 71,
                    "version_label": "Replacement",
                    "checksum": "8" * 64,
                    "mime_type": "application/pdf",
                },
                {
                    "id": 70,
                    "is_root": True,
                    "checksum": "7" * 64,
                    "mime_type": "application/pdf",
                },
            ]
        )
        self.assertEqual(len(document.version_ids), 2)
        self.assertEqual(document.version_ids.filtered("is_current").paperless_version_id, "71")
        self.assertEqual(
            document.version_ids.filtered("is_received_original").paperless_version_id,
            "70",
        )
        self.assertEqual(
            document.version_ids.filtered("is_received_original").label,
            "Received original",
        )
        action = document.version_ids.filtered("is_current").action_download_original()
        self.assertIn("version=71", action["url"])
        document._synchronize_versions(
            [
                {
                    "id": 71,
                    "version_label": "Final replacement",
                    "checksum": "8" * 64,
                },
                {
                    "id": 70,
                    "is_root": True,
                    "checksum": "7" * 64,
                },
            ]
        )
        self.assertEqual(len(document.version_ids), 2)
        self.assertEqual(
            document.version_ids.filtered("is_current").label, "Final replacement"
        )

    def test_replacement_version_preserves_root_policy_and_relationships(self):
        document = self._document(
            187, confidentiality="accounting", accounting_evidence=True
        )
        document.link_to_record("res.partner", self.partner_a.id)
        operation = self.env["usl.document.operation"].create(
            {
                "name": "replacement.pdf",
                "state": "processing",
                "checksum": "9" * 64,
                "mime_type": "application/pdf",
                "company_id": self.company_a.id,
                "paperless_task_id": "task-version",
                "source": "odoo_upload",
                "target_document_id": document.id,
            }
        )
        payload = {
            "id": document.paperless_id,
            "title": document.name,
            "created": "2026-07-29",
            "modified": "2026-07-29T11:00:00Z",
            "versions": [
                {"id": 81, "version_label": "Replacement", "checksum": "9" * 64},
                {"id": 80, "is_root": True, "checksum": "1" * 64},
            ],
        }
        with (
            patch.object(
                PaperlessClient,
                "task",
                return_value={
                    "status": "success",
                    "related_document_ids": [document.paperless_id],
                },
            ),
            patch.object(PaperlessClient, "get_document", return_value=payload),
        ):
            operation.poll()
        self.assertEqual(operation.state, "archived")
        self.assertEqual(operation.document_id, document)
        self.assertEqual(document.confidentiality, "accounting")
        self.assertTrue(document.accounting_evidence)
        self.assertEqual(document.link_count, 1)
        self.assertEqual(len(document.version_ids), 2)
        self.assertEqual(
            document.version_ids.filtered("is_current").submitted_by_id,
            operation.user_id,
        )

    def test_permission_sync_uses_individual_authorized_identities(self):
        document = self._document(
            110,
            permission_sync_state="pending",
        )
        self.env["usl.paperless.user.mapping"].search([
            ("user_id", "in", [self.user.id, self.manager.id])
        ]).unlink()
        self.env["usl.paperless.user.mapping"].create([
            {
                "user_id": self.user.id,
                "paperless_user_id": 21,
                "paperless_username": "documents-user",
                "sync_state": "synchronized",
            },
            {
                "user_id": self.manager.id,
                "paperless_user_id": 22,
                "paperless_username": "admin",
                "sync_state": "synchronized",
            },
        ])
        with patch.object(
            PaperlessClient, "set_document_permissions", return_value={}
        ) as permission_call:
            document.with_user(self.manager).action_sync_permissions()
        self.assertEqual(document.permission_sync_state, "synchronized")
        permission_call.assert_called_once_with(
            110, view_users=[21, 22], change_users=[22]
        )

    def test_permission_sync_failure_blocks_paperless_deep_link(self):
        self.env["ir.config_parameter"].sudo().set_str(
            "usl_documents.paperless_public_url", "https://documents.example.test"
        )
        document = self._document(
            111,
            permission_sync_state="failed",
        )
        self.assertFalse(document.paperless_url)
        with self.assertRaisesRegex(
            Exception, "blocked until your individual archive identity"
        ):
            document.action_open_paperless()

    def test_deep_link_requires_current_users_verified_individual_mapping(self):
        self.env["ir.config_parameter"].sudo().set_str(
            "usl_documents.paperless_public_url", "https://documents.example.test"
        )
        document = self._document(185)
        self.assertFalse(document.with_user(self.user).paperless_url)
        self.env["usl.paperless.user.mapping"].create(
            {
                "user_id": self.user.id,
                "paperless_user_id": 85,
                "paperless_username": "documents-user",
                "sync_state": "synchronized",
            }
        )
        document.invalidate_recordset(["paperless_url"])
        self.assertIn(
            "/documents/185/details", document.with_user(self.user).paperless_url
        )

    def test_cache_policy_and_direct_link_creation_are_not_client_writable(self):
        document = self._document(186)
        with self.assertRaises(AccessError):
            document.with_user(self.user).write({"name": "Spoofed Paperless title"})
        with self.assertRaises(AccessError):
            document.with_user(self.user).write({"confidentiality": "private"})
        with self.assertRaises(AccessError):
            self.env["usl.document.link"].with_user(self.user).create(
                {
                    "document_id": document.id,
                    "res_model": "res.partner",
                    "res_id": self.partner_a.id,
                    "record_name": self.partner_a.display_name,
                    "company_id": self.company_a.id,
                }
            )

    def test_integrity_manifest_reports_versions_links_and_checksums(self):
        document = self._document(112, checksum="d" * 64)
        document.link_to_record("res.partner", self.partner_a.id)
        with (
            patch.object(
                PaperlessClient,
                "compatibility",
                return_value={
                    "server_version": "3.0.4",
                    "api_version": "10",
                    "document_count": 1,
                },
            ),
            patch.object(
                PaperlessClient,
                "list_documents",
                return_value={
                    "count": 1,
                    "next": None,
                    "results": [
                        {
                            "id": 112,
                            "checksum": "d" * 64,
                            "versions": [],
                        }
                    ],
                },
            ),
        ):
            manifest = self.env["usl.document"].integrity_manifest("qa-backup")
        self.assertEqual(manifest["schema"], "usl-documents-integrity-v1")
        self.assertEqual(manifest["backup_id"], "qa-backup")
        self.assertEqual(manifest["paperless_version"], "3.0.4")
        self.assertGreaterEqual(manifest["relationship_count"], 1)
        self.assertIn(
            "d" * 64,
            [item["checksum"] for item in manifest["representative_checksums"]],
        )


@tagged("post_install", "-at_install", "usl_documents")
class TestPaperlessClientContract(TransactionCase):
    def test_api_v10_and_server_3_are_explicitly_qualified(self):
        client = PaperlessClient(self.env)
        with patch.object(
            client,
            "_request",
            return_value=(
                {"count": 2},
                {"x-api-version": "10", "x-version": "3.0.4"},
            ),
        ):
            result = client.compatibility()
        self.assertEqual(result["api_version"], "10")
        self.assertEqual(result["server_version"], "3.0.4")

    def test_unsupported_api_or_major_version_fails_clearly(self):
        client = PaperlessClient(self.env)
        with (
            patch.object(
                client,
                "_request",
                return_value=(
                    {"count": 0},
                    {"X-Api-Version": "9", "X-Version": "3.0.4"},
                ),
            ),
            self.assertRaises(PaperlessCompatibilityError),
        ):
            client.compatibility()
        with (
            patch.object(
                client,
                "_request",
                return_value=(
                    {"count": 0},
                    {"X-Api-Version": "10", "X-Version": "4.0.0"},
                ),
            ),
            self.assertRaises(PaperlessCompatibilityError),
        ):
            client.compatibility()

    def test_unconfigured_client_fails_without_affecting_odoo(self):
        params = self.env["ir.config_parameter"].sudo()
        params.set_str("usl_documents.paperless_url", "")
        params.set_str("usl_documents.paperless_token", "")
        with self.assertRaises(PaperlessUnavailable):
            PaperlessClient(self.env).list_documents()

    def test_fail_closed_workflow_uses_supported_api_for_every_channel(self):
        self.env["ir.config_parameter"].sudo().set_int(
            "usl_documents.paperless_service_user_id", 42
        )
        client = PaperlessClient(self.env)
        with patch.object(
            client,
            "_request",
            side_effect=[
                ({"results": []}, {}),
                (
                    {
                        "id": 7,
                        "name": client.FAIL_CLOSED_WORKFLOW_NAME,
                    },
                    {},
                ),
            ],
        ) as request:
            result = client.ensure_fail_closed_ingestion_policy()
        self.assertTrue(result["created"])
        method, path = request.call_args_list[1].args
        payload = request.call_args_list[1].kwargs["body"]
        self.assertEqual((method, path), ("POST", "/api/workflows/"))
        self.assertEqual(payload["triggers"][0]["sources"], [1, 2, 3, 4])
        self.assertEqual(payload["actions"][0]["assign_owner"], 42)
        self.assertEqual(payload["actions"][0]["assign_view_users"], [])
