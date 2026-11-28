"""Apply the hash-guarded USL overlay to one exact Paperless source tree."""

import sys
from hashlib import sha256
from pathlib import Path

SOURCE_ROOT = Path(
    sys.argv[1] if len(sys.argv) > 1 else "/usr/src/paperless/src",
).resolve()
MISMATCH_MESSAGE = "Paperless v3.5.1 source is incompatible with the USL overlay"


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
        (
            '                re_path(\n                    "^statistics/",',
            '                path("tag_review/", include("paperless_tag_review.urls")),\n'
            '                re_path(\n                    "^statistics/",',
        ),
    ),
)

patch_file(
    "documents/management/commands/document_retagger.py",
    "26ef307c240944123c2bff8db69be648a4297e8d5b684bef2790b5b20dea0432",
    (
        ("import logging\n", "import logging\nfrom uuid import uuid4\n"),
        ("            for document in self.track_with_stats(\n",
         "            tag_review_batch = uuid4()\n            for document in self.track_with_stats(\n"),
        ("                    tags_to_add, tags_to_remove = set_tags(\n                        None,\n                        document,\n",
         "                    tags_to_add, tags_to_remove = set_tags(\n                        None,\n                        document,\n                        tag_review_batch=tag_review_batch,\n"),
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
    "198c7c402769b20843b5bb76d64960b4e3a32faf95402d8d3a0925e6b365940f",
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
    "2e066e8eeede1d100d2f6a3607bcdb248d245e436cde3a408bf05c872dd3cc57",
    (
        ("from django.db import models\n", "from django.db import models, transaction\nfrom paperless_tag_review.runtime import classifier_run\n"),
        ("def set_tags(\n", "@transaction.atomic\n@classifier_run()\ndef set_tags(\n"),
        ("    matched_tags = matching.match_tags(document, classifier)\n",
         "    matched_tags = [tag for tag in matching.match_tags(document, classifier)\n"
         "                    if respects_manual_removals(tag, rejected)]\n"),
        (
            "    # Compute which tags would be removed under replace mode.\n",
            "    from paperless_tag_review.service import apply_reviewed_tags, reviewed_tag_scope, respects_manual_removals\n"
            "    guarded, rejected = reviewed_tag_scope(document)\n"
            "    # Compute which tags would be removed under replace mode.\n",
        ),
        (
            "            document.tags.exclude(\n                is_inbox_tag=True,",
            "            document.tags.exclude(pk__in=guarded).exclude(\n                is_inbox_tag=True,",
        ),
        (
            "        Document.tags.through.objects.filter(document=document).exclude(\n",
            "        Document.tags.through.objects.filter(document=document).exclude(tag_id__in=guarded).exclude(\n",
        ),
        (
            "    tags_to_add = set(matched_tags) - current_tags\n",
            "    tags_to_add = {tag for tag in matched_tags if tag.pk not in guarded} - current_tags\n",
        ),
        (
            "    return tags_to_add, tags_to_remove\n",
            "    reviewed = apply_reviewed_tags(document, classifier, matched_tags, guarded,\n"
            "                                  dry_run=dry_run, batch=kwargs.get('tag_review_batch'))\n"
            "    return tags_to_add | reviewed, tags_to_remove\n",
        ),
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
    "fa9470914bb3da80df39011f5a164fc5ee09e01e2fc115c7d7ea0278f35b9831",
    (
        (
            '''        merge=merge,
    )

    _finish_metadata_edit(affected_docs, update_callback)

    return "OK"


def rotate(
''',
            '''        merge=merge,
    )

    # Permissions and ownership are authorization metadata, not embedding
    # inputs. Tantivy is still refreshed by the bulk task. A queued bulk
    # operation passes a callback; documents/bulk_operations.py applies the
    # same rule there.
    if update_callback is not None:
        update_callback(affected_docs)
    else:
        bulk_update_documents.apply_async(
            kwargs={
                "document_ids": affected_docs,
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

# Paperless 3.5 runs bulk edits as one queued task. That task refreshes the
# affected documents itself, so the permission rule must also live here.
patch_file(
    "documents/bulk_operations.py",
    "8851c3c37731ec3faa030291d748f069338e4bdf6452cd032dbcf1e5b8eea183",
    (
        (
            """    if affected_ids:
        bulk_update_documents(list(dict.fromkeys(affected_ids)))
""",
            """    if affected_ids:
        # Permission and ownership edits do not change embedding inputs.
        # Tantivy is still refreshed; the vectors are not recomputed. Other
        # operations keep the exact upstream call.
        followup_kwargs = (
            {"skip_llm_index": True} if operation == "set_permissions" else {}
        )
        bulk_update_documents(list(dict.fromkeys(affected_ids)), **followup_kwargs)
""",
        ),
    ),
)

patch_file(
    "paperless/settings/__init__.py",
    "80a0b4e12d7531e2ab98bb1377c93064ac7d1b466b33dc3cfcb90d84c51ed0e6",
    (
        (
            '    "documents.apps.DocumentsConfig",\n',
            '    "documents.apps.DocumentsConfig",\n    "paperless_tag_review.apps.PaperlessTagReviewConfig",\n',
        ),
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
    "61b9e97fd5227cd32b50faf7f8487b37fa825fe68077fe3105c3a6e548f3b69e",
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
    "b4baf40ff85e7d96ed133d0ce85600d0b9adb863595b6a7f62490a410a1fec23",
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
    permitted_ids: set[int] | None = None
    if unrestricted:
        # Exclude trashed ids (usually few) rather than send every permitted
        # id on each query. Trashed documents stay indexed until permanent
        # deletion (post_delete, not trash), so must be excluded explicitly.
        trashed_ids = Document.deleted_objects.values_list("pk", flat=True)
        filters = exclude_document_ids_filter(str(pk) for pk in trashed_ids)
    else:
        permitted_ids = set(documents.values_list("pk", flat=True))
""",
            """    config = AIConfig()
    # Personal chat always runs inside an explicit document scope that is
    # rechecked during the stream. The native unrestricted mode has no such
    # scope, so it is refused rather than silently widened. Upstream then
    # scopes retrieval to permitted_ids and drops any other node.
    if unrestricted:
        raise PermissionError("Unrestricted document chat is disabled.")
    permitted_ids: set[int] | None = set(documents.values_list("pk", flat=True))
    authorization_check(sorted(permitted_ids))
""",
        ),
        (
            """    client = AIClient()

    references = _get_document_references(documents, top_nodes)
""",
            """    context_document_ids = sorted(
        {
            int(node.metadata["document_id"])
            for node in top_nodes
            if str(node.metadata.get("document_id", "")).isdigit()
            and int(node.metadata["document_id"]) in permitted_ids
        },
    )
    authorization_check(context_document_ids)
    client = AIClient(client_config)

    references = _get_document_references(documents, top_nodes)
""",
        ),
        (
            """        for chunk in response_stream.response_gen:
            yield chunk
            sys.stdout.flush()
""",
            """        for chunk in response_stream.response_gen:
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
    "0c54fce2adb92228a839ce281ceb46aa2c94d3e08c2d1fb34602965fb92a9036",
    (
        (
            """from paperless_ai.exceptions import LLMProviderError
from paperless_ai.exceptions import LLMTimeoutError
""",
            """from paperless_ai.exceptions import LLMProviderError
from paperless_ai.exceptions import LLMTimeoutError
from paperless_usl_ranges import ranged_file_response
from paperless_usl_tag_scores import scored_cache_entry, suggested_tag_scores, unscored_cache_validator, wants_tag_scores
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
            "            etag_func=suggestions_etag,\n            last_modified_func=suggestions_last_modified,",
            "            etag_func=unscored_cache_validator(suggestions_etag),\n"
            "            last_modified_func=unscored_cache_validator(suggestions_last_modified),",
        ),
        (
            "        document_suggestions = get_suggestion_cache(doc.pk)\n",
            "        include_scores = wants_tag_scores(request)\n"
            "        document_suggestions = None if include_scores else scored_cache_entry(get_suggestion_cache(doc.pk))\n",
        ),
        (
            '        resp_data = {\n            "correspondents": [',
            '        tags = match_tags(doc, classifier, request.user)\n'
            '        resp_data = {\n            "correspondents": [',
        ),
        (
            '"tags": [t.id for t in match_tags(doc, classifier, request.user)],',
            '"tags": [t.id for t in tags],',
        ),
        (
            "        set_suggestions_cache(doc.pk, resp_data, classifier)\n",
            "        resp_data['tag_scores'] = suggested_tag_scores(classifier, doc.suggestion_content, tags)\n"
            "        if include_scores:\n"
            "            return Response(resp_data, headers={'Cache-Control': 'private, no-store'})\n"
            "        set_suggestions_cache(doc.pk, resp_data, classifier)\n",
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

        return StreamingHttpResponse(
            _iterate_in_sync_thread(
                stream_chat_with_documents(
                    query_str=question,
                    documents=documents,
                    unrestricted=unrestricted,
                    output_language=output_language,
                ),
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

        # Upstream 3.5 serves the sync generator chunk by chunk under ASGI.
        return StreamingHttpResponse(
            _iterate_in_sync_thread(
                stream_personal_document_chat(
                    query_str=question,
                    user_id=request.user.pk,
                    document_id=doc_id,
                    output_language=output_language,
                    client_config=client_config,
                ),
            ),
            content_type="text/event-stream",
        )
""",
        ),
    ),
)

patch_file(
    "paperless/serialisers.py",
    "0a0d87b893e71e0888953c0eb96033a04d8329ef9ef1e19009f07fa243de6fcf",
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
    "71e149cb2b9a257df79fb8299570f5164b4ff7cf978372f339a4c3fde5cf7d57",
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


patch_file(
    "paperless_ai/indexing.py",
    "87f5b201f2556adc5e914769e39f2a917e29089a52870074660f6b2bc8c911d8",
    (
        (
            "from paperless_ai.db import db_connection_released\n",
            "from paperless_ai.db import db_connection_released\n"
            "from paperless_ai.index_generation import needs_model_rebuild, replacement_index, require_index_model\n",
        ),
        (
            "    embed_model = get_embedding_model(config)\n    llama_settings.Settings.embed_model",
            "    require_index_model(store, get_configured_model_name(config))\n"
            "    embed_model = get_embedding_model(config)\n    llama_settings.Settings.embed_model",
        ),
        (
            '''    if not rebuild:
        with read_store() as store:
            if store.table_exists() and store.config_mismatch(model_name):
                logger.warning("Embedding model changed; forcing LLM index rebuild.")
                rebuild = True

''',
            "",
        ),
        (
            "        if rebuild or not store.table_exists():\n",
            "        if not rebuild and needs_model_rebuild(store, model_name):\n"
            "            logger.warning('The index model changed or is unknown; building a replacement.')\n"
            "            rebuild = True\n"
            "        if rebuild or not store.table_exists():\n",
        ),
        (
            "    new_nodes = build_document_node(\n",
            "    model_name = get_configured_model_name(config)\n"
            "    with read_store() as current:\n"
            "        require_index_model(current, model_name)\n"
            "    new_nodes = build_document_node(\n",
        ),
        (
            "    with write_store(embed_model_name=get_configured_model_name(config)) as store:\n",
            "    with write_store(embed_model_name=model_name) as store:\n",
        ),
        (
            '''            store.drop_table()
            for document in iter_wrapper(
                QuerySetStream(documents, chunk_size=_INDEX_STREAM_CHUNK_SIZE),
            ):
                nodes = build_document_node(document, chunk_size=chunk_size)
                _embed_nodes(nodes, embed_model)
                store.add(nodes)''',
            '''            with replacement_index(store, model_name, settings.LLM_INDEX_DIR, _exclude_readers) as candidate:
                for document in iter_wrapper(
                    QuerySetStream(documents, chunk_size=_INDEX_STREAM_CHUNK_SIZE),
                ):
                    nodes = build_document_node(document, chunk_size=chunk_size)
                    _embed_nodes(nodes, embed_model)
                    candidate.add(nodes)''',
        ),
        (
            "        store.upsert_document(str(document.id), new_nodes)\n",
            "        require_index_model(store, model_name)\n"
            "        store.upsert_document(str(document.id), new_nodes)\n",
        ),
    ),
)


patch_file(
    "paperless/parsers/tika.py",
    "af83cdc7a248c28305fdc626a8b3f6058bd8a5efd2584f1f593360a33458d86a",
    (
        (
            "from paperless.version import __full_version_str__\n",
            "from paperless.version import __full_version_str__\n"
            "from paperless_usl_gotenberg import pdfa_format\n",
        ),
        (
            "            }:\n"
            "                route.pdf_format(PdfAFormat.A2b)\n",
            "            }:\n"
            "                route.pdf_format(pdfa_format(PdfAFormat.A2b, office=True))\n",
        ),
        (
            "                route.pdf_format(PdfAFormat.A2b)\n"
            "            elif output_type == OutputTypeChoices.PDF_A3:\n"
            "                route.pdf_format(PdfAFormat.A3b)\n",
            "                route.pdf_format(pdfa_format(PdfAFormat.A2b, office=True))\n"
            "            elif output_type == OutputTypeChoices.PDF_A3:\n"
            "                route.pdf_format(pdfa_format(PdfAFormat.A3b, office=True))\n",
        ),
    ),
)


patch_file(
    "paperless/parsers/mail.py",
    "048adf9fbade2604bc88d1e957f1eec26256675fe70bcaf2e7385d14eea44339",
    (
        (
            "from paperless.models import OutputTypeChoices\n",
            "from paperless.models import OutputTypeChoices\n"
            "from paperless_usl_gotenberg import merge_email_pdfs\n"
            "from paperless_usl_gotenberg import pdfa_format\n",
        ),
        (
            '''            with (
                GotenbergClient(
                    host=settings.TIKA_GOTENBERG_ENDPOINT,
                    timeout=settings.CELERY_TASK_TIME_LIMIT,
                ) as client,
                client.merge.merge() as route,
            ):
                # Configure requested PDF/A formatting, if any
                pdf_a_format = self._settings_to_gotenberg_pdfa()
                if pdf_a_format is not None:
                    route.pdf_format(pdf_a_format)

                match pdf_layout:
                    case MailRule.PdfLayout.HTML_TEXT:
                        route.merge([pdf_of_html_content, mail_pdf_file])
                    case MailRule.PdfLayout.HTML_ONLY:
                        route.merge([pdf_of_html_content])
                    case MailRule.PdfLayout.TEXT_ONLY:
                        route.merge([mail_pdf_file])
                    case MailRule.PdfLayout.TEXT_HTML | _:
                        route.merge([mail_pdf_file, pdf_of_html_content])

                try:
                    response = route.run()
                    archive_path.write_bytes(response.content)
                except Exception as err:
                    raise ParseError(
                        f"Error while merging email HTML into PDF: {err}",
                    ) from err''',
            '''            match pdf_layout:
                case MailRule.PdfLayout.HTML_TEXT:
                    paths = [pdf_of_html_content, mail_pdf_file]
                case MailRule.PdfLayout.HTML_ONLY:
                    paths = [pdf_of_html_content]
                case MailRule.PdfLayout.TEXT_ONLY:
                    paths = [mail_pdf_file]
                case MailRule.PdfLayout.TEXT_HTML | _:
                    paths = [mail_pdf_file, pdf_of_html_content]

            try:
                archive_path.write_bytes(
                    merge_email_pdfs(
                        settings.TIKA_GOTENBERG_ENDPOINT,
                        settings.CELERY_TASK_TIME_LIMIT,
                        paths,
                        self._settings_to_gotenberg_pdfa(),
                    ),
                )
            except Exception as err:
                raise ParseError(
                    f"Error while merging email HTML into PDF: {err}",
                ) from err''',
        ),
        (
            "                    route.pdf_format(pdf_a_format)\n\n            try:",
            "                    route.pdf_format(pdfa_format(pdf_a_format))\n\n            try:",
        ),
        (
            "                    route.pdf_format(pdf_a_format)\n\n            # Add attachments",
            "                    route.pdf_format(pdfa_format(pdf_a_format))\n\n            # Add attachments",
        ),
    ),
)
