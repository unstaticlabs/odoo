#!/bin/sh
set -eu

: "${ODOO_CONFIG:=/etc/odoo/odoo.conf}"
: "${ODOO_DATA_DIR:=/var/lib/odoo}"
: "${ODOO_ADDONS_PATH:=/opt/odoo/addons,/opt/odoo/odoo/addons,/mnt/custom-addons}"
: "${ODOO_DB_HOST:=${PGHOST:-db}}"
: "${ODOO_DB_PORT:=${PGPORT:-5432}}"
: "${ODOO_DB_USER:=${PGUSER:-odoo}}"
: "${ODOO_DB_PASSWORD:=${PGPASSWORD:-odoo}}"
: "${ODOO_ADMIN_PASSWORD:=admin}"
: "${ODOO_HTTP_PORT:=8069}"
: "${ODOO_GEVENT_PORT:=8072}"
: "${ODOO_HTTP_INTERFACE:=0.0.0.0}"
: "${ODOO_WORKERS:=0}"
: "${ODOO_PROXY_MODE:=False}"
: "${ODOO_LOG_LEVEL:=info}"
: "${ODOO_LIMIT_TIME_CPU:=600}"
: "${ODOO_LIMIT_TIME_REAL:=1200}"
: "${ODOO_DB_FILTER:=.*}"
: "${ODOO_DEV_MODE:=}"

export ODOO_ADMIN_PASSWORD ODOO_DB_HOST ODOO_DB_PORT ODOO_DB_USER ODOO_DB_PASSWORD
export ODOO_ADDONS_PATH ODOO_DATA_DIR ODOO_HTTP_PORT ODOO_GEVENT_PORT ODOO_HTTP_INTERFACE
export ODOO_WORKERS ODOO_PROXY_MODE ODOO_LOG_LEVEL ODOO_LIMIT_TIME_CPU
export ODOO_LIMIT_TIME_REAL ODOO_DB_FILTER ODOO_DEV_MODE ODOO_CONFIG

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
        "ODOO_ADDONS_PATH",
        "ODOO_DATA_DIR",
        "ODOO_HTTP_PORT",
        "ODOO_GEVENT_PORT",
        "ODOO_HTTP_INTERFACE",
        "ODOO_WORKERS",
        "ODOO_PROXY_MODE",
        "ODOO_LOG_LEVEL",
        "ODOO_LIMIT_TIME_CPU",
        "ODOO_LIMIT_TIME_REAL",
        "ODOO_DB_FILTER",
        "ODOO_DEV_MODE",
    )
}
rendered = template
for key, value in values.items():
    rendered = rendered.replace("${" + key + "}", value)
Path(os.environ.get("ODOO_CONFIG", "/etc/odoo/odoo.conf")).write_text(rendered)
PY

if [ "${1:-}" = "odoo" ] || [ "${1:-}" = "odoo-bin" ] || [ "${1:-}" = "python" ]; then
    wait-for-it "${ODOO_DB_HOST}:${ODOO_DB_PORT}" --timeout=60 --strict -- "$@"
else
    exec "$@"
fi
