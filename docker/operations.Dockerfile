# syntax=docker/dockerfile:1.7

ARG POSTGRES_IMAGE=postgres:16-bookworm@sha256:60f4761b9035e0b8d5218f701a8c3382f641bf12b1604822574cf5be3baeb537
ARG RESTIC_IMAGE=restic/restic:0.19.1@sha256:136600b6ff6843d61d355f7f71f460a166429f35de6fd11b568fece3c9a4d510

FROM ${RESTIC_IMAGE} AS restic
FROM ${POSTGRES_IMAGE} AS runtime

ARG USL_RELEASE_COMMIT=unverified

LABEL org.opencontainers.image.title="USL Odoo Continuous Operations" \
      org.opencontainers.image.revision="${USL_RELEASE_COMMIT}" \
      com.unstaticlabs.odoo.runtime="continuous-operations"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    RESTIC_CACHE_DIR=/cache \
    USL_EINVOICE_LIVE_ENABLED=0 \
    USL_EREPORTING_LIVE_ENABLED=0

RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt,sharing=locked \
    apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates git python3 python3-psycopg2 \
    && rm -rf /var/lib/apt/lists/*

COPY --from=restic /usr/bin/restic /usr/local/bin/restic
COPY scripts/continuous_operations_contracts.py /opt/usl/continuous_operations_contracts.py
COPY scripts/continuous_operations_compose.py /opt/usl/continuous_operations_compose.py
COPY scripts/deployment_run.py /opt/usl/deployment_run.py
COPY scripts/distribution_release.py /opt/usl/distribution_release.py
COPY scripts/odoo_backup.py /opt/usl/odoo_backup.py
COPY scripts/production_cohort.py /opt/usl/production_cohort.py
COPY scripts/release_identity.py /opt/usl/release_identity.py
COPY scripts/retention_policy.py /opt/usl/retention_policy.py
COPY scripts/upgrade_plan.py /opt/usl/upgrade_plan.py
COPY --chmod=755 scripts/continuous-operations /usr/local/bin/continuous-operations
COPY deploy/continuous-operations/compose.yaml /opt/deploy/continuous-operations/compose.yaml

ENTRYPOINT ["continuous-operations"]
CMD ["run", "--help"]
