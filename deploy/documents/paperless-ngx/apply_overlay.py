"""Apply the hash-guarded USL overlay to one exact Paperless source tree."""

import sys
from hashlib import sha256
from pathlib import Path

SOURCE_ROOT = Path(
    sys.argv[1] if len(sys.argv) > 1 else "/usr/src/paperless/src",
).resolve()
MISMATCH_MESSAGE = "Paperless v3.0.5 source is incompatible with the USL overlay"


def patch_file(
    relative_path: str,
    expected_sha256: str,
    replacements: tuple[tuple[str, str], ...],
) -> None:
    path = SOURCE_ROOT / relative_path
    source_bytes = path.read_bytes()
    actual_hash = sha256(source_bytes).hexdigest()
    if actual_hash != expected_sha256:
        raise RuntimeError(
            f"{MISMATCH_MESSAGE}: {relative_path} "
            f"({actual_hash} != {expected_sha256})",
        )
    source = source_bytes.decode("utf-8")
    for anchor, replacement in replacements:
        if source.count(anchor) != 1:
            raise RuntimeError(f"{MISMATCH_MESSAGE}: {relative_path} anchor")
        source = source.replace(anchor, replacement, 1)
    path.write_text(source, encoding="utf-8")


patch_file(
    "paperless/urls.py",
    "d46ce3f52652fc32f6e4e2e1897cec0000fca0ad7b2bb3b41739902b564afc35",
    (
        (
            "from paperless.views import UserViewSet\n",
            (
                "from paperless.views import UserViewSet\n"
                "from paperless_ai.semantic_api import SemanticSearchView\n"
            ),
        ),
        (
            """                            re_path(
                                "^selection_data/",
                                SelectionDataView.as_view(),
                                name="selection_data",
                            ),
""",
            """                            re_path(
                                "^selection_data/",
                                SelectionDataView.as_view(),
                                name="selection_data",
                            ),
                            re_path(
                                "^semantic_search/",
                                SemanticSearchView.as_view(),
                                name="semantic_search",
                            ),
""",
        ),
        (
            """                            path(
                                "generate_auth_token/",
                                GenerateAuthTokenView.as_view(),
                            ),
""",
            """                            path(
                                "generate_auth_token/",
                                GenerateAuthTokenView.as_view(),
                            ),
                            path(
                                "personal_ai/",
                                include("paperless_personal_ai.urls"),
                            ),
""",
        ),
    ),
)

patch_file(
    "paperless_ai/client.py",
    "253a058a3d91bb2a7f7ac388590202f988f5f9fea70a17ffc6db1ec425c91351",
    (
        ("from paperless.config import AIConfig\n", ""),
        (
            """    def __init__(self) -> None:
        self.settings = AIConfig()
        self.llm = self.get_llm()
""",
            """    def __init__(self, settings) -> None:
        # Generative configuration is always resolved for the initiating user.
        # There is deliberately no fallback to Paperless's native global config.
        self.settings = settings
        self.llm = self.get_llm()
""",
        ),
    ),
)

patch_file(
    "paperless_ai/ai_classifier.py",
    "cd0e3838e94df191b0badf3e6d432b47967477e8c1ada548785148f4c67b03b4",
    (
        (
            """def get_ai_document_classification(
    document: Document,
    user: User | None = None,
    output_language: str | None = None,
) -> dict:
""",
            """def get_ai_document_classification(
    document: Document,
    user: User | None = None,
    output_language: str | None = None,
    *,
    client_config,
) -> dict:
""",
        ),
        ("    client = AIClient()\n", "    client = AIClient(client_config)\n"),
    ),
)

patch_file(
    "paperless_ai/chat.py",
    "5f2b90a5171339055d23af13b69fe2acef8a9ca7b4e1105deaee1e16eb9fb975",
    (
        (
            """def stream_chat_with_documents(
    query_str: str,
    documents: list[Document],
    output_language: str | None = None,
):
""",
            """def stream_chat_with_documents(
    query_str: str,
    documents: list[Document],
    output_language: str | None = None,
    *,
    client_config,
    authorization_check,
):
""",
        ),
        (
            """            output_language=output_language,
        )
""",
            """            output_language=output_language,
            client_config=client_config,
            authorization_check=authorization_check,
        )
""",
        ),
        (
            """def _stream_chat_with_documents(
    query_str: str,
    documents: list[Document],
    output_language: str | None = None,
):
""",
            """def _stream_chat_with_documents(
    query_str: str,
    documents: list[Document],
    output_language: str | None = None,
    *,
    client_config,
    authorization_check,
):
""",
        ),
        (
            """    config = AIConfig()
    filters = _document_id_filters(str(doc.pk) for doc in documents)
""",
            """    config = AIConfig()
    document_ids = [doc.pk for doc in documents]
    authorization_check(document_ids)
    filters = _document_id_filters(str(document_id) for document_id in document_ids)
""",
        ),
        (
            """        client = AIClient()

        references = _get_document_references(documents, top_nodes)
""",
            """        context_document_ids = sorted(
            {
                int(node.metadata["document_id"])
                for node in top_nodes
                if str(node.metadata.get("document_id", "")).isdigit()
                and int(node.metadata["document_id"]) in document_ids
            },
        )
        authorization_check(context_document_ids)
        client = AIClient(client_config)

        references = _get_document_references(documents, top_nodes)
""",
        ),
        (
            """            for chunk in response_stream.response_gen:
                yield chunk
                sys.stdout.flush()
""",
            """            for chunk in response_stream.response_gen:
                authorization_check(context_document_ids)
                yield chunk
                sys.stdout.flush()
""",
        ),
        (
            """    except Exception as e:
        logger.exception("Failed to stream document chat response: %s", e)
""",
            """    except Exception:
        # Provider exceptions may retain request headers. Never log their
        # message or traceback because both may expose the personal API key.
        logger.warning("Personal document chat generation failed.")
""",
        ),
    ),
)

patch_file(
    "documents/views.py",
    "6a514ba949b7034804f4846ca6e0b35bb75567d41fc793f33655a6564054feaf",
    (
        (
            """from paperless_ai.ai_classifier import get_ai_document_classification
from paperless_ai.chat import stream_chat_with_documents
from paperless_ai.exceptions import LLMTimeoutError
""",
            """from paperless_ai.exceptions import LLMTimeoutError
from paperless_personal_ai.crypto import PersonalAIKeyServiceError
from paperless_personal_ai.runtime import generate_personal_metadata_suggestions
from paperless_personal_ai.runtime import PersonalAIGenerationError
from paperless_personal_ai.runtime import stream_personal_document_chat
from paperless_personal_ai.service import PersonalAIDisabledError
from paperless_personal_ai.service import PersonalAIEligibilityError
from paperless_personal_ai.service import personal_ai_cache_identity
from paperless_personal_ai.service import resolve_personal_llm_config
""",
        ),
        (
            """        doc = get_object_or_404(
            Document.objects.select_related("owner").prefetch_related("versions"),
            pk=pk,
        )
        if request.user is not None and not has_perms_owner_aware(
            request.user,
            "change_document",
            doc,
        ):
            return HttpResponseForbidden("Insufficient permissions")

        ai_config = AIConfig()
        if not ai_config.ai_enabled:
            return HttpResponseBadRequest("AI is required for this feature")

        output_language = _get_llm_output_language(ai_config=ai_config, request=request)
        llm_cache_backend = ":".join(
            part
            for part in (
                ai_config.llm_backend,
                ai_config.llm_model,
                ai_config.llm_endpoint,
                output_language,
            )
            if part
        )
""",
            """        visible_documents = get_objects_for_user_owner_aware(
            request.user,
            "view_document",
            Document,
        ).select_related("owner").prefetch_related("versions")
        doc = get_object_or_404(visible_documents, pk=pk)

        ai_config = AIConfig()
        if not ai_config.ai_enabled:
            return HttpResponseBadRequest("AI is required for this feature")

        output_language = _get_llm_output_language(ai_config=ai_config, request=request)
        try:
            client_config = resolve_personal_llm_config(
                request.user.pk,
                "metadata_suggestions",
            )
            llm_cache_backend = personal_ai_cache_identity(
                request.user.pk,
                output_language,
            )
        except (PersonalAIEligibilityError, PersonalAIDisabledError) as exc:
            return Response(
                {"ai": [str(exc)]},
                status=status.HTTP_403_FORBIDDEN,
            )
        except PersonalAIKeyServiceError:
            return Response(
                {"ai": [_('The personal AI key service is unavailable.')]},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
""",
        ),
        (
            """            llm_suggestions = get_ai_document_classification(
                doc,
                request.user,
                output_language,
            )
""",
            """            llm_suggestions = generate_personal_metadata_suggestions(
                document=doc,
                user=request.user,
                client_config=client_config,
                output_language=output_language,
            )
""",
        ),
        (
            """        except ValueError as exc:
            logger.exception(
""",
            """        except PersonalAIGenerationError:
            return Response(
                {"ai": [_('Gemini generation is unavailable.')]},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        except ValueError as exc:
            logger.exception(
""",
        ),
        (
            """        doc_id = serializer.validated_data.get("document_id")

        if doc_id:
            try:
                document = Document.objects.get(id=doc_id)
            except Document.DoesNotExist:
                return HttpResponseBadRequest("Document not found")

            if not has_perms_owner_aware(request.user, "view_document", document):
                return HttpResponseForbidden("Insufficient permissions")

            documents = [document]
        else:
            documents = get_objects_for_user_owner_aware(
                request.user,
                "view_document",
                Document,
            )

        output_language = _get_llm_output_language(ai_config=ai_config, request=request)

        response = StreamingHttpResponse(
            stream_chat_with_documents(
                query_str=question,
                documents=documents,
                output_language=output_language,
            ),
            content_type="text/event-stream",
        )
""",
            """        doc_id = serializer.validated_data.get("document_id")
        if doc_id and not get_objects_for_user_owner_aware(
            request.user,
            "view_document",
            Document,
        ).filter(pk=doc_id).exists():
            return HttpResponseForbidden("The document is unavailable")

        try:
            client_config = resolve_personal_llm_config(
                request.user.pk,
                "document_chat",
            )
        except (PersonalAIEligibilityError, PersonalAIDisabledError) as exc:
            return Response(
                {"detail": str(exc)},
                status=status.HTTP_403_FORBIDDEN,
            )
        except PersonalAIKeyServiceError:
            return Response(
                {"detail": _("The personal AI key service is unavailable.")},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        output_language = _get_llm_output_language(ai_config=ai_config, request=request)

        response = StreamingHttpResponse(
            stream_personal_document_chat(
                query_str=question,
                user_id=request.user.pk,
                document_id=doc_id,
                output_language=output_language,
                client_config=client_config,
            ),
            content_type="text/event-stream",
        )
""",
        ),
    ),
)

patch_file(
    "paperless/serialisers.py",
    "37cd870e3358bedef71be82ef0621457d92c221cb0794aa13b2af5a13b27f4e5",
    (
        (
            """    llm_api_key = ObfuscatedPasswordField(
        required=False,
        allow_null=True,
    )
""",
            """    llm_api_key = ObfuscatedPasswordField(read_only=True)
""",
        ),
        (
            """    class Meta:
        model = ApplicationConfiguration
        fields = "__all__"
""",
            """    class Meta:
        model = ApplicationConfiguration
        fields = "__all__"
        read_only_fields = (
            "llm_backend",
            "llm_model",
            "llm_api_key",
            "llm_endpoint",
            "llm_output_language",
        )
""",
        ),
    ),
)
