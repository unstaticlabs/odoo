import base64
from types import SimpleNamespace
from unittest.mock import patch

from odoo import Command, fields
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import TransactionCase, tagged

from ..controllers import documents as documents_controller_module
from ..controllers.documents import DocumentsController
from ..models.document import UslDocument
from ..models.paperless_client import (
    PaperlessClient,
    PaperlessCompatibilityError,
    PaperlessError,
    PaperlessNotFound,
    PaperlessUnavailable,
)
from odoo.addons.mail.tests.common import mail_new_test_user


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
        cls.env.ref(
            "usl_pocketid.provider_pocketid",
        )._usl_pocketid_environment_write({"enabled": False})
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
                "permission_sync_state", "synchronized",
            ),
            **values,
        })

    def _tag(self, paperless_id, name, **values):
        return (
            self.env["usl.paperless.tag"]
            .with_context(usl_documents_cache_write=True)
            .create(
                {
                    "paperless_id": paperless_id,
                    "name": name,
                    "color": values.pop("color", "#336699"),
                    "text_color": values.pop("text_color", "#ffffff"),
                    **values,
                },
            )
        )

    def _correspondent(self, paperless_id, name, **values):
        return (
            self.env["usl.paperless.correspondent"]
            .with_context(usl_documents_cache_write=True)
            .create({"paperless_id": paperless_id, "name": name, **values})
        )

    def _document_type(self, paperless_id, name, **values):
        return (
            self.env["usl.paperless.document.type"]
            .with_context(usl_documents_cache_write=True)
            .create({"paperless_id": paperless_id, "name": name, **values})
        )

    def test_one_archive_document_links_to_multiple_records(self):
        document = self._document(101, checksum="a" * 64)
        first = document.link_to_record("res.partner", self.partner_a.id)
        second = document.link_to_record("res.partner", self.partner_b.id)

        self.assertEqual(document.link_count, 2)
        self.assertEqual(first.document_id, second.document_id)
        first.unlink()
        self.assertTrue(document.exists())
        self.assertEqual(document.link_ids, second)

    def test_added_date_prefers_attributed_submission_history(self):
        document = self._document(
            100,
            paperless_created="2026-08-04 12:00:00",
            submitted_at="2025-12-06 13:05:28",
        )
        self.assertEqual(
            document.archive_added_at,
            fields.Datetime.to_datetime("2025-12-06 13:05:28"),
        )
        document.sudo().with_context(usl_documents_cache_write=True).write(
            {"submitted_at": False},
        )
        self.assertEqual(
            document.archive_added_at,
            fields.Datetime.to_datetime("2026-08-04 12:00:00"),
        )

    def test_document_detail_never_exposes_legacy_migration_fields(self):
        self.env["ir.config_parameter"].sudo().set_str(
            "usl_documents.paperless_custom_fields",
            """[
                {"id": 7, "name": "Purchase order", "data_type": "string"},
                {"id": 8, "name": "Legacy Odoo folder paths", "data_type": "string"}
            ]""",
        )
        document = self._document(
            1001,
            custom_fields_json="""[
                {"field": 7, "value": "PO-2026-42"},
                {"field": 8, "value": "Finance / Purchases"},
                {"field": 999, "value": "orphaned migration value"}
            ]""",
        )

        self.assertEqual(
            document.document_detail(document.id)["custom_fields"],
            [{
                "id": 7,
                "name": "Purchase order",
                "data_type": "string",
                "value": "PO-2026-42",
            }],
        )

    def test_business_relationship_pins_the_current_file_version(self):
        document = self._document(401)
        document._synchronize_versions(
            [
                {"id": 12, "version_label": "Current replacement"},
                {"id": 11, "is_root": True},
            ],
        )
        link = document.link_to_record("res.partner", self.partner_a.id)
        self.assertEqual(link.version_id, "12")
        document._synchronize_versions(
            [
                {"id": 13, "version_label": "Later replacement"},
                {"id": 12, "version_label": "Current replacement"},
                {"id": 11, "is_root": True},
            ],
        )
        self.assertEqual(link.version_id, "12")
        detail = document.document_detail(document.id)
        self.assertEqual(detail["links"][0]["version_label"], "Current replacement")

    def test_reconciliation_pins_legacy_relationship_to_received_original(self):
        document = self._document(403)
        link = self.env["usl.document.link"].sudo().create(
            {
                "document_id": document.id,
                "res_model": "res.partner",
                "res_id": self.partner_a.id,
                "record_name": self.partner_a.display_name,
                "company_id": self.company_a.id,
                "linked_by_id": self.env.user.id,
            },
        )
        document._synchronize_versions(
            [
                {"id": 17, "version_label": "Current replacement"},
                {"id": 16, "is_root": True},
            ],
        )
        self.assertEqual(link.version_id, "16")

    def test_document_detail_reports_archive_outage_without_hiding_cached_context(self):
        document = self._document(402, name="Cached supplier evidence")
        with patch.object(
            PaperlessClient,
            "compatibility",
            side_effect=PaperlessUnavailable("Archive offline"),
        ):
            detail = document.document_detail(document.id, check_archive=True)

        self.assertFalse(detail["archive_available"])
        self.assertEqual(detail["name"], "Cached supplier evidence")

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
        self.assertIn(existing.name, result["message"])
        self.assertEqual(existing.link_count, 1)
        self.assertEqual(
            self.env["usl.document"].search_count([("checksum", "=", checksum)]), 1,
        )
        self.assertEqual(
            self.env["usl.document.operation"].search_count([]), operation_count,
        )

    def test_duplicate_checksum_reuses_root_when_file_is_an_earlier_version(self):
        content = b"received supplier evidence"
        checksum = __import__("hashlib").sha256(content).hexdigest()
        existing = self._document(139, checksum="f" * 64)
        self.env["usl.document.version"].create({
            "document_id": existing.id,
            "paperless_version_id": "received-139",
            "label": "Received original",
            "checksum": checksum,
            "is_received_original": True,
        })

        with patch.object(PaperlessClient, "upload_multipart") as upload:
            result = (
                self.env["usl.document"]
                .with_user(self.user)
                .upload_from_odoo(
                    "supplier-original.pdf",
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
        self.assertEqual(existing.link_ids.version_id, "received-139")
        upload.assert_not_called()

    def _verified_mapping(self, values):
        return (
            self.env["usl.paperless.user.mapping"]
            .sudo()
            .with_context(usl_documents_mapping_no_sync=True)
            .create(values)
        )

    def _enable_pocket_provider(self):
        provider = self.env.ref("usl_pocketid.provider_pocketid")
        provider._usl_pocketid_environment_write(
            {
                "enabled": True,
                "client_id": "odoo-documents-client",
                "auth_endpoint": "https://identity.example.test/authorize",
                "token_endpoint": "https://identity.example.test/token",
                "jwks_uri": "https://identity.example.test/jwks",
                "usl_oidc_issuer": "https://identity.example.test",
                "usl_public_base_url": "https://odoo.example.test",
                "usl_required_group": "documents-users",
            },
        )
        return provider

    def _pocket_identity(self, user=None, subject="documents-user-subject"):
        user = user or self.user
        provider = self._enable_pocket_provider()
        user.sudo().with_context(
            usl_documents_user_access_no_sync=True,
        ).write(
            {
                "usl_identity_classification": "active",
                "usl_pocketid_access": True,
            },
        )
        return self.env["usl.oidc.identity"].create(
            {
                "provider_id": provider.id,
                "issuer": provider.usl_oidc_issuer,
                "subject": subject,
                "user_id": user.id,
            },
        )

    def test_duplicate_in_trash_requires_restore_instead_of_new_binary(self):
        content = b"trashed supplier evidence"
        checksum = __import__("hashlib").sha256(content).hexdigest()
        self._document(140, checksum=checksum, availability_state="trashed")
        with (
            patch.object(PaperlessClient, "upload_multipart") as upload,
            self.assertRaisesRegex(UserError, "already in Trash"),
        ):
            self.env["usl.document"].with_user(self.user).upload_from_odoo(
                "supplier-in-trash.pdf",
                base64.b64encode(content).decode(),
                "application/pdf",
                res_model="res.partner",
                res_id=self.partner_a.id,
                company_id=self.company_a.id,
            )
        upload.assert_not_called()

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
                    }],
                },
            ),
            patch.object(
                PaperlessClient, "upload_multipart", return_value="task-new",
            ) as upload,
        ):
            result = self.env["usl.document"].upload_from_odoo(
                "new.pdf",
                base64.b64encode(content).decode(),
                "application/pdf",
                confidentiality="accounting",
            )
        self.assertEqual(result["state"], "processing")
        self.assertEqual(
            self.env["usl.document.operation"].browse(
                result["operation_id"],
            ).confidentiality,
            "accounting",
        )
        upload.assert_called_once()

    def test_company_and_accountant_permissions_do_not_leak_metadata(self):
        internal = self._document(103)
        evidence = self._document(
            104, confidentiality="accounting", accounting_evidence=True,
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
        with self.assertRaises(AccessError):
            self.env["usl.document.link"].with_user(self.accountant).search([])
        accountant_detail = evidence.with_user(self.accountant).document_detail(
            evidence.id,
        )
        self.assertEqual(
            [item["id"] for item in accountant_detail["links"]],
            [evidence_link.id],
        )
        with self.assertRaises(AccessError):
            evidence.with_user(self.accountant).link_to_record(
                "res.partner",
                self.partner_a.id,
            )
        with self.assertRaises(AccessError):
            evidence.with_user(self.accountant).unlink_from_record(
                "res.partner",
                self.partner_b.id,
            )
        self.assertNotEqual(internal_link, evidence_link)

    def test_linked_record_metadata_is_hidden_without_target_record_access(self):
        document = self._document(1420)
        employee = self.env["hr.employee"].create(
            {"name": "Restricted Employee", "company_id": self.company_a.id},
        )
        document.link_to_record("hr.employee", employee.id)

        detail = document.with_user(self.user).document_detail(document.id)
        workspace = self.env["usl.document"].with_user(self.user).workspace_data(
            workspace="all",
        )

        self.assertFalse(detail["links"])
        self.assertEqual(detail["link_count"], 0)
        self.assertNotIn(
            f"hr.employee:{employee.id}",
            [item["key"] for item in workspace["link_facets"]],
        )
        self.assertNotIn(
            document,
            self.env["usl.document"].with_user(self.user).search(
                [("has_linked_record", "=", True)],
            ),
        )
        self.assertIn(
            document,
            self.env["usl.document"].with_user(self.user).search(
                [("has_linked_record", "=", False)],
            ),
        )

    def test_cross_company_relationship_is_rejected(self):
        document = self._document(107, company_id=self.company_a.id)
        partner = self.env["res.partner"].create({
            "name": "Restricted target",
            "company_id": self.company_b.id,
        })
        with self.assertRaises(ValidationError):
            document.link_to_record("res.partner", partner.id)

    def test_manager_changes_company_with_active_company_and_permission_refresh(self):
        self.manager.write({"company_ids": [Command.link(self.company_b.id)]})
        document = self._document(1407, company_id=self.company_a.id)
        manager_document = document.with_user(self.manager).with_context(
            allowed_company_ids=[self.company_a.id, self.company_b.id],
        )

        manager_detail = manager_document.document_detail(document.id)
        user_detail = document.with_user(self.user).document_detail(document.id)
        self.assertTrue(manager_detail["can_change_company"])
        self.assertFalse(user_detail["can_change_company"])
        with self.assertRaises(AccessError):
            document.with_user(self.user).set_company(self.company_a.id)
        with self.assertRaisesRegex(AccessError, "company switcher"):
            document.with_user(self.manager).with_context(
                allowed_company_ids=[self.company_a.id],
            ).set_company(self.company_b.id)

        with patch.object(
            UslDocument,
            "action_sync_permissions",
            return_value=True,
        ) as sync_permissions:
            detail = manager_document.set_company(self.company_b.id)

        self.assertEqual(document.company_id, self.company_b)
        self.assertEqual(detail["company_id"], self.company_b.id)
        self.assertEqual(document.permission_sync_state, "pending")
        sync_permissions.assert_called_once()

    def test_review_completion_is_available_in_detail_and_fails_closed(self):
        no_company = self._document(
            1409,
            company_id=False,
            review_state="needs_attention",
        )
        no_company_detail = no_company.with_user(self.manager).document_detail(
            no_company.id,
        )
        self.assertFalse(no_company_detail["can_mark_reviewed"])
        self.assertIn("legal company", no_company_detail["review_blocker"])
        with self.assertRaisesRegex(ValidationError, "legal company"):
            no_company.with_user(self.manager).action_mark_reviewed()

        ready = self._document(1410, review_state="needs_attention")
        self.assertTrue(
            ready.with_user(self.manager).document_detail(ready.id)[
                "can_mark_reviewed"
            ],
        )
        self.assertFalse(
            ready.with_user(self.user).document_detail(ready.id)[
                "can_mark_reviewed"
            ],
        )
        with self.assertRaisesRegex(AccessError, "Documents administrators"):
            ready.with_user(self.user).action_mark_reviewed()

        unsafe_access = self._document(
            1411,
            review_state="needs_attention",
            permission_sync_state="failed",
        )
        unsafe_detail = unsafe_access.with_user(self.manager).document_detail(
            unsafe_access.id,
        )
        self.assertFalse(unsafe_detail["can_mark_reviewed"])
        self.assertIn("archive access", unsafe_detail["review_blocker"])
        with self.assertRaisesRegex(UserError, "archive access"):
            unsafe_access.with_user(self.manager).action_mark_reviewed()

        detail = ready.with_user(self.manager).action_mark_reviewed()
        self.assertEqual(ready.review_state, "reviewed")
        self.assertEqual(detail["review_state"], "reviewed")
        self.assertFalse(detail["can_mark_reviewed"])

    def test_company_change_cannot_conflict_with_an_active_business_link(self):
        self.manager.write({"company_ids": [Command.link(self.company_b.id)]})
        document = self._document(1408, company_id=self.company_a.id)
        document.link_to_record("res.partner", self.partner_a.id)
        manager_document = document.with_user(self.manager).with_context(
            allowed_company_ids=[self.company_a.id, self.company_b.id],
        )

        with self.assertRaisesRegex(
            ValidationError,
            "Remove links to records from another company",
        ):
            manager_document.set_company(self.company_b.id)

        self.assertEqual(document.company_id, self.company_a)

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
            patch.object(UslDocument, "_sync_metadata_catalogs", return_value=None),
            patch.object(UslDocument, "action_sync_permissions", return_value=True),
            patch.object(PaperlessClient, "list_documents", return_value=payload),
            patch.object(PaperlessClient, "list_trashed_documents", return_value=[]),
        ):
            first = (
                self.env["usl.document"]
                .with_user(self.manager)
                .sync_from_paperless()
            )
            document = self.env["usl.document"].search(
                [("paperless_id", "=", 108)],
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
            self.env["usl.document"].search_count([("paperless_id", "=", 108)]), 1,
        )
        self.assertEqual(document.link_count, 1)
        self.assertEqual(document.review_state, "classified")
        self.assertEqual(document.company_id, self.company_a)

    def test_incremental_sync_cron_uses_the_system_service_identity(self):
        with patch.object(
            UslDocument,
            "sync_from_paperless",
            autospec=True,
            return_value={"synchronized": 0},
        ) as sync:
            result = (
                self.env["usl.document"]
                .with_user(self.user)
                .cron_sync_from_paperless()
            )
        self.assertEqual(result, {"synchronized": 0})
        self.assertEqual(
            sync.call_args.args[0].env.uid,
            self.env.ref("base.user_root").id,
        )

    def test_full_sync_confirms_recent_odoo_upload_before_marking_it_missing(self):
        document = self._document(
            178,
            name="Just archived filing",
            checksum="a" * 64,
            source="odoo_attachment",
        )
        direct_payload = {
            "id": 178,
            "title": "Just archived filing",
            "checksum": "a" * 64,
            "modified": "2026-08-04T07:00:00Z",
            "tags": [],
            "versions": [{"id": 1, "checksum": "a" * 64}],
        }
        with (
            patch.object(PaperlessClient, "compatibility", return_value={"ok": True}),
            patch.object(UslDocument, "_sync_metadata_catalogs", return_value=None),
            patch.object(
                PaperlessClient,
                "list_documents",
                return_value={"next": None, "results": []},
            ),
            patch.object(PaperlessClient, "list_trashed_documents", return_value=[]),
            patch.object(
                PaperlessClient,
                "get_document",
                return_value=direct_payload,
            ) as get_document,
            patch.object(UslDocument, "action_sync_permissions", return_value=True),
        ):
            self.env["usl.document"].with_user(self.manager).sync_from_paperless(
                full=True,
            )

        get_document.assert_called_once_with(178)
        self.assertEqual(document.availability_state, "available")
        self.assertFalse(document.last_error)

    def test_full_sync_marks_odoo_upload_missing_after_direct_404(self):
        document = self._document(177, source="odoo_upload")
        with (
            patch.object(PaperlessClient, "compatibility", return_value={"ok": True}),
            patch.object(UslDocument, "_sync_metadata_catalogs", return_value=None),
            patch.object(
                PaperlessClient,
                "list_documents",
                return_value={"next": None, "results": []},
            ),
            patch.object(PaperlessClient, "list_trashed_documents", return_value=[]),
            patch.object(
                PaperlessClient,
                "get_document",
                side_effect=PaperlessNotFound("Gone"),
            ),
        ):
            self.env["usl.document"].with_user(self.manager).sync_from_paperless(
                full=True,
            )

        self.assertEqual(document.availability_state, "missing")

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
            patch.object(UslDocument, "_sync_metadata_catalogs", return_value=None),
            patch.object(PaperlessClient, "list_documents", return_value=payload),
            patch.object(PaperlessClient, "list_trashed_documents", return_value=[]),
            patch.object(
                PaperlessClient, "metadata_catalog", return_value=catalog,
            ) as metadata_catalog,
        ):
            self.env["usl.document"].with_user(self.manager).sync_from_paperless()
        document = self.env["usl.document"].search(
            [("paperless_id", "=", 179)],
        )
        self.assertEqual(document.correspondent_name, "Example Supplier")
        self.assertEqual(document.document_type_name, "Invoice")
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
                179, {"document_type": document_type["id"]},
            )
        self.assertEqual(document_type["id"], 9)
        self.assertEqual(result["document_type"], 9)
        self.assertEqual(
            request.call_args_list[1].args[:2],
            ("POST", "/api/document_types/"),
        )
        self.assertEqual(
            request.call_args_list[2].args[:2], ("PATCH", "/api/documents/179/"),
        )

    def test_catalog_sync_preserves_stable_ids_hierarchy_and_relations(self):
        client = PaperlessClient(self.env)
        client.owner_user_id = 3
        payloads = [
            {
                "id": 301,
                "name": "Finance",
                "owner": 3,
                "color": "#112233",
                "text_color": "#ffffff",
                "matching_algorithm": 6,
                "document_count": 2,
                "parent": None,
            },
            {
                "id": 302,
                "name": "Banking",
                "owner": None,
                "color": "#445566",
                "text_color": "#ffffff",
                "matching_algorithm": 3,
                "match": "bank statement",
                "document_count": 1,
                "parent": 301,
            },
        ]
        migrated_parent = {**payloads[0], "owner": None}
        with (
            patch.object(client, "list_metadata", return_value=payloads),
            patch.object(
                client,
                "update_metadata",
                return_value=migrated_parent,
            ) as update_metadata,
        ):
            self.env["usl.paperless.tag"].synchronize_catalog(client=client)
        update_metadata.assert_called_once_with("tags", 301, {"owner": None})
        parent = self.env["usl.paperless.tag"].search(
            [("paperless_id", "=", 301)],
        )
        child = self.env["usl.paperless.tag"].search(
            [("paperless_id", "=", 302)],
        )
        self.assertEqual(child.parent_id, parent)
        self.assertEqual(child.matching_algorithm, "3")
        self.assertEqual(child.match, "bank statement")

        values = self.env["usl.document"]._paperless_values(
            {
                "id": 303,
                "title": "Bank statement",
                "tags": [302],
                "versions": [],
            },
        )
        document = self.env["usl.document"].create(
            {
                **values,
                "company_id": self.company_a.id,
                "review_state": "classified",
            },
        )
        self.assertEqual(document.tag_ids, child)

    def test_document_users_manage_metadata_but_cannot_delete_it(self):
        response = {
            "id": 304,
            "name": "Contracts",
            "color": "#775599",
            "text_color": "#ffffff",
            "matching_algorithm": 6,
            "document_count": 0,
        }
        with patch.object(
            PaperlessClient, "create_metadata", return_value=response,
        ) as create_metadata:
            tag = self.env["usl.paperless.tag"].with_user(self.user).create(
                {
                    "name": "Contracts",
                    "color": "#775599",
                    "matching_algorithm": "6",
                },
            )
        create_metadata.assert_called_once()
        self.assertIsNone(create_metadata.call_args.args[1]["owner"])
        self.assertEqual(create_metadata.call_args.args[1]["match"], "")
        self.assertEqual(tag.paperless_id, 304)
        with patch.object(
            PaperlessClient,
            "update_metadata",
            return_value={**response, "name": "Contracts & legal"},
        ):
            tag.with_user(self.user).write({"name": "Contracts & legal"})
        self.assertEqual(tag.name, "Contracts & legal")
        with self.assertRaises(AccessError):
            tag.with_user(self.user).unlink()
        with patch.object(PaperlessClient, "delete_metadata", return_value={}):
            tag.with_user(self.manager).unlink()

    def test_metadata_create_retry_adopts_committed_shared_paperless_item(self):
        remote = {
            "id": 9199,
            "name": "Retry-safe tag",
            "owner": None,
            "match": "",
            "matching_algorithm": 0,
        }
        with (
            patch.object(
                PaperlessClient,
                "create_metadata",
                side_effect=PaperlessError("already exists"),
            ),
            patch.object(
                PaperlessClient,
                "list_metadata",
                return_value=[remote],
            ),
        ):
            tag = self.env["usl.paperless.tag"].with_user(self.user).create(
                {"name": "Retry-safe tag"},
            )
        self.assertEqual(tag.paperless_id, 9199)
        self.assertEqual(tag.name, "Retry-safe tag")

    def test_metadata_create_normalizes_empty_match_and_compiles_rule_lines(self):
        correspondent_response = {
            "id": 1304,
            "name": "Test Correspondent",
            "match": "",
            "matching_algorithm": 0,
        }
        with patch.object(
            PaperlessClient,
            "create_metadata",
            return_value=correspondent_response,
        ) as create_metadata:
            self.env["usl.paperless.correspondent"].with_user(self.user).create(
                {"name": "Test Correspondent"},
            )
        payload = create_metadata.call_args.args[1]
        self.assertEqual(payload["match"], "")
        self.assertNotIn(False, payload.values())

        tag_response = {
            "id": 1305,
            "name": "Tax evidence",
            "match": '"tax return" VAT',
            "matching_algorithm": 1,
            "color": "#225588",
        }
        with patch.object(
            PaperlessClient,
            "create_metadata",
            return_value=tag_response,
        ) as create_tag:
            tag = self.env["usl.paperless.tag"].with_user(self.user).create(
                {
                    "name": "Tax evidence",
                    "matching_algorithm": "1",
                    "rule_lines": "tax return\nVAT",
                },
            )
        self.assertEqual(
            create_tag.call_args.args[1]["match"],
            '"tax return" VAT',
        )
        self.assertEqual(tag.rule_lines, "tax return\nVAT")

    def test_metadata_count_and_open_action_only_use_accessible_documents(self):
        tag = self._tag(1306, "Daily shortcut")
        self._document(1307, tag_ids=[Command.set(tag.ids)])
        self._document(
            1308,
            company_id=self.company_b.id,
            tag_ids=[Command.set(tag.ids)],
        )
        restricted_tag = tag.with_user(self.user)
        self.assertEqual(restricted_tag.accessible_document_count, 1)
        action = restricted_tag.action_open_documents()
        self.assertEqual(action["params"]["initial_workspace"], "all")
        self.assertEqual(
            action["context"]["search_default_tag_ids"],
            tag.ids,
        )

    def test_document_metadata_updates_paperless_then_refreshes_cache(self):
        tag = self._tag(305, "Reviewed")
        correspondent = self._correspondent(306, "Example Supplier")
        document_type = self._document_type(307, "Supplier invoice")
        document = self._document(308)
        refreshed = {
            "id": 308,
            "title": "July supplier invoice",
            "created": "2026-07-01",
            "correspondent": 306,
            "document_type": 307,
            "tags": [305],
            "versions": [],
        }
        with (
            patch.object(
                PaperlessClient, "update_document_metadata", return_value=refreshed,
            ) as update,
            patch.object(PaperlessClient, "get_document", return_value=refreshed),
        ):
            document.with_user(self.user).update_archive_metadata(
                {
                    "name": "July supplier invoice",
                    "document_date": "2026-07-01",
                    "correspondent_id": correspondent.id,
                    "document_type_id": document_type.id,
                    "tag_ids": [tag.id],
                },
            )
        update.assert_called_once_with(
            308,
            {
                "title": "July supplier invoice",
                "created": "2026-07-01",
                "correspondent": 306,
                "document_type": 307,
                "tags": [305],
            },
        )
        self.assertEqual(document.name, "July supplier invoice")
        self.assertEqual(document.correspondent_id, correspondent)
        self.assertEqual(document.document_type_id, document_type)
        self.assertEqual(document.tag_ids, tag)

    def test_document_metadata_failure_keeps_authoritative_cached_value(self):
        document = self._document(2308, name="Original title")
        with (
            patch.object(
                PaperlessClient,
                "update_document_metadata",
                side_effect=PaperlessUnavailable("Archive offline"),
            ),
            self.assertRaises(PaperlessUnavailable),
        ):
            document.with_user(self.user).update_archive_metadata(
                {"name": "Title that was not saved"},
            )
        self.assertEqual(document.name, "Original title")

    def test_smart_views_and_advanced_filters_use_stable_metadata_ids(self):
        banking = self._tag(309, "Banking")
        supplier = self._correspondent(310, "Example Bank")
        statement = self._document_type(311, "Statement")
        matching = self._document(
            312,
            name="July statement",
            tag_ids=[Command.set(banking.ids)],
            correspondent_id=supplier.id,
            correspondent_name=supplier.name,
            document_type_id=statement.id,
            document_type_name=statement.name,
            document_date="2026-07-15",
            source="paperless",
        )
        self._document(313, name="Other document")
        view = self.env.ref("usl_documents.smart_view_banking")
        view.with_context(usl_documents_archive_view_sync=True).write(
            {"tag_ids": [Command.set(banking.ids)]},
        )

        result = self.env["usl.document"].workspace_data(
            workspace="banking",
            tag_ids=[banking.id],
            correspondent_id=supplier.id,
            document_type_id=statement.id,
            date_from="2026-07-01",
            date_to="2026-07-31",
            source="paperless",
        )
        self.assertEqual(result["count"], 1)
        self.assertEqual(result["documents"][0]["id"], matching.id)
        banking.with_context(usl_documents_cache_write=True).write(
            {"name": "Bank records"},
        )
        renamed = self.env["usl.document"].workspace_data(workspace="banking")
        self.assertEqual(renamed["count"], 1)
        self.assertEqual(view.tag_ids.paperless_id, 309)

    def test_smart_view_created_from_configuration_is_shared_and_visible(self):
        tag = self._tag(1314, "Executive review")
        view = (
            self.env["usl.document.smart.view"]
            .with_user(self.manager)
            .with_context(default_scope="shared")
            .create(
                {
                    "name": "Executive documents",
                    "system_rule": "metadata",
                    "tag_ids": [Command.set(tag.ids)],
                },
            )
        )

        self.assertEqual(view.scope, "shared")
        self.assertFalse(view.user_id)
        self.assertTrue(view.key.startswith("view_"))
        workspace = (
            self.env["usl.document"]
            .with_user(self.user)
            .workspace_data(workspace=view.key)
        )
        self.assertIn(
            view.key,
            [item["key"] for item in workspace["smart_views"]],
        )
        action = view.with_user(self.manager).action_open_documents()
        self.assertEqual(action["params"]["initial_workspace"], view.key)

        personal = self.env["usl.document.smart.view"].with_user(self.user).create(
            {"name": "Private documents"},
        )
        self.assertEqual(personal.scope, "personal")
        self.assertEqual(personal.user_id, self.user)
        self.assertFalse(personal.key)

    def test_personal_saved_view_is_private_and_replays_filters(self):
        tag = self._tag(314, "Tax")
        matching = self._document(
            315,
            name="Tax package",
            tag_ids=[Command.set(tag.ids)],
        )
        view_values = (
            self.env["usl.document.smart.view"]
            .with_user(self.user)
            .save_personal_view("My tax files", {"tag_ids": [tag.id]})
        )
        result = (
            self.env["usl.document"]
            .with_user(self.user)
            .workspace_data(workspace=view_values["key"])
        )
        self.assertEqual(result["count"], 1)
        self.assertEqual(result["documents"][0]["id"], matching.id)
        personal = self.env["usl.document.smart.view"].browse(view_values["id"])
        self.assertEqual(personal.user_id, self.user)
        self.assertNotIn(
            personal,
            self.env["usl.document.smart.view"].with_user(self.accountant).search([]),
        )

    def test_correspondent_contact_mapping_is_optional_and_does_not_link(self):
        correspondent = self._correspondent(330, "Archive Supplier")
        document = self._document(
            331,
            correspondent_id=correspondent.id,
            correspondent_name=correspondent.name,
        )
        with patch.object(PaperlessClient, "update_metadata") as update_metadata:
            correspondent.with_user(self.user).write(
                {"partner_id": self.partner_a.id},
            )
        update_metadata.assert_not_called()
        self.assertEqual(correspondent.partner_id, self.partner_a)
        self.assertFalse(document.link_ids)
        values = self.env["usl.document"]._workspace_document_values(document)
        self.assertEqual(values["correspondent"], self.partner_a.display_name)
        self.assertEqual(
            values["correspondent_archive_name"], "Archive Supplier",
        )

    def test_workspace_hides_inaccessible_correspondent_contact_mapping(self):
        restricted_user = mail_new_test_user(
            self.env,
            login="documents-restricted-workspace",
            name="Documents Restricted Workspace User",
            company_id=self.company_b.id,
            company_ids=[Command.set(self.company_b.ids)],
            groups="usl_documents.group_documents_user",
        )
        correspondent = self._correspondent(
            337,
            "Archive-only safe name",
            partner_id=self.partner_a.id,
        )
        document = self._document(
            338,
            company_id=self.company_b.id,
            correspondent_id=correspondent.id,
            correspondent_name=correspondent.name,
        )

        result = (
            self.env["usl.document"]
            .with_user(restricted_user)
            .workspace_data(workspace="all")
        )

        self.assertEqual(result["count"], 1)
        self.assertEqual(result["documents"][0]["id"], document.id)
        self.assertEqual(
            result["documents"][0]["correspondent"],
            "Archive-only safe name",
        )
        self.assertFalse(result["documents"][0]["correspondent_partner_id"])
        catalog = next(
            item
            for item in result["correspondents"]
            if item["id"] == correspondent.id
        )
        self.assertEqual(catalog["name"], "Archive-only safe name")
        self.assertFalse(catalog["partner_id"])
        self.assertFalse(
            correspondent.with_user(restricted_user).partner_visible_id,
        )
        self.assertEqual(
            correspondent.with_user(self.user).partner_visible_id,
            self.partner_a,
        )
        with self.assertRaises(AccessError):
            correspondent.with_user(restricted_user).write(
                {"partner_visible_id": False},
            )
        self.assertEqual(correspondent.partner_id, self.partner_a)

    def test_visible_correspondent_contact_mapping_can_be_edited(self):
        correspondent = self._correspondent(339, "Visible Contact Mapping")

        correspondent.with_user(self.user).write(
            {"partner_visible_id": self.partner_a.id},
        )

        self.assertEqual(correspondent.partner_id, self.partner_a)
        self.assertEqual(
            correspondent.with_user(self.user).partner_visible_id,
            self.partner_a,
        )

    def test_create_correspondent_from_contact_reuses_then_creates_safely(self):
        exact = self._correspondent(1339, self.partner_a.display_name)
        first = (
            self.env["usl.paperless.correspondent"]
            .with_user(self.user)
            .create_from_partner(self.partner_a.id)
        )
        second = (
            self.env["usl.paperless.correspondent"]
            .with_user(self.user)
            .create_from_partner(self.partner_a.id)
        )
        self.assertEqual(first["id"], exact.id)
        self.assertEqual(second["id"], exact.id)
        self.assertEqual(exact.partner_id, self.partner_a)

        new_partner = self.env["res.partner"].create(
            {"name": "New archive correspondent"},
        )
        remote = {
            "id": 1340,
            "name": new_partner.display_name,
            "match": "",
            "matching_algorithm": 0,
            "owner": None,
        }
        with patch.object(
            PaperlessClient,
            "create_metadata",
            return_value=remote,
        ) as create_metadata:
            created = (
                self.env["usl.paperless.correspondent"]
                .with_user(self.user)
                .create_from_partner(new_partner.id)
            )
        correspondent = self.env["usl.paperless.correspondent"].browse(
            created["id"],
        )
        self.assertEqual(correspondent.partner_id, new_partner)
        self.assertEqual(correspondent.paperless_id, 1340)
        create_metadata.assert_called_once()

    def test_contact_documents_combine_mapping_and_explicit_links_without_duplicates(self):
        correspondent = self._correspondent(
            334,
            self.partner_a.name,
            partner_id=self.partner_a.id,
        )
        mapped = self._document(
            335,
            correspondent_id=correspondent.id,
            correspondent_name=correspondent.name,
        )
        explicitly_linked = self._document(336)
        explicitly_linked.link_to_record("res.partner", self.partner_a.id)
        self.partner_a.invalidate_recordset(["archived_document_count"])

        self.assertEqual(self.partner_a.archived_document_count, 2)
        action = self.partner_a.action_open_documents_workspace()
        self.assertTrue(action["params"]["linked_filter"])
        self.assertEqual(
            action["params"]["mapped_partner_id"], self.partner_a.id,
        )
        result = self.env["usl.document"].workspace_data(
            workspace="all",
            linked_model="res.partner",
            linked_id=self.partner_a.id,
            mapped_partner_id=self.partner_a.id,
        )
        self.assertEqual(
            {item["id"] for item in result["documents"]},
            {mapped.id, explicitly_linked.id},
        )

    def test_company_smart_button_and_linked_workspace_include_trash(self):
        available = self._document(337, name="Available company evidence")
        trashed = self._document(338, name="Company evidence in Trash")
        available.link_to_record("res.company", self.company_a.id)
        trashed.link_to_record("res.company", self.company_a.id)
        trashed.sudo().with_context(usl_documents_cache_write=True).write(
            {"availability_state": "trashed"},
        )
        self.company_a.invalidate_recordset(["archived_document_count"])

        self.assertEqual(self.company_a.archived_document_count, 2)
        result = self.env["usl.document"].workspace_data(
            workspace="all",
            search_domain=[
                ("linked_record_ref", "=", f"res.company:{self.company_a.id}"),
            ],
            linked_model="res.company",
            linked_id=self.company_a.id,
        )
        self.assertEqual(result["count"], 2)
        self.assertEqual(
            {item["id"] for item in result["documents"]},
            {available.id, trashed.id},
        )
        self.assertEqual(
            next(
                item["availability_state"]
                for item in result["documents"]
                if item["id"] == trashed.id
            ),
            "trashed",
        )

    def test_configurable_shortcut_uses_synced_metadata_without_rewriting_view(self):
        base_tag = self._tag(340, "Contracts")
        optional_tag = self._tag(341, "Board approved")
        view = self.env["usl.document.smart.view"].create(
            {
                "name": "Legal review",
                "scope": "shared",
                "system_rule": "metadata",
                "tag_ids": [Command.set(base_tag.ids)],
            },
        )
        native_filter = self.env["ir.filters"].create(
            {
                "name": "Board-approved documents",
                "model_id": "usl.document",
                "action_id": self.env.ref(
                    "usl_documents.action_documents_workspace",
                ).id,
                "domain": repr([("tag_ids", "in", optional_tag.ids)]),
                "context": "{}",
                "sort": "[]",
                "user_ids": [],
            },
        )
        shortcut = self.env["usl.document.quick.filter"].create(
            {
                "name": "Board-approved documents",
                "ir_filter_id": native_filter.id,
                "smart_view_ids": [Command.set(view.ids)],
            },
        )

        self.assertTrue(shortcut.key.startswith("shortcut_"))
        self.assertEqual(
            shortcut.workspace_values()["domain"],
            [("tag_ids", "in", optional_tag.ids)],
        )
        self.assertIn(shortcut, view.quick_filter_ids)
        self.assertEqual(
            view._paperless_filter_rules(),
            [{"rule_type": 6, "value": str(base_tag.paperless_id)}],
        )

    def test_native_shortcut_capture_preserves_domain_grouping_order_and_permissions(self):
        view = self.env.ref("usl_documents.smart_view_accounting")
        values = {
            "domain": repr([("review_state", "=", "needs_attention")]),
            "context": {"group_by": ["company_id", "document_date:month"]},
            "sort": ["name", "document_date desc"],
        }
        shortcut_values = self.env["usl.document.quick.filter"].save_from_search(
            "Evidence to review",
            values,
            icon="fa-check-square-o",
            sequence=17,
            smart_view_ids=view.ids,
        )
        shortcut = self.env["usl.document.quick.filter"].browse(
            shortcut_values["id"],
        )
        self.assertEqual(shortcut.ir_filter_id.model_id, "usl.document")
        self.assertEqual(
            shortcut.ir_filter_id.action_id.id,
            self.env.ref("usl_documents.action_documents_workspace").id,
        )
        self.assertFalse(shortcut.ir_filter_id.user_ids)
        self.assertEqual(
            shortcut_values["domain"],
            [("review_state", "=", "needs_attention")],
        )
        self.assertEqual(
            shortcut_values["group_by"],
            ["company_id", "document_date:month"],
        )
        self.assertEqual(
            shortcut_values["order_by"],
            [
                {"name": "name", "asc": True},
                {"name": "document_date", "asc": False},
            ],
        )
        self.assertIn(shortcut, view.quick_filter_ids)
        with self.assertRaises(AccessError):
            self.env["usl.document.quick.filter"].with_user(
                self.user,
            ).save_from_search("Unsafe shared control", values)

    def test_shortcut_definition_is_created_and_edited_from_one_form(self):
        view = self.env.ref("usl_documents.smart_view_accounting")
        shortcut = self.env["usl.document.quick.filter"].create(
            {
                "name": "Current company review",
                "filter_domain": "[('submitted_by_id', '=', uid)]",
                "group_by_1": "company_id",
                "group_by_2": "document_date:month",
                "sort_by_1": "document_date",
                "sort_direction_1": "desc",
                "sort_by_2": "name",
                "sort_direction_2": "asc",
                "smart_view_ids": [Command.set(view.ids)],
            },
        )

        self.assertTrue(shortcut.ir_filter_id)
        self.assertTrue(shortcut.key.startswith("shortcut_"))
        self.assertEqual(shortcut.ir_filter_id.name, shortcut.name)
        self.assertEqual(
            shortcut.ir_filter_id.domain,
            "[('submitted_by_id', '=', uid)]",
        )
        self.assertEqual(
            shortcut._filter_group_by(),
            ["company_id", "document_date:month"],
        )
        self.assertEqual(
            shortcut._filter_order_by(),
            [
                {"name": "document_date", "asc": False},
                {"name": "name", "asc": True},
            ],
        )

        shortcut.write(
            {
                "name": "Unlinked by correspondent",
                "filter_domain": "[('has_linked_record', '=', False)]",
                "group_by_1": "correspondent_id",
                "group_by_2": False,
                "sort_by_1": "correspondent_id",
                "sort_direction_1": "asc",
                "sort_by_2": False,
            },
        )

        self.assertEqual(shortcut.ir_filter_id.name, shortcut.name)
        self.assertEqual(
            shortcut._filter_domain(),
            [("has_linked_record", "=", False)],
        )
        self.assertEqual(shortcut._filter_group_by(), ["correspondent_id"])
        self.assertEqual(
            shortcut._filter_order_by(),
            [{"name": "correspondent_id", "asc": True}],
        )
        with self.assertRaises(AccessError):
            shortcut.with_user(self.user).write({"group_by_1": "company_id"})

    def test_archive_native_saved_view_uses_stable_paperless_identity(self):
        tag = self._tag(332, "Contracts")
        view = self.env.ref("usl_documents.smart_view_contracts")
        view.with_context(usl_documents_archive_view_sync=True).write(
            {
                "archive_native": True,
                "paperless_id": 52,
                "tag_ids": [Command.set(tag.ids)],
            },
        )
        self.env["usl.document.smart.view"].search(
            [
                ("archive_native", "=", True),
                ("id", "!=", view.id),
            ],
        ).with_context(usl_documents_archive_view_sync=True).write(
            {"archive_native": False},
        )
        remote = {
            "id": 52,
            "name": "Signed agreements",
            "owner": 3,
            "filter_rules": [{"rule_type": 6, "value": "332"}],
        }
        client = PaperlessClient(self.env)
        client.owner_user_id = 3
        shared_remote = {**remote, "owner": None}
        with (
            patch.object(client, "list_saved_views", return_value=[remote]),
            patch.object(
                client,
                "update_saved_view",
                return_value=shared_remote,
            ) as update_saved_view,
        ):
            count = self.env["usl.document.smart.view"].synchronize_archive_views(
                client=client,
            )
        update_saved_view.assert_called_once_with(52, {"owner": None})
        self.assertEqual(count, 1)
        self.assertEqual(view.paperless_id, 52)
        self.assertEqual(view.name, "Signed agreements")
        self.assertEqual(view.tag_ids, tag)
        self.assertEqual(view.paperless_sync_state, "synchronized")

    def test_private_paperless_saved_view_is_not_imported_as_shared(self):
        client = PaperlessClient(self.env)
        client.owner_user_id = 3
        self.env["usl.document.smart.view"].search(
            [("archive_native", "=", True)],
        ).sudo().with_context(usl_documents_archive_view_sync=True).write(
            {"archive_native": False},
        )
        remote = {
            "id": 98,
            "name": "Archive administrator private view",
            "owner": 42,
            "filter_rules": [],
        }
        local = self.env["usl.document.smart.view"]
        with (
            patch.object(client, "list_saved_views", return_value=[remote]),
            patch.object(client, "create_saved_view") as create_saved_view,
        ):
            local.synchronize_archive_views(client=client)
        self.assertFalse(local.search([("paperless_id", "=", 98)]))
        create_saved_view.assert_not_called()

    def test_only_manager_can_reconcile_shared_paperless_saved_views(self):
        with self.assertRaises(AccessError):
            self.env["usl.document.smart.view"].with_user(
                self.user,
            ).synchronize_archive_views(client=PaperlessClient(self.env))

    def test_trash_reconciliation_and_restore_preserve_identity_and_links(self):
        document = self._document(333, name="Signed contract")
        document.link_to_record("res.partner", self.partner_a.id)
        trash_payload = {
            "id": 333,
            "title": "Signed contract",
            "created": "2026-07-01",
            "modified": "2026-07-29T10:00:00Z",
            "tags": [],
            "versions": [{"id": 1, "version_label": "Received original"}],
        }
        empty_page = {"next": None, "results": []}
        with (
            patch.object(PaperlessClient, "compatibility", return_value={"ok": True}),
            patch.object(UslDocument, "_sync_metadata_catalogs", return_value=None),
            patch.object(PaperlessClient, "list_documents", return_value=empty_page),
            patch.object(
                PaperlessClient,
                "list_trashed_documents",
                return_value=[trash_payload],
            ),
        ):
            result = (
                self.env["usl.document"]
                .with_user(self.manager)
                .sync_from_paperless(full=True)
            )
        self.assertEqual(result["trashed"], 1)
        self.assertEqual(document.availability_state, "trashed")
        self.assertEqual(document.permission_sync_state, "pending")
        self.assertEqual(
            self.env["usl.document"].workspace_data(workspace="all")["count"],
            0,
        )
        linked = self.env["usl.document"].workspace_data(
            workspace="all",
            linked_model="res.partner",
            linked_id=self.partner_a.id,
        )
        self.assertEqual(linked["documents"][0]["availability_state"], "trashed")
        detail = self.env["usl.document"].with_user(self.manager).document_detail(
            document.id,
        )
        self.assertFalse(detail["can_edit"])
        self.assertTrue(detail["can_restore"])
        with self.assertRaises(UserError):
            document.link_to_record("res.partner", self.partner_b.id)

        restored_payload = {**trash_payload, "modified": "2026-07-29T11:00:00Z"}
        with (
            patch.object(
                PaperlessClient,
                "restore_trashed_documents",
                return_value={"result": "OK", "doc_ids": [333]},
            ),
            patch.object(
                PaperlessClient, "get_document", return_value=restored_payload,
            ),
            patch.object(UslDocument, "action_sync_permissions", return_value=True),
        ):
            restored = document.restore_from_trash()
        self.assertEqual(restored["state"], "restored")
        self.assertIn("Signed contract", restored["message"])
        self.assertEqual(document.availability_state, "available")
        self.assertEqual(document.paperless_id, 333)
        self.assertEqual(document.link_count, 1)

    def test_direct_restore_clears_old_trash_attribution_before_retrash(self):
        document = self._document(343, name="Archive policy")
        document.with_context(usl_documents_cache_write=True).write(
            {
                "availability_state": "trashed",
                "trashed_at": "2026-07-29 09:00:00",
                "trashed_by_id": self.manager.id,
                "trashed_by_label": self.manager.display_name,
                "retention_until": "2026-08-28 09:00:00",
                "deletion_approved_by_id": self.manager.id,
                "deletion_approved_at": "2026-07-29 09:05:00",
                "deletion_reason": "Superseded",
            },
        )
        active_payload = {
            "id": 343,
            "title": "Archive policy",
            "created": "2026-07-01",
            "modified": "2026-07-29T10:00:00Z",
            "tags": [],
            "versions": [],
        }
        active_page = {"next": None, "results": [active_payload]}
        with (
            patch.object(PaperlessClient, "compatibility", return_value={"ok": True}),
            patch.object(UslDocument, "_sync_metadata_catalogs", return_value=None),
            patch.object(PaperlessClient, "list_documents", return_value=active_page),
            patch.object(PaperlessClient, "list_trashed_documents", return_value=[]),
            patch.object(UslDocument, "action_sync_permissions", return_value=True),
        ):
            self.env["usl.document"].with_user(self.manager).sync_from_paperless(
                full=True,
            )
        self.assertEqual(document.availability_state, "available")
        self.assertFalse(document.trashed_at)
        self.assertFalse(document.trashed_by_id)
        self.assertFalse(document.trashed_by_label)
        self.assertFalse(document.retention_until)
        self.assertFalse(document.deletion_approved_by_id)
        self.assertFalse(document.deletion_approved_at)
        self.assertFalse(document.deletion_reason)

        retrash_payload = {
            **active_payload,
            "deleted_at": "2026-07-29T11:00:00Z",
        }
        with (
            patch.object(PaperlessClient, "compatibility", return_value={"ok": True}),
            patch.object(UslDocument, "_sync_metadata_catalogs", return_value=None),
            patch.object(
                PaperlessClient,
                "list_documents",
                return_value={"next": None, "results": []},
            ),
            patch.object(
                PaperlessClient,
                "list_trashed_documents",
                return_value=[retrash_payload],
            ),
            patch.object(UslDocument, "action_sync_permissions", return_value=True),
        ):
            self.env["usl.document"].with_user(self.manager).sync_from_paperless(
                full=True,
            )
        self.assertEqual(document.availability_state, "trashed")
        self.assertEqual(document.permission_sync_state, "pending")
        self.assertFalse(document.trashed_by_id)
        self.assertEqual(
            document.trashed_by_label,
            "Moved in Paperless (user not provided by its API)",
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
                },
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
                },
            ],
        }
        with (
            patch.object(PaperlessClient, "compatibility", return_value={"ok": True}),
            patch.object(UslDocument, "_sync_metadata_catalogs", return_value=None),
            patch.object(
                PaperlessClient,
                "list_documents",
                side_effect=[page_one, page_two],
            ) as list_documents,
            patch.object(PaperlessClient, "list_trashed_documents", return_value=[]),
        ):
            previous_sync = (
                self.env["ir.config_parameter"]
                .sudo()
                .get_str("usl_documents.last_sync")
            )
            first = self.env["usl.document"].with_user(self.manager).sync_from_paperless(
                limit_pages=1,
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
                self.manager,
            ).sync_from_paperless(limit_pages=1)
        self.assertTrue(second["complete"])
        self.assertEqual(
            self.env["usl.document"].search_count(
                [("paperless_id", "in", [180, 181])],
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
                query="OCR-only phrase", workspace="all", page=1, page_size=1,
            )
        self.assertEqual(result["count"], 2)
        self.assertEqual(len(result["documents"]), 1)
        self.assertEqual(search.call_count, 2)

    def test_native_search_domain_combines_ocr_and_structured_filters(self):
        visible = self._document(1182, name="Visible OCR match")
        self._document(1183, company_id=self.company_b.id)
        with patch.object(
            PaperlessClient,
            "search",
            return_value={
                "count": 2,
                "next": None,
                "results": [{"id": 1182}, {"id": 1183}],
            },
        ) as search:
            result = self.env["usl.document"].with_user(self.user).workspace_data(
                workspace="all",
                search_domain=[
                    ["archive_text", "ilike", "embedded cobalt phrase"],
                    ["company_id", "=", self.company_a.id],
                ],
            )
        self.assertEqual([item["id"] for item in result["documents"]], [visible.id])
        search.assert_called_once()
        self.assertEqual(search.call_args.args[0], "embedded cobalt phrase")

    def test_search_everywhere_uses_paperless_relevance_and_odoo_authorization(self):
        first = self._document(2182, name="First authorized result")
        second = self._document(2183, name="Second authorized result")
        self._document(2184, company_id=self.company_b.id)
        first.link_to_record("res.partner", self.partner_a.id)
        with patch.object(
            PaperlessClient,
            "search",
            return_value={
                "count": 3,
                "next": None,
                "results": [{"id": 2183}, {"id": 2184}, {"id": 2182}],
            },
        ) as search:
            result = self.env["usl.document"].with_user(self.user).workspace_data(
                workspace="all",
                search_domain=[
                    ["all_text", "ilike", "quarterly archive phrase"],
                ],
            )
        self.assertEqual(
            [item["id"] for item in result["documents"]],
            [second.id, first.id],
        )
        search.assert_called_once()
        self.assertEqual(search.call_args.args[0], "quarterly archive phrase")
        self.assertTrue(search.call_args.kwargs["full_text"])

        with patch.object(
            PaperlessClient,
            "search",
            return_value={"count": 0, "next": None, "results": []},
        ):
            linked_label = (
                self.env["usl.document"]
                .with_user(self.user)
                .workspace_data(
                    workspace="all",
                    search_domain=[
                        ["all_text", "ilike", self.partner_a.display_name],
                    ],
                )
            )
        self.assertEqual(
            [item["id"] for item in linked_label["documents"]],
            [first.id],
        )

    def test_workspace_validates_and_applies_every_native_list_order(self):
        tag_a = self._tag(2190, "Alpha")
        tag_z = self._tag(2191, "Zulu")
        correspondent_a = self._correspondent(2192, "Alpha sender")
        correspondent_z = self._correspondent(2193, "Zulu sender")
        type_a = self._document_type(2194, "Alpha type")
        type_z = self._document_type(2195, "Zulu type")
        documents = self.env["usl.document"]
        documents |= self._document(
            2196,
            name="Zulu document",
            document_date="2026-07-02",
            correspondent_id=correspondent_z.id,
            document_type_id=type_z.id,
            tag_ids=[Command.set(tag_z.ids)],
            review_state="reviewed",
        )
        documents |= self._document(
            2197,
            name="Alpha document",
            document_date="2026-07-01",
            correspondent_id=correspondent_a.id,
            document_type_id=type_a.id,
            tag_ids=[Command.set(tag_a.ids)],
            review_state="needs_attention",
        )
        order_fields = [
            "name",
            "document_date",
            "correspondent_id",
            "document_type_id",
            "company_id",
            "tag_sort_key",
            "status_sort_key",
        ]
        for field_name in order_fields:
            result = self.env["usl.document"].workspace_data(
                workspace="all",
                search_domain=[["id", "in", documents.ids]],
                order_by=[{"name": field_name, "asc": True}],
                page_size=1,
            )
            expected = self.env["usl.document"].search(
                [("id", "in", documents.ids)],
                order=f"{field_name} asc, id asc",
                limit=1,
            )
            self.assertEqual(
                [item["id"] for item in result["documents"]],
                expected.ids,
                field_name,
            )
            self.assertEqual(result["count"], 2)
        with self.assertRaises(ValidationError):
            self.env["usl.document"].workspace_data(
                workspace="all",
                order_by=[{"name": "checksum", "asc": True}],
            )

    def test_native_search_bar_searches_paperless_custom_field_values(self):
        visible = self._document(1184, name="Invoice reference match")
        self._document(1185, company_id=self.company_b.id)
        self.env["ir.config_parameter"].sudo().set_str(
            "usl_documents.paperless_custom_fields",
            '[{"id": 7, "name": "Invoice reference", "data_type": "string"}]',
        )
        with patch.object(
            PaperlessClient,
            "search",
            return_value={
                "count": 2,
                "next": None,
                "results": [{"id": 1184}, {"id": 1185}],
            },
        ) as search:
            result = self.env["usl.document"].with_user(self.user).workspace_data(
                workspace="all",
                search_domain=[
                    ["custom_field_text", "ilike", "INV-QA-2026-0042"],
                    ["company_id", "=", self.company_a.id],
                ],
            )
        self.assertEqual([item["id"] for item in result["documents"]], [visible.id])
        search.assert_called_once()
        self.assertEqual(search.call_args.args[0], "")
        self.assertEqual(
            search.call_args.kwargs["filters"]["custom_field_query"],
            '["Invoice reference", "icontains", "INV-QA-2026-0042"]',
        )

    def test_archive_id_and_custom_field_filters_keep_odoo_authorization(self):
        visible = self._document(420)
        self._document(421, company_id=self.company_b.id)
        self.env["ir.config_parameter"].sudo().set_str(
            "usl_documents.paperless_custom_fields",
            '[{"id": 7, "name": "Invoice reference", "data_type": "string"}]',
        )
        with patch.object(
            PaperlessClient,
            "search",
            return_value={
                "count": 2,
                "next": None,
                "results": [{"id": 420}, {"id": 421}],
            },
        ) as search:
            result = self.env["usl.document"].with_user(self.user).workspace_data(
                workspace="all",
                custom_field_id=7,
                custom_field_value="INV-QA",
            )
        self.assertEqual([item["id"] for item in result["documents"]], [visible.id])
        self.assertEqual(
            search.call_args.kwargs["filters"]["custom_field_query"],
            '["Invoice reference", "icontains", "INV-QA"]',
        )
        by_id = self.env["usl.document"].with_user(self.user).workspace_data(
            workspace="all",
            paperless_id=420,
        )
        self.assertEqual(by_id["count"], 1)

    def test_async_success_creates_relationship_only_after_confirmation(self):
        operation = self.env["usl.document.operation"].sudo().create({
            "name": "pending.pdf",
            "state": "processing",
            "checksum": "c" * 64,
            "mime_type": "application/pdf",
            "company_id": self.company_a.id,
            "confidentiality": "accounting",
            "paperless_task_id": "task-1",
            "res_model": "res.partner",
            "res_id": self.partner_a.id,
            "source": "odoo_upload",
            "user_id": self.user.id,
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
        self.assertEqual(
            status[operation.id]["document_name"],
            "Confirmed archive",
        )
        self.assertTrue(operation.document_id)
        self.assertEqual(operation.document_id.confidentiality, "accounting")
        self.assertEqual(operation.document_id.link_count, 1)
        self.assertEqual(operation.document_id.checksum, "c" * 64)

    def test_workspace_restores_active_and_failed_operations_by_role(self):
        active = self.env["usl.document.operation"].sudo().create(
            {
                "name": "processing.pdf",
                "state": "processing",
                "checksum": "4" * 64,
                "company_id": self.company_a.id,
                "user_id": self.user.id,
            },
        )
        failed = self.env["usl.document.operation"].sudo().create(
            {
                "name": "corrupted.pdf",
                "state": "failed",
                "checksum": "5" * 64,
                "company_id": self.company_a.id,
                "error_message": "Corrupted file",
                "user_id": self.user.id,
            },
        )
        workspace = self.env["usl.document"].with_user(self.user).workspace_data(
            workspace="attention",
        )
        self.assertTrue(workspace["can_upload"])
        self.assertEqual(workspace["active_operation"]["id"], active.id)
        self.assertEqual(workspace["failed_operations"][0]["id"], failed.id)
        failed.with_user(self.user).acknowledge()
        self.assertFalse(
            self.env["usl.document.operation"]
            .with_user(self.user)
            .workspace_failures(),
        )
        accountant_workspace = self.env["usl.document"].with_user(
            self.accountant,
        ).workspace_data(workspace="attention")
        self.assertFalse(accountant_workspace["can_upload"])
        self.assertFalse(accountant_workspace["failed_operations"])

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
            ],
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
            ],
        )
        self.assertEqual(len(document.version_ids), 2)
        self.assertEqual(
            document.version_ids.filtered("is_current").label, "Final replacement",
        )

    def test_restore_old_file_queues_new_current_version_without_losing_history(self):
        document = self._document(316)
        document._synchronize_versions(
            [
                {
                    "id": 91,
                    "version_label": "Current replacement",
                    "checksum": "9" * 64,
                    "mime_type": "application/pdf",
                },
                {
                    "id": 90,
                    "is_root": True,
                    "checksum": "8" * 64,
                    "mime_type": "application/pdf",
                    "original_file_name": "received.pdf",
                },
            ],
        )
        with (
            patch.object(
                PaperlessClient,
                "download",
                return_value=(b"received file", {"Content-Type": "application/pdf"}),
            ) as download,
            patch.object(
                PaperlessClient, "update_version", return_value="restore-task",
            ) as update,
        ):
            result = document.with_user(self.user).restore_version("90")
        self.assertEqual(result["state"], "processing")
        self.assertEqual(result["task_id"], "restore-task")
        self.assertIn(document.name, result["message"])
        self.assertEqual(len(document.version_ids), 2)
        download.assert_called_once_with(316, version_id="90", original=True)
        self.assertEqual(update.call_args.args[:4], (316, b"received file", "received.pdf", "application/pdf"))
        self.assertIn("Restored from", update.call_args.kwargs["version_label"])
        operation = self.env["usl.document.operation"].browse(
            result["operation_id"],
        )
        self.assertEqual(operation.target_document_id, document)

    def test_replacement_version_preserves_root_policy_and_relationships(self):
        document = self._document(
            187, confidentiality="accounting", accounting_evidence=True,
        )
        document.link_to_record("res.partner", self.partner_a.id)
        operation = self.env["usl.document.operation"].sudo().create(
            {
                "name": "replacement.pdf",
                "state": "processing",
                "checksum": "9" * 64,
                "mime_type": "application/pdf",
                "company_id": self.company_a.id,
                "paperless_task_id": "task-version",
                "source": "odoo_upload",
                "target_document_id": document.id,
            },
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
            ("user_id", "in", [self.user.id, self.manager.id]),
        ]).unlink()
        self._verified_mapping([
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
            PaperlessClient, "set_document_permissions", return_value={},
        ) as permission_call:
            document.with_user(self.manager).action_sync_permissions()
        self.assertEqual(document.permission_sync_state, "synchronized")
        self.assertTrue(document.permission_checked_at)
        permission_call.assert_called_once_with(
            110, view_users=[21, 22], change_users=[22],
        )

    def test_identity_mapping_cannot_bypass_verification_state(self):
        mapping = self.env["usl.paperless.user.mapping"].create(
            {
                "user_id": self.user.id,
                "paperless_user_id": 29,
                "paperless_username": "documents-user",
                "sync_state": "synchronized",
            },
        )
        self.assertEqual(mapping.sync_state, "pending")
        with self.assertRaises(AccessError):
            mapping.write({"sync_state": "synchronized"})
        with (
            patch.object(
                PaperlessClient,
                "get_user",
                return_value={"id": 29, "username": "documents-user"},
            ),
            patch.object(PaperlessClient, "set_document_permissions"),
        ):
            mapping.with_user(self.manager).action_mark_verified()
        self.assertEqual(mapping.sync_state, "synchronized")
        self.assertTrue(mapping.last_verified_at)

    def test_pocket_profile_assigns_document_roles_without_copying_idp_groups(self):
        definitions = self.env["res.users"]._usl_pocketid_profile_definitions()

        self.assertIn(
            "usl_documents.group_documents_manager",
            definitions["administrator"]["groups"],
        )
        self.assertIn(
            "usl_documents.group_documents_user",
            definitions["collaborator"]["groups"],
        )
        self.assertNotIn(
            "documents-users",
            definitions["collaborator"]["groups"],
        )

    def test_enabled_pocket_requires_durable_identity_before_verification(self):
        self._enable_pocket_provider()
        mapping = self.env["usl.paperless.user.mapping"].create(
            {
                "user_id": self.user.id,
                "paperless_user_id": 129,
                "paperless_username": "documents-user",
            },
        )

        action = mapping.with_user(self.manager).action_mark_verified()

        self.assertEqual(mapping.sync_state, "failed")
        self.assertIn("Pocket ID identity", mapping.last_error)
        self.assertEqual(action["params"]["type"], "danger")

    def test_verified_paperless_mapping_uses_same_pocket_identity(self):
        identity = self._pocket_identity()
        mapping = self.env["usl.paperless.user.mapping"].create(
            {
                "user_id": self.user.id,
                "paperless_user_id": 130,
                "paperless_username": "documents-user",
            },
        )

        with (
            patch.object(
                PaperlessClient,
                "get_user",
                return_value={"id": 130, "username": "documents-user"},
            ),
            patch.object(PaperlessClient, "set_document_permissions"),
        ):
            mapping.with_user(self.manager).action_mark_verified()

        self.assertEqual(mapping.oidc_identity_id, identity)
        self.assertEqual(
            mapping.oidc_subject_fingerprint,
            identity.subject_fingerprint,
        )
        self.assertEqual(mapping.sync_state, "synchronized")

    def test_disabling_pocket_identity_revokes_paperless_mapping(self):
        document = self._document(1415)
        identity = self._pocket_identity()
        mapping = self._verified_mapping(
            {
                "user_id": self.user.id,
                "paperless_user_id": 131,
                "paperless_username": "documents-user",
                "oidc_identity_id": identity.id,
                "sync_state": "synchronized",
            },
        )

        with patch.object(
            PaperlessClient,
            "set_document_permissions",
            return_value={},
        ) as permission_call:
            identity.write({"active": False})

        self.assertEqual(mapping.sync_state, "failed")
        permission_call.assert_called_with(
            document.paperless_id,
            view_users=[],
            change_users=[],
        )

    def test_identity_verification_failure_remains_visible(self):
        mapping = self.env["usl.paperless.user.mapping"].create(
            {
                "user_id": self.user.id,
                "paperless_user_id": 29,
                "paperless_username": "documents-user",
            },
        )
        with patch.object(
            PaperlessClient,
            "get_user",
            side_effect=PaperlessError("Paperless rejected the identity"),
        ):
            action = mapping.with_user(self.manager).action_mark_verified()
        self.assertEqual(mapping.sync_state, "failed")
        self.assertEqual(mapping.last_error, "Paperless rejected the identity")
        self.assertEqual(action["tag"], "display_notification")
        self.assertEqual(action["params"]["type"], "danger")

    def test_identity_change_returns_mapping_to_pending_and_revokes_access(self):
        document = self._document(415)
        mapping = self._verified_mapping(
            {
                "user_id": self.user.id,
                "paperless_user_id": 35,
                "paperless_username": "documents-user",
                "sync_state": "synchronized",
            },
        )
        with patch.object(
            PaperlessClient,
            "set_document_permissions",
            return_value={},
        ) as permission_call:
            mapping.with_user(self.manager).write(
                {"paperless_username": "renamed-user"},
            )
        self.assertEqual(mapping.sync_state, "pending")
        self.assertFalse(mapping.last_verified_at)
        permission_call.assert_called_with(
            document.paperless_id,
            view_users=[],
            change_users=[],
        )

    def test_company_access_loss_revokes_paperless_object_permission(self):
        document = self._document(410)
        self.env.ref("base.user_admin").write(
            {
                "group_ids": [
                    Command.unlink(
                        self.env.ref("usl_documents.group_documents_manager").id,
                    ),
                ],
            },
        )
        mapping = self._verified_mapping(
            {
                "user_id": self.user.id,
                "paperless_user_id": 31,
                "paperless_username": "documents-user",
                "sync_state": "synchronized",
            },
        )
        self.assertTrue(mapping)
        with patch.object(
            PaperlessClient,
            "set_document_permissions",
            return_value={},
        ) as permission_call:
            self.user.write(
                {
                    "company_id": self.company_b.id,
                    "company_ids": [Command.set(self.company_b.ids)],
                },
            )
        permission_call.assert_called_with(
            document.paperless_id,
            view_users=[],
            change_users=[],
        )

    def test_synchronized_identity_without_documents_role_has_empty_access(self):
        user_without_access = mail_new_test_user(
            self.env,
            login="documents-no-role",
            name="Documents identity without role",
            company_id=self.company_a.id,
            company_ids=[Command.set(self.company_a.ids)],
            groups="base.group_user",
        )
        mapping = self._verified_mapping(
            {
                "user_id": user_without_access.id,
                "paperless_user_id": 41,
                "paperless_username": "documents-no-role",
                "sync_state": "synchronized",
            },
        )
        self.assertEqual(
            user_without_access._documents_visible_for_permission_sync(),
            {user_without_access.id: set()},
        )
        self.assertFalse(mapping._mapped_user_documents())
        user_without_access.write(
            {
                "group_ids": [
                    Command.link(self.env.ref("base.group_user").id),
                ],
            },
        )

    def test_noop_group_write_does_not_enqueue_permission_refresh(self):
        self._document(990413)
        self._verified_mapping(
            {
                "user_id": self.user.id,
                "paperless_user_id": 33,
                "paperless_username": "documents-user",
                "sync_state": "synchronized",
            },
        )
        with patch.object(
            PaperlessClient,
            "set_document_permissions",
            return_value={},
        ) as permission_call:
            self.user.write(
                {"group_ids": [Command.link(self.env.ref("base.group_user").id)]},
            )
        permission_call.assert_not_called()

    def test_manager_role_loss_revokes_paperless_change_permission(self):
        document = self._document(414)
        manager_group = self.env.ref("usl_documents.group_documents_manager")
        self.user.sudo().with_context(usl_documents_user_access_no_sync=True).write(
            {"group_ids": [Command.link(manager_group.id)]},
        )
        self._verified_mapping(
            {
                "user_id": self.user.id,
                "paperless_user_id": 34,
                "paperless_username": "documents-user",
                "sync_state": "synchronized",
            },
        )
        with patch.object(
            PaperlessClient,
            "set_document_permissions",
            return_value={},
        ) as permission_call:
            self.user.write(
                {"group_ids": [Command.unlink(manager_group.id)]},
            )
        permission_call.assert_called_with(
            document.paperless_id,
            view_users=[34],
            change_users=[],
        )

    def test_manager_role_loss_rolls_back_when_change_permission_revoke_fails(self):
        self._document(1414)
        manager_group = self.env.ref("usl_documents.group_documents_manager")
        self.user.sudo().with_context(usl_documents_user_access_no_sync=True).write(
            {"group_ids": [Command.link(manager_group.id)]},
        )
        self._verified_mapping(
            {
                "user_id": self.user.id,
                "paperless_user_id": 44,
                "paperless_username": "documents-user",
                "sync_state": "synchronized",
            },
        )
        with (
            patch.object(
                PaperlessClient,
                "set_document_permissions",
                side_effect=PaperlessUnavailable("offline"),
            ),
            self.assertRaisesRegex(UserError, "Access was not changed"),
        ):
            self.user.write({"group_ids": [Command.unlink(manager_group.id)]})

    def test_access_reduction_rolls_back_when_permission_revoke_fails(self):
        self._document(411)
        self._verified_mapping(
            {
                "user_id": self.user.id,
                "paperless_user_id": 32,
                "paperless_username": "documents-user",
                "sync_state": "synchronized",
            },
        )
        with (
            patch.object(
                PaperlessClient,
                "set_document_permissions",
                side_effect=PaperlessUnavailable("offline"),
            ),
            self.assertRaisesRegex(UserError, "Access was not changed"),
        ):
            self.user.write(
                {
                    "company_id": self.company_b.id,
                    "company_ids": [Command.set(self.company_b.ids)],
                },
            )

    def test_retention_gates_permanent_trash_deletion_and_keeps_tombstone(self):
        document = self._document(
            412,
            availability_state="trashed",
        )
        document.with_user(self.manager).write(
            {
                "retention_hold": True,
                "deletion_reason": "Synthetic expired record",
            },
        )
        document.with_user(self.manager).action_approve_permanent_deletion()
        with self.assertRaisesRegex(UserError, "retention hold"):
            document.with_user(self.manager).permanently_delete_from_trash()
        document.with_user(self.manager).write({"retention_hold": False})
        with patch.object(
            PaperlessClient,
            "permanently_delete_trashed_documents",
            return_value={"result": "OK"},
        ) as deletion:
            document.with_user(self.manager).permanently_delete_from_trash()
        deletion.assert_called_once_with([412])
        self.assertEqual(document.availability_state, "permanently_deleted")
        self.assertTrue(document.permanently_deleted_at)
        with patch.object(
            PaperlessClient,
            "set_document_permissions",
            side_effect=PaperlessUnavailable("already removed"),
        ):
            document.with_user(self.manager).action_sync_permissions()
        self.assertEqual(document.availability_state, "permanently_deleted")
        self.assertEqual(document.permission_sync_state, "pending")
        self.assertNotIn(
            document.id,
            [
                item["id"]
                for item in self.env["usl.document"].workspace_data(
                    workspace="all",
                )["documents"]
            ],
        )

    def test_trashed_document_defers_permission_sync_until_restore(self):
        document = self._document(
            1411,
            name="Archived contract in Trash",
            availability_state="trashed",
        )
        with patch.object(
            PaperlessClient,
            "set_document_permissions",
        ) as permission_call:
            document.with_user(self.manager).action_sync_permissions()
        permission_call.assert_not_called()
        self.assertEqual(document.permission_sync_state, "pending")
        self.assertFalse(document.permission_sync_error)
        self.assertFalse(document.permission_checked_at)

    def test_archive_binary_access_requires_synchronized_live_permissions(self):
        synchronized = self._document(1416)
        pending = self._document(1417, permission_sync_state="pending")
        failed = self._document(
            1418,
            availability_state="permission_error",
            permission_sync_state="failed",
        )
        trashed = self._document(
            1419,
            availability_state="trashed",
            permission_sync_state="pending",
        )
        inaccessible = self._document(1420, company_id=self.company_b.id)

        self.assertTrue(
            synchronized.with_user(self.user)._check_archive_binary_access(),
        )
        for document in (pending, failed):
            with self.assertRaisesRegex(
                AccessError,
                "blocked until an administrator synchronizes",
            ):
                document.with_user(self.user)._check_archive_binary_access()
        self.assertFalse(
            trashed.with_user(self.user)._check_archive_binary_access(),
        )

        user_model = self.env["usl.document"].with_user(self.user)
        by_id = {
            document.id: user_model._workspace_document_values(
                document.with_user(self.user),
            )
            for document in (pending, failed, trashed)
        }
        self.assertTrue(by_id[pending.id]["access_error"])
        self.assertTrue(by_id[failed.id]["access_error"])
        self.assertFalse(by_id[trashed.id]["access_error"])

        controller = DocumentsController()
        with patch.object(
            documents_controller_module,
            "request",
            SimpleNamespace(env=self.env(user=self.user)),
        ):
            self.assertEqual(controller._document(synchronized.id), synchronized)
            for document in (pending, failed):
                with self.assertRaises(AccessError):
                    controller._document(document.id)
            self.assertIsNone(controller._document(inaccessible.id))
            self.assertIsNone(controller._document(trashed.id))
            self.assertIsNone(controller._document(999999999))

    def test_move_to_trash_records_actor_preserves_links_and_blocks_deletion(self):
        document = self._document(1412)
        document.link_to_record("res.partner", self.partner_a.id)
        with patch.object(
            PaperlessClient,
            "trash_document",
            return_value={},
        ) as trash:
            result = document.with_user(self.user).move_to_trash()
        trash.assert_called_once_with(1412)
        self.assertEqual(result["state"], "trashed")
        self.assertIn(document.name, result["message"])
        self.assertEqual(document.availability_state, "trashed")
        self.assertEqual(document.permission_sync_state, "pending")
        self.assertEqual(document.trashed_by_id, self.user)
        self.assertEqual(document.link_count, 1)
        detail = document.with_user(self.manager).document_detail(document.id)
        self.assertEqual(detail["trashed_by"], self.user.display_name)
        self.assertIn("active Odoo link", detail["permanent_delete_blocker"])
        document.with_user(self.manager).approve_permanent_deletion(
            "Synthetic QA cleanup",
        )
        with self.assertRaisesRegex(UserError, "Odoo relationship"):
            document.with_user(self.manager).permanently_delete_from_trash()

    def test_download_controller_uses_odoo_19_content_disposition_helper(self):
        disposition = documents_controller_module.content_disposition(
            "supplier invoice.pdf",
        )
        self.assertIn("attachment", disposition)
        self.assertIn("filename", disposition)

    def test_permission_sync_failure_blocks_paperless_deep_link(self):
        self.env["ir.config_parameter"].sudo().set_str(
            "usl_documents.paperless_public_url", "https://documents.example.test",
        )
        document = self._document(
            111,
            permission_sync_state="failed",
        )
        self.assertFalse(document.paperless_url)
        with self.assertRaisesRegex(
            Exception, "blocked until your individual archive identity",
        ):
            document.action_open_paperless()

    def test_deep_link_requires_current_users_verified_individual_mapping(self):
        self.env["ir.config_parameter"].sudo().set_str(
            "usl_documents.paperless_public_url", "https://documents.example.test",
        )
        document = self._document(185)
        self.assertFalse(document.with_user(self.user).paperless_url)
        self._verified_mapping(
            {
                "user_id": self.user.id,
                "paperless_user_id": 85,
                "paperless_username": "documents-user",
                "sync_state": "synchronized",
            },
        )
        document.invalidate_recordset(["paperless_url"])
        self.assertIn(
            "/documents/185/details", document.with_user(self.user).paperless_url,
        )

    def test_cache_policy_and_direct_link_creation_are_not_client_writable(self):
        document = self._document(186)
        with self.assertRaises(AccessError):
            document.with_user(self.user).write({"name": "Spoofed Paperless title"})
        with self.assertRaises(AccessError):
            document.with_user(self.user).write({"confidentiality": "private"})
        with self.assertRaises(AccessError):
            document.with_user(self.user).with_context(
                usl_documents_cache_write=True,
            ).write({"name": "Spoofed through context"})
        with self.assertRaises(AccessError):
            document.with_user(self.user).with_context(
                usl_documents_policy_write=True,
            ).write({"confidentiality": "private"})
        with self.assertRaises(AccessError):
            self.env["usl.document.link"].with_user(self.user).create(
                {
                    "document_id": document.id,
                    "res_model": "res.partner",
                    "res_id": self.partner_a.id,
                    "record_name": self.partner_a.display_name,
                    "company_id": self.company_a.id,
                },
            )
        operation = self.env["usl.document.operation"].sudo().create(
            {
                "name": "trusted-operation.pdf",
                "state": "processing",
                "checksum": "1" * 64,
                "company_id": self.company_a.id,
                "user_id": self.user.id,
            },
        )
        with self.assertRaises(AccessError):
            self.env["usl.document.operation"].with_user(self.user).create(
                {
                    "name": "forged-operation.pdf",
                    "state": "processing",
                    "checksum": "2" * 64,
                    "company_id": self.company_a.id,
                },
            )
        with self.assertRaises(AccessError):
            operation.with_user(self.user).write({"state": "archived"})
        tag = self._tag(9186, "Protected cache identity")
        with self.assertRaises(AccessError):
            tag.with_user(self.user).with_context(
                usl_documents_cache_write=True,
            ).write({"paperless_id": 999999})
        personal = self.env["usl.document.smart.view"].with_user(self.user).create(
            {"name": "Private search"},
        )
        with self.assertRaises(AccessError):
            personal.with_user(self.user).with_context(
                usl_documents_archive_view_sync=True,
            ).write({"paperless_id": 12345})

    def test_integrity_manifest_reports_versions_links_and_checksums(self):
        document = self._document(112, checksum="d" * 64)
        document.link_to_record("res.partner", self.partner_a.id)
        document.with_context(usl_documents_cache_write=True).write(
            {"availability_state": "trashed"},
        )
        self._document(
            113,
            checksum="e" * 64,
            availability_state="permanently_deleted",
            permission_sync_state="failed",
        )
        with (
            patch.object(
                PaperlessClient,
                "compatibility",
                return_value={
                    "server_version": "3.0.4",
                    "api_version": "10",
                    "document_count": 0,
                },
            ),
            patch.object(
                PaperlessClient,
                "list_documents",
                return_value={
                    "count": 0,
                    "next": None,
                    "results": [],
                },
            ),
            patch.object(
                PaperlessClient,
                "list_trashed_documents",
                return_value=[
                    {
                        "id": 112,
                        "checksum": "d" * 64,
                        "versions": [],
                    },
                ],
            ),
        ):
            manifest = self.env["usl.document"].integrity_manifest("qa-backup")
        self.assertEqual(manifest["schema"], "usl-documents-integrity-v1")
        self.assertEqual(manifest["backup_id"], "qa-backup")
        self.assertEqual(manifest["paperless_version"], "3.0.4")
        self.assertEqual(manifest["paperless_trash_count"], 1)
        self.assertEqual(manifest["paperless_total_count"], 1)
        self.assertEqual(manifest["permanent_deletion_tombstone_count"], 1)
        self.assertEqual(manifest["permanently_deleted_paperless_ids"], [113])
        self.assertFalse(manifest["missing_document_ids"])
        self.assertFalse(manifest["permission_sync_failures"])
        self.assertTrue(manifest["integrity_ok"])
        self.assertGreaterEqual(manifest["relationship_count"], 1)
        self.assertIn(
            "d" * 64,
            [item["checksum"] for item in manifest["representative_checksums"]],
        )


@tagged("post_install", "-at_install", "usl_documents")
class TestPaperlessClientContract(TransactionCase):
    def test_multipart_headers_reject_filename_and_content_type_injection(self):
        self.assertEqual(
            PaperlessClient._multipart_filename('invoice"\r\nX-Evil: yes.pdf'),
            "invoice'__X-Evil: yes.pdf",
        )
        self.assertEqual(
            PaperlessClient._multipart_content_type(
                "application/pdf\r\nX-Evil: yes",
            ),
            "application/octet-stream",
        )
        self.assertEqual(
            PaperlessClient._multipart_content_type("application/pdf"),
            "application/pdf",
        )

    def test_invalid_json_response_fails_as_api_compatibility_error(self):
        with self.assertRaises(PaperlessCompatibilityError):
            PaperlessClient._decode_json(b"{not valid JSON")

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

    def test_plain_text_previews_are_safe_inline_html(self):
        content, content_type = DocumentsController._browser_preview(
            b"<script>alert('unsafe')</script> Supplier evidence",
            "text/plain",
        )
        self.assertEqual(content_type, "text/html; charset=utf-8")
        self.assertIn(b"&lt;script&gt;", content)
        self.assertNotIn(b"<script>", content)
        self.assertIn(b"Supplier evidence", content)

    def test_active_preview_types_cannot_execute_in_odoo_origin(self):
        content, content_type = DocumentsController._browser_preview(
            b"<script>alert('unsafe')</script>",
            "application/xhtml+xml",
        )
        self.assertEqual(content_type, "text/html; charset=utf-8")
        self.assertIn(b"&lt;script&gt;", content)
        content, content_type = DocumentsController._browser_preview(
            b"<svg onload=\"alert('unsafe')\"/>",
            "image/svg+xml",
        )
        self.assertEqual(content_type, "application/octet-stream")

    def test_metadata_catalog_crud_uses_supported_endpoints(self):
        client = PaperlessClient(self.env)
        with patch.object(
            client,
            "_request",
            side_effect=[
                (
                    {
                        "next": None,
                        "results": [
                            {
                                "id": 41,
                                "name": "Contracts",
                                "matching_algorithm": 6,
                            },
                        ],
                    },
                    {},
                ),
                ({"id": 42, "name": "Legal"}, {}),
                ({"id": 42, "name": "Legal records"}, {}),
                ({}, {}),
            ],
        ) as request:
            self.assertEqual(client.list_metadata("tags")[0]["id"], 41)
            client.create_metadata("tags", {"name": "Legal"})
            client.update_metadata("tags", 42, {"name": "Legal records"})
            client.delete_metadata("tags", 42)
        self.assertEqual(
            [call.args[:2] for call in request.call_args_list],
            [
                ("GET", "/api/tags/"),
                ("POST", "/api/tags/"),
                ("PATCH", "/api/tags/42/"),
                ("DELETE", "/api/tags/42/"),
            ],
        )

    def test_custom_fields_use_supported_catalog_endpoint(self):
        client = PaperlessClient(self.env)
        with patch.object(
            client,
            "_request",
            side_effect=[
                (
                    {
                        "next": None,
                        "results": [
                            {
                                "id": 7,
                                "name": "Invoice reference",
                                "data_type": "string",
                            },
                        ],
                    },
                    {},
                ),
                (
                    {
                        "id": 8,
                        "name": "Gross amount",
                        "data_type": "monetary",
                    },
                    {},
                ),
                ({}, {}),
            ],
        ) as request:
            self.assertEqual(client.list_custom_fields()[0]["id"], 7)
            created = client.create_custom_field(
                {"name": "Gross amount", "data_type": "monetary"},
            )
            client.delete_custom_field(7)
        self.assertEqual(created["id"], 8)
        self.assertEqual(
            [call.args[:2] for call in request.call_args_list],
            [
                ("GET", "/api/custom_fields/"),
                ("POST", "/api/custom_fields/"),
                ("DELETE", "/api/custom_fields/7/"),
            ],
        )

    def test_saved_views_and_trash_use_supported_endpoints(self):
        client = PaperlessClient(self.env)
        with patch.object(
            client,
            "_request",
            side_effect=[
                ({"next": None, "results": [{"id": 51, "name": "Contracts"}]}, {}),
                ({"id": 52, "name": "Tax"}, {}),
                ({"id": 52, "name": "Tax filings"}, {}),
                ({}, {}),
                ({"next": None, "results": [{"id": 333}]}, {}),
                ({"result": "OK", "doc_ids": [333]}, {}),
                ({"result": "OK", "doc_ids": [334]}, {}),
            ],
        ) as request:
            self.assertEqual(client.list_saved_views()[0]["id"], 51)
            client.create_saved_view({"name": "Tax", "filter_rules": []})
            client.update_saved_view(52, {"name": "Tax filings"})
            client.delete_saved_view(52)
            self.assertEqual(client.list_trashed_documents()[0]["id"], 333)
            restored = client.restore_trashed_documents([333])
            emptied = client.permanently_delete_trashed_documents([334])
        self.assertEqual(restored["result"], "OK")
        self.assertEqual(emptied["result"], "OK")
        self.assertEqual(
            [call.args[:2] for call in request.call_args_list],
            [
                ("GET", "/api/saved_views/"),
                ("POST", "/api/saved_views/"),
                ("PATCH", "/api/saved_views/52/"),
                ("DELETE", "/api/saved_views/52/"),
                ("GET", "/api/trash/"),
                ("POST", "/api/trash/"),
                ("POST", "/api/trash/"),
            ],
        )
        self.assertEqual(
            request.call_args_list[-1].kwargs["body"]["action"],
            "empty",
        )

    def test_fail_closed_workflow_uses_supported_api_for_every_channel(self):
        self.env["ir.config_parameter"].sudo().set_int(
            "usl_documents.paperless_service_user_id", 42,
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
