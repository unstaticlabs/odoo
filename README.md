# Unstatic Labs Odoo Distribution

This repository packages the continuously developed Unstatic Labs Odoo
Community distribution on Odoo `saas~19.3`. Product extensions live in
`custom-addons/`; pinned OCA modules live in `oca-addons/`. Upstream Odoo core
stays unchanged except for documented, distribution-level patches.

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

## Production and releases

Production consumes immutable OCI digests built by CI. Odoo, Paperless, Sign,
the document renderer and the separately built MCP image are released and
recovered as one coordinated cohort. The shared MsgVault-owned Ollama service
remains external infrastructure: releases validate its BGE model contract but
never manage, replace or restore it. Use the
[production runbook](docs/operations/production.md) for upgrades, coordinated
backups, deployment, admission and recovery.

The current production dataset evolves independently from the frozen Online
export. Never reset it from that export. Take a coordinated checkpoint
before risky upgrades or data repairs, and prove releases through an isolated
restore before production admission.

The historical Online reconstruction implementation remains isolated under
`migration/`. [`migration/manage`](migration/manage) is its only public
interface and is retained only for historical audit and reproducibility. It is
not a production recovery path or an ordinary development workflow.

## Product documentation

- [Distribution and module map](docs/product/fork-overview.md)
- [Product roadmap](ROADMAP.md)
- [Accounting architecture](docs/accounting/custom-addon-architecture.md)
- [Multi-company Accounting](docs/accounting/multi-company-accounting.md)
- [Product and migration boundary](docs/operations/product-migration-boundary.md)
- [Pocket ID operations](docs/operations/pocket-id-sso-runbook.md)
- [Electronic-invoice activation](docs/operations/activate-french-electronic-invoicing.md)
- [Document renderer operations](docs/operations/document-renderer-runbook.md)
- User guide at `/usl/user-docs` in a running distribution

## Release identity

The canonical release line is `19-usl`. CI publishes the distribution and
backup images after merge and records their commit, OCI digest, OCA bundle and
policy identity. Tags are discovery aids only; production must deploy digest
references from the validated release artifact.

The source dump, filestore, runtime state, credentials, and generated evidence
are private local artifacts and must never be committed.

## Upstream Odoo

Odoo is a suite of open-source business applications. See the
[Odoo 19 documentation](https://www.odoo.com/documentation/19.0/) for standard
installation and development guidance.
