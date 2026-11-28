# Renovate reads the version and the source commit together (renovate.json).
# Its pull request changes only these two values. The base image digest, the
# archive SHA-256 and the overlay hashes stay manual (README.md).
# renovate: datasource=github-tags depName=paperless-ngx/paperless-ngx
ARG PAPERLESS_VERSION=v3.5.1
ARG PAPERLESS_BASE_IMAGE=ghcr.io/paperless-ngx/paperless-ngx@sha256:8508853b8a0f8394074260957d0be186c8c64f346973b24a7583de4580f306b9
ARG PAPERLESS_FRONTEND_BUILD_IMAGE=node@sha256:996f094d0487f4f9cbc9227a4ebba56e011d3653679909b04a9fa6dc7ab54aa4
ARG PAPERLESS_SOURCE_URL=https://github.com/paperless-ngx/paperless-ngx/archive/35b0439195bbdb87a6d519152e6782abe0720c3b.tar.gz

FROM ${PAPERLESS_BASE_IMAGE} AS frontend-source

ARG PAPERLESS_SOURCE_URL
ADD --checksum=sha256:8416949e59c86befe73b8ac761ca5056a50972ebe9ef7db1d66990745ac7a49a \
    ${PAPERLESS_SOURCE_URL} /tmp/paperless-source.tar.gz
COPY --chmod=0755 \
    deploy/documents/paperless-ngx/apply_frontend_overlay.py \
    /tmp/usl-apply-paperless-frontend-overlay.py
COPY deploy/documents/paperless-ngx/overlay/src-ui/ /tmp/usl-src-ui-overlay/

RUN mkdir -p /tmp/paperless-source \
    && tar -xzf /tmp/paperless-source.tar.gz \
        --directory /tmp/paperless-source --strip-components=1 \
    && cp -a /tmp/usl-src-ui-overlay/. /tmp/paperless-source/src-ui/ \
    && python /tmp/usl-apply-paperless-frontend-overlay.py \
        /tmp/paperless-source/src-ui

FROM ${PAPERLESS_FRONTEND_BUILD_IMAGE} AS compile-frontend

COPY --from=frontend-source /tmp/paperless-source/src-ui /src/src-ui

WORKDIR /src/src-ui
RUN corepack enable \
    && pnpm install --frozen-lockfile
RUN ./node_modules/.bin/ng build --configuration production --localize=false
RUN ./node_modules/.bin/ng build --configuration production

FROM ${PAPERLESS_BASE_IMAGE}

COPY --chmod=0644 deploy/documents/paperless-ngx/overlay/paperless_ai/*.py /usr/src/paperless/src/paperless_ai/
COPY --chmod=0644 \
    deploy/documents/paperless-ngx/overlay/paperless_usl_ranges.py \
    /usr/src/paperless/src/paperless_usl_ranges.py
COPY --chmod=0644 deploy/documents/paperless-ngx/overlay/paperless_usl_tag_scores.py /usr/src/paperless/src/paperless_usl_tag_scores.py
COPY --chmod=0644 deploy/documents/paperless-ngx/overlay/paperless_usl_gotenberg.py \
    /usr/src/paperless/src/paperless_usl_gotenberg.py
COPY deploy/documents/paperless-ngx/overlay/paperless_personal_ai/ \
    /usr/src/paperless/src/paperless_personal_ai/
COPY deploy/documents/paperless-ngx/overlay/paperless_tag_review/ \
    /usr/src/paperless/src/paperless_tag_review/
COPY --chmod=0755 \
    deploy/documents/paperless-ngx/apply_overlay.py \
    /tmp/usl-apply-paperless-overlay.py
COPY --chmod=0755 \
    deploy/documents/paperless-ngx/verify_preview_contract.py \
    /tmp/usl-verify-paperless-preview-contract.py
COPY --chmod=0644 \
    deploy/documents/paperless-ngx/tests/test_gotenberg_contract.py \
    /tmp/usl-test-paperless-gotenberg-contract.py
COPY --from=compile-frontend --chown=1000:1000 \
    /src/src/documents/static/frontend/ \
    /usr/src/paperless/src/documents/static/frontend/

RUN python /tmp/usl-apply-paperless-overlay.py \
    && python /tmp/usl-verify-paperless-preview-contract.py \
    && PYTHONPATH=/usr/src/paperless/src python /tmp/usl-test-paperless-gotenberg-contract.py \
    && python -m compileall -q \
        /usr/src/paperless/src/paperless_usl_tag_scores.py \
        /usr/src/paperless/src/paperless_usl_ranges.py \
        /usr/src/paperless/src/paperless_usl_gotenberg.py \
        /usr/src/paperless/src/paperless/parsers/tika.py \
        /usr/src/paperless/src/paperless/parsers/mail.py \
        /usr/src/paperless/src/paperless_ai/semantic_api.py \
        /usr/src/paperless/src/paperless_personal_ai \
        /usr/src/paperless/src/paperless_tag_review \
        /usr/src/paperless/src/documents/management/commands/document_retagger.py \
        /usr/src/paperless/src/documents/bulk_edit.py \
        /usr/src/paperless/src/documents/bulk_operations.py \
        /usr/src/paperless/src/documents/serialisers.py \
        /usr/src/paperless/src/documents/workflows/ai.py \
        /usr/src/paperless/src/documents/search/_backend.py \
        /usr/src/paperless/src/documents/search/_schema.py \
        /usr/src/paperless/src/documents/signals/handlers.py \
        /usr/src/paperless/src/documents/tasks.py \
        /usr/src/paperless/src/paperless/urls.py \
        /usr/src/paperless/src/paperless/settings/__init__.py \
        /usr/src/paperless/src/paperless/serialisers.py \
        /usr/src/paperless/src/paperless_ai/embedding.py \
        /usr/src/paperless/src/paperless_ai/client.py \
        /usr/src/paperless/src/paperless_ai/ai_classifier.py \
        /usr/src/paperless/src/paperless_ai/chat.py \
        /usr/src/paperless/src/documents/views.py \
    && rm \
        /tmp/usl-apply-paperless-overlay.py \
        /tmp/usl-verify-paperless-preview-contract.py \
        /tmp/usl-test-paperless-gotenberg-contract.py

LABEL org.opencontainers.image.title="USL Paperless-ngx Distribution" \
      org.opencontainers.image.version="3.5.1-usl.3" \
      org.opencontainers.image.revision="35b0439195bbdb87a6d519152e6782abe0720c3b" \
      com.unstaticlabs.paperless.base-digest="sha256:8508853b8a0f8394074260957d0be186c8c64f346973b24a7583de4580f306b9" \
      com.unstaticlabs.paperless.patch="scoped-lexical-search-v1+permission-vector-invariance-v2+deferred-bulk-index-v1+semantic-search-api-v2+personal-gemini-v1+ollama-batch-v1+preview-contract-v1+byte-range-download-v1+workflow-ai-disabled-v1+tag-scores-v1+atomic-index-v1+reviewed-tags-v1+gotenberg-9-archive-pdfa-v1"
