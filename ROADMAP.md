# USL Odoo roadmap

This roadmap records the current product boundary and the work that remains.
Historical implementation checklists and reconstruction notes belong in
`docs/accounting/` and `docs/operations/`; they are not the product roadmap.

## Current release — Accounting v1

Status date: 24 August 2026

- Branch: `19-usl`
- Upstream baseline: `efb98f932f3a568ce550a26ebde06da0e14e65d3`
- Developer/QA product database: `odoo_dev`
- Read-only source snapshot: `odoo_online_source_saas_19_3`

Accounting v1 is engineering-complete for internal daily use. The release is
aligned with the frozen upstream `saas~19.3` baseline and preserves the current
Odoo Online `saas~19.3.1.3` accounting state while keeping
USL behavior isolated in custom add-ons and maintained OCA dependencies.
The same distribution now includes restored Projects, governed Pocket ID SSO,
the focused Paie TESE workflow, Platform Billing and a Paperless-backed
Documents application.

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
- Native multi-company operation with complete company accounting
  configuration, company-scoped roles and same-currency combined management
  statements, synchronized provider-controlled ECB rates and seamless
  company-specific employee expense profiles, verified against both companies
  in the canonical dump. Legal
  consolidation, eliminations, currency translation and unused Enterprise
  payment transports are explicitly outside Accounting v1.
- Configurable, versioned Controls, Reports and Declarations under Accounting
  Configuration, with operational results kept separate.
- Compact interactive financial, partner, tax, management and analytical
  reports with drill-down and consistent PDF/XLSX exports.
- Dynamic analytical pivot, list and graph exploration.
- Accounting Hygiene, declaration preparation, closing workspaces and native
  French FEC generation.
- Scoped read-only accountant access to records, evidence, reports and
  permitted exports without accounting mutation.
- Native Projects with restored tasks, dependencies, chatter, evidence and
  analytic links, plus the maintained `usl_project` extensions.
- Paie TESE records the provider PDF, dated HR/profile context, balanced
  payroll entry and native bank reconciliation without becoming a second
  legal payroll calculator.
- Platform Billing preserves platforms, monthly sessions, payouts, invoices,
  commission bills and delayed or pooled bank settlement in native Accounting,
  including bank-derived foreign-currency rates without rewriting posted
  ledger values.
- Paperless-backed Documents provides authorized search, OCR, previews,
  metadata, versions, Trash and links to Accounting, Contacts, Employees and
  Paie TESE records without duplicating originals in Odoo.
- Canonical reconstruction can rebuild the complete Paperless archive. Normal
  local QA uses a deterministic semantic sample and does not wait for bulk OCR;
  complete ingestion remains a release/cutover qualification gate.
- Pocket ID SSO with immutable identity links and one independent local
  break-glass administrator; Odoo remains authoritative for roles, companies
  and record rules.
- French electronic-invoice reception for UBL, CII and Factur-X invoices and
  credit notes, including native draft bills, original evidence,
  duplicate/retry controls, role-aware browser journeys and controlled
  readiness. It is ready but deliberately inactive.
- Complete Accounting attachment reconstruction preserves source metadata,
  native record/chatter links and access inheritance.
- Reproducible development, reconstruction, parity and evidence workflows.

The current accounting counts, balances, source advisories and qualification
evidence are recorded in
[`docs/accounting/saas-19.3-alignment-register.md`](docs/accounting/saas-19.3-alignment-register.md).
The Milestone 13 candidate note is retained as historical evidence only.

## Deliberately inactive or deferred

These five items are not Accounting v1 defects:

1. professional accountant sign-off and live tax/electronic filing;
2. legal-representative onboarding, registration and activation of the
   preselected Odoo Approved Platform;
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

1. Verify production approved-platform identity acceptance, service terms,
   credentials, support and rollback contacts.
2. Review company identifiers, reception journal and access roles; rerun the
   safe offline acceptance test after the production upgrade.
3. Authorize the deployment-level reception guard and Accounting Manager
   approval explicitly in production while keeping e-reporting disabled.
4. Register the receiver, enable reception-only jobs, and validate one
   controlled supplier invoice through posting, payment and reconciliation.
5. Confirm duplicate, retry, rejection, authentication, suspension and
   rollback procedures from the retained evidence.

No additional product development is planned for reception activation. No
non-production environment may register USL, contact a live provider, retrieve
or send real documents, run live scheduled exchange or submit e-reporting.

### Maintenance

- Keep the SaaS baseline and OCA pins reproducible and reviewed.
- Replay verified upstream fixes without modifying upstream-owned code unless
  an isolated extension is impossible and the trade-off is documented.
- Protect material accounting, reconciliation, security and report behavior
  with focused regression tests.
- Move from `saas-19.3` only through a separately planned and rehearsed upgrade.
- Update user documentation when verified behavior changes; do not preserve
  temporary progress reports as product documentation.
