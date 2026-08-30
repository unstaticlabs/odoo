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
                "from paperless_ai.semantic_api import ScopedSearchView\n"
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
                                "^scoped_search/",
                                ScopedSearchView.as_view(),
                                name="scoped_search",
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
    "documents/search/_schema.py",
    "3acf00eecd2bf0c4991f0453bf0ec6219af03871b00462fad289277972d92479",
    (
        ("SCHEMA_VERSION: Final[int] = 1", "SCHEMA_VERSION: Final[int] = 2"),
        (
            '''    sb.add_text_field(
        "simple_content",
        stored=False,
        tokenizer_name="simple_search_analyzer",
    )
''',
            '''    sb.add_text_field(
        "simple_content",
        stored=False,
        tokenizer_name="simple_search_analyzer",
    )
    sb.add_text_field(
        "simple_metadata",
        stored=False,
        tokenizer_name="simple_search_analyzer",
    )
    sb.add_text_field(
        "simple_custom_fields",
        stored=False,
        tokenizer_name="simple_search_analyzer",
    )
''',
        ),
    ),
)

patch_file(
    "documents/search/_backend.py",
    "ac9664e79a96530dad5b6e79290db2a5f29bb591208c5acd387fca402dc50da3",
    (
        (
            '''        # Original filename - only add if not None/empty
        if document.original_filename:
            doc.add_text("original_filename", document.original_filename)
''',
            '''        # Plain-text companion fields keep broad scoped search in one
        # native Tantivy query instead of one API request per custom field.
        metadata_text: list[str] = []
        if document.original_filename:
            doc.add_text("original_filename", document.original_filename)
            metadata_text.append(document.original_filename)
''',
        ),
        (
            '''            doc.add_text("correspondent_sort", document.correspondent.name)
''',
            '''            doc.add_text("correspondent_sort", document.correspondent.name)
            metadata_text.append(document.correspondent.name)
''',
        ),
        (
            '''            doc.add_text("type_sort", document.document_type.name)
''',
            '''            doc.add_text("type_sort", document.document_type.name)
            metadata_text.append(document.document_type.name)
''',
        ),
        (
            '''            doc.add_text("storage_path", document.storage_path.name)
''',
            '''            doc.add_text("storage_path", document.storage_path.name)
            metadata_text.append(document.storage_path.name)
''',
        ),
        (
            '''            tag_names.append(tag.name)
''',
            '''            tag_names.append(tag.name)
            metadata_text.append(tag.name)
''',
        ),
        (
            '''        # Custom fields — JSON for structured queries (custom_fields.name:x, custom_fields.value:y),
        # companion text field for default full-text search.
        for cfi in document.custom_fields.all():
''',
            '''        if metadata_text:
            doc.add_text("simple_metadata", " ".join(metadata_text))

        # Custom fields retain their structured JSON representation and also
        # share one plain-text field for the bounded scoped-search endpoint.
        custom_field_text: list[str] = []
        for cfi in document.custom_fields.all():
''',
        ),
        (
            '''            doc.add_json(
                "custom_fields",
                {
                    "name": cfi.field.name,
                    "value": search_value,
                },
            )

        # Dates
''',
            '''            doc.add_json(
                "custom_fields",
                {
                    "name": cfi.field.name,
                    "value": search_value,
                },
            )
            custom_field_text.extend((cfi.field.name, str(search_value)))
        if custom_field_text:
            doc.add_text("simple_custom_fields", " ".join(custom_field_text))

        # Dates
''',
        ),
    ),
)

patch_file(
    "documents/tasks.py",
    "a397d85e482531a4357da017d7b401f0e2566acdb1d4de575ce5e196c46b30af",
    (
        (
            "import logging\n",
            "import logging\nimport os\n",
        ),
        (
            "def bulk_update_documents(document_ids) -> None:\n",
            "def bulk_update_documents(document_ids, *, skip_llm_index=False) -> None:\n",
        ),
        (
            (
                "    if ai_config.llm_index_enabled:\n"
                "        update_llm_index(\n"
                "            rebuild=False,\n"
                "            document_ids=document_ids,\n"
                "        )\n"
            ),
            (
                "    if ai_config.llm_index_enabled and not skip_llm_index:\n"
                "        update_llm_index(\n"
                "            rebuild=False,\n"
                "            document_ids=document_ids,\n"
                "        )\n"
            ),
        ),
        (
            '''@shared_task
def update_document_in_llm_index(document) -> None:
    llm_index_add_or_update_document(document)
''',
            '''@shared_task
def update_document_in_llm_index(document) -> None:
    # A migration can be resumed with incremental tasks already in Redis.
    # The worker-side guard makes those tasks harmless while the governed
    # final bulk update remains the single source of vector-index truth.
    if os.getenv(
        "PAPERLESS_USL_DEFER_SEMANTIC_INDEX",
        "false",
    ).lower() == "true":
        logger.info(
            "Deferring queued semantic indexing for document %s until the "
            "governed bulk update.",
            document,
        )
        return
    llm_index_add_or_update_document(document)
''',
        ),
    ),
)

patch_file(
    "documents/signals/handlers.py",
    "1a593a6b81a2a2111ace6dd6527418e88da7761c9a0a9ce282c3322e0f496692",
    (
        (
            "import logging\n",
            "import logging\nimport os\n",
        ),
        (
            '''    if kwargs.get("skip_ai_index"):
        return
    ai_config = AIConfig()
''',
            '''    if kwargs.get("skip_ai_index"):
        return
    if os.getenv(
        "PAPERLESS_USL_DEFER_SEMANTIC_INDEX",
        "false",
    ).lower() == "true":
        logger.info(
            "Deferring semantic indexing for document %s until the governed "
            "bulk update.",
            document.pk,
        )
        return
    ai_config = AIConfig()
''',
        ),
    ),
)

patch_file(
    "documents/bulk_edit.py",
    "f233105bb95c8ad406b006705c55218011f47fd3cfbe263d10e07064537e6058",
    (
        (
            '''    bulk_update_documents.apply_async(
        kwargs={"document_ids": affected_docs},
        headers={"trigger_source": PaperlessTask.TriggerSource.SYSTEM},
    )

    return "OK"


def rotate(
''',
            '''    bulk_update_documents.apply_async(
        kwargs={
            "document_ids": affected_docs,
            # Permissions and ownership are authorization metadata, not
            # embedding inputs. Tantivy is still refreshed by the bulk task.
            "skip_llm_index": True,
        },
        headers={"trigger_source": PaperlessTask.TriggerSource.SYSTEM},
    )

    return "OK"


def rotate(
''',
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
from paperless_usl_ranges import ranged_file_response
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
            """        return serve_file(
            doc=file_doc,
            use_archive=not self.original_requested(request)
            and file_doc.has_archive_version,
            disposition=disposition,
            follow_formatting=request.query_params.get("follow_formatting", False),
        )
""",
            """        return serve_file(
            request=request,
            doc=file_doc,
            use_archive=not self.original_requested(request)
            and file_doc.has_archive_version,
            disposition=disposition,
            follow_formatting=request.query_params.get("follow_formatting", False),
        )
""",
        ),
        (
            """def serve_file(
    *,
    doc: Document,
""",
            """def serve_file(
    *,
    request: Request,
    doc: Document,
""",
        ),
        (
            """    response = FileResponse(file_handle, content_type=mime_type)
    # Firefox is not able to handle unicode characters in filename field
""",
            """    # Firefox is not able to handle unicode characters in filename field
""",
        ),
        (
            """    response["Content-Disposition"] = content_disposition
    return response
""",
            """    checksum = doc.archive_checksum if use_archive else doc.checksum
    return ranged_file_response(
        request,
        file_handle,
        content_type=mime_type,
        content_disposition=content_disposition,
        etag=checksum,
    )
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
