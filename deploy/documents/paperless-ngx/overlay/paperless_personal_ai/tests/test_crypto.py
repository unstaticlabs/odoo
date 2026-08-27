from django.test import TestCase

from paperless_personal_ai.crypto import PersonalAIKeyServiceError, decrypt_api_key
from paperless_personal_ai.models import PersonalAIProfile
from paperless_personal_ai.service import (
    resolve_personal_llm_config,
    update_personal_ai_profile,
)
from paperless_personal_ai.tests.base import PersonalAIKeyFileMixin


class TestPersonalAIEncryption(PersonalAIKeyFileMixin, TestCase):
    def test_per_user_envelope_round_trip_and_randomized_ciphertext(self):
        first_user = self.mapped_user("first")
        second_user = self.mapped_user("second")
        for user in (first_user, second_user):
            update_personal_ai_profile(
                user,
                api_key="same-secret-value",
                metadata_suggestions_enabled=True,
            )

        first = PersonalAIProfile.objects.get(user=first_user)
        second = PersonalAIProfile.objects.get(user=second_user)
        self.assertNotEqual(first.api_key_ciphertext, second.api_key_ciphertext)
        self.assertNotIn("same-secret-value", first.api_key_ciphertext)
        self.assertEqual(decrypt_api_key(first), "same-secret-value")
        self.assertEqual(decrypt_api_key(second), "same-secret-value")

    def test_ciphertext_is_bound_to_user_and_revision(self):
        first_user = self.mapped_user("bound-first")
        second_user = self.mapped_user("bound-second")
        update_personal_ai_profile(first_user, api_key="bound-secret")
        first = PersonalAIProfile.objects.get(user=first_user)
        first.user = second_user
        with self.assertRaises(PersonalAIKeyServiceError):
            decrypt_api_key(first)

    def test_old_master_key_is_rewrapped_to_active_version_on_resolution(self):
        user = self.mapped_user("rotation")
        update_personal_ai_profile(
            user,
            api_key="rotation-secret",
            document_chat_enabled=True,
        )
        profile = PersonalAIProfile.objects.get(user=user)
        self.assertEqual(profile.master_key_version, 1)
        self.write_master_keys(active_version=2, versions=(1, 2))

        config = resolve_personal_llm_config(user.pk, "document_chat")

        self.assertEqual(config.llm_api_key, "rotation-secret")
        profile.refresh_from_db()
        self.assertEqual(profile.master_key_version, 2)
        self.assertEqual(decrypt_api_key(profile), "rotation-secret")
