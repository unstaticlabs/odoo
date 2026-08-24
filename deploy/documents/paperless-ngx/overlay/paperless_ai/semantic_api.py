import logging
import os
import re
from collections.abc import Iterable
from itertools import islice

from django.db.models import Q
from documents.models import Document
from documents.permissions import ViewDocumentsPermissions, permitted_document_ids
from drf_spectacular.utils import extend_schema
from paperless.config import AIConfig
from paperless_ai.db import db_connection_released
from paperless_ai.embedding import get_embedding_model
from paperless_ai.indexing import (
    _document_id_filters,
    llm_index_exists,
    queue_llm_index_update_if_needed,
    read_store,
    truncate_embedding_query,
)
from rest_framework import serializers
from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.status import HTTP_503_SERVICE_UNAVAILABLE
from rest_framework.views import APIView

logger = logging.getLogger("paperless_ai.semantic_api")

MAX_QUERY_LENGTH = 2048
MAX_RESULT_LIMIT = 50
MAX_SCOPE_LENGTH = 10000
MAX_EXCERPT_LENGTH = 500
_VECTOR_FILTER_BATCH_SIZE = 30000
_RETRIEVAL_OVERSAMPLE = 4
_WHITESPACE = re.compile(r"\s+")
_BLANK_QUERY_MESSAGE = "This field may not be blank."
_EMBEDDING_UNAVAILABLE_MESSAGE = "The embedding service is unavailable."
_INDEX_NOT_READY_MESSAGE = "The semantic index is not ready."
_INDEX_UNAVAILABLE_MESSAGE = "The semantic index is unavailable."
_SERVICE_SCOPE_REQUIRED_MESSAGE = (
    "Trusted service identities must supply a document scope."
)


class SemanticSearchUnavailable(RuntimeError):
    pass


class SemanticSearchRequestSerializer(serializers.Serializer):
    query = serializers.CharField(
        max_length=MAX_QUERY_LENGTH,
        trim_whitespace=True,
    )
    limit = serializers.IntegerField(
        default=10,
        min_value=1,
        max_value=MAX_RESULT_LIMIT,
    )
    document_ids = serializers.ListField(
        child=serializers.IntegerField(min_value=1),
        required=False,
        allow_empty=True,
        max_length=MAX_SCOPE_LENGTH,
    )
    tag_ids = serializers.ListField(
        child=serializers.IntegerField(min_value=1),
        required=False,
        allow_empty=False,
        max_length=100,
    )
    correspondent_ids = serializers.ListField(
        child=serializers.IntegerField(min_value=1),
        required=False,
        allow_empty=False,
        max_length=100,
    )
    document_type_ids = serializers.ListField(
        child=serializers.IntegerField(min_value=1),
        required=False,
        allow_empty=False,
        max_length=100,
    )
    created_after = serializers.DateField(required=False)
    created_before = serializers.DateField(required=False)

    def validate_query(self, value: str) -> str:
        if not value.strip():
            raise serializers.ValidationError(_BLANK_QUERY_MESSAGE)
        return value

    def validate(self, attrs):
        start = attrs.get("created_after")
        end = attrs.get("created_before")
        if start and end and start > end:
            raise serializers.ValidationError(
                {"created_before": "Must not precede created_after."},
            )
        return attrs


class SemanticSearchResultSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    rank = serializers.IntegerField()
    similarity = serializers.FloatField()
    excerpt = serializers.CharField()
    metadata = serializers.DictField()


class SemanticSearchResponseSerializer(serializers.Serializer):
    results = SemanticSearchResultSerializer(many=True)
    warnings = serializers.ListField(child=serializers.DictField())


def _batched(values: list[int], size: int) -> Iterable[list[int]]:
    iterator = iter(values)
    while batch := list(islice(iterator, size)):
        yield batch


def _bounded_excerpt(node) -> str:
    # Match upstream paperless_ai's lazy imports so normal Paperless startup
    # does not import the optional AI stack until a semantic request executes.
    from llama_index.core.schema import MetadataMode  # noqa: PLC0415

    text = node.get_content(metadata_mode=MetadataMode.NONE)
    normalized = _WHITESPACE.sub(" ", text).strip()
    if len(normalized) <= MAX_EXCERPT_LENGTH:
        return normalized
    return normalized[: MAX_EXCERPT_LENGTH - 1].rstrip() + "…"


def query_semantic_index(
    query: str,
    *,
    limit: int,
    document_ids: list[int],
) -> list[dict]:
    """Query Paperless's own vector store inside a mandatory document scope."""
    if not document_ids:
        return []
    if not llm_index_exists():
        queue_llm_index_update_if_needed(
            rebuild=False,
            reason="LLM index not found for semantic search.",
        )
        raise SemanticSearchUnavailable(_INDEX_NOT_READY_MESSAGE)

    from llama_index.core.vector_stores.types import VectorStoreQuery  # noqa: PLC0415

    config = AIConfig()
    query_text = truncate_embedding_query(
        query,
        chunk_size=config.llm_embedding_chunk_size,
    )
    try:
        embedding_model = get_embedding_model(config)
        with db_connection_released():
            query_embedding = embedding_model.get_query_embedding(query_text)
    except Exception as error:
        raise SemanticSearchUnavailable(_EMBEDDING_UNAVAILABLE_MESSAGE) from error

    allowed_ids = set(document_ids)
    candidates: list[dict] = []
    per_batch_limit = min(limit * _RETRIEVAL_OVERSAMPLE, 200)
    try:
        with read_store() as store, db_connection_released():
            for batch in _batched(document_ids, _VECTOR_FILTER_BATCH_SIZE):
                response = store.query(
                    VectorStoreQuery(
                        query_embedding=query_embedding,
                        similarity_top_k=per_batch_limit,
                        filters=_document_id_filters(batch),
                    ),
                )
                for node, similarity in zip(
                    response.nodes or [],
                    response.similarities or [],
                    strict=True,
                ):
                    raw_document_id = node.metadata.get("document_id")
                    try:
                        document_id = int(raw_document_id)
                    except (TypeError, ValueError):
                        continue
                    if document_id not in allowed_ids:
                        continue
                    candidates.append(
                        {
                            "document_id": document_id,
                            "similarity": float(similarity),
                            "excerpt": _bounded_excerpt(node),
                        },
                    )
    except SemanticSearchUnavailable:
        raise
    except Exception as error:
        raise SemanticSearchUnavailable(_INDEX_UNAVAILABLE_MESSAGE) from error

    candidates.sort(key=lambda hit: hit["similarity"], reverse=True)
    return candidates


def _scoped_service_usernames() -> set[str]:
    value = os.environ.get(
        "PAPERLESS_USL_SCOPED_SERVICE_USERS",
        "odoo-integration",
    )
    return {item.strip() for item in value.split(",") if item.strip()}


class SemanticSearchView(APIView):
    permission_classes = (IsAuthenticated, ViewDocumentsPermissions)

    @extend_schema(
        request=SemanticSearchRequestSerializer,
        responses={
            200: SemanticSearchResponseSerializer,
            400: None,
            403: None,
            503: SemanticSearchResponseSerializer,
        },
        description=(
            "Search Paperless's local vector index after resolving document "
            "permissions and optional metadata scope. Trusted service users "
            "must always supply document_ids."
        ),
    )
    def post(self, request):
        serializer = SemanticSearchRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        values = serializer.validated_data

        requested_ids = values.get("document_ids")
        if (
            request.user.username in _scoped_service_usernames()
            and requested_ids is None
        ):
            raise PermissionDenied(_SERVICE_SCOPE_REQUIRED_MESSAGE)

        roots = Document.objects.filter(
            root_document__isnull=True,
            id__in=permitted_document_ids(request.user),
        )
        if requested_ids is not None:
            roots = roots.filter(id__in=requested_ids)
        if tag_ids := values.get("tag_ids"):
            roots = roots.filter(tags__id__in=tag_ids)
        if correspondent_ids := values.get("correspondent_ids"):
            roots = roots.filter(correspondent_id__in=correspondent_ids)
        if document_type_ids := values.get("document_type_ids"):
            roots = roots.filter(document_type_id__in=document_type_ids)
        if created_after := values.get("created_after"):
            roots = roots.filter(created__gte=created_after)
        if created_before := values.get("created_before"):
            roots = roots.filter(created__lte=created_before)

        root_ids = list(roots.order_by().values_list("id", flat=True).distinct())
        if not root_ids:
            return Response({"results": [], "warnings": []})

        vector_to_root = {
            document_id: root_id or document_id
            for document_id, root_id in Document.objects.filter(
                Q(id__in=root_ids) | Q(root_document_id__in=root_ids),
            ).values_list("id", "root_document_id")
        }
        try:
            chunk_hits = query_semantic_index(
                values["query"],
                limit=values["limit"],
                document_ids=list(vector_to_root),
            )
        except SemanticSearchUnavailable as error:
            logger.warning("Semantic search is unavailable: %s", error)
            return Response(
                {
                    "results": [],
                    "warnings": [
                        {
                            "code": "semantic_unavailable",
                            "message": str(error),
                        },
                    ],
                },
                status=HTTP_503_SERVICE_UNAVAILABLE,
            )

        best_by_root: dict[int, dict] = {}
        for hit in chunk_hits:
            root_id = vector_to_root.get(hit["document_id"])
            if root_id is not None and root_id not in best_by_root:
                best_by_root[root_id] = hit

        ordered_root_ids = list(best_by_root)[: values["limit"]]
        documents = {
            document.id: document
            for document in Document.objects.filter(id__in=ordered_root_ids)
            .select_related("correspondent", "document_type")
            .prefetch_related("tags")
        }
        results = []
        for rank, root_id in enumerate(ordered_root_ids, start=1):
            document = documents.get(root_id)
            if document is None:
                continue
            hit = best_by_root[root_id]
            results.append(
                {
                    "id": root_id,
                    "rank": rank,
                    "similarity": round(hit["similarity"], 8),
                    "excerpt": hit["excerpt"],
                    "metadata": {
                        "title": document.title,
                        "created": (
                            document.created.isoformat() if document.created else None
                        ),
                        "correspondent": (
                            {
                                "id": document.correspondent_id,
                                "name": document.correspondent.name,
                            }
                            if document.correspondent_id
                            else None
                        ),
                        "document_type": (
                            {
                                "id": document.document_type_id,
                                "name": document.document_type.name,
                            }
                            if document.document_type_id
                            else None
                        ),
                        "tags": [
                            {"id": tag.id, "name": tag.name}
                            for tag in document.tags.all()
                        ],
                    },
                },
            )
        return Response({"results": results, "warnings": []})
