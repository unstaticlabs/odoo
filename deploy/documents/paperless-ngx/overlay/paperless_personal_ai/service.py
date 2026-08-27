import logging
import os
from dataclasses import dataclass, field
from typing import Literal

import httpx
from allauth.socialaccount.models import SocialAccount
from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils import timezone
from paperless.models import LLMBackend
from paperless.network import create_pinned_httpx_client

from paperless_personal_ai.crypto import (
    decrypt_api_key,
    encrypt_api_key,
    load_master_key_ring,
    rewrap_dek_if_needed,
)
from paperless_personal_ai.models import PersonalAIProfile

logger = logging.getLogger("paperless_personal_ai")

PROVIDER = "gemini"
GEMINI_OPENAI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"
APPROVED_MODELS = ("gemini-3.7-flash", "gemini-3.6-flash")
DEFAULT_MODEL = APPROVED_MODELS[0]
DEFAULT_REQUEST_TIMEOUT = 120
PRIVACY_DISCLOSURE = (
    "When you enable a personal Gemini feature, the relevant document text, "
    "filename, metadata, and your prompt are sent to Google Gemini under your "
    "own account. Local upload, OCR, indexing, search, and MCP operations never "
    "use Gemini. Document content is untrusted data and cannot authorize actions."
)
Feature = Literal["metadata_suggestions", "document_chat"]
ELIGIBILITY_MESSAGE = (
    "Personal AI is available only to an active mapped internal user."
)
MODEL_SELECTION_MESSAGE = "Select an approved pinned Gemini model."
INVALID_API_KEY_MESSAGE = "Enter a valid Gemini API key."
KEY_BEFORE_FEATURE_MESSAGE = (
    "Save a personal Gemini API key before enabling a feature."
)
PERSONAL_AI_NOT_ENABLED_MESSAGE = "Personal AI is not enabled."
FEATURE_NOT_ENABLED_MESSAGE = "This personal AI feature is not enabled."
MODEL_NOT_APPROVED_MESSAGE = "The configured Gemini model is not approved."
SAVE_KEY_FIRST_MESSAGE = "Save a Gemini API key first."
MODEL_UNAVAILABLE_MESSAGE = (
    "The configured Gemini model is unavailable for this credential."
)
CONNECTION_FAILED_MESSAGE = "Gemini rejected the connection test or is unavailable."


class PersonalAIEligibilityError(PermissionError):
    pass


class PersonalAIDisabledError(PermissionError):
    pass


class PersonalAIConnectionError(RuntimeError):
    pass


@dataclass(frozen=True)
class PersonalLLMConfig:
    llm_backend: str = LLMBackend.OPENAI_LIKE
    llm_model: str = DEFAULT_MODEL
    llm_api_key: str = field(default="", repr=False)
    llm_endpoint: str = GEMINI_OPENAI_BASE_URL
    llm_request_timeout: int = DEFAULT_REQUEST_TIMEOUT
    llm_allow_internal_endpoints: bool = False


def _base_group_name() -> str:
    return os.environ.get(
        "PAPERLESS_SSO_BASE_GROUP",
        "USL Odoo document users",
    ).strip()


def is_eligible_internal_user(user) -> bool:
    if (
        user is None
        or not getattr(user, "is_authenticated", False)
        or not user.is_active
        or not _base_group_name()
        or not user.groups.filter(name=_base_group_name()).exists()
    ):
        return False
    return SocialAccount.objects.filter(
        user_id=user.pk,
        provider="pocket-id",
        extra_data__usl_odoo_managed=True,
    ).exists()


def require_eligible_internal_user(user) -> None:
    if not is_eligible_internal_user(user):
        raise PersonalAIEligibilityError(ELIGIBILITY_MESSAGE)


def safe_profile_payload(user) -> dict:
    require_eligible_internal_user(user)
    profile, _created = PersonalAIProfile.objects.get_or_create(
        user=user,
        defaults={"model_name": DEFAULT_MODEL},
    )
    return {
        "provider": PROVIDER,
        "approved_models": list(APPROVED_MODELS),
        "model_name": profile.model_name,
        "metadata_suggestions_enabled": profile.metadata_suggestions_enabled,
        "document_chat_enabled": profile.document_chat_enabled,
        "api_key_configured": profile.has_api_key,
        "credential_revision": profile.credential_revision,
        "last_tested_at": profile.last_tested_at,
        "privacy_disclosure": PRIVACY_DISCLOSURE,
    }


def update_personal_ai_profile(
    user,
    *,
    model_name: str | None = None,
    metadata_suggestions_enabled: bool | None = None,
    document_chat_enabled: bool | None = None,
    api_key: str | None = None,
) -> dict:
    require_eligible_internal_user(user)
    with transaction.atomic():
        profile, _created = PersonalAIProfile.objects.select_for_update().get_or_create(
            user=user,
            defaults={"model_name": DEFAULT_MODEL},
        )
        if model_name is not None:
            if model_name not in APPROVED_MODELS or "latest" in model_name.casefold():
                raise ValueError(MODEL_SELECTION_MESSAGE)
            profile.model_name = model_name
        if api_key is not None:
            normalized_key = api_key.strip()
            if (
                not normalized_key
                or len(normalized_key) > 512
                or any(character.isspace() for character in normalized_key)
            ):
                raise ValueError(INVALID_API_KEY_MESSAGE)
            revision = profile.credential_revision + 1
            encrypted = encrypt_api_key(
                user_id=user.pk,
                revision=revision,
                api_key=normalized_key,
            )
            profile.api_key_ciphertext = encrypted.ciphertext
            profile.api_key_nonce = encrypted.nonce
            profile.wrapped_dek = encrypted.wrapped_dek
            profile.wrapped_dek_nonce = encrypted.wrapped_dek_nonce
            profile.master_key_id = encrypted.master_key_id
            profile.master_key_version = encrypted.master_key_version
            profile.credential_revision = revision
            profile.last_tested_at = None
        if metadata_suggestions_enabled is not None:
            profile.metadata_suggestions_enabled = metadata_suggestions_enabled
        if document_chat_enabled is not None:
            profile.document_chat_enabled = document_chat_enabled
        if (
            profile.metadata_suggestions_enabled or profile.document_chat_enabled
        ) and not profile.has_api_key:
            raise ValueError(KEY_BEFORE_FEATURE_MESSAGE)
        profile.provider = PROVIDER
        profile.save()
    return safe_profile_payload(user)


def disable_personal_ai(user) -> dict:
    require_eligible_internal_user(user)
    PersonalAIProfile.objects.filter(user=user).update(
        metadata_suggestions_enabled=False,
        document_chat_enabled=False,
    )
    return safe_profile_payload(user)


def delete_personal_ai_credential(user) -> dict:
    require_eligible_internal_user(user)
    with transaction.atomic():
        profile, _created = PersonalAIProfile.objects.select_for_update().get_or_create(
            user=user,
            defaults={"model_name": DEFAULT_MODEL},
        )
        profile.metadata_suggestions_enabled = False
        profile.document_chat_enabled = False
        profile.api_key_ciphertext = ""
        profile.api_key_nonce = ""
        profile.wrapped_dek = ""
        profile.wrapped_dek_nonce = ""
        profile.master_key_id = ""
        profile.master_key_version = 0
        profile.credential_revision += 1
        profile.last_tested_at = None
        profile.save()
    return safe_profile_payload(user)


def resolve_personal_llm_config(user_id: int, feature: Feature) -> PersonalLLMConfig:
    user = get_user_model().objects.filter(pk=user_id).first()
    require_eligible_internal_user(user)
    try:
        profile = PersonalAIProfile.objects.get(user_id=user_id)
    except PersonalAIProfile.DoesNotExist as exc:
        raise PersonalAIDisabledError(PERSONAL_AI_NOT_ENABLED_MESSAGE) from exc
    enabled = {
        "metadata_suggestions": profile.metadata_suggestions_enabled,
        "document_chat": profile.document_chat_enabled,
    }[feature]
    if not enabled or not profile.has_api_key:
        raise PersonalAIDisabledError(FEATURE_NOT_ENABLED_MESSAGE)
    if profile.model_name not in APPROVED_MODELS:
        raise PersonalAIDisabledError(MODEL_NOT_APPROVED_MESSAGE)
    ring = load_master_key_ring()
    api_key = decrypt_api_key(profile, key_ring=ring)
    rewrap_dek_if_needed(profile, key_ring=ring)
    return PersonalLLMConfig(
        llm_model=profile.model_name,
        llm_api_key=api_key,
    )


def assert_personal_ai_feature_authorized(user_id: int, feature: Feature):
    user = get_user_model().objects.filter(pk=user_id).first()
    require_eligible_internal_user(user)
    try:
        profile = PersonalAIProfile.objects.only(
            "metadata_suggestions_enabled",
            "document_chat_enabled",
            "api_key_ciphertext",
            "api_key_nonce",
            "wrapped_dek",
            "wrapped_dek_nonce",
            "master_key_id",
            "master_key_version",
            "credential_revision",
            "model_name",
        ).get(user_id=user_id)
    except PersonalAIProfile.DoesNotExist as exc:
        raise PersonalAIDisabledError(PERSONAL_AI_NOT_ENABLED_MESSAGE) from exc
    enabled = {
        "metadata_suggestions": profile.metadata_suggestions_enabled,
        "document_chat": profile.document_chat_enabled,
    }[feature]
    if not enabled or not profile.has_api_key or profile.model_name not in APPROVED_MODELS:
        raise PersonalAIDisabledError(FEATURE_NOT_ENABLED_MESSAGE)
    return user


def personal_ai_cache_identity(user_id: int, output_language: str | None) -> str:
    user = assert_personal_ai_feature_authorized(user_id, "metadata_suggestions")
    profile = PersonalAIProfile.objects.get(user=user)
    return ":".join(
        (
            "usl-personal-gemini",
            str(user_id),
            profile.model_name,
            str(profile.credential_revision),
            output_language or "default",
        ),
    )


def test_personal_ai_connection(user) -> dict:
    require_eligible_internal_user(user)
    try:
        profile = PersonalAIProfile.objects.get(user=user)
        ring = load_master_key_ring()
        api_key = decrypt_api_key(profile, key_ring=ring)
        rewrap_dek_if_needed(profile, key_ring=ring)
    except PersonalAIProfile.DoesNotExist as exc:
        raise PersonalAIConnectionError(SAVE_KEY_FIRST_MESSAGE) from exc
    try:
        with create_pinned_httpx_client(
            GEMINI_OPENAI_BASE_URL,
            allow_internal=False,
            timeout=20,
            follow_redirects=False,
        ) as client:
            response = client.get(
                f"{GEMINI_OPENAI_BASE_URL}models",
                headers={"Authorization": f"Bearer {api_key}"},
            )
            response.raise_for_status()
            model_ids = {
                str(item.get("id", "")).removeprefix("models/")
                for item in response.json().get("data", [])
                if isinstance(item, dict)
            }
    except (AttributeError, httpx.HTTPError, TypeError, ValueError):
        logger.warning(
            "Personal Gemini connection test failed for user %s",
            user.pk,
        )
        raise PersonalAIConnectionError(CONNECTION_FAILED_MESSAGE) from None
    if profile.model_name not in model_ids:
        raise PersonalAIConnectionError(MODEL_UNAVAILABLE_MESSAGE)
    profile.last_tested_at = timezone.now()
    profile.save(update_fields=["last_tested_at", "updated_at"])
    return safe_profile_payload(user)
