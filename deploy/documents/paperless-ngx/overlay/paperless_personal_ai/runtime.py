import logging

from documents.models import Document
from documents.permissions import get_objects_for_user_owner_aware
from paperless_ai.ai_classifier import get_ai_document_classification
from paperless_ai.chat import stream_chat_with_documents

from paperless_personal_ai.service import (
    PersonalLLMConfig,
    assert_personal_ai_feature_authorized,
)

DOCUMENT_UNAVAILABLE = "The document is unavailable."
DOCUMENT_ACCESS_CHANGED = "Document access changed during chat."
GENERATION_UNAVAILABLE = "Gemini generation is unavailable."

logger = logging.getLogger("paperless_personal_ai.runtime")


class PersonalAIGenerationError(RuntimeError):
    """A deliberately credential-free personal generation failure."""


def generate_personal_metadata_suggestions(
    *,
    document: Document,
    user,
    client_config: PersonalLLMConfig,
    output_language: str | None,
) -> dict:
    current_user = assert_personal_ai_feature_authorized(
        user.pk,
        "metadata_suggestions",
    )
    visible = get_objects_for_user_owner_aware(
        current_user,
        "view_document",
        Document,
    ).filter(pk=document.pk)
    current_document = visible.first()
    if current_document is None:
        raise PermissionError(DOCUMENT_UNAVAILABLE)
    try:
        return get_ai_document_classification(
            current_document,
            current_user,
            output_language,
            client_config=client_config,
        )
    except Exception:  # noqa: BLE001 - sanitize third-party exceptions
        # Provider exceptions may retain request headers. Never log or chain them.
        logger.warning(
            "Personal metadata generation failed for user %s and document %s",
            current_user.pk,
            current_document.pk,
        )
        raise PersonalAIGenerationError(GENERATION_UNAVAILABLE) from None


def stream_personal_document_chat(
    *,
    query_str: str,
    user_id: int,
    document_id: int | None,
    output_language: str | None,
    client_config: PersonalLLMConfig,
):
    user = assert_personal_ai_feature_authorized(user_id, "document_chat")
    documents = get_objects_for_user_owner_aware(
        user,
        "view_document",
        Document,
    )
    if document_id is not None:
        documents = documents.filter(pk=document_id)
        if not documents.exists():
            raise PermissionError(DOCUMENT_UNAVAILABLE)

    def authorization_check(context_document_ids: list[int]) -> None:
        current_user = assert_personal_ai_feature_authorized(
            user_id,
            "document_chat",
        )
        if not context_document_ids:
            return
        visible_ids = set(
            get_objects_for_user_owner_aware(
                current_user,
                "view_document",
                Document,
            )
            .filter(pk__in=context_document_ids)
            .values_list("pk", flat=True),
        )
        if visible_ids != set(context_document_ids):
            raise PermissionError(DOCUMENT_ACCESS_CHANGED)

    yield from stream_chat_with_documents(
        query_str=query_str,
        documents=documents,
        output_language=output_language,
        client_config=client_config,
        authorization_check=authorization_check,
    )
