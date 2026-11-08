# Unstatic Labs Odoo Distribution

The continuously developed Unstatic Labs Odoo Community distribution, on Odoo
`saas~19.3`. Product extensions live in `custom-addons/`; pinned OCA modules live
in `oca-addons/`. Upstream Odoo core stays unchanged except for documented,
distribution-level patches.

The product covers multi-company Accounting, Projects, Expenses, Platform
Billing, TESE payroll evidence, Documents backed by Paperless-ngx, Sign, Pocket ID
authentication, governed PDF rendering and French electronic-invoice reception.

## Local development

Docker with Compose, disk for PostgreSQL, Odoo, Paperless and build caches, and a
local `.env` created from `.env.example`.

```bash
cp .env.example .env
make doctor
make dev
make status
```

Open <http://localhost:8069/web/login?db=odoo_dev>. A clean local installation
uses `admin` / `admin`; production-derived runtimes use Pocket ID instead.

```bash
make deploy MODULE=usl_accounting  # update mounted code and the named module
make rebuild                       # rebuild after image or dependency changes
make logs SERVICE=odoo
make stop                          # stop containers and preserve data
make accounting-addon-tests
make user-docs-build
make product-migration-boundary    # a finalized database carries no migration module
```

`make help` lists the complete routine development surface.

## Documentation

- [`AGENTS.md`](AGENTS.md): repository boundaries, invariants, development and
  delivery conventions.
- [Product specifications](docs/product/README.md)
- [Accounting specifications](docs/accounting/README.md)
- [Operations](docs/operations/README.md): production, releases, backup and
  recovery, runbooks.
- [User guide](docs/users/README.md), served at `/usl/user-docs` in a running
  distribution.

Odoo itself is documented at
[odoo.com/documentation/19.0](https://www.odoo.com/documentation/19.0/).
