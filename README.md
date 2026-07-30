# Unstatic Labs Odoo Community fork

This repository is Unstatic Labs’ production-oriented Accounting product on
Odoo Community `saas~19.2`. It extends upstream through isolated modules under
`custom-addons/` and pinned OCA dependencies; upstream Odoo core remains
unchanged.

Accounting v1 provides the daily cockpit for journals, invoices, bills,
expenses, payments, bank transactions and reconciliation, plus assets,
deferrals, currencies, analytics, Hygiene, closing, declarations, interactive
financial reports, PDF/XLSX exports and FEC. A scoped read-only accountant can
inspect the same accounting and evidence without posting, reconciling,
configuring or locking records.

French electronic-invoice reception is implemented and validated offline for
UBL, CII and Factur-X invoices and credit notes. It remains **Ready but
inactive**: no directory registration, production provider endpoint, scheduled
reception or e-reporting may be enabled before the deliberate production
activation procedure is approved.

Primary entry points:

- **Accounting > Overview** for daily operational state;
- **Accounting > Reporting > Analyse analytique** for exploratory pivot analysis;
- **Accounting > Configuration** for governed Controls, Reports, Declarations
  and E-Invoicing;
- `/usl/user-docs` for role- and task-based user guidance;
- `/usl/user-docs/how-to/activate-electronic-invoice-reception.md` for the
  production reception switch and rollback checklist;
- [Accounting development workflow](docs/operations/accounting-development-workflow.md)
  for safe iteration;
- [Accounting compatibility harness](docs/accounting/accounting-compat-harness.md)
  for reconstruction and parity evidence.
- [Projects restoration runbook](docs/operations/project-restoration.md) for
  repeatable Odoo Online project and task recovery.
- [Product and migration boundary](docs/agents/product-migration-boundary.md)
  for keeping reconstruction machinery out of the delivered Odoo runtime.

The integration baseline is upstream commit
`8a44ecc8da96e341ac472fec27352d138ed2edd7`. The source dump and generated
validation evidence are private local artifacts and must never be committed.

## Upstream Odoo

Odoo is a suite of web-based open source business applications. Standard Odoo
installation and developer documentation is available from
[odoo.com](https://www.odoo.com/documentation/19.0/).

## Docker and Dev Container setup

This fork includes two local workflows for Odoo `saas~19.2` Community. The
branch is pinned to upstream commit
`8a44ecc8da96e341ac472fec27352d138ed2edd7`. Local development uses one
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
- `test` adds Chromium only for browser-capable automated tests;
- `dev` adds developer tools but uses the repository bind mount instead of
  embedding a second copy of the source tree.

The root `.dockerignore` is an allowlist containing only files copied by the
Dockerfile. Private dumps, filestores, Git history, custom/OCA add-ons,
documentation and migration artifacts are therefore never sent to the Docker
builder. Custom add-ons, pinned OCA add-ons and user documentation remain
available through the explicit Compose mounts.

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

- `usl_accounting`: dependency-light extensions of native and pinned OCA
  Accounting models;
- `usl_expense_batch`: the independent Expenses claim-batch feature;
- `rebuild_account_migration`: the historical compatibility owner for stable
  product models, XML IDs and reconstruction entry points. Its technical name
  is not exposed in normal Accounting navigation;
- `usl_bootstrap`: a synthetic disposable test fixture, never a product
  dependency.

See
[`docs/accounting/custom-addon-architecture.md`](docs/accounting/custom-addon-architecture.md)
for dependency direction, ownership policy and future extraction rules.

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
It makes no network call. Log in as `qa.manager` / `qa-manager` or
`qa.reviewer` / `qa-reviewer`.

### Helper commands

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
make disable-tours                    # disable automatic tours for internal QA users
scripts/odoo-dev configure-pocket-id  # apply Pocket ID to canonical odoo_dev
scripts/pocket-id-dev bootstrap       # generate ignored local target secrets
make login-link USER=valentin  # local passwordless login for any Pocket user
scripts/target-finalize               # apply target-only config after migration
scripts/target-reconstruct            # rebuild canonical data and target config
scripts/odoo-dev ruff custom-addons
scripts/odoo-dev update       # pull service images and rebuild
scripts/odoo-dev reset        # delete local Compose volumes
```

The normal shorthand is:

```bash
make dev       # start the existing environment
make deploy    # apply ordinary custom add-on changes
make rebuild   # rebuild images, then deploy
make target-finalize    # reapply and validate target-only configuration
make target-reconstruct # recreate odoo_dev from the dump, then finalize it
```

These helpers serve canonical `odoo_dev` by default. It is the disposable,
production-shaped product target: reconstructed Online business data plus
target-only configuration such as Pocket ID. `make dev`, `make deploy` and
`make rebuild` use the pinned local Pocket ID overlay, keep the database filter
at `^odoo_dev$`, provision stable local identities, and reapply the governed
Odoo policy when configuration or modules are updated.

Source parity and target configuration remain separate stages. The Online dump
has no Pocket ID state, so `scripts/target-reconstruct` first validates the
Accounting import, then restores Projects, finalizes every temporary migration
module out of the product, and finally applies Pocket ID. Migration tooling is
a maintained repository deliverable under `migration/` and `scripts/`; it is
not installed or exposed in the normal Odoo UI.

The local Pocket ID workflow is pinned in `compose.pocket-id.yaml`, binds only
to loopback, and stores generated secrets and stable immutable subjects in the
ignored mode-0600 `.pocket-id.env`. Follow the
[Pocket ID SSO runbook](docs/operations/pocket-id-sso-runbook.md); never place
the client secret, break-glass password or raw subjects in Git. Production
uses its own HTTPS issuer, approved secrets and owner-confirmed subjects.

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

Installed application domains: Contacts, Discuss, Accounting/Invoicing, French accounting localization, Expenses, Projects and Tasks, Employees, Sales, Settings and application management.

Deliberate product boundaries: Community does not provide the Enterprise
application launcher or unrelated Enterprise applications such as Documents,
Sign, Knowledge, To-do, AI features or TESE Payroll. Live bank synchronization
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
