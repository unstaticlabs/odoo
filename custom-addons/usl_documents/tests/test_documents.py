import base64
from unittest.mock import patch

from odoo import Command
from odoo.exceptions import AccessError, ValidationError
from odoo.tests import TransactionCase, tagged

from ..controllers.documents import DocumentsController
from ..models.document import UslDocument
from ..models.paperless_client import (
    PaperlessClient,
    PaperlessCompatibilityError,
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
            )
        self.assertEqual(result["state"], "processing")
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
        accountant_links = self.env["usl.document.link"].with_user(
            self.accountant,
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
            patch.object(UslDocument, "_sync_metadata_catalogs", return_value=None),
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
        payloads = [
            {
                "id": 301,
                "name": "Finance",
                "color": "#112233",
                "text_color": "#ffffff",
                "matching_algorithm": 6,
                "document_count": 2,
                "parent": None,
            },
            {
                "id": 302,
                "name": "Banking",
                "color": "#445566",
                "text_color": "#ffffff",
                "matching_algorithm": 3,
                "match": "bank statement",
                "document_count": 1,
                "parent": 301,
            },
        ]
        with patch.object(client, "list_metadata", return_value=payloads):
            self.env["usl.paperless.tag"].synchronize_catalog(client=client)
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
        self.assertEqual(document.tag_names, "Banking")

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
            "filter_rules": [{"rule_type": 6, "value": "332"}],
        }
        client = PaperlessClient(self.env)
        with patch.object(client, "list_saved_views", return_value=[remote]):
            count = self.env["usl.document.smart.view"].synchronize_archive_views(
                client=client,
            )
        self.assertEqual(count, 1)
        self.assertEqual(view.paperless_id, 52)
        self.assertEqual(view.name, "Signed agreements")
        self.assertEqual(view.tag_ids, tag)
        self.assertEqual(view.paperless_sync_state, "synchronized")

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
        self.assertEqual(document.availability_state, "available")
        self.assertEqual(document.paperless_id, 333)
        self.assertEqual(document.link_count, 1)

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
            PaperlessClient, "set_document_permissions", return_value={},
        ) as permission_call:
            document.with_user(self.manager).action_sync_permissions()
        self.assertEqual(document.permission_sync_state, "synchronized")
        self.assertTrue(document.permission_checked_at)
        permission_call.assert_called_once_with(
            110, view_users=[21, 22], change_users=[22],
        )

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
        self.env["usl.paperless.user.mapping"].create(
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
            self.env["usl.document.link"].with_user(self.user).create(
                {
                    "document_id": document.id,
                    "res_model": "res.partner",
                    "res_id": self.partner_a.id,
                    "record_name": self.partner_a.display_name,
                    "company_id": self.company_a.id,
                },
            )

    def test_integrity_manifest_reports_versions_links_and_checksums(self):
        document = self._document(112, checksum="d" * 64)
        document.with_context(usl_documents_cache_write=True).write(
            {"availability_state": "trashed"},
        )
        document.link_to_record("res.partner", self.partner_a.id)
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
        self.assertFalse(manifest["missing_document_ids"])
        self.assertTrue(manifest["integrity_ok"])
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

    def test_plain_text_previews_are_safe_inline_html(self):
        content, content_type = DocumentsController._browser_preview(
            b"<script>alert('unsafe')</script> Supplier evidence",
            "text/plain",
        )
        self.assertEqual(content_type, "text/html; charset=utf-8")
        self.assertIn(b"&lt;script&gt;", content)
        self.assertNotIn(b"<script>", content)
        self.assertIn(b"Supplier evidence", content)

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
            ],
        ) as request:
            self.assertEqual(client.list_saved_views()[0]["id"], 51)
            client.create_saved_view({"name": "Tax", "filter_rules": []})
            client.update_saved_view(52, {"name": "Tax filings"})
            client.delete_saved_view(52)
            self.assertEqual(client.list_trashed_documents()[0]["id"], 333)
            restored = client.restore_trashed_documents([333])
        self.assertEqual(restored["result"], "OK")
        self.assertEqual(
            [call.args[:2] for call in request.call_args_list],
            [
                ("GET", "/api/saved_views/"),
                ("POST", "/api/saved_views/"),
                ("PATCH", "/api/saved_views/52/"),
                ("DELETE", "/api/saved_views/52/"),
                ("GET", "/api/trash/"),
                ("POST", "/api/trash/"),
            ],
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
