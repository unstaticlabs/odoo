# Milestone 13 current progress report

Last updated: 2026-07-23

Audience: Valentin, USL finance operators, the USL accountant, and the next implementation agent.

This report describes the current implementation state. It is not a closure report. Deterministic reconstruction, the Community-compatible accounting report workbench and the main accounting review surfaces are technically implemented; professional report, tax, FEC and milestone acceptance is still pending.

The configuration-specific source/target decisions are recorded in
[Milestone 13 Accounting Configuration Capability Matrix](milestone-13-configuration-capability-matrix.md).

Formal checkpoint:

- [Milestone 13 checkpoint - 2026-07-23](milestone-13-checkpoint-2026-07-23.md)

Related target document:

- [Milestone 13 reporting and closing UX target](milestone-13-reporting-and-closing-ux-target.md)
- [Milestone 13 screenshot parity and user-journey scorecard](milestone-13-screenshot-parity-matrix.md)

## Target

The target remains a Community 19 accounting product that can rebuild Unstatic Labs accounting from the Odoo Online `saas~19.2` backup, preserve the accounting meaning of the source records, and let normal accounting users inspect ledger, tax, statutory, management and FEC outputs through Odoo.

The benchmark source is the production dump in `usl-online-dump/`, with the previously verified SHA-256:

```text
bf16ce18965e4ce1b23d7b79930b6e43ca7f510339ac6d2db280231f91d1449f
```

The main validation target database used by the harness is:

```text
odoo_rebuild_accounting_test
```

The older `odoo19` database must be treated as development or synthetic state unless it has been explicitly rebuilt from the import pipeline.

The clarified product target now explicitly includes:

- daily closing preparation workflows: reconcile, review, journal entries and evidence checks;
- customer invoices and refunds as usable Odoo business records where source data permits;
- vendor bills and refunds as usable Odoo business records where source data permits;
- expenses where source records permit reconstruction, otherwise a documented limitation;
- polished annual accounts, SIG, CAF, ratios, tax and FEC exports;
- guided declaration views showing what to enter on CFS Pro and Portailpro, with source drill-down and review status.

## What has been implemented so far

### Compatibility pipeline

Implemented:

- `make accounting-source-restore`
- `make accounting-extract`
- `make accounting-target-reset`
- `make accounting-target-import`
- `make accounting-target-validate`
- `make accounting-reports`
- additional comparison, idempotence, failure-test, document-regeneration and FEC-validation stages

Observed current stage artifacts under `artifacts/accounting-compat/private/` show:

- source restore: `passed`
- target reset: `passed`
- target import: `partial`, classified as `POSTED_SOURCE_REPLAY_THROUGH_SNAPSHOT`
- target validation: `passed`, classified as `POSTED_LEDGER_SLICE_PARITY`
- compare: `passed`, classified as `POSTED_LEDGER_SLICE_PARITY`
- reports: `passed`, classified as `DYNAMIC_ODOO_REPORT_WORKBENCH_TECHNICALLY_VALIDATED`
- idempotence guardrail: `passed`
- target failure guardrails: `passed`
- document-regeneration cases: `passed`
- Track B native expenses: `passed`, classified as `TRACK_B_NATIVE_EXPENSE_REPLAY`
- Track B native business documents: `passed`, classified as `TRACK_B_NATIVE_BUSINESS_DOCUMENT_REPLAY`
- Track B native document evidence browser: `passed`, classified as `TRACK_B_NATIVE_DOCUMENT_EVIDENCE_BROWSER_GOLDEN_JOURNEY`
- Accounting Hygiene browser: `passed`, classified as `ACCOUNTING_HYGIENE_BROWSER_GOLDEN_JOURNEY`
- Track B native assets: `passed`, classified as `TRACK_B_NATIVE_ASSET_DEPRECIATION_REPLAY`
- Track B native deferrals: `passed`, classified as `TRACK_B_NATIVE_DEFERRAL_AND_OPENING_REPLAY`
- Track B native expense settlement: `passed`, classified as `TRACK_B_NATIVE_EXPENSE_SETTLEMENT`
- Track B native document bank settlement: `passed`, classified as `TRACK_B_NATIVE_DOCUMENT_BANK_SETTLEMENT`
- Track B native multi-plan analytics: `passed`, classified as `TRACK_B_NATIVE_MULTI_PLAN_ANALYTIC_REPLAY`
- hybrid replacement reset: `passed`, classified as `TRACK_B_NATIVE_STATE_CLONED_FOR_REPLACEMENT`
- hybrid historical import: `passed`, classified as `HYBRID_HISTORICAL_EXACT_NATIVE_CURRENT_IMPORT`
- hybrid replacement validation: `partial`, classified as `HYBRID_REPLACEMENT_TARGET_EXPLAINED_NATIVE_DIFFERENCES`
- hybrid replacement browser: `passed`, classified as `HYBRID_REPLACEMENT_MANAGER_REVIEWER_BROWSER_GOLDEN_JOURNEY`
- reconciliation review browser: `passed`, classified as `RECONCILIATION_BOUNDARY_MANAGER_REVIEWER_BROWSER_GOLDEN_JOURNEY`
- FEC role browser: `passed`, classified as `FEC_MANAGER_REVIEWER_OPERATOR_BROWSER_GOLDEN_JOURNEY`
- FEC validation artifact: `passed`, classified as `OFFICIAL_DGFIP_SOURCE_VALIDATION_PASSED`
- capability/report parity matrix: `passed`, classified as `TECHNICAL_CAPABILITY_MATRIX_COMPLETE_PROFESSIONAL_ACCEPTANCE_PENDING` (`54` rows, `0` discovery or technical-gap rows)
- readiness: `blocked`, classified as `TECHNICAL_REHEARSAL_PASSED_PROFESSIONAL_ACCEPTANCE_PENDING`

The readiness artifact now gates on the hybrid reset/import/validation/browser chain, FEC role browser and reconciliation review browser as well as the core replay/report controls. It reports `0` technical failures, `1` open P0, `1` open P1, `1` accountant-owned P2 and `45` draft review decisions. The hybrid validation's classified `partial` status is expected technical evidence for its fully explained native differences; it is not professional acceptance or candidate promotion. Readiness remains blocked until the named reviewer decisions are recorded.

### Runtime side-effect controls

Status: improved, not final production security.

Evidence: the Compose `odoo`, `init-db` and `devcontainer` services now pass `ODOO_MAX_CRON_THREADS=0` by default, and the rendered `/etc/odoo/odoo.conf` includes `max_cron_threads = 0`. After rebuilding and restarting the local Odoo service, recent logs no longer showed scheduled-job execution entries for mail, SMS, VIES, PEPPOL or PDP jobs.

Impact: imported-accounting browser testing is safer because the Odoo scheduler does not execute background jobs against restored or freshly initialized databases by default. The import pipeline still neutralizes target cron and mail/fetchmail records after import; this runtime setting is an additional guard.

Remaining work: production, staging or explicit cron-test environments must define their own policy. This does not replace a final security review of outgoing network access, credentials, mail servers, payment providers or electronic-invoicing services.

### OCA accounting foundation

Implemented after the product decision memo:

- `make oca-addons-sync` fetches pinned OCA 19.0 repositories into ignored local checkouts under `oca-src/`.
- selected OCA modules are exposed through ignored symlinks under `oca-addons/`.
- Docker Compose and Dev Container add-on paths now include `oca-addons/`.
- Docker Compose now mounts both `oca-src/` and `oca-addons/` into the normal Odoo and init containers so symlinked OCA modules resolve at runtime.
- target reset/import harness containers force the OCA add-ons path so old `.env` files do not hide installed OCA modules.

The following OCA modules installed successfully on the disposable imported target database `odoo_rebuild_accounting_test`:

- `date_range` `19.0.1.0.0`
- `report_xlsx` `19.0.1.0.2`
- `report_xlsx_helper` `19.0.1.0.0`
- `account_statement_base` `19.0.1.0.0`
- `account_reconcile_oca` `19.0.1.0.3`
- `account_statement_import_base` `19.0.1.0.0`
- `account_statement_import_file` `19.0.1.0.0`
- `account_statement_import_file_reconcile_oca` `19.0.1.0.0`
- `account_financial_report` `19.0.0.0.15`
- `account_tax_balance` `19.0.1.0.2`
- `partner_statement` `19.0.1.1.0`
- `mis_builder` `19.0.1.1.1`

The OCA modules remain installed as maintained accounting foundations for reconciliation, statement import, partner statements, XLSX support and technical report comparison. Their report actions remain available for technical validation, but duplicate OCA report menus are hidden from normal Accounting navigation.

The full-accounting role hierarchy is now activated by the USL add-on: an Accounting Manager implies Odoo's full Accounting user group instead of remaining in Community's invoicing-only mode. This exposes the native/OCA accounting surfaces without per-database manual group repair. The Accounting app now uses the seven-area navigation from the product target: `Dashboard`, `Customers`, `Vendors`, `Accounting`, `Review`, `Reporting`, and `Configuration`.

The selected report architecture compared three credible options:

1. expose the maintained OCA report wizards directly; this supplied useful calculations but fragmented the normal user experience across unrelated wizards and viewers;
2. depend on the proprietary Enterprise `account_reports` application; that application is absent from the Community checkout and its code is not copied;
3. implement one original Community-compatible dynamic workbench over the native ledger, while retaining OCA as a maintained technical foundation.

Option 3 is implemented. The normal `Reporting` menus for Trial Balance, General Ledger, Journal Report, Partner Ledger, Customer Statement, Open Items, Aged Receivable, Aged Payable, Balance Sheet, Profit and Loss, VAT/tax, management, assets, deferrals, French statements and declarations now open the same full-page workbench. It provides:

- company and multi-company scope, with statutory/FEC single-company safeguards;
- native-ledger scope by default and an explicit imported-only audit scope;
- month, quarter, fiscal-year, year-to-date and custom periods;
- previous-period, previous-year and custom comparisons;
- journal, account, partner and analytic filters;
- grouping by section, account, partner, journal, month or analytic account;
- search, expand/collapse, row-level source drilldown and visible draft-entry warnings;
- consistent CSV, XLSX and PDF metadata and downloadable output;
- a true Trial Balance equation with opening, debit, credit, movement and closing columns.

The OCA Trial Balance, General Ledger, Journal Ledger, Open Items, Aged Partner Balance and MIS reports were retained and tested as comparison surfaces. The primary Accounting menus and the Balance Sheet/Profit and Loss entries no longer send normal users to those competing screens.

The latest `make accounting-reports` run passed with `90` grouped Trial Balance rows, `180` expanded rows, a smaller collapsed result, successful search, previous-year comparison, `4` excluded-draft warnings, matching `180`-row XLSX output, canonical-menu checks and duplicate-menu hiding. All `38` source report families remain at `level_4_evidence_partial` until an authorized accountant records acceptance.

Technical note: OCA MIS Builder's detail-label lookup was active-account-only. The custom addon now patches that lookup to include inactive accounts, because archived accounts are valid historical accounting evidence when they carry posted source lines.

Runtime caveat: the normal Compose `odoo` service requires the local `.env` value `ODOO_ADDONS_PATH=/opt/odoo/addons,/opt/odoo/odoo/addons,/mnt/custom-addons,/mnt/oca-addons`. An older local `.env` without `/mnt/oca-addons` caused installed OCA menus to exist while Odoo could not load OCA web assets. This was corrected locally during the current session.

The developer guides now make refresh behavior explicit. Module updates are
database-specific; a running server must be restarted for Python changes;
backend views, menus, ACLs and manifest declarations require a module update;
assets should be checked with `debug=assets` enabled and a hard refresh; mounted
`docs/users/` Markdown needs only a page reload. The guide also forbids using
source restore, extraction or target reset as a UI cache-refresh mechanism and
names the exact-import and hybrid databases separately.

### Source extraction and reconstruction

The current import status reports these production-derived records represented in the target:

- 2 companies
- 1,877 historical EUR, USD and GBP currency rates
- 140 imported accounts
- 31 imported journals
- 49 source-traced reconciliation models and 49 source-traced model lines
- 4,843 imported posted moves
- 11,392 imported posted move lines
- 97 imported payments
- 3,040 imported bank statement lines
- 3,741 imported reconciliation records
- 332 imported accounting attachments
- 3 imported assets
- 91 imported depreciation schedule lines
- 110 deferred schedule lines
- 632 analytic lines
- 38 imported source report definitions

The isolated Track B native proof additionally preserves:

- `215/215` current-period business-document binaries and `202/202`
  source-designated main attachments;
- `263/263` current-period expense binaries and `245/245`
  source-designated main attachments;
- zero missing files, unmapped targets, checksum mismatches, duplicate traces or
  main-selection mismatches.

Those bytes are replayed onto their traced native bills/expenses only after
source checksum and size verification. The standard Community attachment
workbench, thumbnail and PDF viewer provide the source-document review path.
A disposable hybrid candidate now integrates the Track B native records with
the exact benchmark history. It remains separate from the exact target until
its classified native differences and user journeys receive acceptance.

The target validation artifact reports 31 comparison groups and no failed
comparison group for the posted ledger slice. It now compares all 49 source
reconciliation models and all 49 model lines with zero missing, extra or
mismatched rows, while the target retains its two untraced native defaults.
The currency-rate comparison is broad-snapshot rather than benchmark-only and
passes with zero missing, extra or mismatched rows; the idempotence signature
includes the full `res_currency_rate`, `account_reconcile_model` and
`account_reconcile_model_line` populations.

Historical-rate architecture deliberately separates reconstruction from future automation. Three credible options were considered: (1) replay exact source `res.currency.rate` rows and use manual future rates; (2) install a maintained Odoo/OCA automatic provider; or (3) add a focused ECB adapter over Odoo's native rate model. Exact replay remains selected for history because it preserves the conversions that drove posted source accounting. The checked Community/OCA 19 dependency set has no deployable automatic updater, so option 3 is selected for future reference rates. It writes only non-source-traced native rows, skips any source-traced same-date rate, records provider/retrieval metadata, and runs daily after the normal ECB publication window. ECB rates remain informational reference rates; transaction-specific bank or platform conversions remain authoritative where they define the accounting event.

### Odoo add-on work

The custom add-on is:

```text
custom-addons/rebuild_account_migration/
```

Its manifest depends on Community modules:

- `account`
- `account_payment`
- `analytic`
- `hr_expense`
- `l10n_fr_account`

It now also directly depends on the selected OCA foundation modules for financial reports, MIS reports, reconciliation, bank statement import and partner statements:

- `account_financial_report`
- `account_reconcile_oca`
- `account_statement_import_file`
- `account_tax_balance`
- `mis_builder`
- `partner_statement`

The add-on currently provides:

- import-run records
- discrepancy records
- review-decision records
- external report values
- source traceability records
- source report catalogue, lines, columns and expressions
- imported review views over moves, move lines, payments and reconciliations
- imported report line models and list/pivot views
- fixed-asset and deferred-schedule review models
- an export wizard for imported accounting reports
- a dependency on native Odoo Expenses so the standard employee-expense workflow is installed and reachable from Accounting
- a user-docs browser at `/usl/user-docs`
- a company-scoped Accounting Hygiene workbench backed by live native records,
  current closing controls, configurable unusual-balance detection and
  separated Valentin/Prosper decision queues
- source-traced native reconciliation models, explicit Accounting Manager
  configuration routes and a read-only matched-item inspection route backed by
  Odoo's native `Unreconcile` action
- native supplier-bill and employee-expense email ingestion tests plus a
  deployment runbook that keeps inbound aliases inactive until a controlled
  domain, mail route and real-delivery smoke test exist

This add-on is evidence and reconstruction infrastructure. It is not yet a full replacement for the Enterprise `account_reports` interactive reporting product.

Current FEC access behavior: accounting review users and normal finance
operators can open the canonical FEC wizard, generate the complete posted
benchmark in forced test mode and retrieve the file from a dedicated Download
tab. Only an Accounting Manager can clear test mode and use the official path
that may update lock dates. This is enforced in the native and custom wizard
server paths and their combined views.

### User documentation

User documentation exists under:

```text
docs/users/
```

It is structured with Diataxis-style sections:

- tutorials
- how-to guides
- reference
- explanations

The docs are also exposed from Odoo through an authenticated route:

```text
/usl/user-docs
```

and through the custom menu item:

```text
Accounting -> Review -> Advanced Audit -> User Guide
```

This is implemented, but the content and menu placement need another UX pass because the current information architecture still feels too technical for frequent CEO/accountant workflows.

## What has been validated so far

Validated by artifacts or code inspection:

- The source restore uses a dedicated `accounting-source-db` service and is designed not to recreate the target `db` service.
- The source database is restored as `odoo_online_source_saas_19_2`.
- The target reconstruction database is reset and initialized as `odoo_rebuild_accounting_test`.
- The import pipeline creates source-traced target accounting records.
- Posted imported moves balance in the validated ledger slice.
- Source and target control comparisons pass for the validated posted ledger slice.
- Source currency-rate reconstruction now passes exact broad-snapshot parity: all `1,877` native EUR, USD and GBP rates from `2024-01-01` through `2026-07-20` match by source ID, company, date, technical rate, ECB provider and retained source retrieval timestamp. The importer is idempotent and uses Odoo's native rate semantics rather than inventing or inverting rates. Browser smoke testing verified the USD rate history and visible `Source Provider = ecb` provenance in the native currency form, including the `2026-06-30` rate of `1.1394` USD per EUR.
- Future reference-rate automation now passes the live provider and browser gates. The official ECB feed supplied reference date `2026-07-22`; the target stored native GBP `0.8534` and USD `1.1408` per EUR rows with provider and retrieval timestamps. A second retrieval created `0` rows and updated the same `2`, the historical source-traced count stayed `1,877`, the daily cron is active, the manager workspace opened successfully, and the reviewer persona was denied configuration access.
- Track B now has a separate disposable `odoo_rebuild_accounting_track_b` database and clean native expense, business-document, settlement, General Reconciliation, bank, asset, deferral and multi-plan analytic replay for `2025-10-01` through `2026-06-30`. All `325` source expenses pass native submission, approval/refusal, receipt and company-payment workflows with `0` blocked/mismatched records; the replay creates `97` company payments and `79` grouped receipts for `95` employee-paid expenses, while preserving `125` approved, `3` draft and `5` refused records. All `284` source documents (`36` customer invoices, `161` vendor bills, `3` supplier refunds and `84` purchase receipts) are then created or reused from commercial fields and posted through normal Odoo APIs; `284/284` match source header amounts, due dates and per-account debit/credit/balance/amount-currency effects, with `0` blocked cases and `0` mismatches across `170` EUR and `114` USD documents. Expense settlement creates `106` native bank transactions and replays `181` source allocations through OCA against `176` expense settlement lines; all `97` company payments and `95` employee-paid expenses finish paid. Expense and document settlement preserve `19` and `48` exact outside-only counterpart lines respectively. Document settlement covers all `233` directly linked bank transactions and `339` source document/bank edges. General Reconciliation covers all `111` non-bank document partials and `114` document endpoints by posting `21` manual entries with `72` lines, creating `71` input partials and tracing `40` native exchange partials. Direct categorization adds `1,415` transactions: `1,229` exact OCA categorizations and the source's `186` intentionally open lines. The external stage completes the final `95` transactions with `125` exact counterpart lines, `17` manual payroll/tax/clearing moves, `75` native input partials and `12` native exchange partials. Native bank coverage is now `1,841/1,841`. OCA asset replay creates `3` assets and `91` depreciation lines, with `28` posted and `63` future. The native deferral workflow represents `5` schedules and `82` lines, with `34` posted, `48` future and one opening-boundary reversal. The final analytic stage represents `29` post-posting corrections and reconciles source/target allocations and actual analytic lines across `13` accounts, with `324` direct traces, no mismatch and only a currency-precision theoretical `+0.01/-0.01` pair. The remaining `48` source relationships are deliberate draft/post-cutoff boundaries (`37` draft documents, `2` draft entries and `9` post-cutoff documents); no draft document is posted to manufacture parity. Final current-document payment states exactly match source, all stage reruns create nothing, and no finalized source journal line is passed into document or expense creation.
- A third disposable database, `odoo_rebuild_accounting_replacement`, now combines the completed Track B state with exact benchmark history. Historical parity is exact at `2,046` moves, `4,809` lines and debit/credit `1,064,045.02`; exactly four validated native move aliases prevent duplicate representations. The combined candidate has `4,541` posted moves and `10,727` posted lines, with no unbalanced move or duplicate source identity. Every current-period journal and account-balance difference is classified as native cash-basis timing/aggregation, native exchange timing/aggregation or OCA bank-allocation segmentation. The `12` account differences net to zero, while the EUR `2.64` profit-and-loss difference still requires professional acceptance. Manager/reviewer browser journeys now pass on the combined candidate: both opened Accounting Home, the `105`-row native Trial Balance, `245` vendor documents and `325` expenses; the reviewer had no Configuration, settings or operational mutation controls. The employee link remains on standard `hr.employee.public`, while migration-only employee trace fields are restricted to HR users. The candidate is not promoted because professional and named-user acceptance remain open.
- Historical move identity is now a blocking comparison across all `2,046` benchmark moves, including entry reference, date, journal, sequence prefix and sequence number. The four reused aliases preserve their exact `OD` references through Odoo's ORM instead of being silently renumbered or duplicated. Source and replacement profiles both have no blank or duplicate references, no duplicate sequence number, `2` sequence gaps and `3` date-order decreases. The five exceptions are preserved source anomalies and appear as an investigating P2 accountant-owned discrepancy; technical parity passes, but their business explanation and professional acceptance remain open.
- Reconciliation records are imported and compared as data.
- Attachment metadata and selected binaries are imported and checked.
- The custom report export wizard can generate CSV, XLSX, PDF and FEC TXT payloads.
- The FEC validation artifact exists and reports a successful DGFiP source-validation run.

Not yet professionally accepted as finished product behavior:

- deliberate acceptance of draft/post-cutoff prepayment boundaries, write-offs and undo behavior
- accountant acceptance of dynamic report formulas, French variants, drilldowns and export presentation
- accountant validation of the source cash-basis VAT treatment
- accountant review of FEC and statutory/tax outputs

## Community baseline versus USL custom work

### Provided by Odoo Community in this fork

Odoo Community currently provides the core accounting objects used by the reconstruction:

- companies
- partners
- accounts
- journals
- journal entries
- journal items
- taxes
- tax repartitions and tags
- fiscal positions
- payments
- currencies
- analytic accounting
- French chart/localization foundation through `l10n_fr_account`
- the standard Accounting/Invoicing menu tree under `account.menu_finance`
- the native French FEC wizard model `l10n_fr.fec.export.wizard`

The Community `account` module also defines reporting menus and a setting named `module_account_reports`, but no local `addons/account_reports/` module exists in this checkout. The Enterprise-like dynamic reporting engine is therefore not present as an installed local add-on.

### Developed by USL so far

USL-developed work currently covers:

- source restore orchestration
- schema-aware extraction
- private canonical snapshot files
- target reset/import orchestration
- source-traced exact ledger replay
- imported reconciliation preservation
- imported asset/deferred evidence
- imported source report catalogue preservation
- report-line evidence views
- canonical Community-compatible dynamic accounting report workbench
- user documentation browser
- discrepancy, decision and external-value review models
- regression tests for selected FEC and add-on behavior

### Important distinction

The current reporting implementation is an original Community-compatible dynamic workbench. It queries native `account.move.line` and related native records by default; the imported-only scope is an explicit audit option. It does not depend on or copy Enterprise `account_reports`.

Official Odoo 19 documentation describes dynamic accounting reports such as Balance Sheet, Profit and Loss, Executive Summary, General Ledger, Aged Receivable, Aged Payable, Cash Flow Statement and Tax Report, with expand/drill-down behavior, period comparison and PDF/XLSX export:

- https://www.odoo.com/documentation/19.0/applications/finance/accounting/reporting.html

Official Odoo 19 tax-return documentation also describes a Tax Return workflow from the Accounting Dashboard, with review, submit and pay steps, validation checks, tax-return locks, and PDF/XLSX export:

- https://www.odoo.com/documentation/19.0/applications/finance/accounting/reporting/tax_returns.html

The USL workbench now reproduces the required normal-user behavior for filters, period presets, comparisons, grouping, search, expand/collapse, draft warnings, drilldowns and screen-consistent exports. It is not claimed to be the proprietary Enterprise implementation, and professional acceptance of formulas and statutory interpretations remains outside the technical harness.

## Feedback assessment

### Manual navigation to `/odoo/accounting`

Status: implemented and browser-smoke-tested on `odoo_rebuild_accounting_test`.

Evidence: the upstream Community menu root is named `Invoicing` in
`addons/account/views/account_menuitem.xml`, while the journal dashboard action
exists as the child menu `menu_board_journal_1`. The custom add-on now updates
`account.menu_finance` to display `Accounting` and routes it to a
company-scoped operational Accounting Home. The native journal dashboard
remains available through the `Dashboard` child menu and Home header.

Impact: users no longer need to discover `/odoo/accounting`. Opening the app
shows bank/cash state, daily document queues, open balances, closing,
declarations and prepared actions before users choose a workflow.

The add-on now activates Odoo's documented full-accounting group hierarchy for Accounting Managers. Without that extension, Community grants a manager configuration and invoicing access but hides the full Accounting, Review and reconciliation surfaces. With it, the browser shows the required first-level areas: `Dashboard`, `Customers`, `Vendors`, `Accounting`, `Review`, `Reporting`, and `Configuration`.

Frequent reconciliation paths are deliberately distinct:

- `Accounting > Transactions > Bank Matching` opens the cross-journal OCA bank workbench for unreconciled statement lines;
- journal-card `Transactions` opens transaction history, while `Reconcile … Items` opens that journal's matching workbench;
- `Accounting > Closing > General Reconciliation` opens OCA reconciliation grouped by account and partner;
- `Review > Control > Accounting Hygiene` opens the company-scoped daily control workbench; reconstruction-only evidence remains under `Review > Advanced Audit`;
- raw source/import evidence remains under `Review > Advanced Audit`.

Native Odoo `Employee Expenses` remains available in the Vendors area.

Browser validation passed for an Accounting Manager and a disposable scoped
read-only reviewer. Final named-user acceptance by Valentin and Prosper remains
a professional/product gate, not a missing route.

### Reconciliation workbenches and boundary review

Status: technically implemented; boundary-policy acceptance remains open.

Evidence: imported bank statement lines and reconciliation records exist in the target import status, and custom review views exist for reconciliation evidence. Browser smoke testing on `odoo_rebuild_accounting_test` verified that `Accounting > Transactions > Bank Matching` opens the OCA reconciliation kanban workbench with unreconciled imported bank statement lines such as Shine, Revolut and Wise transactions. The Banque Shine dashboard card opened `63` unreconciled items against a `90,178.28 EUR` global balance. Selecting a line opened the split matching workbench with the bank line, suspense line, candidate journal items, manual operation, chatter, validate/reset controls and the `942.00 EUR` DGFiP refund candidate.

`Accounting > Closing > General Reconciliation` now opens the separate OCA account/partner reconciliation workbench. The browser showed eight account/partner reconciliation groups and candidate journal items for suspense, VAT credit, supplier, social, shareholder-current-account and deferred-expense accounts.

Resolved implementation issue: the OCA `account_reconcile_oca` kanban workbench originally failed in Odoo 19 with a web-client `KanbanArchParser` error: `Cannot read properties of undefined (reading 'type')`. The custom add-on now overrides the OCA kanban card with an Odoo-19-compatible card that keeps required fields at the kanban root and renders card values without nested `<field>` tags.

Track B validates native partial reconciliation, document netting, payment-state
transitions and Odoo/OCA exchange-difference generation. For the historical
draft boundary, the normal Advanced Audit action now opens all `75` policy
rows (`39` partial and `36` full) by default. Manager and scoped-reviewer
browser journeys opened balanced imported/generated partial and full scopes.
The forms are read-only; the reviewer can preview and prepare a decision but
cannot see `Apply Native Partial`. Full rows offer review-only acceptance or a
separately authorized workflow and have no misleading apply action.

Impact: transaction history, Bank Matching, General Reconciliation and
historical boundary evidence now have distinct, tested user journeys.
Technical behavior is verified without changing the locked replay baseline.
The remaining P1 item is the accountant/product decision to retain review-only
treatment or authorize a separate application workflow.

### PDF and XLSX report readability

Status: technically implemented; accountant presentation acceptance remains pending.

Evidence: the same workbench result drives the on-screen preview and the CSV, XLSX and PDF payloads. XLSX uses typed numeric cells, report headers and filter metadata; PDF uses structured ReportLab document tables with company, period, scope and filter evidence. The report harness validates workbook/PDF structure and row counts, and both manager and reviewer browser journeys reached the download surface without an access error.

Impact: exports are no longer raw static prototypes. They are readable review packages tied to the exact dynamic filters shown in Odoo.

Remaining work: obtain independent accountant feedback on presentation density, French statutory conventions and the final annual-accounts package before recording professional acceptance.

### Menus and documentation are hard to read

Status: first pass implemented; final workflow polish remains.

Evidence: the custom menu tree previously exposed many granular imported report and evidence views under normal Accounting reporting menus. The active menu tree now keeps raw `Imported ...` evidence views under `Review > Advanced Audit`, while the first level matches the seven-area product target. Bank Matching sits under Accounting transactions, General Reconciliation under Accounting closing, and Issues under Review control.

Impact: managers now receive reproducible full-accounting access and users have stable entry points for the normal Accounting journeys while technical evidence remains reachable.

Required work: continue polishing once the OCA report screens, business documents, expenses and reconciliation workflows are validated. Menu polish is not complete until the final report/declaration screens have stable names and destinations.

### FEC access error

Status: fixed for Accounting Manager, accountant reviewer and finance operator;
official non-test export remains manager-only.

Evidence: base Odoo limits `l10n_fr.fec.export.wizard` creation to full
accounting users. The add-on grants transient-only access through the
read-only accounting group, forces reviewer/operator exports to complete
posted test files, blocks their journal exclusions and official mode, and
keeps both native and custom official paths manager-only. Fresh add-on tests
cover all three roles.

Impact: the reviewer and finance operator each generated and downloaded the
`4,781`-row benchmark FEC with debit/credit `1,064,045.02`; their test-mode
checkbox was locked. The manager generated the official path and downloaded
the same ledger scope while the existing `2025-09-30` lock date remained
unchanged.

The first browser pass exposed a hidden download: FEC preview is forbidden but
the Download tab was nested below preview state. Moving download to its own
state fixed retrieval. The first manager official pass then exposed Odoo's
lazy second-cursor behavior: the new cursor could not see the wrapper's
uncommitted native transient. The wrapper now consumes the native stream on
its request cursor; official/test semantics remain controlled by `test_file`.
Professional FEC review remains open.

### Settings cash-basis error

Status: fixed in the importer; existing imported development targets need a module update and helper run, or a fresh target import.

Evidence: the exact error says a setting cannot be disabled while some taxes are cash basis. Target inspection found imported `account.tax` records with `tax_exigibility = on_payment` while the corresponding `res.company.tax_exigibility` setting was disabled. Odoo core intentionally blocks that inconsistent state in Settings. The importer now enables the company cash-basis setting when imported taxes require it and records an import note; it does not alter the imported tax exigibility rules.

Impact: opening Settings should no longer raise the cash-basis warning after the target has been updated. Cash-basis VAT remains an accounting configuration requiring source and accountant review; the fix only removes the internal Odoo inconsistency.

Remaining work: verify with the source tax profile and accountant whether USL uses VAT on collection, an option for VAT on debits, or a mixed treatment by date/activity. Preserve the exact source behavior in tax reports and CA12 support.

### Payment providers

Status: removed from the Milestone 13 target scope.

Payment providers may remain available if provided by Community or other installed modules, but they are not a required Milestone 13 feature. Future bank synchronization remains a roadmap topic because it affects accounting operations after reconstruction.

### Supplied report references

Status: reviewed and captured as product targets.

Evidence: the supplied annual accounts PDF contains the expected annual report package structure: cover, summary, accountant attestation, Bilan Actif, Bilan Passif, Compte de resultat, detailed account reports, accounting methods, ratios, SIG and CAF. The supplied SIG PDF/XLSX confirms the desired dynamic report/export style: company header, VAT number, period, page numbering, filter sheet and typed numeric balances. The supplied tax workbook confirms the desired declaration-support pattern: official-style VAT sections and boxes, period, company filter, balance and adjustment columns.

Impact: Milestone 13 reporting cannot stop at ledger controls or raw CSV/PDF artifacts. The product must generate readable, reviewable accounting packages and guide the user through official declaration values.

Current outcome: the dynamic report screens, readable exports, declaration workspaces and closing-package workflows now exist. The supplied references remain the presentation benchmark for the independent accountant review.

## Current progress summary

### Complete enough to preserve

- isolated accounting source restore service
- source extraction and private snapshot generation
- clean target reset/import pipeline
- posted ledger replay into a source-traced target
- target validation and comparison artifacts for the posted ledger slice
- imported source report catalogue preservation
- canonical dynamic report workbench and technical report evidence
- FEC generation/validation harness artifact
- accountant/review evidence models
- Diataxis user docs and Odoo docs browser
- Track B checksum-verified native bill/expense evidence and main-attachment selection
- Accounting Hygiene daily queues, direct evidence links and manager/reviewer browser controls
- configurable natural-balance policies and a live seven-account unusual-balance review queue

### Partial and not yet acceptable

- professional acceptance of report formulas, variants and presentation
- professional policy acceptance of the draft-boundary reconciliation treatment
- tax-return workflow UX
- broader accounting menu hierarchy and daily workflow naming
- accountant validation of cash-basis VAT treatment
- professional acceptance of the hybrid candidate's classified EUR `2.64` exchange profit-and-loss difference and explicit promotion of that disposable candidate

### Not yet complete

- Level 4 report parity for mandatory reports
- full statutory French statements with accountant acceptance
- final CA12 and tax package review
- accountant-reviewed FEC dossier
- external-accountant onboarding and named-user acceptance
- final closure evidence package

## Checklist left to complete

### Immediate

- [x] Add a clear Accounting app/menu entry that opens the operational Accounting Home directly while retaining the native journal Dashboard.
- [x] Make module refresh/upgrade instructions and UI refresh behavior visible in the dev guide.
- [x] Diagnose the bank journal transaction view and reconcile it with imported `account.bank.statement.line` records.
- [x] Adopt the OCA workbench for operational bank/general reconciliation while retaining custom read-only historical evidence views under Advanced Audit.
- [x] Integrate the proven Track B customer invoices, vendor bills, supplier refunds and source-derived expenses into a disposable hybrid replacement candidate; their checksum-verified source binaries and main selections pass, and the source period contains no customer credit-note case.
- [x] Validate reports, manager/reviewer permissions and browser journeys on the hybrid candidate.
- [ ] Obtain professional acceptance of the classified native differences and explicitly promote or reject the hybrid candidate.
- [x] Diagnose and fix the FEC permission path for Accounting Manager, accountant reviewer and finance operator roles; all three generated and downloaded the benchmark in the replacement browser.
- [x] Diagnose and fix the Settings cash-basis tax error without changing tax meaning.
- [x] Complete menu organization around CEO/accountant workflows. The seven-area navigation now includes top-level Closing and Declarations destinations plus the standard closing and tax/fiscal submenus.

### Short term

- [x] Replace the machine-oriented report output with structured screen, XLSX and PDF presentation; final accountant presentation acceptance remains open.
- [x] Deliver one canonical Community-compatible dynamic workbench for normal report navigation while retaining OCA report screens as technical comparison surfaces.
- [x] Cover the mandatory report launchers with the same dynamic filter, comparison, grouping, drilldown and export behavior.
- [x] Preserve benchmark CSV/XLSX evidence packages as audit artifacts and label them separately from the live native-ledger workbench.
- [x] Add CFS Pro declaration guidance views with field, value, source, calculation, warning and reviewer state. The workflow links to the current official source and professional filing portal; no electronic filing is claimed.
- [x] Add a versioned declaration schedule and calendar for the confirmed French SASU profile, including conditional form suppression.
- [x] Add row-level drill-down from report lines to native journal items or analytic lines in the normal UI.
- [x] Update user docs for the canonical report workbench, native/imported scope, comparisons, grouping, drilldown and export.
- [ ] Complete the named-user acceptance matrix for Valentin, accountant and finance operator. Automated ACL tests and live Accounting Manager/read-only reviewer report journeys pass.
- [x] Publish a single end-to-end Prosper acceptance/onboarding walkthrough that connects access preflight, reports, reconciliation, statutory evidence, closing, FEC and durable decisions. Execution by Prosper remains part of the unchecked named-user acceptance item above.

### Milestone 13 core

- [ ] Prove all mandatory reports at Level 4 parity.
- [ ] Complete French statutory report semantics.
- [ ] Complete VAT, CA12 and tax-package mapping review.
- [x] Complete the technical fixed-asset/depreciation reconciliation to statements and tax mappings. The register, annual statements and 2033-A/2033-C rows agree at EUR `10,430.49` gross, EUR `1,676.05` accumulated depreciation and EUR `8,754.44` net; typed evidence records expose `3` assets and `91` schedule lines. Final accountant acceptance remains part of the report/statutory gate.
- [x] Validate programmatic lock-date behavior, reviewer gating and before/after evidence for the new closing workspace. Final named-user browser validation remains part of the acceptance walkthrough.
- [x] Validate sequence and chronology behavior. Exact name/date/prefix/number parity is blocking; the locked `2,046`-move benchmark preserves the source's `2` gaps and `3` date-order decreases, while the full `4,843`-move replay preserves `16` gaps and `104` decreases. Both match source exactly and expose the anomalies for accountant review rather than resequencing locked history.
- [x] Validate full and partial reconciliation behavior through a user-facing review path. Track B proves native accounting effects; the exact-target manager/reviewer journey shows all `39` partial and `36` full boundary rows, balanced endpoint previews and decision controls without mutating the replay baseline.
- [ ] Complete the accountant-reviewed FEC dossier. Official DGFiP structural validation already passes.
- [x] Run a second clean reconstruction and compare deterministic outputs. The focused hybrid reset/import/validate sequence reproduced the prior clean candidate signature and every classified difference after the final FEC/UI changes.
- [ ] Resolve or formally accept every P0/P1 discrepancy.

### Later roadmap

- [x] Keep payment providers out of the Milestone 13 required scope.
- [x] Keep bank synchronization as a later roadmap topic after the historical accounting core is trustworthy.

## Remaining questions and doubts

- Acceptance question: does the accountant accept the formulas, PCG variants, statutory interpretations and presentation exposed by the canonical Community-compatible workbench?
- Policy question: does the accountant accept review-only treatment for the `75` draft-boundary reconciliation rows, or request a separately authorized application workflow? Technical native and read-only review behavior now pass.
- Accounting question: which cash-basis VAT behavior in the source is legally required for USL, and which behavior is only a side effect of imported localization configuration? The technical Settings inconsistency is fixed, but the tax rule still requires accountant validation.
- Accounting question: which generated statutory PDFs/XLSX require further visual alignment with the supplied accountant benchmark, and which technically validated workbench exports are acceptable as-is?
- Access question: does the accountant accept native-record access as the final document-evidence scope? Current ACL tests allow source-replayed attachments only through an accessible accounting parent and continue to deny private technical attachments.
- What exact accountant review workflow is required before Milestone 13 can close?

## Bottom line

Current implementation includes the reconstruction pipeline, source-traced
accounting data, an operational Accounting Home, native workflow workbenches,
one canonical dynamic report product, declaration/closing workspaces and
technical evidence.

It is not yet professionally accepted as an Enterprise replacement. The
remaining gates are accountant/product acceptance of report formulas,
statutory semantics, presentation, FEC/declarations and the documented
cross-boundary reconciliation policy—not an absent dynamic report or landing
screen.
