# syntax=docker/dockerfile:1.7

ARG PYTHON_IMAGE=python:3.12-slim-bookworm

# Compile Python dependencies once. Build tools and development headers remain
# in this disposable stage and are not copied into any runnable image.
FROM ${PYTHON_IMAGE} AS python-dependencies

ENV PIP_DISABLE_PIP_VERSION_CHECK=1

RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt,sharing=locked \
    apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential \
        libldap2-dev \
        libpq-dev \
        libsasl2-dev \
        libxml2-dev \
        libxslt1-dev \
        zlib1g-dev

COPY requirements.txt /tmp/requirements.txt
COPY docker/constraints.txt /tmp/constraints.txt

RUN --mount=type=cache,target=/root/.cache/pip \
    python -m pip install --upgrade pip==25.2 \
    && mkdir -p /opt/wheels \
    && python -m pip download --dest /opt/wheels pip==25.2 \
    && python -m pip wheel \
        --wheel-dir /opt/wheels \
        --constraint /tmp/constraints.txt \
        --requirement /tmp/requirements.txt

# npm is needed only to resolve rtlcss and its JavaScript dependencies. The
# runnable image receives that package and Node.js, not npm's build ecosystem.
FROM ${PYTHON_IMAGE} AS node-dependencies

RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt,sharing=locked \
    --mount=type=cache,target=/root/.npm \
    apt-get update \
    && apt-get install -y --no-install-recommends nodejs npm \
    && npm install --global --prefix /opt/node rtlcss@4.3.0

# Shared runnable environment. It contains only runtime libraries; Python
# wheels are installed from the builder without copying the wheelhouse.
FROM ${PYTHON_IMAGE} AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    LANG=C.UTF-8

RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt,sharing=locked \
    apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        fontconfig \
        fonts-dejavu-core \
        fonts-font-awesome \
        fonts-inconsolata \
        fonts-noto-core \
        fonts-roboto-unhinted \
        libfreetype6 \
        libjpeg62-turbo \
        libldap-2.5-0 \
        libmagic1 \
        libpq5 \
        libsasl2-2 \
        nodejs \
        postgresql-client \
        wait-for-it \
        wkhtmltopdf \
    && groupadd --gid 1000 odoo \
    && useradd --uid 1000 --gid 1000 --create-home --shell /bin/bash odoo \
    && mkdir -p /etc/odoo /opt/odoo /var/lib/odoo /mnt/custom-addons \
    && chown -R odoo:odoo /etc/odoo /opt/odoo /var/lib/odoo /mnt/custom-addons

COPY --link --from=node-dependencies /opt/node/ /usr/local/
COPY --from=python-dependencies /tmp/requirements.txt /tmp/requirements.txt
COPY --from=python-dependencies /tmp/constraints.txt /tmp/constraints.txt
RUN --mount=type=bind,from=python-dependencies,source=/opt/wheels,target=/opt/wheels \
    python -m pip install \
        --no-index \
        --find-links /opt/wheels \
        pip==25.2 \
    && python -m pip install \
        --no-index \
        --find-links /opt/wheels \
        --constraint /tmp/constraints.txt \
        --requirement /tmp/requirements.txt \
    && rm /tmp/requirements.txt /tmp/constraints.txt

COPY --link --chown=1000:1000 debian/odoo.conf /etc/odoo/upstream-odoo.conf
COPY --link --chown=1000:1000 docker/odoo.conf.template /etc/odoo/odoo.conf.template
COPY --link --chown=1000:1000 --chmod=755 docker/entrypoint.sh /usr/local/bin/odoo-entrypoint

EXPOSE 8069 8072
VOLUME ["/var/lib/odoo"]
ENTRYPOINT ["odoo-entrypoint"]
CMD ["odoo", "--config=/etc/odoo/odoo.conf"]

# Self-contained deployment image. Source changes invalidate only these copy
# layers, not system packages or Python dependency compilation.
FROM runtime AS base

WORKDIR /opt/odoo

COPY --link --chown=1000:1000 requirements.txt setup.py MANIFEST.in ./
COPY --link --chown=1000:1000 --chmod=755 odoo-bin ./
COPY --link --chown=1000:1000 setup ./setup
COPY --link --chown=1000:1000 odoo ./odoo
COPY --link --chown=1000:1000 addons ./addons

RUN ln -s /opt/odoo/odoo-bin /usr/local/bin/odoo

USER odoo

# Resolve the pinned OCA add-on symlinks into real module trees so the product
# image carries only the selected modules, not the oca-src vendor checkouts.
# Run `make oca-addons-sync` before building this stage.
FROM ${PYTHON_IMAGE} AS oca-resolve

COPY oca-src /srv/oca-src
COPY oca-addons /srv/oca-addons
RUN cp -rL /srv/oca-addons /srv/resolved \
    && find /srv/resolved -mindepth 2 -maxdepth 2 -name __manifest__.py | grep -q . \
    || { echo "No resolved OCA modules; run make oca-addons-sync first" >&2; exit 1; }

# Self-contained product image for QA/production deployments. It embeds the
# custom add-ons, resolved OCA add-ons and user documentation that Compose
# bind-mounts in development, so a deployment host needs no repository
# checkout. The test-only usl_bootstrap fixture is excluded by .dockerignore.
FROM base AS product

COPY --link --chown=1000:1000 --from=oca-resolve /srv/resolved ./oca-addons
COPY --link --chown=1000:1000 custom-addons ./custom-addons
COPY --link --chown=1000:1000 docs/users ./docs/users

ENV ODOO_ADDONS_PATH=/opt/odoo/addons,/opt/odoo/odoo/addons,/opt/odoo/custom-addons,/opt/odoo/oca-addons \
    USL_USER_DOCS_PATH=/opt/odoo/docs/users

# Qualified deployment artifact. Unlike the development image, this stage is
# self-contained: the exact USL/OCA add-ons and user documentation travel with
# the image and cannot drift with a host checkout after qualification.
FROM product AS distribution

ARG USL_COMPONENT_INPUT_SHA256=unverified
ARG USL_OCA_BUNDLE_SHA256=unverified
ARG USL_ACTION_RISK_POLICY_SHA256=unverified

LABEL org.opencontainers.image.title="USL Odoo Distribution" \
      com.unstaticlabs.odoo.component-input-sha256="${USL_COMPONENT_INPUT_SHA256}" \
      com.unstaticlabs.odoo.oca-bundle-sha256="${USL_OCA_BUNDLE_SHA256}" \
      com.unstaticlabs.odoo.action-risk-policy-sha256="${USL_ACTION_RISK_POLICY_SHA256}" \
      com.unstaticlabs.odoo.runtime="distribution"

ENV USL_COMPONENT_INPUT_SHA256="${USL_COMPONENT_INPUT_SHA256}" \
    USL_OCA_BUNDLE_SHA256="${USL_OCA_BUNDLE_SHA256}" \
    USL_ACTION_RISK_POLICY_SHA256="${USL_ACTION_RISK_POLICY_SHA256}"


# Browser-capable test image, built only when the test profile is requested.
FROM base AS test

USER root

RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt,sharing=locked \
    --mount=type=cache,target=/root/.cache/pip \
    apt-get update \
    && apt-get install -y --no-install-recommends chromium \
    && PIP_NO_CACHE_DIR=0 python -m pip install \
        responses==0.26.2 \
        websocket-client==1.9.0

USER odoo

# The repository is bind-mounted at /workspace/odoo by Compose, so embedding a
# second 1.2 GB source copy in the Dev Container is unnecessary and misleading.
FROM runtime AS dev

USER root

RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt,sharing=locked \
    --mount=type=cache,target=/root/.cache/pip \
    apt-get update \
    && apt-get install -y --no-install-recommends \
        bash-completion \
        less \
        openssh-client \
        procps \
        sudo \
        vim-tiny \
    && PIP_NO_CACHE_DIR=0 python -m pip install \
        debugpy==1.8.16 \
        inotify==0.2.10 \
        pytest==8.4.1 \
        responses==0.26.2 \
        ruff==0.16.1 \
    && printf 'odoo ALL=(root) NOPASSWD:ALL\n' > /etc/sudoers.d/odoo \
    && chmod 0440 /etc/sudoers.d/odoo \
    && ln -s /workspace/odoo/odoo-bin /usr/local/bin/odoo

WORKDIR /workspace/odoo
USER odoo
