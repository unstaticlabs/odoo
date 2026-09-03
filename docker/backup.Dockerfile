# syntax=docker/dockerfile:1.7

ARG POSTGRES_IMAGE=postgres:16-bookworm@sha256:60f4761b9035e0b8d5218f701a8c3382f641bf12b1604822574cf5be3baeb537
ARG RESTIC_IMAGE=restic/restic:0.19.1@sha256:136600b6ff6843d61d355f7f71f460a166429f35de6fd11b568fece3c9a4d510
ARG DOCKER_CLI_IMAGE=docker:28.5.1-cli@sha256:9190b0613792e658a7783cf14b2d5ace5941bb68ede7276922ea36ee457d76ad

FROM ${RESTIC_IMAGE} AS restic
FROM ${DOCKER_CLI_IMAGE} AS docker-cli
FROM ${POSTGRES_IMAGE} AS runtime

ARG USL_COMPONENT_INPUT_SHA256=unverified

LABEL org.opencontainers.image.title="USL Odoo Backup Tool" \
      com.unstaticlabs.odoo.component-input-sha256="${USL_COMPONENT_INPUT_SHA256}" \
      com.unstaticlabs.odoo.runtime="backup"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    RESTIC_CACHE_DIR=/cache

RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt,sharing=locked \
    apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates python3 python3-psycopg2 \
    && rm -rf /var/lib/apt/lists/*

COPY --from=restic /usr/bin/restic /usr/local/bin/restic
COPY --from=docker-cli /usr/local/bin/docker /usr/local/bin/docker
COPY --from=docker-cli /usr/local/libexec/docker/cli-plugins/docker-compose /usr/local/libexec/docker/cli-plugins/docker-compose
COPY operations /opt/usl/operations
COPY --chmod=755 scripts/cohort-runtime /usr/local/bin/usl-cohort-runtime
COPY --chmod=755 scripts/usl-stack /usr/local/bin/usl-stack

ENV PYTHONPATH=/opt/usl

ENTRYPOINT ["/usr/local/bin/usl-cohort-runtime"]
CMD ["--help"]
