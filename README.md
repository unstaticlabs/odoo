# Unstatic Labs Odoo Distribution

This repository is Unstatic Labs’ production-oriented Odoo Distribution on
Odoo Community `saas~19.3`. It extends upstream through isolated modules under
`custom-addons/` and pinned OCA dependencies. Upstream Odoo core remains
unchanged except for the two documented fiscal-year sequencing patches.

Accounting v1 provides the daily cockpit for journals, invoices, bills,
expenses, payments, bank transactions and reconciliation, plus assets,
deferrals, currencies, analytics, Hygiene, closing, declarations, interactive
financial reports, PDF/XLSX exports and FEC. A scoped read-only accountant can
inspect the same accounting and evidence without posting, reconciling,
configuring or locking records.

Documents provides a native Odoo workspace backed by Paperless-ngx for
search, OCR, previews, metadata, versions and originals. Odoo remains the
authority for companies, business links and access; every file operation is
authorized through Odoo. Files attached to supported business records remain
immediately usable in Odoo and are archived asynchronously with their record,
company and safe classification context.

Official human-readable PDFs use the governed USL LaTeX document system.
Customer invoices and credit notes, canonical accounting statements and
official correspondence retain Odoo's report, mail, portal and attachment
workflows while rendering in an isolated, pinned service. Machine exports and
third-party originals remain native or source passthrough.

French electronic-invoice reception is implemented and validated offline for
UBL, CII and Factur-X invoices and credit notes. It remains **Ready but
inactive**: no directory registration, production provider endpoint, scheduled
reception or e-reporting may be enabled before the deliberate production
activation procedure is approved.

Primary entry points:

- [Distribution fork overview](docs/product/fork-overview.md) for the complete
  feature-to-module, screen, documentation and maturity map;
- **Accounting > Overview** for daily operational state;
- **Expenses > Expense Batches** (**Lots de dépenses** in French) for contextual trip, project, event and
  periodic claims with mixed payer review;
- **Accounting > Reporting > Analyse analytique** for exploratory pivot analysis;
- **Accounting > Configuration** for governed Controls, Reports, Declarations
  and E-Invoicing;
- **Documents** for the searchable Paperless-backed business archive;
- `/usl/user-docs` for role- and task-based user guidance;
- `/usl/user-docs/how-to/activate-electronic-invoice-reception.md` for the
  production reception switch and rollback checklist;
- [Accounting development workflow](docs/operations/accounting-development-workflow.md)
  for safe iteration;
- [Accounting compatibility harness](docs/accounting/accounting-compat-harness.md)
  for reconstruction and parity evidence.
- [LaTeX document system](docs/product/latex-document-system.md) and its
  [operations runbook](docs/operations/document-renderer-runbook.md) for the
  governed renderer, output classifications and deployment contract.
- [Accounting restoration boundary](migration/accounting_restore/README.md)
  for the one-off importer lifecycle and finalization contract.
- [Multi-company Accounting](docs/accounting/multi-company-accounting.md) for
  company isolation, combined management reporting and the explicit legal
  consolidation boundary.
- [Projects restoration runbook](docs/operations/project-restoration.md) for
  repeatable Odoo Online project and task recovery.
- [Product and migration boundary](docs/agents/product-migration-boundary.md)
  for keeping reconstruction machinery out of the delivered Odoo runtime.
- [Source-truth migration](docs/operations/source-truth-migration.md) for the
  current-product gate, whole-source coverage ledger, filestore integrity and
  deterministic replay contract.
- [Pre-production release](docs/operations/preproduction-release.md) for the
  one-command qualified build, reconstruction, gate, deployment and rollback.

The integration baseline is upstream commit
`aef56898d9ea5a97948af04c03ae101d17b8b4a3`. The source dump and generated
validation evidence are private local artifacts and must never be committed.

## Upstream Odoo

Odoo is a suite of web-based open source business applications. Standard Odoo
installation and developer documentation is available from
[odoo.com](https://www.odoo.com/documentation/19.0/).

## Docker and Dev Container setup

This Distribution includes two local workflows for Odoo `saas~19.3` Community. The
branch is pinned to upstream commit
`aef56898d9ea5a97948af04c03ae101d17b8b4a3`. Local development uses one
disposable product database named `odoo_dev`:

- Developer workflow: use the Dev Container and run Odoo from the mounted source tree.
- QA/test workflow: use Docker Compose only, with no editor container, to run a local Odoo service and PostgreSQL.

Both workflows build Odoo from this repository, use PostgreSQL from Compose, store PostgreSQL data and the Odoo filestore in named volumes, and keep custom addons outside Odoo core under `custom-addons/`.

### Build design

The root `Dockerfile` uses purpose-specific stages:

- `python-dependencies` compiles pinned Python wheels and is never shipped;
- `node-dependencies` resolves `rtlcss` without shipping npm's build tooling;
- `runtime` contains only shared runtime libraries and configuration;
- `base` adds the Odoo source for self-contained QA/deployment images;
- `product` extends `base` with the custom add-ons, the resolved pinned OCA
  add-ons and the user docs, so deployment hosts pull one registry artifact
  and need no repository checkout. Run `make oca-addons-sync` before building
  it. The test-only `usl_bootstrap` fixture is excluded. The
  `.github/workflows/product-image.yml` workflow publishes it to
  `ghcr.io/unstaticlabs/usl-odoo` pinned by commit SHA;
- `distribution` extends that product image with the exact release identity,
  including the canonical reviewed action-risk policy digest, used by the
  qualified pre-production and production gates;
- `test` adds Chromium only for browser-capable automated tests;
- `dev` adds developer tools but uses the repository bind mount instead of
  embedding a second copy of the source tree.

The root `.dockerignore` is an allowlist containing only files copied by the
Dockerfile. Private dumps, filestores, Git history and migration artifacts are
never sent to the builder. Development uses explicit Compose mounts for custom
add-ons, pinned OCA add-ons and user documentation. The qualified
`distribution` stage copies those same release inputs into the image, and
`compose.preprod.yaml` removes the checkout mounts at runtime.

System packages, Python wheels, Odoo core and standard add-ons use separate
cache boundaries. Editing ordinary Python/XML/JavaScript source does not
recompile Python dependencies; changing only `custom-addons/` does not require
an image rebuild at all. Use:

```bash
make deploy    # custom add-on code, views, security or documentation mounts
make rebuild   # Dockerfile, requirements, system packages or upstream core
```

Keep BuildKit enabled (the default in current Docker Engine and Docker
Desktop). The Dockerfile uses cache and bind mounts that do not become image
layers.

Pinned OCA synchronization also reuses the main checkout's already-fetched
commits when a linked worktree has no local checkout. Each worktree still gets
an independent patched tree; subsequent syncs avoid network fetches while the
exact pinned commits are already present.

### Dependency updates

Dependabot monitors the maintained Python and Node tooling dependency sets,
pinned GitHub Actions, the Distribution and Paperless/backup Dockerfiles, and
the root and production-backup Compose service images from
`.github/dependabot.yml`. Compatible language-tooling updates are grouped
monthly; workflow and container updates are checked weekly. Python major
versions remain governed by the pinned upstream Odoo baseline.

Dependabot PRs are qualification candidates, never automatic upgrades. Rebuild
the image and run the checks appropriate to the affected runtime; Paperless,
Pocket ID, PostgreSQL and other stateful-service changes require the clean
install, reconstructed `odoo_dev` and pre-production release gates before
merge. Odoo upstream commits and pinned OCA source revisions are updated by
their documented replay/synchronization workflow, not by Dependabot.

Dependabot is not a Coding Agent, so its authenticated bot PRs are exempt from
Coding-Agent branch, attribution and structured-handoff metadata. They still
run repository contracts and the full Distribution image qualification; this
exception is keyed to GitHub's `dependabot[bot]` actor, never to a spoofable
branch name. Impeccable package candidates must refresh and review the generated
provider assets in the same dependency PR.

Milestone 13 also uses pinned OCA add-ons for Community accounting reports, reconciliation and spreadsheet/PDF support. Fetch them before running imported-accounting or report work:

```bash
make oca-addons-sync
```

This creates local ignored checkouts under `oca-src/` and symlinks selected modules into `oca-addons/`.

For the normal Compose `odoo` service, the OCA symlink targets are mounted from both directories:

- `oca-src/` -> `/mnt/oca-src`
- `oca-addons/` -> `/mnt/oca-addons`

Your local `.env` must keep `/mnt/oca-addons` in `ODOO_ADDONS_PATH`. If an older `.env` omits it, the database may contain installed OCA modules while the running Odoo server cannot load their Python code or browser assets.

### Prerequisites

- Docker Desktop or Docker Engine with Docker Compose.
- VS Code or Cursor with Dev Containers support for developer workflow.
- Enough disk space for Odoo, PostgreSQL, Python wheels, fonts, and `wkhtmltopdf`.

### Environment configuration

Create a local environment file first:

```bash
cp .env.example .env
```

The `.env` file is ignored by Git. Change these values before using the stack beyond local development:

- `ODOO_ADMIN_PASSWORD`: Odoo database manager master password. Default: `admin`.
- `POSTGRES_PASSWORD`: PostgreSQL password. Default: `odoo`.
- `ODOO_DB_PASSWORD`: Odoo's PostgreSQL password. Default: `odoo`.

Other useful variables:

- `ODOO_INIT_DB`: database created by the init profile. Default:
  `odoo_dev`.
- `ODOO_INIT_MODULES`: modules installed during first init. Default:
  `rebuild_account_migration`.
- `ODOO_ADDONS_PATH`: addon path list for the Compose Odoo service.
- `ODOO_HTTP_PORT` and `ODOO_GEVENT_PORT`: host ports. Defaults: Odoo's standard
  development ports `8069` and `8072`.
- `ODOO_WORKERS`, `ODOO_PROXY_MODE`, `ODOO_DB_FILTER`, and limits: deployment-oriented runtime controls.
- `USL_DOCUMENT_RENDERER_IMAGE`: immutable reviewed renderer image; production
  must use a registry digest, while local development defaults to the exact
  submodule revision tag.
- `USL_DOCUMENT_RENDERER_URL`: internal HTTPS endpoint used by Odoo. The
  Compose default is `https://usl-document-renderer:8443` and has no host
  route.
- `USL_EINVOICE_LIVE_ENABLED`: external reception guard; default `0` and set
  to `1` only during the documented production activation.
- `USL_EREPORTING_LIVE_ENABLED`: separate regulatory-flow guard; default `0`
  and never enabled as part of invoice-reception activation.

### 1. Developer workflow: Dev Container

Use this path when changing code, creating custom addons, running tests, or debugging.

From a fresh clone:

```bash
cp .env.example .env
docker compose --profile devcontainer build devcontainer
```

Open the repository in VS Code or Cursor and run **Dev Containers: Reopen in Container**. The Dev Container starts the Compose `db` service and a long-running `devcontainer` service. It does not automatically create an Odoo database and it does not start the normal Compose `odoo` web service.

If you added or refreshed OCA add-ons after the Dev Container was already running, rebuild or recreate the Dev Container so its environment includes `oca-addons/`.

Inside the Dev Container, initialize the development database once:

```bash
odoo --config=/etc/odoo/odoo.conf \
  --database=odoo_dev \
  --init=rebuild_account_migration \
  --without-demo=true \
  --stop-after-init
```

Then start a live development server:

```bash
odoo --config=/etc/odoo/odoo.conf --database=odoo_dev
```

Open <http://localhost:8069/web/login?db=odoo_dev>.

Default login for a freshly initialized local database:

```text
Email/Login: admin
Password: admin
```

The Dev Container renders `/etc/odoo/odoo.conf` with:

```ini
db_host = db
db_port = 5432
db_user = odoo
db_password = odoo
addons_path = /workspace/odoo/addons,/workspace/odoo/odoo/addons,/workspace/odoo/custom-addons,/workspace/odoo/oca-addons
max_cron_threads = 0
dev_mode = reload,xml,qweb
```

`max_cron_threads = 0` is intentional for local accounting parity work. It prevents scheduled jobs from sending mail, polling external services, or running e-invoicing/background integrations while imported production-derived data is being inspected.

The normal long-running Compose `odoo` service instead defaults to one cron
thread so configured product automation, including currency-rate retrieval,
actually runs. Init, test and Dev Container helper services remain at zero.
Set `ODOO_MAX_CRON_THREADS=0` explicitly while restoring or auditing an
imported database.

Develop custom modules in `custom-addons/`. Do not modify Odoo core unless the change is intentionally part of this fork.

The production custom-module boundaries are:

- `usl_locale`: the dependency-light presentation foundation that enforces
  day-first (`DD/MM/YYYY`) dates and keeps list-view company columns aligned
  with the currently selected single- or multi-company scope;
- `usl_accounting`: dependency-light extensions of native and pinned OCA
  Accounting models;
- `usl_expense_batch`: optional contextual Expense Batches with native
  analytic inheritance, visible line exceptions and mixed-payer review;
- `usl_project`: the ongoing Projects product extensions;
- `usl_tese_payroll`: external-provider payroll evidence, Accounting and HR
  workflow without legal payroll calculation;
- `usl_documents`: the Odoo Documents workspace, Paperless synchronization
  and record-level authorization;
- `usl_documents_accounting`: Accounting-specific document links and evidence;
- `usl_pocketid`: Pocket ID authentication and identity governance;
- `usl_access_control`: recoverability-based Distribution roles, protected
  irreversible actions and attributable Agent audit;
- `usl_platform_billing`: the independent content-platform payout billing
  application;
- `usl_platform_billing_pocketid`: governed Pocket ID access for Platform
  Billing administrators;
- `rebuild_account_migration`: the historical compatibility owner for stable
  operational product models and XML IDs. Despite its technical name, it
  contains no importer, source bindings, parity objects or migration UI;
- `usl_bootstrap`: a synthetic disposable test fixture, never a product
  dependency.

See
[`docs/accounting/custom-addon-architecture.md`](docs/accounting/custom-addon-architecture.md)
for dependency direction, ownership policy and future extraction rules.

One-off Accounting, identity, Product, HR, Projects, Paie TESE, Platform
Billing and Documents restoration lives under `migration/`.
The normal Odoo service cannot load that path. The production migration and
`make target-reconstruct-product` load
the temporary importer through a dedicated service, validates the restored
facts, uninstalls it, and refuses the target unless the normal product registry
is free of migration models, fields, views and XML IDs.

Useful commands inside the Dev Container:

```bash
ruff check custom-addons
odoo --config=/etc/odoo/odoo.conf --database=odoo_dev --update=your_module --stop-after-init
odoo --config=/etc/odoo/odoo.conf --database=odoo_dev --test-enable --stop-after-init --init=your_module
```

From the host, run:

```bash
scripts/odoo-dev test your_module odoo_test_your_module
```

This performs a clean, module-scoped backend and browser test run in the
Chromium-enabled `test` image. The helper stops and restores the normal
development server and removes the named test database, filestore and
container afterward. It refuses to use `odoo_dev` as a test database.

Build the user-documentation site with its separate pinned toolchain:

```bash
make user-docs-build
```

MkDocs is intentionally not a production Odoo dependency. The Make target
creates and reuses the ignored `.venv-docs` environment from pinned
`requirements-docs.txt`, so a clean checkout does not require a global MkDocs
installation. Override `USER_DOCS_VENV` only when another isolated location is
required.

Debug configurations are available in `.devcontainer/launch.json`:

- `Odoo: Run`
- `Odoo: Debug current test module`

If you want to start Odoo manually under debugpy:

```bash
python -Xfrozen_modules=off -m debugpy --listen 0.0.0.0:5678 \
  /workspace/odoo/odoo-bin --config=/etc/odoo/odoo.conf --database=odoo_dev
```

Do not run the normal Compose `odoo` service and a Dev Container Odoo server on the same host ports at the same time. Stop the normal service first if needed:

```bash
docker compose stop odoo
```

Accounting development keeps the reconstructed product in `odoo_dev`; the
validation databases are disposable pipeline proofs only. Read these before
iterating on reconstruction or reports:

- [Run imported accounting data in development](docs/operations/run-imported-accounting-dev.md)
- [Accounting development workflow](docs/operations/accounting-development-workflow.md)
- [Milestone 13 reporting and closing UX target](docs/accounting/milestone-13-reporting-and-closing-ux-target.md)

### 2. QA/test workflow: Compose only

Use this path when you want a local test or QA deployment without opening the Dev Container.

From a fresh clone:

```bash
cp .env.example .env
scripts/odoo-dev init-db
make dev
```

Open <http://localhost:8069/web/login?db=odoo_dev>.

Default login for a freshly initialized local database:

```text
Email/Login: admin
Password: admin
```

Disposable human QA accounts use the same simple convention:
`admin` / `admin` for the administrator and `<login>` / `admin` for every
named QA user. This convention must never be used in staging or production and
does not apply to database passwords, API tokens, or application secret keys.

The Compose Odoo service uses:

```ini
db_host = db
db_port = 5432
db_user = odoo
db_password = odoo
addons_path = /opt/odoo/addons,/opt/odoo/odoo/addons,/mnt/custom-addons,/mnt/oca-addons
```

This workflow runs the repository-built image and mounts `custom-addons/`, `oca-src/`, `oca-addons/` and the user docs into the Odoo container. It is suitable for QA checks, local acceptance testing, and validating behavior after rebuilds.

For a fresh or explicitly disposable database, prepare the safe French
e-invoicing QA company with the French company chart and EUR, Odoo PA demo
connection, representative bills and role-specific logins:

```bash
scripts/odoo-dev bootstrap-einvoice-qa
```

The bootstrap refuses existing company identities and any enabled live guard.
It makes no network call. Log in as `qa.manager` / `admin` or
`qa.reviewer` / `admin`.

### Developer commands

Use Make as the human-facing interface. Plain `make` shows the common
workflows and does not start or change services:

```bash
make                              # show common workflows
make doctor                       # read-only ownership and configuration diagnosis
make dev                          # start the existing development target
make deploy                       # update the normal product add-on graph
make deploy MODULE=usl_accounting # update one selected module
make rebuild                      # rebuild the image, then deploy
make status                       # show owners, service health and local URLs
make logs SERVICE=odoo            # follow one service; omit SERVICE for all logs
make stop                         # stop containers; preserve data
make help-advanced                # migration, validation and specialized QA
```

`make deploy` updates an existing `odoo_dev`; it never creates an empty
replacement when reconstructed data is missing. `make doctor` reports
`Target: present` when deployment is safe. If it reports `Target: missing`,
use the qualified QA seed with `make qa` or reconstruct the shipped product
scope with `make target-reconstruct-product` before deploying add-on changes.

For branch and worktree QA, choose the smallest honest data profile:

```bash
make qa                              # complete cached production-shaped state
make qa-reuse                        # revalidate an unchanged worktree target in place
make qa-clean CONFIRM=qa-volumes     # retire only this worktree's QA volumes
make qa PROFILE=no-documents         # complete Odoo ledger; no Documents runtime
make qa PROFILE=documents-smoke      # deterministic source-derived document sample
make qa PROFILE=clean-install        # clean product plus self-contained fixtures
make qa-cache-status                 # read-only cache identity/freshness check
make qa-cache-refresh                # fresh migration, then atomic seed publication
make qa-cache-qualify-resume         # retry qualification after migration finalization
make qa-cache-prune CONFIRM=qa-seeds # remove superseded private seeds; keep current
```

Each QA target owns independent writable PostgreSQL, Odoo and Paperless
volumes. No live volume is shared between worktrees. The host-local seed is
private and ignored by Git; Pocket identities, API tokens and environment
secrets are recreated after hydration. Missing or incompatible seeds fail
immediately instead of silently starting a long source ingestion. Partial
profiles are stamped in the database and cannot pass a pre-production gate.
Arbitrary partial accounting ledgers are intentionally unsupported.
Seed pruning is never automatic and requires the explicit confirmation above.

`make qa` remains the deterministic cold path: it deletes only that worktree's
isolated QA volumes and hydrates the shared qualified seed. After it passes,
`make qa-reuse` can restart and revalidate the same target without another
restore, module upgrade or fixture replay. Reuse is refused unless the seed
manifest, migration digest, full-profile stamp, project identity and required
volumes still match. Because the target is intentionally writable, use plain
`make qa` after manual business-data changes or whenever a pristine baseline
is required.

When a worktree no longer needs its warm QA target, `make qa-clean
CONFIRM=qa-volumes` removes only that checkout's computed `usl-odoo-qa-*`
containers, network and writable volumes. It preserves the shared seed,
source package, images, BuildKit cache and project-bound identity environment.

The underlying scripts remain stable automation interfaces:

```bash
scripts/odoo-dev build        # build Odoo images
scripts/odoo-dev start        # start PostgreSQL and Odoo
scripts/odoo-dev deploy       # update the Accounting add-on and redeploy
scripts/odoo-dev rebuild      # rebuild images, update the add-on, and redeploy
scripts/odoo-dev stop         # stop services
scripts/odoo-dev logs odoo    # follow Odoo logs
scripts/odoo-dev init-db      # initialize ODOO_INIT_DB with ODOO_INIT_MODULES
scripts/odoo-dev shell        # open a one-off devcontainer shell
scripts/odoo-dev test base    # run an Odoo module test pass
scripts/odoo-dev test-js rebuild_account_migration  # frontend unit tests
scripts/odoo-dev test-tag '/module:Class.test_method'  # installed focused test
scripts/odoo-dev bootstrap-einvoice-qa  # network-free PA demo and QA accounts
scripts/odoo-dev bootstrap-immediate-settlement-qa
                                      # three-action foreign settlement QA cases
make tese-qa-bootstrap                # synthetic end-to-end Paie TESE journeys
make tese-qa-bootstrap TESE_QA_GENERATION=02
                                      # fresh generation after using generation 01
make disable-tours                    # disable automatic tours for internal QA users
make repair-pocket-id                 # repair and verify this project's SSO
scripts/pocket-id-dev bootstrap       # generate ignored local target secrets
make login-link USER=valentin  # local passwordless login for any Pocket user
make paperless-users           # reconcile governed users and document access
make action-risk-inventory     # reject unclassified/changed source actions
make action-risk-compile-policy # derive the compact protected worker policy
make action-risk-runtime       # compare the exact installed odoo_dev registry
make product-assets            # compile every installed-registry asset bundle
scripts/documents-stack qa up         # isolated Odoo/Paperless/Pocket QA stack
scripts/documents-stack qa bootstrap  # idempotent synthetic Documents archive
make documents-restore                # isolated Documents migration rehearsal
scripts/target-finalize               # apply target-only config after migration
make migrate-production SOURCE_SHA=<sha256> # full-source, fresh production migration
make target-reconstruct-product       # rebuild current product scopes from source
make target-reconstruct-reuse-documents # rebuild Odoo; reuse verified Paperless ingestion
scripts/migration-source-truth inventory # audit all populated source perimeters
scripts/migration-source-truth report    # write private model/table/field gap evidence
scripts/odoo-dev ruff custom-addons
scripts/odoo-dev update       # pull service images and rebuild
scripts/odoo-dev reset        # delete local Compose volumes
```

The normal development workflow is:

```bash
make dev       # start the existing environment
make deploy    # apply ordinary custom add-on changes
make rebuild   # rebuild images, then deploy
make target-finalize    # reapply identities, permissions and target config
make target-reconstruct-product # recreate product scopes, then finalize odoo_dev
make target-reconstruct-reuse-documents
                        # same Odoo rebuild; skip unchanged Paperless OCR safely
```

`make migrate-production SOURCE_SHA=<exact dump SHA-256>` is the authoritative
fresh path. It requires a clean checkout, the full profile, fresh Paperless
ingestion, the source-wide coverage gate and the complete attachment ledger.
It stops before resetting `odoo_dev` if any populated Online scope has no final
treatment. During repeated development runs against unchanged inputs,
`make target-reconstruct-reuse-documents` retains the existing
Paperless volumes, verifies their private content-addressed checkpoint, and
then reruns the complete Documents importer to rebuild Odoo links and verify
every original, preview and permission. A newer dump or compatible importer
change is reconciled incrementally, so only new binaries are ingested. A
changed Paperless/OCR contract or archive drift rejects reuse. The separate
`make target-reconstruct-product` command performs a fresh reconstruction of
the scopes currently shipped by the Distribution, but is not full-source
production evidence. Pre-production qualification uses the strict production
path.

The same-project checkpoint is retained as an advanced diagnostic. Normal
worktree QA uses `make qa`: the qualified Paperless export is imported into new
volumes with zero OCR submissions, then current-branch modules, links,
permissions and isolated identities are reapplied.

The exact pre-production release lifecycle is one host command from a clean
release branch:

```bash
scripts/preprod-release all /absolute/path/to/usl-online-dump
```

It synchronizes the pinned OCA commits, builds a commit-tagged self-contained
image, reconstructs `odoo_dev`, records the dump/image/module identity in the
database, starts the no-bind-mount runtime and runs the source, exact action
registry, database, schema, image, service and direct-Paperless-identity gates.
Both regulatory live
guards remain `0`. Target finalization provisions and verifies the individual
Paperless identities and synchronizes document permissions without importing
source credentials or relying on first login. The final gate checks that state;
the separate browser acceptance uses `make login-link USER=<username>` for each
persona on local HTTP QA. External HTTPS deployments require Pocket ID
passkeys and must not retain the QA one-time-link exception.

The main checkout owns the default `usl-odoo-saas-19-3` Compose project.
Linked worktrees must use a dedicated project and non-conflicting ports; every
host helper verifies the Compose working-directory label before it mutates a
container. If a previous command or worktree left the canonical project mixed,
the safety refusal is intentional. Diagnose it first, then explicitly replace
only its containers from the main checkout:

```bash
make doctor
make dev-reclaim CONFIRM=usl-odoo-saas-19-3
make deploy
```

`make doctor` is read-only and reports every container, service, state,
checkout owner and branch. Reclaim is never automatic: it requires the exact
project confirmation, refuses while a migration, test or one-shot initializer
is active, and removes/recreates project containers only. Named PostgreSQL,
Odoo filestore, Paperless and Pocket ID volumes, images, source dumps and
backups are preserved. Data deletion remains the separate, explicitly named
reset workflow.

A linked worktree cannot reclaim or use the canonical project. Give it an
explicit project and non-conflicting ports instead. For example:

```bash
COMPOSE_PROJECT=usl-odoo-preprod-9642 \
ODOO_HTTP_PORT=18669 ODOO_GEVENT_PORT=18672 \
POCKET_ID_HTTP_PORT=11411 PAPERLESS_HTTP_PORT=18010 \
USL_ONLINE_DUMP_DIR=/absolute/path/to/usl-online-dump \
make target-reconstruct-product
```

That first reconstruction writes a project-bound `.pocket-id.env`. Use the
same project name for every later command in that worktree:

```bash
make COMPOSE_PROJECT=usl-odoo-preprod-9642 doctor
make COMPOSE_PROJECT=usl-odoo-preprod-9642 deploy
make COMPOSE_PROJECT=usl-odoo-preprod-9642 login-link USER=valentin
```

`make doctor` now checks three things together: Compose ownership, the
`odoo_dev` database, and agreement between the running Odoo process and its
database Pocket ID provider. If SSO is missing or stale, it prints the exact
project and ports for `make ... repair-pocket-id`. `make login-link` runs
the same check and refuses to generate a misleading link until the runtime is
repaired. Module and frontend tests restore the worktree's Pocket ID overlay
when they restart Odoo. Worktree deploys update modules through that overlay
without reclassifying synthetic QA users; the canonical target retains its
strict full-policy deploy.

Runtime repair changes only environment-owned provider settings and recreates
Odoo with the correct overlay. It does not alter users or permissions. The
stricter `make configure-pocket-id` remains available when the complete named
identity policy must be reapplied.

Pocket ID secrets and immutable test subjects are checkout-local by default.
Never copy `.pocket-id.env` between worktrees. Set `POCKET_ID_ENV_FILE` only
when deliberately reusing the same file for the same explicit Compose project.

These helpers serve canonical `odoo_dev` by default. It is the disposable,
production-shaped product target: reconstructed Online business data plus
target-only configuration such as Pocket ID. `make dev`, `make deploy` and
`make rebuild` use the pinned local Pocket ID overlay, keep the database filter
at `^odoo_dev$`, provision stable local identities, and reapply the governed
Odoo policy when configuration or modules are updated.

Source parity and target configuration remain separate stages. The Online dump
has no Pocket ID state, so the reconstruction orchestrator validates Accounting,
installs Documents security before restoring identities, restores Product,
HR and Projects, rebuilds the Paperless archive, restores Paie TESE and
Platform Billing, and only then finalizes every temporary migration module out
of the product. Projects, Paie TESE and Platform Billing retain their exact
temporary source bindings until this global boundary so a failed QA archive
run can resume without guessing from user-visible names. Its final
target-only step provisions the governed Pocket identities in both Odoo and
Paperless, maps the same immutable people, and synchronizes their exact
Paperless document permissions. It never imports source credentials or SSO
state. `make paperless-users` reapplies this identity/access reconciliation in
the default development topology. For an isolated release candidate, use
`scripts/preprod-release finalize-reconstruction SOURCE_DIR` instead so Odoo is
stopped and the exact qualified image and release Compose topology stay in use.
Migration tooling is a maintained repository deliverable under `migration/`
and `scripts/`; it is not installed or exposed in the normal Odoo UI.

A failed QA cache refresh no longer requires another full ledger replay. After
Accounting has passed exact parity, the failure output offers
`make qa-cache-resume`; it revalidates the same source and target Accounting
state before continuing downstream stages. If the migration boundary was
already finalized and only seed capture or final QA qualification failed, use
`make qa-cache-qualify-resume`; it revalidates the finalized business state and
runtime without reinstalling migration modules or replaying source identities.
Cache refresh configures and validates the complete delivered product before
capturing a credential-sanitized seed, then reapplies local identity settings
before the final multi-company acceptance. Paperless OIDC subjects and other
environment-bound integrations are stripped from the seed and reprovisioned
for each isolated QA project.
Both shortcuts are QA-only. The production migration command always
reconstructs from source.

The local Pocket ID workflow is pinned in `compose.pocket-id.yaml`, binds only
to loopback, and stores generated secrets and stable immutable subjects in the
ignored mode-0600 `.pocket-id.env`. Follow the
[Pocket ID SSO runbook](docs/operations/pocket-id-sso-runbook.md); never place
the client secret, break-glass password or raw subjects in Git. Production
uses its own HTTPS issuer, approved secrets and owner-confirmed subjects.
Pocket ID is the sole human login in finalized targets. Local QA uses
`make login-link USER=<username>`; the emergency administrator is unavailable
unless a short, explicitly expired incident window is enabled. API keys remain
the supported non-human authentication method.
The Documents wrapper also registers a separate Paperless OIDC client and
never reuses Odoo's client secret. Canonical local credentials stay in the
ignored mode-0600 `.pocket-id.env`; the isolated Documents QA wrapper uses
its own ignored mode-0600 `.documents-qa-sso.env`.

### Optional bootstrap fixture

`custom-addons/usl_bootstrap` remains available for isolated module tests and
smoke fixtures. It should not be installed into the normal `odoo_dev`
Accounting product database.

To create an explicitly named throwaway fixture:

```bash
ODOO_INIT_DB=odoo_bootstrap_fixture \
ODOO_INIT_MODULES=usl_bootstrap \
scripts/odoo-dev init-db
```

Delete the fixture after the isolated test; do not use it for product QA or
accounting parity evidence.

Installed application domains: Contacts, Discuss, Accounting/Invoicing,
French accounting localization, Expenses, Projects and Tasks, Employees,
Paie TESE, Documents, Sales, Settings and application management.

Deliberate product boundaries: Community does not provide the Enterprise
application launcher or unrelated Enterprise applications such as Sign,
Knowledge, To-do, AI features or Odoo Enterprise Payroll. Documents is the
Paperless-backed Community replacement; Paie TESE is the focused
external-provider payroll and accounting workflow. Live bank synchronization
and production electronic-invoicing connectivity remain inactive. Provider
identity verification and acceptance of the platform terms occur during the
deliberate production activation; passing the offline and demo tests does not
register the company in the French directory. Brands such
as SBFH, GBC, Yoshi, Smash and KinkVerse are represented as projects or
analytic contexts under the single legal company.

### Production-derived accounting reconstruction

To try the imported accounting data from the Odoo Online backup, use the dedicated guide:

```text
docs/operations/run-imported-accounting-dev.md
```

Important: run `make accounting-*` from the host shell, not from inside the Dev Container. The Dev Container runs Odoo, but it does not currently include the Docker CLI required by the accounting harness.

For ordinary custom add-on changes:

```bash
make deploy
```

After changing Python/system dependencies, the Dockerfile, or core source:

```bash
make rebuild
```

### Passwords and databases

- Odoo web login in a newly initialized database: `admin` / `admin`.
- Odoo database manager master password: `ODOO_ADMIN_PASSWORD`, default `admin`.
- PostgreSQL credentials: `ODOO_DB_USER=odoo` and `ODOO_DB_PASSWORD=odoo` by default.
- Developer/QA database: `odoo_dev`.

Starting the Dev Container does not create a database. Running
`scripts/odoo-dev init-db` or an explicit
`odoo --init=rebuild_account_migration --stop-after-init` command does.

### Troubleshooting

- If Odoo is not ready immediately after restart, wait for `docker compose ps` to show `healthy`.
- If port `8069` or `8072` is already in use, change `ODOO_HTTP_PORT` or `ODOO_GEVENT_PORT` in `.env`.
- If database initialization fails because the database already exists, set a new `ODOO_INIT_DB` or reset local state with `scripts/odoo-dev reset`.
- If custom addons are not visible, make sure the addon directory contains a module with both `__init__.py` and `__manifest__.py`.
- If dependencies change, rebuild instead of installing packages manually in a running container.

## Security

If you believe you have found a security issue, check our [Responsible Disclosure page](https://www.odoo.com/security-report)
for details and get in touch with us via email.
