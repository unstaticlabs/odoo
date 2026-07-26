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

French electronic-invoice reception is implemented and validated offline. It
remains visibly **Not Connected** in development: no directory registration,
production provider endpoint or scheduled exchange may be enabled before the
production activation procedure is approved.

Primary entry points:

- **Accounting > Overview** for daily operational state;
- **Accounting > Reporting > Analytic Reporting** for exploratory pivot analysis;
- **Accounting > Configuration** for governed Controls, Reports, Declarations
  and electronic-invoice readiness;
- `/usl/user-docs` for role- and task-based user guidance;
- [Accounting development workflow](docs/operations/accounting-development-workflow.md)
  for safe iteration;
- [Accounting compatibility harness](docs/accounting/accounting-compat-harness.md)
  for reconstruction and parity evidence.

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

Develop custom modules in `custom-addons/`. Do not modify Odoo core unless the change is intentionally part of this fork.

Useful commands inside the Dev Container:

```bash
ruff check custom-addons
odoo --config=/etc/odoo/odoo.conf --database=odoo_dev --update=your_module --stop-after-init
odoo --config=/etc/odoo/odoo.conf --database=odoo_dev --test-enable --stop-after-init --init=your_module
```

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

Milestone 13 accounting development uses a separate imported target database and workflow. Read these before iterating on accounting reconstruction or reports:

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
scripts/odoo-dev ruff custom-addons
scripts/odoo-dev update       # pull service images and rebuild
scripts/odoo-dev reset        # delete local Compose volumes
```

The normal shorthand is:

```bash
make dev       # start the existing environment
make deploy    # apply ordinary custom add-on changes
make rebuild   # rebuild images, then deploy
```

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

Known first-iteration gaps compared with the current Odoo Online environment: Community does not provide the Enterprise application launcher experience, Documents, Sign, Knowledge, Dashboards, To-do, AI features, TESE Payroll, Platform Invoicing, or live bank synchronization. Brands such as SBFH, GBC, Yoshi, Smash, and KinkVerse are represented as projects or analytic contexts under the single legal company only.

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
