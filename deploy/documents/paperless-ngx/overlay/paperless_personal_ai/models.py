from django.conf import settings
from django.db import models


class PersonalAIProfile(models.Model):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="usl_personal_ai_profile",
    )
    metadata_suggestions_enabled = models.BooleanField(default=False)
    document_chat_enabled = models.BooleanField(default=False)
    provider = models.CharField(max_length=32, default="gemini", editable=False)
    model_name = models.CharField(max_length=64, default="gemini-3.7-flash")
    api_key_ciphertext = models.TextField(blank=True, default="", editable=False)
    api_key_nonce = models.CharField(max_length=32, blank=True, default="", editable=False)
    wrapped_dek = models.TextField(blank=True, default="", editable=False)
    wrapped_dek_nonce = models.CharField(
        max_length=32,
        blank=True,
        default="",
        editable=False,
    )
    master_key_id = models.CharField(
        max_length=64,
        blank=True,
        default="",
        editable=False,
    )
    master_key_version = models.PositiveIntegerField(default=0, editable=False)
    credential_revision = models.PositiveIntegerField(default=0, editable=False)
    last_tested_at = models.DateTimeField(null=True, blank=True, editable=False)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "personal AI profile"
        verbose_name_plural = "personal AI profiles"

    @property
    def has_api_key(self) -> bool:
        return bool(
            self.api_key_ciphertext
            and self.api_key_nonce
            and self.wrapped_dek
            and self.wrapped_dek_nonce
            and self.master_key_id
            and self.master_key_version
            and self.credential_revision,
        )
