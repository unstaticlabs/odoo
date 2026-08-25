from unittest.mock import patch

from django.contrib.auth.models import Permission, User
from documents.models import Document
from guardian.shortcuts import assign_perm
from paperless_ai.semantic_api import SemanticSearchUnavailable
from rest_framework import status
from rest_framework.test import APITestCase


class TestSemanticSearchApi(APITestCase):
    def setUp(self) -> None:
        self.user = User.objects.create_user(username="reader")
        self.user.user_permissions.add(
            Permission.objects.get(codename="view_document"),
        )
        self.client.force_authenticate(user=self.user)

    @staticmethod
    def _document(title: str, *, owner: User) -> Document:
        return Document.objects.create(
            title=title,
            content=f"OCR content for {title}",
            checksum=f"checksum-{title}",
            mime_type="application/pdf",
            owner=owner,
        )

    @patch("paperless_ai.semantic_api.query_semantic_index")
    def test_resolves_permissions_before_vector_retrieval(self, query_index) -> None:
        allowed = self._document("allowed", owner=self.user)
        other_user = User.objects.create_user(username="other")
        forbidden = self._document("forbidden", owner=other_user)
        query_index.return_value = [
            {
                "document_id": forbidden.id,
                "similarity": 0.99,
                "excerpt": "must be ignored",
            },
            {
                "document_id": allowed.id,
                "similarity": 0.8,
                "excerpt": "allowed excerpt",
            },
        ]

        response = self.client.post(
            "/api/documents/semantic_search/",
            {
                "query": "find the allowed evidence",
                "document_ids": [allowed.id, forbidden.id],
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        query_index.assert_called_once_with(
            "find the allowed evidence",
            limit=10,
            document_ids=[allowed.id],
        )
        self.assertEqual(
            [item["id"] for item in response.data["results"]],
            [allowed.id],
        )
        self.assertNotContains(response, "forbidden")

    @patch("paperless_ai.semantic_api.query_semantic_index")
    def test_object_grant_is_searchable(self, query_index) -> None:
        owner = User.objects.create_user(username="owner")
        document = self._document("granted", owner=owner)
        assign_perm("view_document", self.user, document)
        query_index.return_value = [
            {
                "document_id": document.id,
                "similarity": 0.75,
                "excerpt": "bounded excerpt",
            },
        ]

        response = self.client.post(
            "/api/documents/semantic_search/",
            {"query": "granted content"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["results"][0]["id"], document.id)

    @patch("paperless_ai.semantic_api.query_semantic_index")
    def test_service_identity_requires_explicit_scope(self, query_index) -> None:
        service = User.objects.create_user(username="odoo-integration")
        service.user_permissions.add(
            Permission.objects.get(codename="view_document"),
        )
        self.client.force_authenticate(user=service)

        response = self.client.post(
            "/api/documents/semantic_search/",
            {"query": "unscoped service query"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        query_index.assert_not_called()

    @patch("paperless_ai.semantic_api.query_semantic_index")
    def test_service_empty_scope_fails_closed(self, query_index) -> None:
        service = User.objects.create_user(username="odoo-integration")
        service.user_permissions.add(
            Permission.objects.get(codename="view_document"),
        )
        self.client.force_authenticate(user=service)

        response = self.client.post(
            "/api/documents/semantic_search/",
            {"query": "empty scope", "document_ids": []},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data, {"results": [], "warnings": []})
        query_index.assert_not_called()

    @patch("paperless_ai.semantic_api.query_semantic_index")
    def test_embedding_outage_is_bounded_and_structured(self, query_index) -> None:
        document = self._document("available root", owner=self.user)
        query_index.side_effect = SemanticSearchUnavailable(
            "The embedding service is unavailable.",
        )

        response = self.client.post(
            "/api/documents/semantic_search/",
            {"query": "meaning", "document_ids": [document.id]},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_503_SERVICE_UNAVAILABLE)
        self.assertEqual(response.data["results"], [])
        self.assertEqual(
            response.data["warnings"][0]["code"],
            "semantic_unavailable",
        )

    @patch("paperless_ai.semantic_api.query_semantic_index")
    def test_metadata_facets_narrow_before_retrieval(self, query_index) -> None:
        included = self._document("included", owner=self.user)
        excluded = self._document("excluded", owner=self.user)
        included.document_type_id = None
        included.save()
        query_index.return_value = []

        response = self.client.post(
            "/api/documents/semantic_search/",
            {
                "query": "facet",
                "document_ids": [included.id, excluded.id],
                "created_after": "2999-01-01",
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        query_index.assert_not_called()

    @patch("paperless_ai.semantic_api.query_semantic_index")
    def test_rejects_invalid_query_and_limit(self, query_index) -> None:
        response = self.client.post(
            "/api/documents/semantic_search/",
            {"query": " ", "limit": 51},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        query_index.assert_not_called()

    @patch("paperless_ai.semantic_api.query_semantic_index")
    def test_similar_document_is_authorized_and_excluded_from_candidates(
        self,
        query_index,
    ) -> None:
        source = self._document("source", owner=self.user)
        candidate = self._document("candidate", owner=self.user)
        query_index.return_value = [
            {
                "document_id": source.id,
                "similarity": 1.0,
                "excerpt": "source must be ignored",
            },
            {
                "document_id": candidate.id,
                "similarity": 0.8,
                "excerpt": "candidate excerpt",
            },
        ]

        response = self.client.post(
            "/api/documents/semantic_search/",
            {
                "document_id": source.id,
                "document_ids": [source.id, candidate.id],
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        query_index.assert_called_once_with(
            f"{source.title}\n{source.get_effective_content() or ''}",
            limit=10,
            document_ids=[candidate.id],
        )
        self.assertEqual(
            [item["id"] for item in response.data["results"]],
            [candidate.id],
        )
        self.assertNotContains(response, "source must be ignored")

    @patch("paperless_ai.semantic_api.query_semantic_index")
    def test_similar_document_guess_is_indistinguishable_and_never_retrieved(
        self,
        query_index,
    ) -> None:
        other = User.objects.create_user(username="private-owner")
        forbidden = self._document("private source", owner=other)

        for source_id in (forbidden.id, 999999):
            response = self.client.post(
                "/api/documents/semantic_search/",
                {"document_id": source_id, "document_ids": [source_id]},
                format="json",
            )
            self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
            self.assertEqual(
                response.data["detail"],
                "The source document is unavailable.",
            )
        query_index.assert_not_called()

    @patch("paperless_ai.semantic_api.query_semantic_index")
    def test_service_source_must_belong_to_explicit_scope(self, query_index) -> None:
        service = User.objects.create_user(username="odoo-integration")
        service.user_permissions.add(
            Permission.objects.get(codename="view_document"),
        )
        source = self._document("service source", owner=service)
        candidate = self._document("service candidate", owner=service)
        self.client.force_authenticate(user=service)

        response = self.client.post(
            "/api/documents/semantic_search/",
            {
                "document_id": source.id,
                "document_ids": [candidate.id],
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(
            response.data["detail"],
            "The source document is unavailable.",
        )
        query_index.assert_not_called()

    @patch("paperless_ai.semantic_api.query_semantic_index")
    def test_similar_empty_candidate_scope_does_not_open_index(self, query_index) -> None:
        source = self._document("only source", owner=self.user)

        response = self.client.post(
            "/api/documents/semantic_search/",
            {"document_id": source.id, "document_ids": [source.id]},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data, {"results": [], "warnings": []})
        query_index.assert_not_called()

    @patch("paperless_ai.semantic_api.query_semantic_index")
    def test_rejects_both_or_neither_query_modes(self, query_index) -> None:
        source = self._document("source", owner=self.user)
        for payload in (
            {},
            {"query": "meaning", "document_id": source.id},
        ):
            response = self.client.post(
                "/api/documents/semantic_search/",
                payload,
                format="json",
            )
            self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        query_index.assert_not_called()
