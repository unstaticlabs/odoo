# syntax=docker/dockerfile:1.7

ARG POSTGRES_IMAGE=postgres:16-bookworm@sha256:60f4761b9035e0b8d5218f701a8c3382f641bf12b1604822574cf5be3baeb537
ARG RESTIC_IMAGE=restic/restic:0.19.1@sha256:136600b6ff6843d61d355f7f71f460a166429f35de6fd11b568fece3c9a4d510

FROM ${RESTIC_IMAGE} AS restic
FROM ${POSTGRES_IMAGE} AS runtime

ARG USL_RELEASE_COMMIT=unverified

LABEL org.opencontainers.image.title="USL Odoo Backup Tool" \
      org.opencontainers.image.revision="${USL_RELEASE_COMMIT}" \
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
COPY --chmod=755 scripts/odoo_backup.py /usr/local/bin/odoo-backup-runtime

ENTRYPOINT ["python3", "/usr/local/bin/odoo-backup-runtime"]
CMD ["--help"]
