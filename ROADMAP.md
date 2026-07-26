# USL Odoo roadmap

This roadmap records the current product boundary and the work that remains.
Historical implementation checklists and reconstruction notes belong in
`docs/accounting/` and `docs/operations/`; they are not the product roadmap.

## Current release — Accounting v1

Status date: 26 July 2026

- Branch: `saas-19.2-usl-feat-accounting`
- Upstream baseline: `8a44ecc8da96e341ac472fec27352d138ed2edd7`
- Developer/QA product database: `odoo_dev`
- Read-only source snapshot: `odoo_online_source_saas_19_2`

Accounting v1 is engineering-complete for internal daily use. The release
preserves the current Odoo Online `saas~19.2` accounting state while keeping
USL behavior isolated in custom add-ons and maintained OCA dependencies.

### Shipped

- Daily Accounting Overview with cash on banks, projected cash after
  settlement, freshness, obligations, anomalies, declarations and closing
  priorities.
- Native customer invoices, credit notes, supplier bills, refunds, expenses,
  payments, bank transactions and journal entries.
- Transactions, Bank Matching, General Reconciliation and Matched Items/Undo
  as distinct, role-aware journeys.
- Assets, depreciation, deferrals, historical currencies and multi-plan
  analytical accounting.
- Configurable, versioned Controls, Reports and Declarations under Accounting
  Configuration, with operational results kept separate.
- Compact interactive financial, partner, tax, management and analytical
  reports with drill-down and consistent PDF/XLSX exports.
- Dynamic analytical pivot, list and graph exploration.
- Accounting Hygiene, declaration preparation, closing workspaces and native
  French FEC generation.
- Scoped read-only accountant access to records, evidence, reports and
  permitted exports without accounting mutation.
- French electronic-invoice reception capability, representative document
  validation and a controlled readiness screen. It remains deliberately
  disconnected.
- Complete reconstruction of 704 Accounting attachments with source metadata,
  native record/chatter links and access inheritance.
- Reproducible development, reconstruction, parity and evidence workflows.

The verified accounting counts, balances, source advisories and evidence index
are recorded in
[`docs/accounting/milestone-13-final-candidate.md`](docs/accounting/milestone-13-final-candidate.md).

## Deliberately inactive or deferred

These five items are not Accounting v1 defects:

1. professional accountant sign-off and live tax/electronic filing;
2. selection, registration and activation of a production approved
   electronic-invoicing platform;
3. live bank synchronization and payment-provider ingestion;
4. probabilistic or autonomous AI matching and posting;
5. production deployment and cutover from the disposable development
   environment.

The complete source reconciliation graph is now materialized natively.
Preserved source chronology exceptions remain explicit assurance evidence;
the target must not introduce any additional exception.

## Next release gates

### Production cutover

1. Approve the exact release commit and deployment configuration.
2. Take and verify a final source backup and filestore.
3. Run one clean reconstruction from that frozen source.
4. Re-run parity, roles, reports, reconciliation, FEC and attachment controls.
5. Obtain Accounting Manager acceptance and the required professional review.
6. Execute the documented deployment and rollback rehearsal.
7. Replace the development environment only through the approved cutover
   process.

### September 2026 electronic-invoice activation

1. Select and approve the production platform.
2. Review company identifiers, reception journal and access roles.
3. Authorize activation explicitly in production.
4. Receive and validate one controlled supplier invoice.
5. Confirm duplicate, rejection, suspension and rollback procedures.

No development environment may register USL in a live directory or run live
scheduled exchanges.

### Maintenance

- Keep the SaaS baseline and OCA pins reproducible and reviewed.
- Replay verified upstream fixes without modifying upstream-owned code unless
  an isolated extension is impossible and the trade-off is documented.
- Protect material accounting, reconciliation, security and report behavior
  with focused regression tests.
- Move from `saas-19.2` only through a separately planned and rehearsed upgrade.
- Update user documentation when verified behavior changes; do not preserve
  temporary progress reports as product documentation.
