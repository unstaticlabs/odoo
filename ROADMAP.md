# USL Odoo roadmap

This roadmap records the current product boundary and the work that remains.
Historical implementation checklists and reconstruction notes belong in
`docs/accounting/` and `docs/operations/`; they are not the product roadmap.

## Current release — Distribution production candidate

Status date: 27 August 2026

- Branch: `19-usl`
- Upstream baseline: `aef56898d9ea5a97948af04c03ae101d17b8b4a3`
- Developer/QA product database: `odoo_dev`
- Read-only source snapshot: `odoo_online_source_saas_19_3`

Accounting v1 is engineering-complete for internal daily use. The current
`19-usl` baseline is not yet the final production release. Expense Analytics
is integrated at `aae5994a7ec`, and B2C sales/inventory is integrated through
merge `368812b2868`. Monthly bank-statement email ingestion is integrated
through merge `64c1f2b1207`. Paperless 3.0 and Native Sign remain independently
reviewable workstreams. Collaboration History remains an active, unmerged
workstream. Distribution Access Control is integrated and qualified on the
dedicated merge candidate through `b951d3395f7`; its merge-commit PR still has
to land on `19-usl`. All remaining workstreams must be merged or explicitly
rejected before the final production qualification; no feature
worktree, old QA seed or rehearsal candidate can be promoted directly.

The integrated baseline is aligned with the frozen upstream `saas~19.3`
baseline and preserves the current
Odoo Online `saas~19.3.1.3` accounting state while keeping
USL behavior isolated in custom add-ons and maintained OCA dependencies.
The same distribution now includes restored Projects, governed Pocket ID SSO,
the focused Paie TESE workflow, Platform Billing and a Paperless-backed
Documents application.
The validated upstream catch-up is retained through merge `4104a3abbef`. The
complete current fork surface, module ownership, migration boundary and
maturity map is maintained in
[`docs/product/fork-overview.md`](docs/product/fork-overview.md).

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
- B2C preserves incomplete and overlapping Etsy, Medusa, Stripe, Revolut and
  Printful history as canonical auditable records without manufacturing native
  sales, payments, stock moves or accounting effects. It restores the complete
  46-template catalog, keeps future operations native, and exposes explicit
  SKU, currency, accounting-link and opening-stock coverage controls.
- Scheduled bank-statement ingestion retains each approved email and original
  file, imports exact OFX identities into native statements, requires the
  official PDF in Documents, and provides monthly balance, continuity,
  certification and controlled-reopening checks without creating a parallel
  ledger.
- Paperless-backed Documents provides authorized search, OCR, previews,
  metadata, versions, Trash and links to Accounting, Contacts, Employees and
  Paie TESE records without duplicating originals in Odoo.
- Canonical reconstruction can rebuild the complete Paperless archive. Normal
  local QA uses a deterministic semantic sample and does not wait for bulk OCR;
  complete ingestion remains a release/cutover qualification gate.
- Pocket ID SSO with immutable identity links and one independent local
  break-glass administrator; Odoo remains authoritative for roles, companies
  and record rules.
- Recoverability-based Distribution roles for Valentin, Roger, Prosper and
  Agents, with a separately enforced irreversible-action capability, immutable
  audit evidence and explicit two-company Accounting review for Prosper. The
  merged candidate qualifies 49,713 reviewed action keys and loads only the
  compact protected runtime projection in workers.
- French electronic-invoice reception for UBL, CII and Factur-X invoices and
  credit notes, including native draft bills, original evidence,
  duplicate/retry controls, role-aware browser journeys and controlled
  readiness. It is ready but deliberately inactive.
- Complete Accounting attachment reconstruction preserves source metadata,
  native record/chatter links and access inheritance.
- Reproducible development, reconstruction, parity and evidence workflows.

### Integrated migration and release foundations

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
- A project-wide Impeccable code-first UI workflow and design detector for
  future Odoo view, accessibility, responsive and frontend work.
- Dump-bound source gap evidence that names every populated model, relation
  table and stored/manual field under its delivered or blocked scope.
- A pre-mutation Docker capacity guard and accurate exit-137 resource
  classification, without stopping unrelated feature projects.

These foundations and the B2C work are now part of local `19-usl`. After Docker
capacity was increased, a fresh full reconstruction of source dump
`0b9916db4807206f63b654bd2933ac89b0aab30ba7e0a1004edc4c060490238f`
completed on 26 August and published a reusable sanitized QA seed. The run
proved repeatable Product, Accounting, Identity, B2C, HR, Projects, Documents,
Paie TESE and Platform Billing restoration, 645 archived Paperless documents,
638 live authorized document mappings, and a final product-only registry. The
same canonical run restored all 40 checksum-locked B2C source files and linked
all 2,893 immutable provider-evidence rows to the searchable archive.
Two independent isolated seed hydrations then reproduced identical Accounting,
Documents and Paperless controls with zero OCR submissions. Current-source
performance comparison and the production dress rehearsal remain outstanding.
The subsequent bank-ingestion merge passed clean installs for all four affected
product modules, the pinned OCA OFX suite and repeated upgrades of canonical
`odoo_dev`; its accounting, B2C and stock fingerprints remained unchanged.
That focused evidence does not promote the earlier full seed to a final
release candidate. A new full reconstruction is still required after the
remaining workstreams merge.

The current candidate's isolated clean-install and repeated-update check passes
for all fifteen presently delivered product modules with no migration registry
or schema residue. That closes the earlier partial-`odoo_dev` ambiguity; it is
module-installation evidence, not a substitute for the final full migration.

The rehearsal is not a production admission candidate. The strict whole-source
gate now identifies four incomplete scopes—attachments, Collaboration,
preferences and Sign—the attachment ledger has 98 pending source attachment
IDs, and the separately governed physical
opening-stock count is still not evidenced. B2C itself now has complete honest
dispositions: nine aliases are exactly verified, 100 are explicitly not
applicable, all 180 source-ledger moves have monthly session links, all 40
source files are archived, and no accounting relationship or SKU mapping is
left unexplained and pending. Aggregate monthly coverage is not presented as
one-to-one order accounting, and the 35 header-only Medusa orders remain
visible without invented lines. The remaining whole-source and physical-count
items are visible release blockers, not silently discarded data.

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

1. Migration-performance/portable-candidate — **merged through
   `61580c1704c`**.
2. Expense Analytics — **merged into `19-usl` at `aae5994a7ec`**.
3. B2C sales/inventory — **merged into `19-usl` at `368812b2868`; full
   canonical rehearsal passed with complete evidence, relationship and alias
   dispositions; physical opening stock remains a separate later operation**.
4. Review and merge Paperless 3.0 and requalify the official export/import and
   zero-OCR paths.
5. Review and merge Native Sign with signing evidence and permission gates.
6. Review and merge Collaboration History, then close the corresponding
   strict source scope and attachment dispositions without importing
   migration-only provenance into the product.
7. Distribution Access Control — **integrated on the dedicated candidate
   through `b951d3395f7`; clean install/update, canonical finalization, named
   personas, multi-company policy, backend and desktop/mobile suites pass.
   Merge-commit PR and live interactive browser acceptance remain.**
8. Monthly bank-statement email ingestion — **merged at `64c1f2b1207`; clean
   product/OCA suites and repeated canonical upgrade passed. Production still
   requires the private OFX cut-over preview/apply/repeat and a routed synthetic
   mail test.**
9. From clean final `19-usl`, repeat clean install, update, repeated update,
   full local reconstruction and complete product/migration boundary across
   every delivered module after the remaining workstreams are merged.

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
