# USL Accounting

Technical module name: `rebuild_account_migration`

This isolated add-on is the USL Accounting product layer for the Community
`saas~19.3` fork. The historical technical name is retained to preserve module,
model and XML-ID continuity; users see **USL Accounting**, not migration
tooling.

## Product scope

- Accounting Overview and cash position;
- document, payment and reconciliation extensions;
- Accounting Hygiene, closing and declarations;
- configurable Controls, Reports and Declarations;
- interactive financial and management reports with PDF/XLSX;
- analytical pivot/list/graph reporting;
- assets, deferrals, currency and FEC integration;
- scoped read-only accountant access;
- electronic-invoice reception readiness, inactive until approved production
  activation.

Normal Accounting menus and the product registry expose only operational
concepts. Reconstruction, source comparison, parity review, source bindings
and import objects are not part of this add-on. They live in the temporary
`migration/accounting_restore` add-on path, which the canonical reconstruction
uninstalls before the target is accepted.

This product module does not alter standard Odoo tour state. The dev/QA
deployment helper sets the native per-user `tour_enabled` preference to false
for internal users in `odoo_dev` and other explicitly operated QA targets.
This runs after `make dev`, `make deploy`, and `make rebuild`;
`make disable-tours` reapplies it directly.

## Dependencies

The manifest is authoritative. Standard Odoo models remain the system of
record; maintained OCA modules provide Community reconciliation, reporting,
asset and statement-import capabilities. USL behavior extends those modules
without modifying upstream Odoo core.

Shared extensions of existing native and OCA models live in
`usl_accounting`; expense claim batches live in `usl_expense_batch`. This
module remains their compatibility consumer and retains installed
`rebuild.*` models and stable XML/data ownership. See the
[custom add-on architecture decision](../../docs/accounting/custom-addon-architecture.md).

The one-off source-faithful expense stage reads the former Online expense-to-bank
suggestion cache only as migration evidence. It classifies every candidate,
many-to-many and selected-line association, recomputes current operational
suggestions through `usl_accounting`, and proves that refreshes are idempotent
and leave expenses, moves, lines, payments and reconciliations unchanged. The
legacy `x_sl_expense_bank_candidate` model, `x_*` fields, server actions, ACLs
and inherited view are never imported. That stage is implemented by the
temporary `usl_accounting_restore` module, not by this product module.

The authenticated user guide at `/usl/user-docs` renders the repository files
under `docs/users/` with the pinned CommonMark runtime. Common Markdown
formatting, nested lists, tables, code blocks and repository-relative links are
supported; raw HTML is displayed as text and generated HTML is sanitized.

## Development

Use the repository workflow:

```bash
make deploy
scripts/odoo-dev test-tag '/usl_accounting,/rebuild_account_migration'
scripts/odoo-dev test-js rebuild_account_migration
```

The sole developer/QA product database is `odoo_dev`. Do not open the read-only
source database with target code, and do not replace `odoo_dev` with a
validation database. One-off reconstruction and parity commands are documented in
[`docs/operations/accounting-development-workflow.md`](../../docs/operations/accounting-development-workflow.md).

## Release evidence

Durable Accounting requirements live under `docs/accounting/`. Current
priorities and production acceptance gates live in `ROADMAP.md` and
`docs/operations/production.md`. Private production-derived evidence must
never be committed.
