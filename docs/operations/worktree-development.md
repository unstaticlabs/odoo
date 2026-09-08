# Developing in a git worktree

A Compose project belongs to exactly one checkout, because its bind mounts and
build context resolve from that checkout's path. The canonical project
`usl-odoo-saas-19-3` therefore belongs to the main checkout alone, and every
linked worktree needs its own project name and its own published host ports.

The tooling refuses the canonical project from a worktree rather than letting
two checkouts share containers, which would leave the running Odoo serving one
checkout's code while another believes it owns the stack.

## Get this worktree's settings

```bash
make worktree-env
```

It prints one assignment per line, derived from the checkout path, so the same
worktree always resolves to the same project and ports:

```
COMPOSE_PROJECT_NAME=usl-wt-<worktree-directory>
ODOO_HTTP_PORT=<derived>
ODOO_GEVENT_PORT=<derived>
POCKET_ID_HTTP_PORT=<derived>
PAPERLESS_HTTP_PORT=<derived>
```

Ports move together in steps of ten from a base offset of 10000. The gaps
inside one set are 3, 59 and 6598, none of them a multiple of ten, so no
worktree's port can land on another worktree's. Two worktrees collide only when
their paths fall in the same bucket of four hundred, and Docker then reports a
plain bind failure rather than silently sharing a runtime.

## Use them

Persist them once for the checkout:

```bash
make worktree-env >> .env
```

Compose reads `.env` automatically; `make` reads the project name from it as a
last fallback, and `usl_cli_load_local_port_defaults` reads the same four ports,
so `make status` prints URLs matching what Compose actually published. Explicit
shell variables still win over the file.

Or prefix a single command:

```bash
COMPOSE_PROJECT_NAME=usl-wt-example ODOO_HTTP_PORT=18639 ODOO_GEVENT_PORT=18642 \
  POCKET_ID_HTTP_PORT=11981 PAPERLESS_HTTP_PORT=18580 \
  make dev
```

Any command blocked by the scope guard prints exactly this prefix, already
filled in for the checkout you ran it from.

## Naming the project

Three spellings reach the same setting. Prefer the first:

| Variable | Use |
| --- | --- |
| `COMPOSE_PROJECT_NAME` | Compose's own variable; what `make worktree-env` emits, and the only one a bare `docker compose` reads |
| `COMPOSE_PROJECT` | `make`-only alias, highest precedence |
| `ODOO_SAAS_COMPOSE_PROJECT` | Legacy spelling, still accepted |

`make` resolves `COMPOSE_PROJECT`, then `COMPOSE_PROJECT_NAME`, then
`ODOO_SAAS_COMPOSE_PROJECT`, then `COMPOSE_PROJECT_NAME` from `.env`, and
exports the result as `COMPOSE_PROJECT_NAME` for Compose and every helper
script. `scripts/odoo-dev` and `accounting_compat.cli` resolve the same order.

## The database

`make worktree-env` isolates the runtime, not the data. A worktree still starts
with no `odoo_dev` database, and `make doctor` reports it as missing. Deploy
cannot recreate source data; reconstruct a migration target only with
`migration/manage qa refresh`.

For work that needs a registry rather than production-shaped data — running an
add-on test suite, or `make action-risk-discover` — initialize a scratch
database from the tracked root modules instead. See
[the action-risk review procedure](action-risk-inventory.md), which also
explains why the installed module set must equal the tracked `modules` list
before a discovery diff can be trusted.

## When a project is already taken

`make doctor` classifies the project as `unused`, `owned`, `foreign` or
`mixed`. For `foreign` or `mixed`, reclaim it from the checkout that should own
it:

```bash
make dev-reclaim CONFIRM=<project>
```

The confirmation must name the same project the command resolved, so a stale
copy-paste cannot remove the wrong checkout's containers. Only containers are
removed; named volumes survive, so the database and filestore are untouched and
the next `make dev` recreates the containers against the confirming checkout.

## Targets that do not check

Six targets call `docker compose` directly and never consult the scope guard:
`action-risk-discover`, `action-risk-runtime`, `accounting-addon-tests`,
`accounting-multicompany-acceptance`, `product-assets` and
`french-translations`. Run them from a worktree only with an explicit
`COMPOSE_PROJECT`; otherwise they attach to the canonical project and create
the mixed ownership the guard exists to prevent.
