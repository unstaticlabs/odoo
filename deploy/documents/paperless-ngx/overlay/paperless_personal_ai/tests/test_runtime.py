from unittest.mock import patch

from django.contrib.auth.models import Permission
from django.test import override_settings
from documents.models import Document
from paperless_ai.client import AIClient
from rest_framework import status
from rest_framework.test import APITestCase

from paperless_personal_ai.runtime import (
    GENERATION_UNAVAILABLE,
    PersonalAIGenerationError,
    generate_personal_metadata_suggestions,
)
from paperless_personal_ai.service import (
    resolve_personal_llm_config,
    update_personal_ai_profile,
)
from paperless_personal_ai.tests.base import PersonalAIKeyFileMixin


@override_settings(AI_ENABLED=True)
class TestPersonalAIRuntimeBoundary(PersonalAIKeyFileMixin, APITestCase):
    def setUp(self):
        super().setUp()
        self.user = self.mapped_user("read-only-user")
        self.user.user_permissions.add(
            Permission.objects.get(codename="view_document"),
        )
        self.document = Document.objects.create(
            title="Allowed document",
            content="Allowed document body",
            checksum="personal-ai-allowed",
            mime_type="application/pdf",
            owner=self.user,
        )
        self.client.force_authenticate(self.user)

    def test_native_client_has_no_global_configuration_fallback(self):
        with self.assertRaises(TypeError):
            AIClient()

    @patch("paperless_personal_ai.runtime.get_ai_document_classification")
    def test_provider_exception_cannot_leak_credential(self, classify):
        secret = "provider-exception-secret"
        update_personal_ai_profile(
            self.user,
            api_key=secret,
            metadata_suggestions_enabled=True,
        )
        classify.side_effect = RuntimeError(f"Authorization: Bearer {secret}")
        config = resolve_personal_llm_config(
            self.user.pk,
            "metadata_suggestions",
        )

        with self.assertLogs("paperless_personal_ai.runtime", "WARNING") as logs:
            with self.assertRaisesRegex(
                PersonalAIGenerationError,
                GENERATION_UNAVAILABLE,
            ) as raised:
                generate_personal_metadata_suggestions(
                    document=self.document,
                    user=self.user,
                    client_config=config,
                    output_language="English",
                )

        self.assertNotIn(secret, str(raised.exception))
        self.assertIsNone(raised.exception.__cause__)
        self.assertNotIn(secret, "\n".join(logs.output))

    @patch("documents.views.generate_personal_metadata_suggestions")
    def test_read_only_owner_may_generate_but_not_apply_suggestions(self, generate):
        update_personal_ai_profile(
            self.user,
            api_key="suggestion-secret",
            metadata_suggestions_enabled=True,
        )
        generate.return_value = {
            "title": "Suggested title",
            "tags": [],
            "correspondents": [],
            "document_types": [],
            "storage_paths": [],
            "dates": [],
        }

        response = self.client.get(
            f"/api/documents/{self.document.pk}/ai_suggestions/",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["title"], "Suggested title")
        self.assertFalse(self.user.has_perm("documents.change_document"))

    @patch("documents.views.stream_personal_document_chat")
    def test_chat_rejects_unrelated_document_before_streaming(self, stream_chat):
        update_personal_ai_profile(
            self.user,
            api_key="chat-secret",
            document_chat_enabled=True,
        )
        other = self.mapped_user("private-owner")
        private_document = Document.objects.create(
            title="Private document",
            content="Private body",
            checksum="personal-ai-private",
            mime_type="application/pdf",
            owner=other,
        )

        response = self.client.post(
            "/api/documents/chat/",
            {"q": "Tell me", "document_id": private_document.pk},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        stream_chat.assert_not_called()

    @patch("documents.views.stream_personal_document_chat")
    def test_chat_passes_only_initiating_user_and_authorized_document(self, stream_chat):
        update_personal_ai_profile(
            self.user,
            api_key="chat-secret",
            document_chat_enabled=True,
        )
        stream_chat.return_value = iter(("answer",))

        response = self.client.post(
            "/api/documents/chat/",
            {"q": "Tell me", "document_id": self.document.pk},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(b"".join(response.streaming_content), b"answer")
        self.assertEqual(stream_chat.call_args.kwargs["user_id"], self.user.pk)
        self.assertEqual(
            stream_chat.call_args.kwargs["document_id"],
            self.document.pk,
        )
