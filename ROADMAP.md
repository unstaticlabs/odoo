# USL Odoo roadmap

This roadmap records the current product boundary and the work that remains.
Historical implementation checklists and reconstruction notes belong in
`docs/accounting/` and `docs/operations/`; they are not the product roadmap.

## Current release — Distribution production candidate

Status date: 28 August 2026

- Branch: `19-usl`
- Upstream baseline: `aef56898d9ea5a97948af04c03ae101d17b8b4a3`
- Developer/QA product database: `odoo_dev`
- Read-only source snapshot: `odoo_online_source_saas_19_3`

Accounting v1 and the current product perimeter are engineering-complete for
local migration QA. The release is not production until the final frozen
Online package is reconstructed, the coordinated Odoo/Paperless/Ollama/Sign
cohort is independently restored, the current financial and access controls
pass, and the first production backup is restored successfully.

Older source checksums, reconstruction seeds, candidates, counts, and
fingerprints are historical only and cannot qualify the current release.
Migration QA and transition reconstruction are fresh-source operations through
`migration/manage`; no shared reconstruction cache or resume path exists.

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
  analytic links, plus the maintained `usl_project` extensions. The current
  integration candidate also retains the project name in task browser titles
  and breadcrumbs across Back/Forward history restoration.
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
- Canonical reconstruction rebuilds the complete Paperless archive from the
  frozen source. Migration QA does not use a shared archive seed or sample in
  place of full source coverage.
- Pocket ID SSO with immutable identity links and one independent local
  break-glass administrator; Odoo remains authoritative for roles, companies
  and record rules.
- Recoverability-based Distribution roles for Valentin, Roger, Prosper and
  Agents, with a separately enforced irreversible-action capability, immutable
  audit evidence and explicit two-company Accounting review for Prosper. The
  merged candidate qualifies 50,041 reviewed source actions and 42,669 runtime
  actions while loading only the compact protected runtime projection in
  workers.
- French electronic-invoice reception for UBL, CII and Factur-X invoices and
  credit notes, including native draft bills, original evidence,
  duplicate/retry controls, role-aware browser journeys and controlled
  readiness. It is ready but deliberately inactive.
- Complete Accounting attachment reconstruction preserves source metadata,
  native record/chatter links and access inheritance.
- Source-backed Collaboration history restores native chatter, tracking,
  followers, activities, recipients, reactions and attachment relationships
  across rebuilt business records without recreating outbound delivery queues.
  Unsupported technical history remains sealed private evidence, and the
  temporary restore module is removed from the delivered database.
- Reproducible development, reconstruction, parity and evidence workflows.

### Integrated migration and release foundations

- Deterministic bounded Accounting replay with stage timings, source-identity
  indexing and parity-preserving batching.
- One fail-closed `migration/manage` interface for QA, transition, portable
  candidates, evolved cohorts, and cutover.
- Exact runtime identity for the source, project, database, ports, images,
  Compose topology, Docker resources, secrets, and release commit.
- Fresh-source reconstruction with no shared seed, checkpoint, or resume path.
- Native macOS Ollama for local embedding work and pinned containerized Ollama
  on Linux production.
- External-Pocket-ID production preflight, stage, configure, gate, reset and
  fingerprint-confirmed admission without owning or mutating Pocket ID.
- Immutable GHCR `distribution` image publication and release/OCA label checks.
- Dump-bound source gap evidence that names every populated model, relation
  table and stored/manual field under its delivered or blocked scope.
- Exact Docker-label, working-directory, and recorded-resource ownership for
  every lifecycle or cleanup action.
- Immutable candidate and coordinated cohort verification with independent
  restore before production admission.

The current accounting counts, balances, source advisories and qualification
evidence are recorded in
[`docs/accounting/saas-19.3-alignment-register.md`](docs/accounting/saas-19.3-alignment-register.md).
Only evidence bound to the final frozen source and current release is valid for
admission.

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
[migration](docs/operations/migration.md) and
[production](docs/operations/production.md) runbooks.

### Merge train

1. Migration-performance/portable-candidate — **merged through
   `61580c1704c`**.
2. Documents migration and performance follow-up — **integrated; final
   evidence must come from the current frozen source**.
3. Expense Analytics — **merged into `19-usl` at `aae5994a7ec`**.
4. B2C sales/inventory — **merged into `19-usl` at `368812b2868`; full
   canonical rehearsal passed with complete evidence, relationship and alias
   dispositions; physical opening stock remains a separate later operation**.
5. Paperless 3.0 — **integrated; clean module/install/update/browser suites,
   AMD64 overlay
   build, complete archive reconstruction and vector parity pass. The
   release-cohort restore and signed-in manual journeys remain required**.
6. Collaboration History — **merged into `19-usl`;
   clean reconstruction, repeated import and product-boundary qualification
   passed; its source scope and attachment relationships are closed without
   migration-only provenance or outbound delivery state in the product**.
7. Review and merge Native Sign with signing evidence and permission gates.
8. Review and merge the Templating system with rendered-output, permissions,
   Accounting integration and deployment gates.
9. Distribution Access Control — **merged into `19-usl`; the final merged
   registry passes the requalified 50,041-action source inventory and
   42,669-action runtime inventory. Signed-in persona acceptance remains.**
10. Monthly bank-statement email ingestion — **merged at `64c1f2b1207`; clean
   product/OCA suites and repeated canonical upgrade passed. Production still
   requires the private OFX cut-over preview/apply/repeat and a routed synthetic
   mail test.**
11. Dynamic Project task history titles — **merged into `19-usl`
   through merge `602df379352`; 77 focused desktop webclient tests and 320
   assertions passed. Actual Projects Back/Forward browser acceptance and
   signed-in Projects acceptance remains pending.**
12. **Current locked-source reconstruction and complete product/migration
   boundary pass.** Repeat them from the final frozen source after Native Sign
   and Templating are merged or explicitly rejected.

Every release change invalidates earlier migration admission evidence.

### Production cutover

1. Before freezing Online, qualify a fresh full reconstruction from the most
   recent package and complete a production dress rehearsal.
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
