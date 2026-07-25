# syntax=docker/dockerfile:1.7

ARG PYTHON_IMAGE=python:3.12-slim-bookworm

FROM ${PYTHON_IMAGE} AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    LANG=C.UTF-8

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential \
        ca-certificates \
        curl \
        fontconfig \
        fonts-dejavu-core \
        fonts-font-awesome \
        fonts-inconsolata \
        fonts-noto-core \
        fonts-roboto-unhinted \
        gcc \
        git \
        gnupg \
        libfreetype6 \
        libjpeg62-turbo \
        libldap2-dev \
        libpq-dev \
        libsasl2-dev \
        libxml2-dev \
        libxslt1-dev \
        nodejs \
        npm \
        postgresql-client \
        python3-dev \
        wait-for-it \
        wkhtmltopdf \
        zlib1g-dev \
    && npm install --global rtlcss@4.3.0 \
    && groupadd --gid 1000 odoo \
    && useradd --uid 1000 --gid 1000 --create-home --shell /bin/bash odoo \
    && mkdir -p /etc/odoo /opt/odoo /var/lib/odoo /mnt/custom-addons \
    && chown -R odoo:odoo /etc/odoo /opt/odoo /var/lib/odoo /mnt/custom-addons \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /opt/odoo

COPY --chown=odoo:odoo requirements.txt setup.py MANIFEST.in odoo-bin ./
COPY --chown=odoo:odoo docker/constraints.txt ./docker/constraints.txt
COPY --chown=odoo:odoo setup ./setup
COPY --chown=odoo:odoo odoo ./odoo
RUN pip install --upgrade pip==25.2 \
    && pip install --constraint docker/constraints.txt -r requirements.txt \
    && chmod +x /opt/odoo/odoo-bin \
    && ln -s /opt/odoo/odoo-bin /usr/local/bin/odoo

COPY --chown=odoo:odoo addons ./addons
COPY --chown=odoo:odoo debian/odoo.conf /etc/odoo/upstream-odoo.conf
COPY --chown=odoo:odoo docker/odoo.conf.template /etc/odoo/odoo.conf.template
COPY --chown=odoo:odoo docker/entrypoint.sh /usr/local/bin/odoo-entrypoint
RUN chmod +x /usr/local/bin/odoo-entrypoint

USER odoo

EXPOSE 8069 8072
VOLUME ["/var/lib/odoo", "/mnt/custom-addons"]

ENTRYPOINT ["odoo-entrypoint"]
CMD ["odoo", "--config=/etc/odoo/odoo.conf"]

FROM base AS dev

USER root
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        bash-completion \
        less \
        openssh-client \
        procps \
        sudo \
        vim-tiny \
    && pip install \
        debugpy==1.8.16 \
        inotify==0.2.10 \
        pytest==8.4.1 \
        ruff==0.15.0 \
    && echo "odoo ALL=(root) NOPASSWD:ALL" > /etc/sudoers.d/odoo \
    && chmod 0440 /etc/sudoers.d/odoo \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /workspace/odoo
USER odoo
