# QA VPS stack — infra handoff

Deploy a QA instance of the USL Odoo Accounting product as a Komodo-managed
Docker Compose stack, exposed only through a Cloudflare Tunnel. This document
is self-contained: the compose file and env template below are the deliverable.

## Topology

- `db`: PostgreSQL 16, named volume, never exposed.
- `odoo`: prebuilt product image pulled from a registry (never built on the
  VPS), named filestore volume, no published ports.
- `cloudflared`: remote-managed Cloudflare Tunnel, the only path in.

Phase 1 excludes the Documents/Paperless stack (see "Phase 2" at the end).

## Inputs you will receive from Roger (do not guess or substitute these)

| Variable | Meaning |
|---|---|
| `ODOO_IMAGE` | Pinned registry reference, e.g. `ghcr.io/unstaticlabs/usl-odoo:<git-sha>`. The image embeds Odoo core, custom addons, OCA addons and user docs. |
| `CLOUDFLARE_TUNNEL_TOKEN` | Token of a remote-managed tunnel created for this VPS. |
| `USL_POCKET_ID_CLIENT_ID` / `USL_POCKET_ID_CLIENT_SECRET` | A **QA-specific** OIDC client registered on the existing production Pocket ID. Never the production Odoo client. |
| QA hostname | e.g. `odoo-qa.<domain>` — used in several variables below. |

## Hard rules

1. Publish **no** ports on the VPS. All ingress goes through the tunnel.
   (Optionally bind `127.0.0.1:8069` for on-host debugging, nothing else.)
2. Generate strong unique secrets: `openssl rand -base64 32` for passwords,
   never the defaults `odoo` / `admin`, and never `admin`/`admin` app logins.
3. `USL_EINVOICE_LIVE_ENABLED` and `USL_EREPORTING_LIVE_ENABLED` stay `0`.
   These guard French e-invoicing regulatory side effects. Never flip them.
4. Do **not** configure any Paperless URL in phase 1. In particular, never
   point this stack at the production Paperless instance — the Odoo Documents
   module writes to the archive it is pointed at.
5. `ODOO_MAX_CRON_THREADS` starts at `0`. A production-derived database will
   be restored into this stack; scheduled jobs (mail, integrations) must not
   run until the database has been reviewed and outgoing mail neutralized.
   Roger will say when to raise it to `1`.
6. Pin every image by exact tag (and digest where possible). No `:latest`.

## Compose file

Deploy as a Komodo stack named `usl-odoo-qa`. Komodo's stack environment
provides the variables (see env template below).

```yaml
name: usl-odoo-qa

services:
  db:
    image: postgres:16-bookworm
    restart: unless-stopped
    environment:
      POSTGRES_DB: postgres
      POSTGRES_USER: odoo
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD:?}
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U \"$${POSTGRES_USER}\" -d \"$${POSTGRES_DB}\""]
      interval: 10s
      timeout: 5s
      retries: 10
      start_period: 10s
    volumes:
      - postgres-data:/var/lib/postgresql/data

  odoo:
    image: ${ODOO_IMAGE:?}
    restart: unless-stopped
    depends_on:
      db:
        condition: service_healthy
    environment:
      ODOO_DB_HOST: db
      ODOO_DB_PORT: 5432
      ODOO_DB_USER: odoo
      ODOO_DB_PASSWORD: ${POSTGRES_PASSWORD:?}
      ODOO_DB_NAME: ${ODOO_DB_NAME:-odoo_usl_qa}
      ODOO_DB_FILTER: ${ODOO_DB_FILTER:-^odoo_usl_qa$}
      ODOO_ADMIN_PASSWORD: ${ODOO_ADMIN_PASSWORD:?}
      ODOO_HTTP_PORT: 8069
      ODOO_GEVENT_PORT: 8072
      ODOO_HTTP_INTERFACE: 0.0.0.0
      ODOO_PROXY_MODE: "True"
      ODOO_WORKERS: ${ODOO_WORKERS:-2}
      ODOO_MAX_CRON_THREADS: ${ODOO_MAX_CRON_THREADS:-0}
      ODOO_LOG_LEVEL: ${ODOO_LOG_LEVEL:-info}
      USL_DEPLOYMENT_ENV: preproduction
      USL_EINVOICE_LIVE_ENABLED: "0"
      USL_EREPORTING_LIVE_ENABLED: "0"
      USL_POCKET_ID_ENABLED: ${USL_POCKET_ID_ENABLED:-0}
      USL_POCKET_ID_ISSUER: ${USL_POCKET_ID_ISSUER:-}
      USL_POCKET_ID_CLIENT_ID: ${USL_POCKET_ID_CLIENT_ID:-}
      USL_POCKET_ID_CLIENT_SECRET: ${USL_POCKET_ID_CLIENT_SECRET:-}
      USL_POCKET_ID_ODOO_BASE_URL: ${USL_POCKET_ID_ODOO_BASE_URL:-}
      USL_POCKET_ID_REQUIRED_GROUP: ${USL_POCKET_ID_REQUIRED_GROUP:-}
      USL_POCKET_ID_SCOPES: openid profile email groups
    healthcheck:
      test: ["CMD-SHELL", "python -c \"import urllib.request; urllib.request.urlopen('http://127.0.0.1:8069/web/health?db_server_status=1', timeout=5).read()\""]
      interval: 15s
      timeout: 5s
      retries: 20
      start_period: 30s
    volumes:
      - odoo-data:/var/lib/odoo

  cloudflared:
    image: ${CLOUDFLARED_IMAGE:?}   # pin an exact cloudflare/cloudflared release tag
    restart: unless-stopped
    command: ["tunnel", "--no-autoupdate", "run", "--token", "${CLOUDFLARE_TUNNEL_TOKEN:?}"]
    depends_on:
      odoo:
        condition: service_started

volumes:
  postgres-data:
  odoo-data:
```

## Environment template (Komodo stack environment)

```dotenv
# --- provided by Roger, do not invent ---
ODOO_IMAGE=CHANGE_ME_PINNED_REGISTRY_IMAGE
CLOUDFLARE_TUNNEL_TOKEN=CHANGE_ME
USL_POCKET_ID_CLIENT_ID=CHANGE_ME
USL_POCKET_ID_CLIENT_SECRET=CHANGE_ME
USL_POCKET_ID_ISSUER=CHANGE_ME_PROD_POCKET_ID_URL          # e.g. https://identity.example.com
USL_POCKET_ID_ODOO_BASE_URL=CHANGE_ME_QA_URL               # e.g. https://odoo-qa.example.com

# --- generate on the VPS side (openssl rand -base64 32) ---
POSTGRES_PASSWORD=CHANGE_ME
ODOO_ADMIN_PASSWORD=CHANGE_ME

# --- fixed for QA ---
CLOUDFLARED_IMAGE=CHANGE_ME_PINNED                          # exact cloudflare/cloudflared tag
ODOO_DB_NAME=odoo_usl_qa
ODOO_DB_FILTER=^odoo_usl_qa$
ODOO_WORKERS=2                                              # ~1 per vCPU on a small VPS
ODOO_MAX_CRON_THREADS=0                                     # keep 0 until told otherwise
USL_POCKET_ID_ENABLED=0                                     # flip to 1 once the QA OIDC client exists
USL_POCKET_ID_REQUIRED_GROUP=                               # optional QA gate group
```

## Cloudflare Tunnel configuration

Remote-managed tunnel with two public-hostname ingress rules, in this order:

1. `odoo-qa.<domain>`, path `websocket` → `http://odoo:8072`
   (Odoo's gevent/websocket endpoint; live chat/notifications break without it)
2. `odoo-qa.<domain>`, no path → `http://odoo:8069`

Recommended: put a Cloudflare Access policy on the hostname while this is QA.

Known edge limits to accept for QA: ~100 s proxied-request timeout (long
synchronous report exports may 524) and 100 MB upload cap on non-Enterprise
plans.

## Bring-up and smoke test

1. Deploy the stack; wait for `db` and then `odoo` to report healthy.
   `odoo` will be healthy even before any database exists.
2. Optional empty-database smoke test (verifies image + config end to end):

   ```bash
   docker compose run --rm odoo odoo --config=/etc/odoo/odoo.conf \
     --database=odoo_usl_qa_smoke --init=base --without-demo=true --stop-after-init
   ```

   Then drop it: `docker compose exec db psql -U odoo -d postgres -c 'DROP DATABASE odoo_usl_qa_smoke'`.
3. Confirm `https://odoo-qa.<domain>/web/health` returns OK through the tunnel
   and that nothing on the VPS listens publicly (`ss -tlnp`).
4. Report back: the real QA database (a restored production-derived dump plus
   filestore copied into the `odoo-data` volume) is loaded by Roger, not by
   this stack. Leave the volumes in place.

## Phase 2 (not now)

A later iteration adds an **isolated** QA Paperless-ngx stack (its own
Postgres, Valkey, Gotenberg, Tika, own tunnel hostname, own Pocket ID client)
for the Documents module. It will be handed off separately. Until then the
Documents UI in QA may show sync errors; that is expected and acceptable.
