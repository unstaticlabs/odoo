ARG PAPERLESS_BASE_IMAGE=ghcr.io/paperless-ngx/paperless-ngx@sha256:65a4cabf0169ea7fbd90ab7bb28ba3f8b5909613635acda1a03ad606f34b456b

FROM ${PAPERLESS_BASE_IMAGE}

COPY --chmod=0644 \
    deploy/documents/paperless-ngx/overlay/paperless_ai/semantic_api.py \
    /usr/src/paperless/src/paperless_ai/semantic_api.py
COPY --chmod=0755 \
    deploy/documents/paperless-ngx/apply_overlay.py \
    /tmp/usl-apply-paperless-overlay.py

RUN python /tmp/usl-apply-paperless-overlay.py \
    && python -m compileall -q \
        /usr/src/paperless/src/paperless_ai/semantic_api.py \
        /usr/src/paperless/src/paperless/urls.py \
    && rm /tmp/usl-apply-paperless-overlay.py

LABEL org.opencontainers.image.title="USL Paperless-ngx Distribution" \
      org.opencontainers.image.version="3.0.5-usl.2" \
      org.opencontainers.image.revision="8fb73b2709e4c38180a7632edf32f32fe2315961" \
      com.unstaticlabs.paperless.base-digest="sha256:65a4cabf0169ea7fbd90ab7bb28ba3f8b5909613635acda1a03ad606f34b456b" \
      com.unstaticlabs.paperless.patch="semantic-search-api-v2"
