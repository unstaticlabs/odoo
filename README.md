# Odoo

[![Build Status](https://runbot.odoo.com/runbot/badge/flat/1/master.svg)](https://runbot.odoo.com/runbot)
[![Tech Doc](https://img.shields.io/badge/master-docs-875A7B.svg?style=flat&colorA=8F8F8F)](https://www.odoo.com/documentation/master)
[![Help](https://img.shields.io/badge/master-help-875A7B.svg?style=flat&colorA=8F8F8F)](https://www.odoo.com/forum/help-1)
[![Nightly Builds](https://img.shields.io/badge/master-nightly-875A7B.svg?style=flat&colorA=8F8F8F)](https://nightly.odoo.com/)

Odoo is a suite of web based open source business apps.

The main Odoo Apps include an [Open Source CRM](https://www.odoo.com/page/crm),
[Website Builder](https://www.odoo.com/app/website),
[eCommerce](https://www.odoo.com/app/ecommerce),
[Warehouse Management](https://www.odoo.com/app/inventory),
[Project Management](https://www.odoo.com/app/project),
[Billing &amp; Accounting](https://www.odoo.com/app/accounting),
[Point of Sale](https://www.odoo.com/app/point-of-sale-shop),
[Human Resources](https://www.odoo.com/app/employees),
[Marketing](https://www.odoo.com/app/social-marketing),
[Manufacturing](https://www.odoo.com/app/manufacturing),
[...](https://www.odoo.com/)

Odoo Apps can be used as stand-alone applications, but they also integrate seamlessly so you get
a full-featured [Open Source ERP](https://www.odoo.com) when you install several Apps.

## Getting started with Odoo

For a standard installation please follow the [Setup instructions](https://www.odoo.com/documentation/master/administration/install/install.html)
from the documentation.

To learn the software, we recommend the [Odoo eLearning](https://www.odoo.com/slides),
or [Scale-up, the business game](https://www.odoo.com/page/scale-up-business-game).
Developers can start with [the developer tutorials](https://www.odoo.com/documentation/master/developer/howtos.html).

## Docker and Dev Container setup

This fork includes two local workflows for Odoo 19 Community:

- Developer workflow: use the Dev Container and run Odoo from the mounted source tree.
- QA/test workflow: use Docker Compose only, with no editor container, to run a local Odoo service and PostgreSQL.

Both workflows build Odoo from this repository, use PostgreSQL from Compose, store PostgreSQL data and the Odoo filestore in named volumes, and keep custom addons outside Odoo core under `custom-addons/`.

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

- `ODOO_INIT_DB`: database created by the init profile. Default: `odoo19`.
- `ODOO_INIT_MODULES`: modules installed during first init. Default: `usl_bootstrap`.
- `ODOO_ADDONS_PATH`: addon path list for the Compose Odoo service.
- `ODOO_HTTP_PORT` and `ODOO_GEVENT_PORT`: host ports. Defaults: `8069` and `8072`.
- `ODOO_WORKERS`, `ODOO_PROXY_MODE`, `ODOO_DB_FILTER`, and limits: deployment-oriented runtime controls.

### 1. Developer workflow: Dev Container

Use this path when changing code, creating custom addons, running tests, or debugging.

From a fresh clone:

```bash
cp .env.example .env
docker compose --profile devcontainer build devcontainer
```

Open the repository in VS Code or Cursor and run **Dev Containers: Reopen in Container**. The Dev Container starts the Compose `db` service and a long-running `devcontainer` service. It does not automatically create an Odoo database and it does not start the normal Compose `odoo` web service.

Inside the Dev Container, initialize the development database once:

```bash
odoo --config=/etc/odoo/odoo.conf \
  --database=odoo19 \
  --init=base \
  --without-demo=true \
  --stop-after-init
```

Then start a live development server:

```bash
odoo --config=/etc/odoo/odoo.conf --database=odoo19
```

Open <http://localhost:8069/web/login?db=odoo19>.

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
addons_path = /workspace/odoo/addons,/workspace/odoo/odoo/addons,/workspace/odoo/custom-addons
dev_mode = reload,xml,qweb
```

Develop custom modules in `custom-addons/`. Do not modify Odoo core unless the change is intentionally part of this fork.

Useful commands inside the Dev Container:

```bash
ruff check custom-addons
odoo --config=/etc/odoo/odoo.conf --database=odoo19 --update=your_module --stop-after-init
odoo --config=/etc/odoo/odoo.conf --database=odoo19 --test-enable --stop-after-init --init=your_module
```

Debug configurations are available in `.devcontainer/launch.json`:

- `Odoo: Run`
- `Odoo: Debug current test module`

If you want to start Odoo manually under debugpy:

```bash
python -Xfrozen_modules=off -m debugpy --listen 0.0.0.0:5678 \
  /workspace/odoo/odoo-bin --config=/etc/odoo/odoo.conf --database=odoo19
```

Do not run the normal Compose `odoo` service and a Dev Container Odoo server on the same host ports at the same time. Stop the normal service first if needed:

```bash
docker compose stop odoo
```

### 2. QA/test workflow: Compose only

Use this path when you want a local test or QA deployment without opening the Dev Container.

From a fresh clone:

```bash
cp .env.example .env
scripts/odoo-dev build
scripts/odoo-dev init-db
scripts/odoo-dev start
```

Open <http://localhost:8069/web/login?db=odoo19>.

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
addons_path = /opt/odoo/addons,/opt/odoo/odoo/addons,/mnt/custom-addons
```

This workflow runs the repository-built image and mounts `custom-addons/` into `/mnt/custom-addons`. It is suitable for QA checks, local acceptance testing, and validating behavior after rebuilds.

### Helper commands

```bash
scripts/odoo-dev build        # build Odoo images
scripts/odoo-dev start        # start PostgreSQL and Odoo
scripts/odoo-dev stop         # stop services
scripts/odoo-dev logs odoo    # follow Odoo logs
scripts/odoo-dev init-db      # initialize ODOO_INIT_DB with ODOO_INIT_MODULES
scripts/odoo-dev shell        # open a one-off devcontainer shell
scripts/odoo-dev test base    # run an Odoo module test pass
scripts/odoo-dev ruff custom-addons
scripts/odoo-dev update       # pull service images and rebuild
scripts/odoo-dev reset        # delete local Compose volumes
```

### Unstatic Labs demo database

The local demo baseline is provided by `custom-addons/usl_bootstrap`. It installs the standard Community apps for Contacts, Discuss/chatter, Accounting/Invoicing with French localization, Expenses, Projects, Employees, and Sales, then creates fictional `.test` development data for the single company `Unstatic Labs`.

Initialize the database:

```bash
ODOO_INIT_MODULES=usl_bootstrap scripts/odoo-dev init-db
```

Start Odoo:

```bash
scripts/odoo-dev start
```

Reset and rebuild the same baseline:

```bash
scripts/odoo-dev reset
ODOO_INIT_MODULES=usl_bootstrap scripts/odoo-dev init-db
scripts/odoo-dev start
```

Open <http://localhost:8069/web/login?db=odoo19>.

Default development login:

```text
Email/Login: admin
Password: admin
```

Installed application domains: Contacts, Discuss, Accounting/Invoicing, French accounting localization, Expenses, Projects and Tasks, Employees, Sales, Settings and application management.

Known first-iteration gaps compared with the current Odoo Online environment: Community does not provide the Enterprise application launcher experience, Documents, Sign, Knowledge, Dashboards, To-do, AI features, TESE Payroll, Platform Invoicing, or live bank synchronization. Brands such as SBFH, GBC, Yoshi, Smash, and KinkVerse are represented as projects or analytic contexts under the single legal company only.

After changing Python, system, or Docker dependencies:

```bash
docker compose --profile devcontainer build
docker compose up -d --force-recreate odoo
```

### Passwords and databases

- Odoo web login in a newly initialized database: `admin` / `admin`.
- Odoo database manager master password: `ODOO_ADMIN_PASSWORD`, default `admin`.
- PostgreSQL credentials: `ODOO_DB_USER=odoo` and `ODOO_DB_PASSWORD=odoo` by default.
- Default local database name: `odoo19`.

Starting the Dev Container does not create a database. Running `scripts/odoo-dev init-db` or the explicit `odoo --init=base --stop-after-init` command does.

### Troubleshooting

- If Odoo is not ready immediately after restart, wait for `docker compose ps` to show `healthy`.
- If port `8069` or `8072` is already in use, change `ODOO_HTTP_PORT` or `ODOO_GEVENT_PORT` in `.env`.
- If database initialization fails because the database already exists, set a new `ODOO_INIT_DB` or reset local state with `scripts/odoo-dev reset`.
- If custom addons are not visible, make sure the addon directory contains a module with both `__init__.py` and `__manifest__.py`.
- If dependencies change, rebuild instead of installing packages manually in a running container.

## Security

If you believe you have found a security issue, check our [Responsible Disclosure page](https://www.odoo.com/security-report)
for details and get in touch with us via email.
