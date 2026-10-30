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
COMPOSE_PROJECT_NAME=usl-wt-<worktree-directory>-<path-checksum>
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

The project name includes the full checkout path's checksum. Managed checkouts
such as `worktrees/feedback/odoo` and `worktrees/agents/odoo` both end in `odoo`,
but must own different Compose projects. Persisted `.env` settings and explicit
project names keep their existing values; this changes newly generated defaults.
For an existing stack, keep its current project name until you intentionally
move that stack. Changing the name starts a separate project rather than adopting
the existing containers or volumes.

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

For ordinary local-password browser QA, create a disposable human with identity
classification unset, using the same native groups and companies as the intended
reviewer. An `active` identity is Pocket ID-managed in this distribution; its
native form/password login can be denied even when an `HttpCase` login helper
can impersonate it. Verify an actual password session before handing off the
fixture. Keep the intended SSO identity separate and never weaken Pocket ID
policy to make a local QA login work. Identity/SSO changes require their own
representative authentication journey.

Use a unique `*.localhost` hostname for each concurrent browser QA stack when
the local resolver supports it, for example `http://example-qa.localhost:18069`.
Different ports on `localhost` still share host cookies: signing into another
stack can replace the first stack's Odoo session. Hostname isolation avoids
that collision without changing authentication policy.

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

To use a different disposable database with this Make target, pass
`ACTION_RISK_DB=<task_database>` to `make`. Setting only `ODOO_ACTION_RISK_DB`
does not select it: the Make recipe exports that variable from
`ACTION_RISK_DB`, whose default is `odoo_action_risk`. Confirm the resolved name
before starting; this target drops and recreates its selected database.
The direct `scripts/odoo-dev action-risk-db` interface instead reads
`ODOO_ACTION_RISK_DB=<task_database>`.

It builds in its own `odoo_action_risk` database, dropped and recreated on
every run. The closure is never installed over an existing registry, because a
few Odoo action records store a raw database row id that is fixed on first
install and never rewritten: `base.ir_cron_act` embeds the id of
`mail.ir_cron_module_update_notification`, and the two account payment-receipt
actions embed the id of the receipt mail template. A database that inherits
another one's row ids reports exactly those entries as changed while its module
set still matches, which reads as an accounting change nobody made. Your own
`odoo_dev` is left alone.

It installs the surface's own `root_modules` — read from the tracked file, so
the database cannot drift from what the check compares against — then removes
the reviewed optional auto-installs with the same
`scripts/odoo/enforce_product_module_scope.py` that `ci-product-database` uses,
because the delivered closure does not carry them. Finally it checks the
resulting module set against the delivered surface and refuses if it differs in
either direction, so a module that starts auto-installing is reported here
rather than as spurious action entries later. That check compares the module
set only; it cannot see where the rows came from, which is why the database is
recreated rather than reused.

Between those steps it updates the closure twice, matching
`scripts/ci-product-database`. The second pass is not redundant: removing the
optional auto-installs leaves records the first update rewrites, so
data-defined actions only settle on the second. A database built without them
reports digest drift in modules the branch never touched. Afterwards
`make action-risk-discover` and `make action-risk-runtime` read that same
`odoo_action_risk` database from this checkout. See
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

Seven targets call `docker compose` directly and never consult the scope guard:
`action-risk-discover`, `action-risk-runtime`, `accounting-addon-tests`,
`addon-tests`, `accounting-multicompany-acceptance`, `product-assets` and
`french-translations`. Run them from a worktree only with an explicit
`COMPOSE_PROJECT`; otherwise they attach to the canonical project and create
the mixed ownership the guard exists to prevent.

## Running one add-on's tests

```bash
make addon-tests MODULE=usl_documents
make addon-tests MODULE=usl_documents ADDON_TEST_TAGS=/usl_documents:TestDocumentDownloadGrantHttp
```

Each run installs the module into a throwaway database and drops the container
afterwards, so it never touches the development database.

`HttpCase` serves real HTTP from that throwaway database, and Odoo resolves the
database of an incoming request through `dbfilter`. A filter that does not match
leaves `request.db` empty, Odoo serves the nodb routing map, and every
`auth="public"` route answers 404 — which reads as a broken controller. The
development default is `^odoo_dev$`, so a hand-written `docker compose run`
against `odoo_mytest_20260101` hits exactly that. The image entrypoint pins
`dbfilter` to the `--database` value for any `--test-enable` run, matching what
`scripts/ci-product-database` passes explicitly; a normal server run keeps the
configured filter untouched.

### Comparing a predecessor's translation catalogue

When qualifying an upgrade, verify the actually loaded predecessor add-on path,
stored module version and expected old methods before capturing evidence. A
priority predecessor path can load old Python correctly while leaving the current
add-on path configured too. Odoo's native translation resource reader scans all
configured paths, so its export can include both revisions and falsely report
zero newly added strings. Export with an exclusive pinned custom-addons tree,
including its dependencies, and compare that source catalogue with the candidate.
Keep the ordinary QA server, source and ownership settings separate from the
predecessor's one-shot runner.
