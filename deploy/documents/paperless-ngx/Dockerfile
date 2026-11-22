ARG PAPERLESS_BASE_IMAGE=ghcr.io/paperless-ngx/paperless-ngx@sha256:d704e84f88300eb6ab001ab15720a2330ff99169cf9f94d6b8fcc44b63a84c84
ARG PAPERLESS_FRONTEND_BUILD_IMAGE=node@sha256:996f094d0487f4f9cbc9227a4ebba56e011d3653679909b04a9fa6dc7ab54aa4
ARG PAPERLESS_SOURCE_URL=https://github.com/paperless-ngx/paperless-ngx/archive/d939d873e11dd14896d1bfe23f65b1baa6513ba7.tar.gz

FROM ${PAPERLESS_BASE_IMAGE} AS frontend-source

ARG PAPERLESS_SOURCE_URL
ADD --checksum=sha256:9359f9f6c9e71d2736d9ac8074c0563d35367fa32dcdbf09ded58482b02fe8ec \
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

COPY --chmod=0644 \
    deploy/documents/paperless-ngx/overlay/paperless_ai/semantic_api.py \
    /usr/src/paperless/src/paperless_ai/semantic_api.py
COPY --chmod=0644 \
    deploy/documents/paperless-ngx/overlay/paperless_usl_ranges.py \
    /usr/src/paperless/src/paperless_usl_ranges.py
COPY --chmod=0644 deploy/documents/paperless-ngx/overlay/paperless_usl_tag_scores.py /usr/src/paperless/src/paperless_usl_tag_scores.py
COPY deploy/documents/paperless-ngx/overlay/paperless_personal_ai/ \
    /usr/src/paperless/src/paperless_personal_ai/
COPY --chmod=0755 \
    deploy/documents/paperless-ngx/apply_overlay.py \
    /tmp/usl-apply-paperless-overlay.py
COPY --chmod=0755 \
    deploy/documents/paperless-ngx/verify_preview_contract.py \
    /tmp/usl-verify-paperless-preview-contract.py
COPY --from=compile-frontend --chown=1000:1000 \
    /src/src/documents/static/frontend/ \
    /usr/src/paperless/src/documents/static/frontend/

RUN python /tmp/usl-apply-paperless-overlay.py \
    && python /tmp/usl-verify-paperless-preview-contract.py \
    && python -m compileall -q \
        /usr/src/paperless/src/paperless_usl_tag_scores.py \
        /usr/src/paperless/src/paperless_usl_ranges.py \
        /usr/src/paperless/src/paperless_ai/semantic_api.py \
        /usr/src/paperless/src/paperless_personal_ai \
        /usr/src/paperless/src/documents/bulk_edit.py \
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
        /tmp/usl-verify-paperless-preview-contract.py

LABEL org.opencontainers.image.title="USL Paperless-ngx Distribution" \
      org.opencontainers.image.version="3.4.2-usl.1" \
      org.opencontainers.image.revision="d939d873e11dd14896d1bfe23f65b1baa6513ba7" \
      com.unstaticlabs.paperless.base-digest="sha256:d704e84f88300eb6ab001ab15720a2330ff99169cf9f94d6b8fcc44b63a84c84" \
      com.unstaticlabs.paperless.patch="scoped-lexical-search-v1+permission-vector-invariance-v1+deferred-bulk-index-v1+semantic-search-api-v2+personal-gemini-v1+ollama-batch-v1+preview-contract-v1+byte-range-download-v1+workflow-ai-disabled-v1+tag-scores-v1"
