# Unstatic Labs Odoo Distribution

This repository packages the Unstatic Labs Odoo Community distribution on
Odoo `saas~19.3`. Product extensions live in `custom-addons/`; pinned OCA
modules live in `oca-addons/`. Upstream Odoo core stays unchanged except for
documented, distribution-level patches.

The product includes multi-company Accounting, Projects, Expenses, Platform
Billing, TESE payroll evidence, Documents backed by Paperless-ngx, Sign,
Pocket ID authentication, governed PDF rendering, and offline-validated French
electronic-invoice reception.

Electronic-invoice reception and e-reporting remain disabled outside an
explicit production activation:

```text
USL_EINVOICE_LIVE_ENABLED=0
USL_EREPORTING_LIVE_ENABLED=0
```

## Repository boundaries

- `custom-addons/` contains delivered product behavior.
- `migration/` contains one-shot Online reconstruction, candidate, cohort, and
  cutover tooling. It is never part of the normal Odoo add-ons path.
- `operations/` and `deploy/` contain production image, backup, and recovery
  assets.
- `docs/product/`, `docs/accounting/`, `docs/operations/`, and `docs/users/`
  contain product, control, operator, and user documentation.
- `private/` contains ignored runtime state and evidence. Keep the directory
  mode `0700` and files containing secrets or identity state at `0600`.

A finalized database must not contain migration modules, menus, models,
fields, or XML IDs. Check this boundary with:

```bash
make product-migration-boundary
```

## Local development

Requirements:

- Docker Desktop or Docker Engine with Compose;
- enough disk space for PostgreSQL, Odoo, Paperless, and build caches;
- a local `.env` created from `.env.example`.

Start and inspect the ordinary development runtime:

```bash
cp .env.example .env
make doctor
make dev
make status
```

Open <http://localhost:8069/web/login?db=odoo_dev>. A clean local installation
uses `admin` / `admin`; production-derived runtimes use Pocket ID instead.

Common commands:

```bash
make deploy MODULE=usl_accounting  # update mounted code and the named module
make rebuild                       # rebuild after image or dependency changes
make logs SERVICE=odoo
make stop                          # stop containers and preserve data
make accounting-addon-tests
make user-docs-build
```

Run `make help` for the complete routine development surface. Migration is
intentionally absent from the Makefile.

## Migration and production

[`migration/manage`](migration/manage) is the only public migration command.
It resolves runtime identity once, records it under `private/migration/`, and
passes that exact state to every child stage. Start with the
[migration runbook](docs/operations/migration.md).

Production consumes immutable OCI digests built by CI. Operators must use the
[production runbook](docs/operations/production.md) for release validation,
coordinated backup, cutover, admission, and recovery. Production deployment is
CI-owned; local development and migration commands never deploy production.

## Product documentation

- [Distribution and module map](docs/product/fork-overview.md)
- [Accounting architecture](docs/accounting/custom-addon-architecture.md)
- [Multi-company Accounting](docs/accounting/multi-company-accounting.md)
- [Product and migration boundary](docs/operations/product-migration-boundary.md)
- [Pocket ID operations](docs/operations/pocket-id-sso-runbook.md)
- [Electronic-invoice activation](docs/operations/activate-french-electronic-invoicing.md)
- [Document renderer operations](docs/operations/document-renderer-runbook.md)
- User guide at `/usl/user-docs` in a running distribution

## Release identity

The canonical branch is `19-usl`. CI publishes the distribution and backup
images after merge and records their commit, OCI digest, OCA bundle, and policy
identity. Tags are discovery aids only; production must deploy digest
references from the validated release artifact.

The source dump, filestore, runtime state, credentials, and generated evidence
are private local artifacts and must never be committed.

## Upstream Odoo

Odoo is a suite of open-source business applications. See the
[Odoo 19 documentation](https://www.odoo.com/documentation/19.0/) for standard
installation and development guidance.
