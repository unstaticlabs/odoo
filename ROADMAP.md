# USL Odoo roadmap

This roadmap records the current product boundary and the work that remains.
Historical implementation checklists and reconstruction notes belong in
`docs/accounting/` and `docs/operations/`; they are not the product roadmap.

## Current release — Distribution production candidate

Status date: 25 August 2026

- Branch: `19-usl`
- Upstream baseline: `efb98f932f3a568ce550a26ebde06da0e14e65d3`
- Developer/QA product database: `odoo_dev`
- Read-only source snapshot: `odoo_online_source_saas_19_3`

Accounting v1 is engineering-complete for internal daily use. The current
`19-usl` baseline is not yet the final production release. Expense Analytics
is now integrated at `aae5994a7ec`; B2C sales/inventory, Paperless 3.0, Native
Sign and monthly bank-statement email ingestion remain independently reviewable
workstreams. They must be reviewed and merged before the final full
reconstruction; no feature worktree, old QA seed or rehearsal candidate can be
promoted directly.

The integrated baseline is aligned with the frozen upstream `saas~19.3`
baseline and preserves the current
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
- Optional Expense Batches for trips, productions, projects and periodic
  claims. Products retain expense nature while the Batch applies shared
  purpose, SBFH/Epic analytics and controlled account context with visible
  exceptions, mixed-payer progress and journal traceability. The focused form
  keeps interactive analytics and specific line attention in the main journey.
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

### Implemented on the migration-performance candidate, pending integration

- Deterministic bounded Accounting replay with stage timings, source-identity
  indexing and parity-preserving batching.
- Fail-closed full-profile QA seed manifests and portable sanitized Odoo plus
  Paperless production candidates.
- External-Pocket-ID production preflight, stage, configure, gate, reset and
  fingerprint-confirmed admission without owning or mutating Pocket ID.
- Immutable GHCR `distribution` image publication and release/OCA label checks.
- A focused launcher that de-emphasizes Discussion, To-do, Dashboards and Apps.
- Contextual French terminology guards and corrected visible Accounting,
  Documents, HR/Paie and navigation language.
- Dump-bound source gap evidence that names every populated model, relation
  table and stored/manual field under its delivered or blocked scope.
- A pre-mutation Docker capacity guard and accurate exit-137 resource
  classification, without stopping unrelated feature projects.

These items are not marked shipped until their branch is reviewed and merged
into `19-usl`. The complete reusable QA seed publication, two zero-OCR
hydrations, current-source performance comparison and production dress
rehearsal remain outstanding. A fresh no-Documents rehearsal reached the
Accounting import but was OOM-killed by the shared 8 GiB Docker VM; its target
must be reset and the run repeated on a responsive, sufficiently isolated
runtime before it counts as evidence.

The current candidate's isolated clean-install and repeated-update check passes
for all twelve presently delivered product modules with no migration registry
or schema residue. That closes the earlier partial-`odoo_dev` ambiguity; it is
module-installation evidence, not a substitute for the final full migration.

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

The canonical, evidence-bearing sequence is maintained in the
[production cut-over readiness register](docs/operations/production-cutover-readiness.md).

### Merge train

1. Review and merge the migration-performance/portable-candidate work.
2. Expense Analytics — **merged into `19-usl` at `aae5994a7ec`**.
3. Review and merge B2C sales/inventory with complete historical source
   disposition and analytics dimensions.
4. Review and merge Paperless 3.0 and requalify the official export/import and
   zero-OCR paths.
5. Review and merge Native Sign with signing evidence and permission gates.
6. Review and merge monthly bank-statement email ingestion with idempotence,
   failure visibility and a manual-import fallback.
7. From clean final `19-usl`, run clean install, update, repeated update, full
   local reconstruction and complete product/migration boundary across every
   delivered module.

The exact merge order may change to resolve dependencies, but each feature
remains independently reviewed and every merge invalidates earlier release
evidence.

### Production cutover

1. Before freezing Online, qualify a full reconstruction from the most recent
   available dump, publish/hydrate the reusable full QA seed and complete a
   production dress rehearsal.
2. Approve the exact release commit, immutable Distribution image, deployment
   configuration, owners, monitoring and backup/recovery plan.
3. Freeze Odoo Online read-only; take and verify a new final source backup and
   filestore. Any resumed source activity invalidates the candidate.
4. Run one clean full reconstruction, strict whole-source/attachment gates and
   build an independently fingerprint-approved portable candidate.
5. Stage into fresh production application volumes with cron, mail, providers,
   OCR and ingress paused; preserve the existing Pocket ID and host policies.
6. Configure identities, run parity, roles, reports, reconciliation, FEC,
   Documents, B2C/inventory, Sign, email-bank and browser controls.
7. Obtain technical, infrastructure, Accounting Manager, professional and
   final business go/no-go decisions; admit the exact fingerprint.
8. Activate ingress, backups, mail, bank ingestion and other external services
   only through their explicit post-admission gates. Keep both regulatory live
   flags at `0` until separate legal/provider runbooks are approved.

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
