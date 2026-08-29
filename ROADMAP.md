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

Accounting v1 is engineering-complete for internal daily use. The current
`19-usl` baseline is not yet the final production release. Expense Analytics
is integrated at `aae5994a7ec`, and B2C sales/inventory is integrated through
merge `368812b2868`. Monthly bank-statement email ingestion is integrated
through merge `64c1f2b1207`. Paperless 3.0, the schema-v4 migration-cache/
Documents-performance follow-up, Collaboration History, Distribution Access
Control, and the project/task browser-title fix are merged directly into
`19-usl` with their reviewed ancestry preserved. Native Sign and the Templating
system remain the final independently reviewable product workstreams. Both must
be merged or explicitly rejected before final production qualification; no
topic worktree, old QA seed, or rehearsal candidate can be promoted directly.

The combined Documents implementation preserves the exact reviewed tips of
both topic branches through explicit merge commits. Its five affected product
modules passed clean install, upgrade and identical repeated upgrade; Documents
also passed its query budgets and desktop/mobile Chromium suites. The exact
Paperless overlay also builds successfully for production `linux/amd64`. A
fresh locked-source reconstruction and finalization now pass on `19-usl`.
The finalized target was requalified after the final service-image pinning and
published content-qualified schema-v4 seed
`302bb448494fab08162303cbac26ae007db657d49daa5df23f9733abfc20df29`.
Its isolated cold hydration passed in 391 seconds, including current-branch
upgrade/finalization, with exact sealed controls and zero OCR submissions. The
immediate fail-closed warm reuse passed in 15 seconds with zero downloaded bytes
and zero OCR submissions. This is reusable rehearsal evidence only: merging
Sign or Templating invalidates it and requires a new qualification. Release
qualification still requires the signed-in browser matrix.

The 27 August integrated performance pass additionally reduced the measured
40-document workspace from 426 to 75 SQL queries and from 432.8 ms to 67.3 ms,
and replaced per-record archive-status counts with one grouped query. The
Paperless `3.0.5-usl.7` candidate separates bulk source ingestion from one
verified semantic-index update so CPU-bound BGE-M3 jobs no longer block every
subsequent upload. Its image build, hash guards, focused Django tests and
migration safety tests pass. The optimized locked-source reconstruction reused
646 exact governed roots without submitting bytes, archived all 832 eligible
native attachments, and finalized 1,148 live Paperless documents plus nine
Trash records with exact 8,654-row vector parity and zero active tasks.

The integrated product also preserves project-specific task titles
when browser history rebuilds the Projects action. Its generic desktop
webclient regression is qualified through merge `602df379352`. An actual
Projects signed-in browser journey remains a release gate; the feature itself
is merged into `19-usl`.

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
- Canonical reconstruction can rebuild the complete Paperless archive. Normal
  local QA uses a deterministic semantic sample and does not wait for bulk OCR;
  complete ingestion remains a release/cutover qualification gate.
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
- Content-qualified schema-v4 reconstruction seeds, explicit verified warm
  worktree reuse, batched Documents queries, lazy Documents workspace assets,
  and bounded Odoo worker/connection/memory/request budgets. These are merged
  into `19-usl`; shared-seed publication and the real cold/warm reuse cycle
  pass on the consolidated tree.

These foundations and the B2C work are now part of local `19-usl`. After Docker
capacity was increased, a fresh full reconstruction of source dump
`ad313e28586fafa27a4f6a266df57080456613dff1c8c2c6d7e012732bf633b1`
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
release candidate. The current locked-source reconstruction supersedes it,
but the final frozen-source run is still required after Native Sign and the
Templating system are decided.

The 27 August Collaboration integration candidate completed another canonical
reconstruction from the same locked source. Its two Collaboration passes were
identical at 50,005 messages, 36,946 tracking values, 5,862 followers and 895
activities. After product review, 49,186 operational messages are retained and
819 messages are deliberately not copied: 66 default/demo Knowledge messages,
554 generated configuration notifications or tracking events with no customer
or operational content, and 199 retired Documents folder/URL activity messages.
Their exact disposition is checksum-sealed outside the product; no placeholder
PDF or parallel archive model is delivered. Finalization
removed every temporary migration model/field/table/XML ID and passed the
product database boundary. Identity and Documents qualification retained the
source-backed Paperless archive and produced zero changes on repeated
synchronization. The shared reusable QA seed was not published from the
integration branch because publication is correctly restricted to a clean
`19-usl` checkout. These results qualify this merge but, like all earlier
rehearsals, must be repeated after the remaining workstreams merge.

The current `19-usl` isolated clean-install and repeated-update check passes
for all fifteen presently delivered product modules with no migration registry
or schema residue. That closes the earlier partial-`odoo_dev` ambiguity; it is
module-installation evidence, not a substitute for the final full migration.

The 27 August final integrated audit reconstructed canonical `odoo_dev`:
5,425 moves, 12,991 move lines, 2,861 partial
reconciliations, balanced posted debit/credit of EUR 2,900,936.82, all five
source-journal move counts, 304 B2C orders, 457 lines, 1,821 payment events,
261 fulfilments, 2,893 evidence rows, 45 source product-value rows and zero
stock moves/quants remain intact. Finalization now delivers exactly 15 product
modules, passes the 50,041-action source and 42,669-action runtime security
inventories, and leaves no migration registry or schema residue.

The current locked-source rehearsal now clears all 19 strict source scopes:
226,836 source records have explicit dispositions, with zero blocked records,
relation rows or stored fields. The previously open AI, Sales/Marketing and
Studio scopes are deliberately dropped because they contain experiments,
default configuration or customizations superseded by the Distribution.
Preferences are translated or explicitly recomputed/dropped. Nine default
dashboard definitions are recomputed or rejected as unsupported configuration,
and the genuine strategy PDF is retained byte-for-byte as a restricted
manager-only Document. B2C has complete honest dispositions: nine aliases are
exactly verified, 100 are explicitly not applicable, all 180 source-ledger
moves have monthly session links, all 40 source files are archived, and no
accounting relationship or SKU mapping is left unexplained and pending.
Aggregate monthly coverage is not presented as one-to-one order accounting,
and the 35 header-only Medusa orders remain visible without invented lines.
The rehearsal is still not a production admission candidate: a final
frozen-source rerun, the Native Sign and Templating decisions, isolated-host
dress rehearsal and operational approvals remain required. The
30 September physical opening-stock count is a separate go-live prerequisite;
the migration correctly creates no unsupported historical stock activity.
The clean 27 August reconstruction additionally passed final product-boundary,
outbound-queue and multi-company controls with 15 delivered modules, 1,148 live
Documents, nine Trash records and no migration residue. The resulting
`odoo_dev` is the canonical local pre-production target; Native Sign or any
later release change invalidates it for final admission.

The former 107 pending attachment IDs now have explicit Collaboration,
Preferences, signing-evidence, dashboard, or restricted-Documents
dispositions. This merged tree still requires a new source-complete run before
those earlier results become evidence for the combined candidate.

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
2. Migration cache/Documents performance follow-up — **merged into `19-usl`
   from exact feature tip `3d2b2b49382`; schema-v4 seed publication and one
   exact-tree cold/warm cycle pass with zero OCR work and zero warm downloads**.
3. Expense Analytics — **merged into `19-usl` at `aae5994a7ec`**.
4. B2C sales/inventory — **merged into `19-usl` at `368812b2868`; full
   canonical rehearsal passed with complete evidence, relationship and alias
   dispositions; physical opening stock remains a separate later operation**.
5. Paperless 3.0 — **merged into `19-usl` from exact feature tip
   `2ba19d6fa90`; clean module/install/update/browser suites, AMD64 overlay
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
