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
inside one set are 3, 59 and 6599, none of them a multiple of ten, so no
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

`make worktree-env` isolates the runtime, not the data, so a worktree starts
with no database and `make doctor` reports one missing. What to do next depends
on what you actually need, and the two needs are not interchangeable.

**A registry to develop and test against.** Built from the add-ons in this
checkout, no source dump involved:

```bash
make init-db
```

That installs `ODOO_INIT_MODULES`, defaulting to `rebuild_account_migration`.
Set it to install something else:

```bash
ODOO_INIT_MODULES=usl_documents make init-db
```

**The action-risk closure.** Discovery compares against the module set recorded
in the tracked `action_surface.json`, and a database one module wide of it
produces thousands of spurious entries that hide the change under review:

```bash
make action-risk-db
```

It installs the surface's own `root_modules` — read from the tracked file, so
the database cannot drift from what the check compares against — and then
prunes whatever `auto_install` dragged in, because the delivered closure does
not carry those. It refuses to prune when the tracked closure is not fully
installed, so pointing it at the wrong database cannot empty it. Afterwards
`make action-risk-discover` works from this checkout. See
[the action-risk review procedure](action-risk-inventory.md) for what to do
with the resulting diff.

**Production-shaped data.** Neither of the above reconstructs the evolved data
cohort. Deploy cannot recreate source data; reconstruct a migration target only
with `migration/manage qa refresh`.

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
