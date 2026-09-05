#!/bin/sh
set -eu

: "${ODOO_CONFIG:=/etc/odoo/odoo.conf}"
: "${ODOO_DATA_DIR:=/var/lib/odoo}"
: "${ODOO_ADDONS_PATH:=/opt/odoo/addons,/opt/odoo/odoo/addons,/mnt/custom-addons}"
: "${ODOO_DB_HOST:=${PGHOST:-db}}"
: "${ODOO_DB_PORT:=${PGPORT:-5432}}"
: "${ODOO_DB_USER:=${PGUSER:-odoo}}"
: "${ODOO_DB_PASSWORD:=${PGPASSWORD:-odoo}}"
: "${ODOO_DB_NAME:=}"
: "${ODOO_ADMIN_PASSWORD:=admin}"
: "${ODOO_HTTP_PORT:=8069}"
: "${ODOO_GEVENT_PORT:=8072}"
: "${ODOO_HTTP_INTERFACE:=0.0.0.0}"
: "${ODOO_WORKERS:=0}"
: "${ODOO_MAX_CRON_THREADS:=0}"
: "${ODOO_PROXY_MODE:=False}"
: "${ODOO_LIST_DB:=True}"
: "${ODOO_LOG_LEVEL:=info}"
: "${ODOO_DB_MAXCONN:=32}"
: "${ODOO_LIMIT_MEMORY_SOFT:=2147483648}"
: "${ODOO_LIMIT_MEMORY_HARD:=2684354560}"
: "${ODOO_LIMIT_REQUEST:=65536}"
: "${ODOO_LIMIT_TIME_CPU:=600}"
: "${ODOO_LIMIT_TIME_REAL:=1200}"
: "${ODOO_DB_FILTER:=.*}"
: "${ODOO_DEV_MODE:=}"
: "${ODOO_DEFAULT_PRODUCTIVITY_APPS:=True}"
: "${ODOO_SERVER_WIDE_MODULES:=web}"
: "${ODOO_SMTP_SERVER:=localhost}"
: "${ODOO_SMTP_PORT:=25}"
: "${ODOO_SMTP_SSL:=False}"
: "${ODOO_SMTP_USER:=}"
: "${ODOO_SMTP_PASSWORD:=}"
: "${ODOO_EMAIL_FROM:=}"
: "${ODOO_FROM_FILTER:=}"

export ODOO_ADMIN_PASSWORD ODOO_DB_HOST ODOO_DB_PORT ODOO_DB_USER ODOO_DB_PASSWORD ODOO_DB_NAME
export ODOO_ADDONS_PATH ODOO_DATA_DIR ODOO_HTTP_PORT ODOO_GEVENT_PORT ODOO_HTTP_INTERFACE
export ODOO_WORKERS ODOO_MAX_CRON_THREADS ODOO_PROXY_MODE ODOO_LOG_LEVEL ODOO_LIMIT_TIME_CPU
export ODOO_LIST_DB
export ODOO_SERVER_WIDE_MODULES
export ODOO_LIMIT_TIME_REAL ODOO_DB_FILTER ODOO_DEV_MODE ODOO_CONFIG ODOO_DEFAULT_PRODUCTIVITY_APPS
export ODOO_DB_MAXCONN ODOO_LIMIT_MEMORY_SOFT ODOO_LIMIT_MEMORY_HARD ODOO_LIMIT_REQUEST
export ODOO_SMTP_SERVER ODOO_SMTP_PORT ODOO_SMTP_SSL ODOO_SMTP_USER ODOO_SMTP_PASSWORD
export ODOO_EMAIL_FROM ODOO_FROM_FILTER

mkdir -p "$ODOO_DATA_DIR" /mnt/custom-addons "$(dirname "$ODOO_CONFIG")"

python - <<'PY'
import os
from pathlib import Path

template = Path("/etc/odoo/odoo.conf.template").read_text()
values = {
    key: os.environ.get(key, "")
    for key in (
        "ODOO_ADMIN_PASSWORD",
        "ODOO_DB_HOST",
        "ODOO_DB_PORT",
        "ODOO_DB_USER",
        "ODOO_DB_PASSWORD",
        "ODOO_DB_NAME",
        "ODOO_ADDONS_PATH",
        "ODOO_DATA_DIR",
        "ODOO_HTTP_PORT",
        "ODOO_GEVENT_PORT",
        "ODOO_HTTP_INTERFACE",
        "ODOO_WORKERS",
        "ODOO_MAX_CRON_THREADS",
        "ODOO_PROXY_MODE",
        "ODOO_LIST_DB",
        "ODOO_LOG_LEVEL",
        "ODOO_DB_MAXCONN",
        "ODOO_LIMIT_MEMORY_SOFT",
        "ODOO_LIMIT_MEMORY_HARD",
        "ODOO_LIMIT_REQUEST",
        "ODOO_LIMIT_TIME_CPU",
        "ODOO_LIMIT_TIME_REAL",
        "ODOO_DB_FILTER",
        "ODOO_DEV_MODE",
        "ODOO_DEFAULT_PRODUCTIVITY_APPS",
        "ODOO_SERVER_WIDE_MODULES",
        "ODOO_SMTP_SERVER",
        "ODOO_SMTP_PORT",
        "ODOO_SMTP_SSL",
        "ODOO_SMTP_USER",
        "ODOO_SMTP_PASSWORD",
        "ODOO_EMAIL_FROM",
        "ODOO_FROM_FILTER",
    )
}
rendered = template
for key, value in values.items():
    rendered = rendered.replace("${" + key + "}", value)
database_name = os.environ.get("ODOO_DB_NAME", "").strip()
rendered = rendered.replace(
    "${ODOO_DB_NAME_CONFIG}",
    f"db_name = {database_name}" if database_name else "",
)
Path(os.environ.get("ODOO_CONFIG", "/etc/odoo/odoo.conf")).write_text(rendered)
PY

if [ "${1:-}" = "odoo" ] || [ "${1:-}" = "odoo-bin" ] || [ "${1:-}" = "python" ]; then
    wait-for-it "${ODOO_DB_HOST}:${ODOO_DB_PORT}" --timeout=60 --strict -- "$@"
else
    exec "$@"
fi
