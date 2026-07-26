# USL Accounting

Technical module name: `rebuild_account_migration`

This isolated add-on is the USL Accounting product layer for the Community
`saas~19.2` fork. The historical technical name is retained to preserve module,
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

Normal Accounting menus expose only operational concepts. Reconstruction,
source comparison, parity review and import objects are restricted to technical
administrators and remain outside the product navigation.

## Dependencies

The manifest is authoritative. Standard Odoo models remain the system of
record; maintained OCA modules provide Community reconciliation, reporting,
asset and statement-import capabilities. USL behavior extends those modules
without modifying upstream Odoo core.

## Development

Use the repository workflow:

```bash
make deploy
scripts/odoo-dev test-tag '/rebuild_account_migration'
scripts/odoo-dev test-js rebuild_account_migration
```

The sole developer/QA product database is `odoo_dev`. Do not open the read-only
source database with target code, and do not replace `odoo_dev` with a
validation database. Reconstruction and parity commands are documented in
[`docs/operations/accounting-development-workflow.md`](../../docs/operations/accounting-development-workflow.md).

## Release evidence

Current acceptance scope, accounting counts, advisories and evidence locations
are recorded in
[`docs/accounting/milestone-13-final-candidate.md`](../../docs/accounting/milestone-13-final-candidate.md).
Private production-derived artifacts under `artifacts/accounting-compat/private/`
must never be committed.
