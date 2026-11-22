from unittest.mock import patch

from odoo import Command
from odoo.exceptions import AccessError, ValidationError
from odoo.tests import TransactionCase, tagged

from odoo.addons.mail.tests.common import mail_new_test_user
from odoo.addons.usl_documents.models.paperless_client import (
    PaperlessClient,
    PaperlessError,
    PaperlessUnavailable,
)


@tagged("post_install", "-at_install", "usl_documents", "usl_documents_mcp")
class TestDocumentsMcp(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company_a = cls.env.company
        cls.company_b = cls.env["res.company"].create({"name": "MCP Restricted"})
        cls.user = mail_new_test_user(
            cls.env,
            login="documents-mcp-user",
            name="Documents MCP User",
            company_id=cls.company_a.id,
            company_ids=[Command.set(cls.company_a.ids)],
            groups="usl_documents.group_documents_user",
        )
        cls.other_user = mail_new_test_user(
            cls.env,
            login="documents-mcp-other",
            name="Documents MCP Other User",
            company_id=cls.company_a.id,
            company_ids=[Command.set(cls.company_a.ids)],
            groups="usl_documents.group_documents_user",
        )
        cls.manager = mail_new_test_user(
            cls.env,
            login="documents-mcp-manager",
            name="Documents MCP Manager",
            company_id=cls.company_a.id,
            company_ids=[Command.set(cls.company_a.ids)],
            groups="usl_documents.group_documents_manager",
        )
        cls.scope_tag = cls.env["usl.paperless.tag"].sudo().with_context(
            usl_documents_cache_write=True,
        ).create({"paperless_id": 99001, "name": "MCP Isolated Scope"})
        cls.scope_correspondent = (
            cls.env["usl.paperless.correspondent"]
            .sudo()
            .with_context(usl_documents_cache_write=True)
            .create({"paperless_id": 99002, "name": "MCP Scoped Correspondent"})
        )
        cls.scope_document_type = (
            cls.env["usl.paperless.document.type"]
            .sudo()
            .with_context(usl_documents_cache_write=True)
            .create({"paperless_id": 99003, "name": "MCP Scoped Type"})
        )

    def _document(self, paperless_id, **values):
        return self.env["usl.document"].sudo().create(
            {
                "name": values.pop("name", f"Document {paperless_id}"),
                "paperless_id": paperless_id,
                "company_id": values.pop("company_id", self.company_a.id),
                "confidentiality": values.pop("confidentiality", "internal"),
                "review_state": values.pop("review_state", "classified"),
                "availability_state": values.pop("availability_state", "available"),
                "permission_sync_state": values.pop(
                    "permission_sync_state",
                    "synchronized",
                ),
                **values,
            },
        )

    def test_get_uses_one_indistinguishable_denial_for_hidden_and_missing_ids(self):
        hidden = self._document(5101, company_id=self.company_b.id)
        documents = self.env["usl.document"].with_user(self.user)

        for document_id in (hidden.id, 999999):
            with self.assertRaisesRegex(AccessError, "The document is unavailable"):
                documents.mcp_get(document_id)

    def test_get_content_is_bounded_and_requires_binary_authorization(self):
        document = self._document(5102)
        blocked = self._document(5103, permission_sync_state="pending")
        documents = self.env["usl.document"].with_user(self.user)

        with patch.object(
            PaperlessClient,
            "get_document",
            return_value={"content": "0123456789"},
        ) as get_document:
            page = documents.mcp_get_content(document.id, offset=3, limit=4)
            with self.assertRaisesRegex(AccessError, "The document is unavailable"):
                documents.mcp_get_content(blocked.id)

        self.assertEqual(page["content"], "3456")
        self.assertEqual(page["next_offset"], 7)
        self.assertTrue(page["has_more"])
        get_document.assert_called_once_with(document.paperless_id)

    def test_search_pre_scopes_company_and_rechecks_remote_results(self):
        allowed = self._document(
            5104,
            name="Allowed agreement",
            tag_ids=[Command.set(self.scope_tag.ids)],
        )
        hidden = self._document(
            5105,
            name="Hidden agreement",
            company_id=self.company_b.id,
            tag_ids=[Command.set(self.scope_tag.ids)],
        )
        documents = self.env["usl.document"].with_user(self.user)

        with (
            patch.object(
                PaperlessClient,
                "scoped_search",
                return_value={
                    "results": [
                        {
                            "id": hidden.paperless_id,
                            "rank": 1,
                            "excerpt": "hidden lexical OCR",
                        },
                        {
                            "id": allowed.paperless_id,
                            "rank": 2,
                            "excerpt": "allowed lexical OCR",
                        },
                    ],
                    "truncated": False,
                },
            ) as lexical,
            patch.object(
                PaperlessClient,
                "semantic_search",
                return_value={
                    "results": [
                        {
                            "id": hidden.paperless_id,
                            "similarity": 0.99,
                            "excerpt": "hidden semantic OCR",
                        },
                        {
                            "id": allowed.paperless_id,
                            "similarity": 0.8,
                            "excerpt": "allowed semantic OCR",
                        },
                    ],
                    "warnings": [],
                },
            ) as semantic,
        ):
            result = documents.mcp_search(
                "agreement renewal",
                company_id=self.company_a.id,
                tag_ids=self.scope_tag.ids,
            )

        self.assertEqual([item["id"] for item in result["results"]], [allowed.id])
        self.assertNotIn("hidden", str(result).lower())
        self.assertEqual(
            lexical.call_args.kwargs["document_ids"],
            [allowed.paperless_id],
        )
        self.assertEqual(lexical.call_args.kwargs["fields"], "all")
        self.assertTrue(lexical.call_args.kwargs["include_excerpt"])
        self.assertEqual(
            semantic.call_args.kwargs["document_ids"],
            [allowed.paperless_id],
        )
        self.assertLessEqual(len(result["results"][0]["excerpt"]), 500)
        self.assertEqual(result["results"][0]["excerpt"], "allowed lexical OCR")
        self.assertEqual(
            {item["source"] for item in result["results"][0]["provenance"]},
            {"paperless_lexical", "paperless_semantic"},
        )

    def test_hybrid_search_keeps_lexical_results_during_semantic_outage(self):
        document = self._document(5106, name="Continuity evidence")
        documents = self.env["usl.document"].with_user(self.user)
        with (
            patch.object(
                PaperlessClient,
                "scoped_search",
                return_value={
                    "results": [
                        {"id": document.paperless_id, "rank": 1},
                    ],
                    "truncated": False,
                },
            ),
            patch.object(
                PaperlessClient,
                "semantic_search",
                side_effect=PaperlessUnavailable("offline"),
            ),
        ):
            result = documents.mcp_search("continuity")

        self.assertEqual(result["results"][0]["id"], document.id)
        self.assertEqual(result["warnings"][0]["code"], "semantic_unavailable")

    def test_saved_view_browse_applies_personal_filters_without_paperless(self):
        allowed = self._document(
            51061,
            name="Saved view result",
            tag_ids=[Command.set(self.scope_tag.ids)],
        )
        self._document(51062, name="Outside saved view")
        view_values = self.env["usl.document.smart.view"].with_user(
            self.user,
        ).save_personal_view(
            "MCP tag view",
            {"tag_ids": self.scope_tag.ids},
        )
        documents = self.env["usl.document"].with_user(self.user)

        with (
            patch.object(PaperlessClient, "scoped_search") as lexical,
            patch.object(PaperlessClient, "semantic_search") as semantic,
        ):
            result = documents.mcp_search(saved_view_id=view_values["id"])

        self.assertEqual([item["id"] for item in result["results"]], [allowed.id])
        self.assertEqual(result["mode"], "browse")
        self.assertEqual(result["saved_view"]["name"], "MCP tag view")
        self.assertEqual(
            result["results"][0]["provenance"],
            [{"source": "odoo_saved_view"}],
        )
        lexical.assert_not_called()
        semantic.assert_not_called()

    def test_saved_view_query_scopes_semantic_search(self):
        allowed = self._document(
            51063,
            name="Semantic saved result",
            tag_ids=[Command.set(self.scope_tag.ids)],
        )
        outside = self._document(51064, name="Outside semantic saved view")
        view_values = self.env["usl.document.smart.view"].with_user(
            self.user,
        ).save_personal_view(
            "MCP semantic view",
            {
                "query": "renewal obligations",
                "tag_ids": self.scope_tag.ids,
            },
        )
        documents = self.env["usl.document"].with_user(self.user)

        with (
            patch.object(PaperlessClient, "scoped_search") as lexical,
            patch.object(
                PaperlessClient,
                "semantic_search",
                return_value={
                    "results": [
                        {"id": outside.paperless_id, "similarity": 0.99},
                        {"id": allowed.paperless_id, "similarity": 0.82},
                    ],
                    "warnings": [],
                },
            ) as semantic,
        ):
            result = documents.mcp_search(
                saved_view_id=view_values["id"],
                mode="semantic",
            )

        self.assertEqual(result["query"], "renewal obligations")
        self.assertEqual([item["id"] for item in result["results"]], [allowed.id])
        self.assertEqual(
            semantic.call_args.kwargs["document_ids"],
            [allowed.paperless_id],
        )
        lexical.assert_not_called()

    def test_saved_view_browse_applies_complete_structured_filter_scope(self):
        project = self.env["project.project"].create({"name": "MCP Filter Project"})
        allowed = self._document(
            51065,
            name="Complete saved filter result",
            tag_ids=[Command.set(self.scope_tag.ids)],
            correspondent_id=self.scope_correspondent.id,
            document_type_id=self.scope_document_type.id,
            document_date="2026-08-15",
            paperless_created="2026-08-10 10:00:00",
            confidentiality="accounting",
            review_state="reviewed",
            source="paperless",
        )
        self._document(
            51066,
            name="Outside complete saved filter",
            tag_ids=[Command.set(self.scope_tag.ids)],
            correspondent_id=self.scope_correspondent.id,
            document_type_id=self.scope_document_type.id,
            document_date="2026-08-15",
            paperless_created="2026-08-10 10:00:00",
            confidentiality="internal",
            review_state="reviewed",
            source="paperless",
        )
        self.env["usl.document.link"].sudo().with_context(
            usl_documents_link_policy_write=True,
        ).create(
            {
                "document_id": allowed.id,
                "res_model": project._name,
                "res_id": project.id,
                "record_name": project.display_name,
                "company_id": self.company_a.id,
                "active": True,
            },
        )
        view_values = self.env["usl.document.smart.view"].with_user(
            self.user,
        ).save_personal_view(
            "MCP complete filter view",
            {
                "company_id": self.company_a.id,
                "tag_ids": self.scope_tag.ids,
                "correspondent_id": self.scope_correspondent.id,
                "document_type_id": self.scope_document_type.id,
                "date_from": "2026-08-01",
                "date_to": "2026-08-31",
                "added_from": "2026-08-01",
                "added_to": "2026-08-31",
                "source": "paperless",
                "confidentiality": "accounting",
                "review_state": "reviewed",
                "linked_state": "linked",
                "linked_record": f"{project._name}:{project.id}",
            },
        )

        result = self.env["usl.document"].with_user(self.user).mcp_search(
            saved_view_id=view_values["id"],
        )

        self.assertEqual([item["id"] for item in result["results"]], [allowed.id])
        self.assertEqual(result["saved_view"]["filters"]["linked_state"], "linked")

    def test_saved_views_list_only_shared_and_callers_personal_views(self):
        shared = self.env["usl.document.smart.view"].with_user(self.manager).create(
            {
                "name": "MCP shared saved view",
                "scope": "shared",
                "system_rule": "metadata",
                "tag_ids": [Command.set(self.scope_tag.ids)],
            },
        )
        own_values = self.env["usl.document.smart.view"].with_user(
            self.user,
        ).save_personal_view("MCP own saved view", {"review_state": "reviewed"})
        other_values = self.env["usl.document.smart.view"].with_user(
            self.other_user,
        ).save_personal_view("MCP other saved view", {})

        result = self.env["usl.document"].with_user(
            self.user,
        ).mcp_list_saved_views(query="MCP")
        result_ids = {item["id"] for item in result["results"]}

        self.assertIn(shared.id, result_ids)
        self.assertIn(own_values["id"], result_ids)
        self.assertNotIn(other_values["id"], result_ids)
        shared_payload = next(
            item for item in result["results"] if item["id"] == shared.id
        )
        self.assertEqual(
            shared_payload["tags"],
            [{"id": self.scope_tag.id, "name": self.scope_tag.name}],
        )

    def test_record_only_browse_respects_document_rules_and_paginates(self):
        project = self.env["project.project"].create({"name": "Evidence audit"})
        task = self.env["project.task"].create({"name": "Match the export", "project_id": project.id})
        first = self._document(5151, name="First export", document_date="2026-07-01")
        second = self._document(5152, name="Second export", document_date="2026-07-02")
        hidden = self._document(5153, company_id=self.company_b.id)
        self._document(5154, name="Unrelated export")
        links = self.env["usl.document.link"].sudo().with_context(usl_documents_link_policy_write=True)
        links.create([
            {
                "document_id": document.id,
                "res_model": task._name,
                "res_id": task.id,
                "record_name": task.name,
                "company_id": document.company_id.id,
            }
            for document in first | second | hidden
        ])
        documents = self.env["usl.document"].with_user(self.user)
        scope = {"linked_model": task._name, "linked_id": task.id}
        with patch.object(PaperlessClient, "scoped_search") as remote_search:
            page = documents.mcp_search(**scope, limit=1)
            next_page = documents.mcp_search(**scope, limit=1, offset=1)

        self.assertEqual([item["id"] for item in page["results"]], [second.id])
        self.assertTrue(page["has_more"])
        self.assertEqual([item["id"] for item in next_page["results"]], [first.id])
        self.assertFalse(next_page["has_more"])
        self.assertEqual(page["results"][0]["provenance"], [{"source": "odoo_linked_record"}])
        remote_search.assert_not_called()
        with self.assertRaises(ValidationError):
            documents.mcp_search(linked_model=task._name)
        project.privacy_visibility = "followers"
        with self.assertRaises(AccessError):
            documents.mcp_search(**scope)

    def test_hidden_and_missing_saved_views_share_one_denial(self):
        other_values = self.env["usl.document.smart.view"].with_user(
            self.other_user,
        ).save_personal_view("MCP hidden saved view", {})
        documents = self.env["usl.document"].with_user(self.user)

        for saved_view_id in (other_values["id"], 999999):
            with self.assertRaisesRegex(AccessError, "saved view is unavailable"):
                documents.mcp_search(
                    "confidential query",
                    saved_view_id=saved_view_id,
                )

        with self.assertRaises(ValidationError):
            documents.mcp_list_saved_views(scope="private")

    def test_search_never_sends_unsynchronized_roots_to_paperless(self):
        allowed = self._document(51071, name="Synchronized comparison root")
        blocked = self._document(
            5107,
            name="Cached blocked title",
            permission_sync_state="pending",
        )
        documents = self.env["usl.document"].with_user(self.user)
        with (
            patch.object(
                PaperlessClient,
                "scoped_search",
                return_value={"results": [], "truncated": False},
            ) as lexical,
            patch.object(
                PaperlessClient,
                "semantic_search",
                return_value={"results": [], "warnings": []},
            ) as semantic,
        ):
            result = documents.mcp_search("Cached blocked title")

        self.assertEqual(result["results"][0]["id"], blocked.id)
        self.assertEqual(result["results"][0]["excerpt"], "")
        self.assertEqual(
            result["results"][0]["provenance"][0]["source"],
            "odoo_metadata",
        )
        remote_scope = lexical.call_args.kwargs["document_ids"]
        lexical.assert_called_once()
        self.assertNotIn(blocked.paperless_id, remote_scope)
        self.assertIn(allowed.paperless_id, remote_scope)
        self.assertNotIn(
            blocked.paperless_id,
            semantic.call_args.kwargs["document_ids"],
        )
        self.assertIn(
            allowed.paperless_id,
            semantic.call_args.kwargs["document_ids"],
        )

    def test_find_similar_scopes_source_and_filters_malicious_results(self):
        source = self._document(
            5108,
            name="Source",
            tag_ids=[Command.set(self.scope_tag.ids)],
        )
        candidate = self._document(
            5109,
            name="Candidate",
            tag_ids=[Command.set(self.scope_tag.ids)],
        )
        hidden = self._document(
            5110,
            company_id=self.company_b.id,
            tag_ids=[Command.set(self.scope_tag.ids)],
        )
        documents = self.env["usl.document"].with_user(self.user)

        with patch.object(
            PaperlessClient,
            "semantic_search_by_document",
            return_value={
                "results": [
                    {"id": source.paperless_id, "similarity": 1.0},
                    {"id": hidden.paperless_id, "similarity": 0.99},
                    {
                        "id": candidate.paperless_id,
                        "similarity": 0.81,
                        "excerpt": "similar candidate",
                    },
                ],
                "warnings": [],
            },
        ) as similar:
            result = documents.mcp_find_similar(
                source.id,
                tag_ids=self.scope_tag.ids,
                source="paperless",
            )

        self.assertEqual([item["id"] for item in result["results"]], [candidate.id])
        self.assertEqual(similar.call_args.args, (source.paperless_id,))
        self.assertEqual(
            similar.call_args.kwargs["document_ids"],
            [source.paperless_id, candidate.paperless_id],
        )

    def test_versions_omit_integrity_hashes_and_links_obey_target_access(self):
        document = self._document(5111)
        version = self.env["usl.document.version"].sudo().create(
            {
                "document_id": document.id,
                "paperless_version_id": "v1",
                "label": "Received original",
                "checksum": "do-not-expose",
                "archive_checksum": "also-private",
                "is_current": True,
            },
        )
        project = self.env["project.project"].create({"name": "Visible Project"})
        self.env["usl.document.link"].sudo().with_context(
            usl_documents_link_policy_write=True,
        ).create(
            {
                "document_id": document.id,
                "res_model": project._name,
                "res_id": project.id,
                "record_name": project.display_name,
                "company_id": self.company_a.id,
                "archive_mode": "automatic",
                "policy_role": "library",
                "document_role": "library",
                "attachment_origin": "migration",
                "policy_reason": "mcp_test",
            },
        )
        documents = self.env["usl.document"].with_user(self.user)

        versions = documents.mcp_get_versions(document.id)
        links = documents.mcp_get_links(document.id)

        self.assertEqual(versions["versions"][0]["id"], version.id)
        self.assertNotIn("checksum", versions["versions"][0])
        self.assertEqual(links["links"][0]["record_id"], project.id)
        self.assertEqual(links["links"][0]["model"], "project.project")

    def test_integrity_is_opt_in_and_sizes_require_an_exact_variant(self):
        document = self._document(5181)
        records = self.env["usl.document.version"].sudo()
        original, archive = "a" * 64, "b" * 64
        old = records.create({"document_id": document.id, "paperless_version_id": "81",
                              "label": "Received", "checksum": "c" * 32, "is_received_original": True})
        current = records.create({"document_id": document.id, "paperless_version_id": "82", "label": "Revision",
                                  "checksum": original, "archive_checksum": archive, "is_current": True})
        documents = self.env["usl.document"].with_user(self.user)

        def head(document_id, *, version_id, original):
            self.assertEqual((document_id, version_id), (5181, "82"))
            digest = "a" * 64 if original else "b" * 64
            return {"status": 200, "headers": {"ETag": f'"{digest}"', "Content-Length": "12" if original else "20"}}

        with patch.object(PaperlessClient, "probe_download", side_effect=head) as probe:
            self.assertNotIn("integrity", documents.mcp_get_versions(document.id)["versions"][0])
            probe.assert_not_called()
            rows = documents.mcp_get_versions(document.id, include_integrity=True)["versions"]
            self.assertEqual(probe.call_count, 2)
        by_id = {row["id"]: row["integrity"] for row in rows}
        self.assertEqual(by_id[current.id]["original"], {"algorithm": "sha256", "digest": original, "size_bytes": 12})
        self.assertEqual(by_id[current.id]["archive"], {"algorithm": "sha256", "digest": archive, "size_bytes": 20})
        self.assertEqual(by_id[old.id]["original"], {"algorithm": "md5", "digest": "c" * 32, "size_bytes": None})
        self.assertIsNone(by_id[old.id]["archive"])
        for headers in (
            {"ETag": f'"{archive}"', "Content-Length": "12"},
            {"ETag": f'W/"{original}"', "Content-Length": "12"}, {"Content-Length": "12"},
            {"ETag": f'"{original}"', "Content-Length": "-1"}, {"ETag": f'"{original}"'},
            {"ETag": f'"{original}"', "Content-Length": "9007199254740992"},
        ):
            with self.subTest(headers=headers), patch.object(PaperlessClient, "probe_download",
                    return_value={"status": 200, "headers": headers}):
                result = documents.mcp_get_versions(document.id, include_integrity=True)["versions"][0]["integrity"]
                self.assertEqual(result["original"]["digest"], original)
                self.assertIsNone(result["original"]["size_bytes"])
        with patch.object(PaperlessClient, "probe_download", return_value={
            "status": 200, "headers": {"ETag": f'"{original}"', "Content-Length": "9007199254740991"},
        }):
            result = documents.mcp_get_versions(document.id, include_integrity=True)["versions"][0]["integrity"]
            self.assertEqual(result["original"]["size_bytes"], 9007199254740991)
        with patch.object(PaperlessClient, "probe_download", side_effect=PaperlessError("unavailable")):
            result = documents.mcp_get_versions(document.id, include_integrity=True)["versions"][0]["integrity"]
            self.assertIsNone(result["original"]["size_bytes"])
            self.assertIsNone(result["archive"]["size_bytes"])

    def test_integrity_does_not_disclose_files_without_binary_access(self):
        document = self._document(5182, permission_sync_state="pending")
        self.env["usl.document.version"].sudo().create({
            "document_id": document.id, "paperless_version_id": "83", "label": "Pending",
            "checksum": "a" * 64, "archive_checksum": "b" * 64, "is_current": True,
        })
        documents = self.env["usl.document"].with_user(self.user)
        with patch.object(PaperlessClient, "probe_download") as probe:
            row = documents.mcp_get_versions(document.id, include_integrity=True)["versions"][0]
            self.assertFalse(row["binary_available"])
            self.assertNotIn("integrity", row)
            hidden = self._document(5183, company_id=self.company_b.id)
            with self.assertRaises(AccessError):
                documents.mcp_get_versions(hidden.id, include_integrity=True)
            probe.assert_not_called()

    def test_catalogs_are_bounded_and_use_archive_catalog_acls(self):
        tag = self.env["usl.paperless.tag"].sudo().with_context(
            usl_documents_cache_write=True,
        ).create({"paperless_id": 9001, "name": "MCP Tag"})

        result = self.env["usl.document"].with_user(self.user).mcp_list_tags(
            query="MCP Tag",
            limit=1,
        )

        self.assertEqual(result["results"], [{"id": tag.id, "name": tag.name}])


@tagged("post_install", "-at_install", "usl_documents", "usl_documents_mcp")
class TestPaperlessClientSimilarSearch(TransactionCase):
    def test_document_similarity_keeps_source_inside_each_scoped_request(self):
        client = PaperlessClient(self.env)
        scope = list(range(1, 10002))
        with patch.object(
            client,
            "_request",
            return_value=({"results": [], "warnings": []}, None),
        ) as request:
            client.semantic_search_by_document(
                10001,
                document_ids=scope,
                limit=7,
            )

        self.assertEqual(request.call_count, 2)
        for call in request.call_args_list:
            body = call.kwargs["body"]
            self.assertEqual(body["document_id"], 10001)
            self.assertIn(10001, body["document_ids"])
            self.assertLessEqual(len(body["document_ids"]), 10000)
            self.assertNotIn("query", body)

    def test_document_similarity_rejects_source_outside_scope_without_request(self):
        client = PaperlessClient(self.env)
        with patch.object(client, "_request") as request:
            with self.assertRaisesRegex(PaperlessError, "outside the authorized scope"):
                client.semantic_search_by_document(
                    99,
                    document_ids=[1, 2],
                )
        request.assert_not_called()
