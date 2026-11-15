"""Apply the hash-guarded USL overlay to one exact Paperless source tree."""

import sys
from hashlib import sha256
from pathlib import Path

SOURCE_ROOT = Path(
    sys.argv[1] if len(sys.argv) > 1 else "/usr/src/paperless/src",
).resolve()
MISMATCH_MESSAGE = "Paperless v3.4.2 source is incompatible with the USL overlay"


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
    "ecc66cab999f5a168de44f61269119e981c5a4c3f93512a5c734e8a704adc1ae",
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
    "e29e31fe02179e1b35dd67079b22ee4c205050c1dfaac14b2886a7ddd10301a7",
    (
        # The two companion fields change schema_fingerprint(), so Paperless's
        # own needs_rebuild() check rebuilds only the search index once.
        (
            '''            for name in ("simple_title", "simple_content")
''',
            '''            for name in (
                "simple_title",
                "simple_content",
                "simple_metadata",
                "simple_custom_fields",
            )
''',
        ),
    ),
)

patch_file(
    "documents/search/_backend.py",
    "798ff2cbcf8386a576e11b402a0b703b18bc2bb514dc69f0772a93d555ec5052",
    (
        (
            '''        # Original filename - only add if not None/empty
        if document.original_filename:
            doc.add_text(
                "original_filename",
                normalize_search_text(document.original_filename),
            )
''',
            '''        # Plain-text companion fields keep broad scoped search in one
        # native Tantivy query instead of one API request per custom field.
        metadata_text: list[str] = []
        if document.original_filename:
            original_filename = normalize_search_text(document.original_filename)
            doc.add_text("original_filename", original_filename)
            metadata_text.append(original_filename)
''',
        ),
        (
            '''            doc.add_text("correspondent_sort", correspondent)
''',
            '''            doc.add_text("correspondent_sort", correspondent)
            metadata_text.append(correspondent)
''',
        ),
        (
            '''            doc.add_text("type_sort", document_type)
''',
            '''            doc.add_text("type_sort", document_type)
            metadata_text.append(document_type)
''',
        ),
        (
            '''            doc.add_text(
                "storage_path",
                normalize_search_text(document.storage_path.name),
            )
''',
            '''            storage_path = normalize_search_text(document.storage_path.name)
            doc.add_text("storage_path", storage_path)
            metadata_text.append(storage_path)
''',
        ),
        (
            '''            tag_names.append(tag_name)
''',
            '''            tag_names.append(tag_name)
            metadata_text.append(tag_name)
''',
        ),
        (
            '''        # Custom fields: JSON for structured queries (custom_fields.name:x,
        # custom_fields.value:y). There is no companion text field here, unlike
        # notes: custom field values are reachable only through the JSON field.
        for cfi in document.custom_fields.all():
''',
            '''        if metadata_text:
            doc.add_text("simple_metadata", " ".join(metadata_text))

        # Custom fields retain their structured JSON representation and also
        # share one plain-text field for the bounded scoped-search endpoint.
        # The companion field is not in Paperless's default search fields.
        custom_field_text: list[str] = []
        for cfi in document.custom_fields.all():
''',
        ),
        (
            '''            doc.add_json(
                "custom_fields",
                {
                    "name": normalize_search_text(cfi.field.name),
                    "value": normalize_search_text(search_value),
                },
            )

        # Barcodes: JSON field like custom_fields, only filled when stored
''',
            '''            custom_field_name = normalize_search_text(cfi.field.name)
            custom_field_value = normalize_search_text(search_value)
            doc.add_json(
                "custom_fields",
                {
                    "name": custom_field_name,
                    "value": custom_field_value,
                },
            )
            custom_field_text.extend((custom_field_name, custom_field_value))
        if custom_field_text:
            doc.add_text("simple_custom_fields", " ".join(custom_field_text))

        # Barcodes: JSON field like custom_fields, only filled when stored
''',
        ),
    ),
)

patch_file(
    "documents/tasks.py",
    "c33a6e8f5bac5ba7ef9e6b38c282db51cdfa294ed2294528d959288d6b547134",
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
                "        from paperless_ai.indexing import update_llm_index\n"
                "\n"
                "        update_llm_index(\n"
                "            rebuild=False,\n"
                "            document_ids=document_ids,\n"
                "        )\n"
            ),
            (
                "    if ai_config.llm_index_enabled and not skip_llm_index:\n"
                "        from paperless_ai.indexing import update_llm_index\n"
                "\n"
                "        update_llm_index(\n"
                "            rebuild=False,\n"
                "            document_ids=document_ids,\n"
                "        )\n"
            ),
        ),
        (
            '''@shared_task
def update_document_in_llm_index(document) -> None:
    from paperless_ai.indexing import llm_index_add_or_update_document

    llm_index_add_or_update_document(document)
''',
            '''@shared_task
def update_document_in_llm_index(document) -> None:
    from paperless_ai.indexing import llm_index_add_or_update_document

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
    "f05978600bf3bf8836a38515e1cf904b2ccd813a201db0050ea5e91bf1a000ac",
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
    "75771ea266a5a0c53850d27230d973c254d129cd67c73a385472e2a1d4ce2718",
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
    "paperless/settings/__init__.py",
    "1691555e1b45f12ef87fc252ca677f7d023fdb3cb28f70fe7a25a3edab24bab5",
    (
        (
            '''if LLM_EMBEDDING_CHUNK_SIZE < 1:
    raise ImproperlyConfigured("PAPERLESS_AI_LLM_EMBEDDING_CHUNK_SIZE must be >= 1")
LLM_CONTEXT_SIZE = get_int_from_env("PAPERLESS_AI_LLM_CONTEXT_SIZE", 8192)
''',
            '''if LLM_EMBEDDING_CHUNK_SIZE < 1:
    raise ImproperlyConfigured("PAPERLESS_AI_LLM_EMBEDDING_CHUNK_SIZE must be >= 1")
LLM_EMBEDDING_BATCH_SIZE = get_int_from_env(
    "PAPERLESS_AI_LLM_EMBEDDING_BATCH_SIZE",
    32,
)
if not 1 <= LLM_EMBEDDING_BATCH_SIZE <= 2048:
    raise ImproperlyConfigured(
        "PAPERLESS_AI_LLM_EMBEDDING_BATCH_SIZE must be between 1 and 2048",
    )
LLM_CONTEXT_SIZE = get_int_from_env("PAPERLESS_AI_LLM_CONTEXT_SIZE", 8192)
''',
        ),
    ),
)

patch_file(
    "paperless_ai/embedding.py",
    "4317f77c81044c7b8b859211dad272771f3f8f759ea1efe8bec9ad4afdd879f8",
    (
        (
            '''                model_name=config.llm_embedding_model or "embeddinggemma",
                base_url=endpoint,
                ollama_additional_kwargs={"num_ctx": config.llm_context_size},
''',
            '''                model_name=config.llm_embedding_model or "embeddinggemma",
                base_url=endpoint,
                embed_batch_size=settings.LLM_EMBEDDING_BATCH_SIZE,
                ollama_additional_kwargs={"num_ctx": config.llm_context_size},
''',
        ),
    ),
)

patch_file(
    "paperless_ai/client.py",
    "81384bf5508d189cb5804ee3e59e625e5bab13e26a692bdd3f8ef51c9eab87c6",
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
    "aa57804b7455da617970ea49101c86d3fa02000a75e817995fe44182fd9ad092",
    (
        (
            """def get_ai_document_classification(
    document: Document,
    user: User | None = None,
    output_language: str | None = None,
) -> ClassificationSuggestions:
""",
            """def get_ai_document_classification(
    document: Document,
    user: User | None = None,
    output_language: str | None = None,
    *,
    client_config,
) -> ClassificationSuggestions:
""",
        ),
        ("    client = AIClient()\n", "    client = AIClient(client_config)\n"),
    ),
)

patch_file(
    "paperless_ai/chat.py",
    "b65bd0547ef722a308ae3c9b575a5c20a13bf4c1ae0ce720f6a53e322125ec5d",
    (
        (
            """def stream_chat_with_documents(
    query_str: str,
    documents: QuerySet[Document],
    *,
    unrestricted: bool = False,
    output_language: str | None = None,
):
""",
            """def stream_chat_with_documents(
    query_str: str,
    documents: QuerySet[Document],
    *,
    unrestricted: bool = False,
    output_language: str | None = None,
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
    documents: QuerySet[Document],
    *,
    unrestricted: bool = False,
    output_language: str | None = None,
):
""",
            """def _stream_chat_with_documents(
    query_str: str,
    documents: QuerySet[Document],
    *,
    unrestricted: bool = False,
    output_language: str | None = None,
    client_config,
    authorization_check,
):
""",
        ),
        (
            """    config = AIConfig()
    if unrestricted:
        # Exclude trashed ids (usually few) instead of an IN filter over the
        # full permitted set, which risks the vector store's bound parameter
        # limit (_MAX_IN_VALUES) on large installs. Trashed documents stay
        # indexed until permanent deletion (delete_document_from_llm_index
        # hangs off post_delete, not trash), so must be excluded explicitly.
        trashed_ids = Document.deleted_objects.values_list("pk", flat=True)
        filters = exclude_document_ids_filter(str(pk) for pk in trashed_ids)
    else:
        filters = document_id_filters(
            str(pk) for pk in documents.values_list("pk", flat=True)
        )
""",
            """    config = AIConfig()
    # Personal chat always runs inside an explicit document scope that is
    # rechecked during the stream. The native unrestricted mode has no such
    # scope, so it is refused rather than silently widened.
    if unrestricted:
        raise PermissionError("Unrestricted document chat is disabled.")
    document_ids = list(documents.values_list("pk", flat=True))
    authorization_check(document_ids)
    filters = document_id_filters(str(document_id) for document_id in document_ids)
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
    "07c836f31036a98105866035620fbe576559d95d344c1734cdd5d1774852e270",
    (
        (
            """from paperless_ai.exceptions import LLMProviderError
from paperless_ai.exceptions import LLMTimeoutError
""",
            """from paperless_ai.exceptions import LLMProviderError
from paperless_ai.exceptions import LLMTimeoutError
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
            """            return serve_file(
                doc=file_doc,
                use_archive=not self.original_requested(request)
                and file_doc.has_archive_version,
                disposition="inline",
            )
""",
            """            return serve_file(
                request=request,
                doc=file_doc,
                use_archive=not self.original_requested(request)
                and file_doc.has_archive_version,
                disposition="inline",
            )
""",
        ),
        (
            """                return serve_file(
                    doc=share_link.document,
                    use_archive=share_link.file_version == ShareLink.FileVersion.ARCHIVE
                    and share_link.document.has_archive_version,
                    disposition="inline",
                )
""",
            """                return serve_file(
                    request=request,
                    doc=share_link.document,
                    use_archive=share_link.file_version == ShareLink.FileVersion.ARCHIVE
                    and share_link.document.has_archive_version,
                    disposition="inline",
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
            """    follow_formatting: bool = False,
) -> FileResponse:
""",
            """    follow_formatting: bool = False,
) -> HttpResponse:
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
            Document.objects.select_related(
                "owner",
                "root_document__owner",
            ).prefetch_related("versions"),
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

        from paperless_ai.ai_classifier import get_ai_document_classification
        from paperless_ai.ai_classifier import get_llm_output_language
""",
            """        doc = get_object_or_404(
            Document.objects.select_related(
                "owner",
                "root_document__owner",
            ).prefetch_related("versions"),
            pk=pk,
        )
        # Personal suggestions need only read access. A version is checked by
        # its root, and an invisible document looks the same as a missing one.
        if not has_perms_owner_aware(request.user, "view_document", doc):
            raise Http404

        ai_config = AIConfig()
        if not ai_config.ai_enabled:
            return HttpResponseBadRequest("AI is required for this feature")

        from paperless_ai.ai_classifier import get_llm_output_language
""",
        ),
        (
            """        llm_cache_backend = ":".join(
            part
            for part in (
                ai_config.llm_backend,
                ai_config.llm_model,
                ai_config.llm_endpoint,
                output_language,
                f"user={request.user.pk}",
            )
            if part
        )
""",
            """        try:
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
            """                llm_suggestions = get_ai_document_classification(
                    doc,
                    request.user,
                    output_language,
                )
""",
            """                llm_suggestions = generate_personal_metadata_suggestions(
                    document=doc,
                    user=request.user,
                    client_config=client_config,
                    output_language=output_language,
                )
""",
        ),
        (
            """            except ValueError as exc:
                logger.exception(
""",
            """            except PersonalAIGenerationError:
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
                document = Document.objects.select_related(
                    "root_document__owner",
                ).get(id=doc_id)
            except Document.DoesNotExist:
                return HttpResponseBadRequest("Document not found")

            if not has_perms_owner_aware(
                request.user,
                "view_document",
                document,
            ):
                return HttpResponseForbidden("Insufficient permissions")

            documents = Document.objects.filter(pk=document.pk)
            unrestricted = False
        else:
            documents = Document.objects.filter(
                id__in=permitted_document_ids(request.user),
            )
            unrestricted = user_is_unrestricted(request.user)

        from paperless_ai.ai_classifier import get_llm_output_language
        from paperless_ai.chat import stream_chat_with_documents

        output_language = get_llm_output_language(
            ai_config=ai_config,
            user=request.user,
        )

        response = StreamingHttpResponse(
            stream_chat_with_documents(
                query_str=question,
                documents=documents,
                unrestricted=unrestricted,
                output_language=output_language,
            ),
            content_type="text/event-stream",
        )
""",
            """        doc_id = serializer.validated_data.get("document_id")
        if doc_id and not Document.objects.filter(
            pk=doc_id,
            id__in=permitted_document_ids(request.user),
        ).exists():
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

        from paperless_ai.ai_classifier import get_llm_output_language

        output_language = get_llm_output_language(
            ai_config=ai_config,
            user=request.user,
        )

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
    "5db6a451c083ca274c30c6a36f160376b77c4ac3a77c8fedf511a288917f21be",
    (
        (
            """    llm_api_key = ObfuscatedPasswordField(
        required=False,
        allow_null=True,
        max_length=1024,
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

# Paperless 3.1 added an "apply AI suggestions" workflow action. It runs
# without an initiating user and would generate as the document owner. Personal
# Gemini generation requires the person's own action and consent, so the API
# refuses the action type and the worker refuses any stored instance.
_WORKFLOW_AI_DISABLED = (
    "AI suggestion workflow actions are not available. A person requests AI "
    "suggestions from a document after they switch them on in My profile."
)

patch_file(
    "documents/serialisers.py",
    "c942d1cbe76aa1a090de2699ac754bbae40fa3df2d4199eec1d290af74ce244b",
    (
        (
            '''        if (
            "type" in attrs
            and attrs["type"] == WorkflowAction.WorkflowActionType.APPLY_AI_SUGGESTIONS
        ):
            fields = attrs.get("ai_suggestion_fields")
''',
            f'''        if (
            "type" in attrs
            and attrs["type"] == WorkflowAction.WorkflowActionType.APPLY_AI_SUGGESTIONS
        ):
            raise serializers.ValidationError(
                {{"type": [{_WORKFLOW_AI_DISABLED!r}]}},
            )
        if (
            "type" in attrs
            and attrs["type"] == WorkflowAction.WorkflowActionType.APPLY_AI_SUGGESTIONS
        ):
            fields = attrs.get("ai_suggestion_fields")
''',
        ),
    ),
)

patch_file(
    "documents/workflows/ai.py",
    "2dbfa4fbd63a8abb53e8ba00e0d45bb3ff56d18766c1de93a74f283568a40d4f",
    (
        (
            '''    Returns the names of the fields that were actually changed.
    """
''',
            f'''    Returns the names of the fields that were actually changed.
    """
    logger.warning(
        "Workflow action %s did not change document %s: %s",
        action.pk,
        document.pk,
        {_WORKFLOW_AI_DISABLED!r},
        extra={{"group": logging_group}},
    )
    return []
''',
        ),
    ),
)
