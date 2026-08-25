from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.test import override_settings
from rest_framework import status
from rest_framework.test import APITestCase

from paperless_personal_ai.models import PersonalAIProfile
from paperless_personal_ai.service import GEMINI_OPENAI_BASE_URL
from paperless_personal_ai.tests.base import PersonalAIKeyFileMixin


@override_settings(AI_ENABLED=True)
class TestPersonalAIProfileAPI(PersonalAIKeyFileMixin, APITestCase):
    endpoint = "/api/profile/personal_ai/"

    def setUp(self):
        super().setUp()
        self.user = self.mapped_user("profile-user")
        self.client.force_authenticate(self.user)

    def test_default_is_off_and_response_never_contains_credential_material(self):
        response = self.client.get(self.endpoint)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertFalse(response.data["metadata_suggestions_enabled"])
        self.assertFalse(response.data["document_chat_enabled"])
        self.assertFalse(response.data["api_key_configured"])
        serialized = str(response.data)
        for forbidden in (
            "ciphertext",
            "wrapped_dek",
            "master_key_id",
            "api_key_nonce",
        ):
            self.assertNotIn(forbidden, serialized)

    def test_user_can_save_replace_and_independently_enable_features(self):
        response = self.client.patch(
            self.endpoint,
            {
                "api_key": "first-personal-secret",
                "metadata_suggestions_enabled": True,
                "document_chat_enabled": False,
                "model_name": "gemini-3.7-flash",
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.data["api_key_configured"])
        self.assertTrue(response.data["metadata_suggestions_enabled"])
        self.assertFalse(response.data["document_chat_enabled"])
        self.assertNotContains(response, "first-personal-secret")
        profile = PersonalAIProfile.objects.get(user=self.user)
        first_ciphertext = profile.api_key_ciphertext

        response = self.client.patch(
            self.endpoint,
            {"api_key": "replacement-secret", "document_chat_enabled": True},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        profile.refresh_from_db()
        self.assertNotEqual(profile.api_key_ciphertext, first_ciphertext)
        self.assertEqual(profile.credential_revision, 2)
        self.assertNotContains(response, "replacement-secret")

    def test_feature_cannot_be_enabled_without_a_key(self):
        response = self.client.patch(
            self.endpoint,
            {"metadata_suggestions_enabled": True},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(
            PersonalAIProfile.objects.filter(
                user=self.user,
                metadata_suggestions_enabled=True,
            ).exists(),
        )

    def test_latest_alias_and_unapproved_model_are_rejected(self):
        for model in ("gemini-latest", "gemini-3.5-pro"):
            response = self.client.patch(
                self.endpoint,
                {"model_name": model},
                format="json",
            )
            self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_unmapped_inactive_and_service_identities_are_excluded(self):
        identities = [
            get_user_model().objects.create_user(username="unmapped"),
            self.mapped_user("inactive", active=False),
            get_user_model().objects.create_user(username="odoo-integration"),
        ]
        for user in identities:
            self.client.force_authenticate(user)
            response = self.client.get(self.endpoint)
            self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_admin_api_cannot_select_or_read_another_users_profile(self):
        other = self.mapped_user("other-user")
        self.client.force_authenticate(other)
        self.client.patch(
            self.endpoint,
            {"api_key": "other-users-secret"},
            format="json",
        )
        admin = self.mapped_user("mapped-admin")
        admin.is_staff = True
        admin.is_superuser = True
        admin.save()
        self.client.force_authenticate(admin)

        response = self.client.get(f"{self.endpoint}?user_id={other.pk}")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertFalse(response.data["api_key_configured"])
        self.assertNotContains(response, "other-users-secret")

    def test_disable_and_delete_take_effect_immediately(self):
        self.client.patch(
            self.endpoint,
            {
                "api_key": "deactivation-secret",
                "metadata_suggestions_enabled": True,
                "document_chat_enabled": True,
            },
            format="json",
        )

        disabled = self.client.post(f"{self.endpoint}disable/", {}, format="json")
        deleted = self.client.delete(self.endpoint)

        self.assertEqual(disabled.status_code, status.HTTP_200_OK)
        self.assertFalse(disabled.data["metadata_suggestions_enabled"])
        self.assertFalse(disabled.data["document_chat_enabled"])
        self.assertEqual(deleted.status_code, status.HTTP_200_OK)
        self.assertFalse(deleted.data["api_key_configured"])
        profile = PersonalAIProfile.objects.get(user=self.user)
        self.assertFalse(profile.api_key_ciphertext)
        self.assertFalse(profile.wrapped_dek)

    @patch("paperless_personal_ai.service.create_pinned_httpx_client")
    def test_connection_uses_only_fixed_models_endpoint(self, create_client):
        self.client.patch(
            self.endpoint,
            {"api_key": "connection-secret"},
            format="json",
        )
        response_mock = MagicMock()
        response_mock.raise_for_status.return_value = None
        response_mock.json.return_value = {
            "data": [{"id": "models/gemini-3.7-flash"}],
        }
        client = MagicMock()
        client.get.return_value = response_mock
        create_client.return_value.__enter__.return_value = client

        response = self.client.post(f"{self.endpoint}test/", {}, format="json")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        client.get.assert_called_once()
        self.assertEqual(client.get.call_args.args[0], f"{GEMINI_OPENAI_BASE_URL}models")
        self.assertNotContains(response, "connection-secret")
