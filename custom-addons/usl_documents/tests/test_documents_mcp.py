from unittest.mock import patch

from odoo import Command
from odoo.exceptions import AccessError
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
        cls.scope_tag = cls.env["usl.paperless.tag"].sudo().with_context(
            usl_documents_cache_write=True,
        ).create({"paperless_id": 99001, "name": "MCP Isolated Scope"})

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
                "search",
                return_value={
                    "count": 2,
                    "next": None,
                    "results": [
                        {"id": hidden.paperless_id, "content": "hidden OCR"},
                        {"id": allowed.paperless_id, "content": "allowed OCR"},
                    ],
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
            lexical.call_args.kwargs["filters"]["id__in"],
            str(allowed.paperless_id),
        )
        self.assertEqual(
            semantic.call_args.kwargs["document_ids"],
            [allowed.paperless_id],
        )
        self.assertLessEqual(len(result["results"][0]["excerpt"]), 500)
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
                "search",
                return_value={
                    "count": 1,
                    "next": None,
                    "results": [
                        {"id": document.paperless_id, "content": "continuity"},
                    ],
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

    def test_search_never_sends_unsynchronized_roots_to_paperless(self):
        blocked = self._document(
            5107,
            name="Cached blocked title",
            permission_sync_state="pending",
        )
        documents = self.env["usl.document"].with_user(self.user)
        with (
            patch.object(
                PaperlessClient,
                "search",
                return_value={"count": 0, "next": None, "results": []},
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
        for call in lexical.call_args_list:
            self.assertNotIn(
                str(blocked.paperless_id),
                str(call.kwargs.get("filters") or {}),
            )
        self.assertNotIn(
            blocked.paperless_id,
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
