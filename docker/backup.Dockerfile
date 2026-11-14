# syntax=docker/dockerfile:1.7

ARG POSTGRES_IMAGE=postgres:16-bookworm@sha256:60f4761b9035e0b8d5218f701a8c3382f641bf12b1604822574cf5be3baeb537
ARG RESTIC_IMAGE=restic/restic:0.19.1@sha256:136600b6ff6843d61d355f7f71f460a166429f35de6fd11b568fece3c9a4d510
ARG DOCKER_CLI_IMAGE=docker:28.5.1-cli@sha256:9190b0613792e658a7783cf14b2d5ace5941bb68ede7276922ea36ee457d76ad
ARG COMPOSE_IMAGE=docker/compose-bin:v5.5.0@sha256:962d5ea7017e5ef425bfa47e492efa2c27d0492e8af58c497011e73b2ce98ee0

FROM ${RESTIC_IMAGE} AS restic
FROM ${DOCKER_CLI_IMAGE} AS docker-cli
FROM ${COMPOSE_IMAGE} AS compose
FROM ${POSTGRES_IMAGE} AS runtime

ENV USL_OPERATIONS_CONTAINER=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    RESTIC_CACHE_DIR=/cache

RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt,sharing=locked \
    apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl openssl python3 python3-psycopg2 rsync \
    && rm -rf /var/lib/apt/lists/*

COPY --from=restic /usr/bin/restic /usr/local/bin/restic
COPY --from=docker-cli /usr/local/bin/docker /usr/local/bin/docker
COPY --from=compose /docker-compose /usr/local/libexec/docker/cli-plugins/docker-compose

# Compose 5.5.0 first fixes env_file resolution in config --hash (#14002).
# Verify the official release binary for each published runtime architecture.
ARG TARGETARCH
RUN case "$TARGETARCH" in \
        amd64) compose_sha256=c57ab918abd5b05ca7e7d0f275875dd1330a695074f309dc9eab1b49efafcd4b ;; \
        arm64) compose_sha256=ff42489f5a9b879d5d117c5ffea6defc27390b3286da8ad52cbc9c6ab5df590e ;; \
        *) echo "Unsupported operations architecture: $TARGETARCH" >&2; exit 1 ;; \
    esac \
    && echo "$compose_sha256  /usr/local/libexec/docker/cli-plugins/docker-compose" | sha256sum --check --status \
    && test "$(docker compose version --short)" = "5.5.0"
COPY operations /opt/usl/operations
COPY scripts/odoo/production_quarantine.py /opt/usl/scripts/odoo/production_quarantine.py
COPY scripts/odoo/production_activate.py /opt/usl/scripts/odoo/production_activate.py
COPY scripts/odoo/production_side_effect_boundary.py /opt/usl/scripts/odoo/production_side_effect_boundary.py
COPY scripts/generate-receipt-fetcher-certs /opt/usl/scripts/generate-receipt-fetcher-certs
COPY scripts/sign-services-smoke.py /opt/usl/scripts/sign-services-smoke.py
COPY compose.resources.production.json compose.resources.staging.json /opt/usl/
COPY deploy/production.cron-policy.json /opt/usl/deploy/production.cron-policy.json
COPY --chmod=755 scripts/cohort-runtime /usr/local/bin/usl-cohort-runtime
COPY --chmod=755 scripts/usl-stack /usr/local/bin/usl-stack

# Provenance identity. It is declared after the package and copy layers so
# that a new input digest changes only the image config, not those layers.
ARG USL_COMPONENT_INPUT_SHA256=unverified

LABEL org.opencontainers.image.title="USL Odoo Backup Tool" \
      com.unstaticlabs.odoo.component-input-sha256="${USL_COMPONENT_INPUT_SHA256}" \
      com.unstaticlabs.odoo.runtime="backup"

ENV PYTHONPATH=/opt/usl

ENTRYPOINT ["/usr/local/bin/usl-cohort-runtime"]
CMD ["--help"]
