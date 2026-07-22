# Milestone 13 current progress report

Last updated: 2026-07-22

Audience: Valentin, USL finance operators, the USL accountant, and the next implementation agent.

This report describes the current implementation state. It is not a closure report. The current system has made real progress on deterministic source reconstruction and evidence capture, but it is not yet an Odoo Online Enterprise-equivalent accounting user experience.

Related target document:

- [Milestone 13 reporting and closing UX target](milestone-13-reporting-and-closing-ux-target.md)

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
- reports: `partial`, classified as `HARNESS_AND_ODOO_REPORT_ARTIFACTS_PARTIAL`
- idempotence guardrail: `passed`
- target failure guardrails: `passed`
- document-regeneration cases: `passed`
- FEC validation artifact: `passed`, classified as `OFFICIAL_DGFIP_SOURCE_VALIDATION_PASSED`
- readiness: `blocked`, classified as `TECHNICAL_REHEARSAL_PASSED_PROFESSIONAL_ACCEPTANCE_PENDING`

This means the technical import and validation harness is materially ahead of the user-facing product.

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

Observed Odoo menu entries now include OCA Trial Balance, General Ledger, Journal Ledger, Partner Ledger, statement reports, tax balance support, MIS Reporting, Import Statement and Reconcile actions.

Status: this is dependency and platform enablement, not final report parity. The next implementation phase must map these OCA screens into the USL Accounting UX, validate report calculations against imported controls, and decide where custom USL reports remain necessary.

Runtime caveat: the normal Compose `odoo` service requires the local `.env` value `ODOO_ADDONS_PATH=/opt/odoo/addons,/opt/odoo/odoo/addons,/mnt/custom-addons,/mnt/oca-addons`. An older local `.env` without `/mnt/oca-addons` caused installed OCA menus to exist while Odoo could not load OCA web assets. This was corrected locally during the current session.

### Source extraction and reconstruction

The current import status reports these production-derived records represented in the target:

- 2 companies
- 135 imported accounts
- 31 imported journals
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

The target validation artifact reports 28 comparison groups and no failed comparison group for the posted ledger slice.

### Odoo add-on work

The custom add-on is:

```text
custom-addons/rebuild_account_migration/
```

Its manifest depends on Community modules:

- `account`
- `account_payment`
- `analytic`
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
- a user-docs browser at `/usl/user-docs`

This add-on is evidence and reconstruction infrastructure. It is not yet a full replacement for the Enterprise `account_reports` interactive reporting product.

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
Accounting -> Review -> Rebuild Evidence -> User Guide
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
- Reconciliation records are imported and compared as data.
- Attachment metadata and selected binaries are imported and checked.
- The custom report export wizard can generate CSV, XLSX, PDF and FEC TXT payloads.
- The FEC validation artifact exists and reports a successful DGFiP source-validation run.

Not yet validated as finished product behavior:

- normal Odoo app launcher entry into a clearly named Accounting app
- native Odoo bank reconciliation workbench parity
- dynamic Odoo Online-style accounting reports
- readable templated PDF/XLSX reports
- report UX parity with filters, unfold, annotations and exports as seen on screen
- accountant access for official non-test FEC export
- Settings behavior with cash-basis taxes
- accountant review of FEC and statutory/tax outputs
- complete second clean rehearsal evidence after the latest UX changes

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
- custom export wizard
- user documentation browser
- discrepancy, decision and external-value review models
- regression tests for selected FEC and add-on behavior

### Important distinction

The current reporting implementation is evidence-oriented. It calculates and exports from imported ledger data, but it does not yet behave like Odoo Online's dynamic report workbench.

Official Odoo 19 documentation describes dynamic accounting reports such as Balance Sheet, Profit and Loss, Executive Summary, General Ledger, Aged Receivable, Aged Payable, Cash Flow Statement and Tax Report, with expand/drill-down behavior, period comparison and PDF/XLSX export:

- https://www.odoo.com/documentation/19.0/applications/finance/accounting/reporting.html

Official Odoo 19 tax-return documentation also describes a Tax Return workflow from the Accounting Dashboard, with review, submit and pay steps, validation checks, tax-return locks, and PDF/XLSX export:

- https://www.odoo.com/documentation/19.0/applications/finance/accounting/reporting/tax_returns.html

The current USL implementation does not yet reproduce that full interactive behavior.

## Feedback assessment

### Manual navigation to `/odoo/accounting`

Status: implemented at menu/action level; browser UX smoke test still recommended.

Evidence: the upstream Community menu root is named `Invoicing` in `addons/account/views/account_menuitem.xml`, while the dashboard action exists as the child menu `menu_board_journal_1`. The custom add-on now updates `account.menu_finance` to display `Accounting` and to target `account.open_account_journal_dashboard_kanban`, whose action path is `accounting`.

Impact: users should no longer need to manually discover `/odoo/accounting` to reach the accounting dashboard.

Additional menu work now exposes first-level `Review Issues` and `Reconcile Bank Transactions` entries, renames the supplier area to `Suppliers and Expenses`, renames reporting to `Reports and Declarations`, and groups raw source/import evidence under `Review and Audit > Advanced Audit`.

Remaining work: validate the app-launcher behavior in the browser and continue the broader menu/workbench redesign after customer/vendor/expense reconstruction and report parity are implemented.

### Missing/equivalent reconciliation view

Status: partially improved, not complete.

Evidence: imported bank statement lines and reconciliation records exist in the target import status, and custom review views exist for reconciliation evidence. The Accounting dashboard now exposes a first-level `Reconcile Bank Transactions` entry, and browser smoke testing on `odoo_rebuild_accounting_test` showed it opens the OCA reconciliation kanban workbench with unreconciled imported bank statement lines such as Shine, Revolut and Wise transactions. Browser smoke testing of the Banque Shine dashboard card also opened `Statement lines`, displayed `63` items and showed the journal `Global Balance` without a visible client error.

Resolved implementation issue: the OCA `account_reconcile_oca` kanban workbench originally failed in Odoo 19 with a web-client `KanbanArchParser` error: `Cannot read properties of undefined (reading 'type')`. The custom add-on now overrides the OCA kanban card with an Odoo-19-compatible card that keeps required fields at the kanban root and renders card values without nested `<field>` tags.

Impact: historical bank balances may be visible on the dashboard while the transaction/reconciliation workspace feels empty or disconnected.

Required work: validate the native operational reconciliation flow end to end, including matching invoices/bills/payments, write-offs, partial reconciliations, and interaction with imported historical reconciliation evidence. This remains a product/accounting UX blocker for Milestone 13 until behavior and accounting effects are verified.

### Poor PDF and XLSX report readability

Status: confirmed implementation limitation.

Evidence: `custom-addons/rebuild_account_migration/models/report_export_wizard.py` uses `xlsxwriter` for XLSX and low-level ReportLab `canvas` drawing for PDFs. It does not use a full Odoo report template stack or the absent `account_reports` dynamic report engine.

Impact: exports are useful as machine evidence, but they are not accountant-ready or comparable to Odoo Online's report presentation.

Required work: replace or wrap the current report wizard with a user-facing dynamic report experience and human-readable exports. The likely target is an independently implemented Community-compatible equivalent of the relevant Odoo reporting behavior, using lawful source-record analysis and standard Odoo extension patterns, not copied Enterprise code.

### Menus and documentation are hard to read

Status: first pass implemented; final workflow polish remains.

Evidence: the custom menu tree previously exposed many granular imported report and evidence views under normal Accounting reporting menus. The active menu tree now keeps raw `Imported ...` evidence views under `Review and Audit > Advanced Audit`, and first-level Accounting destinations include `Review Issues`, `Reconcile Bank Transactions`, `Customers`, `Suppliers and Expenses`, and `Reports and Declarations`.

Impact: users have clearer entry points for the five priority workflows while technical evidence remains reachable.

Required work: continue polishing once the OCA report screens, business documents, expenses and reconciliation workflows are validated. Menu polish is not complete until the final report/declaration screens have stable names and destinations.

### FEC access error

Status: fixed for accountant-review test exports through the custom USL export wizard; official non-test export remains manager-only.

Evidence: the native `l10n_fr.fec.export.wizard` creation ACL remains limited to `account.group_account_user`. The custom FEC export now checks company access, blocks non-test official FEC generation for non-managers, and uses the custom wizard as the reviewed export boundary for accountant-review users.

Impact: accountant-review users can generate review/test FEC files through the USL Accounting report export screen without gaining full accounting write access. They still cannot generate official non-test FEC files because that path may update fiscal lock dates.

Remaining work: browser-smoke the FEC menu action with the intended accountant user and define whether any non-manager role should ever be allowed to generate official non-test FEC files.

### Settings cash-basis error

Status: observed by user, not fully diagnosed.

Evidence: the exact error says a setting cannot be disabled while some taxes are cash basis. The current source/import perimeter includes cash-basis tax behavior as an explicit validation topic. The current report did not change settings behavior.

Impact: Settings may be trying to toggle a tax-cash-basis option inconsistent with imported or localized tax configuration.

Required work: inspect the settings value, cash-basis taxes, and whether the target import or module install creates a contradictory configuration. Do not blindly disable cash-basis behavior because that can affect VAT accounting meaning.

### Payment providers

Status: removed from the Milestone 13 target scope.

Payment providers may remain available if provided by Community or other installed modules, but they are not a required Milestone 13 feature. Future bank synchronization remains a roadmap topic because it affects accounting operations after reconstruction.

### Supplied report references

Status: reviewed and captured as product targets.

Evidence: the supplied annual accounts PDF contains the expected annual report package structure: cover, summary, accountant attestation, Bilan Actif, Bilan Passif, Compte de resultat, detailed account reports, accounting methods, ratios, SIG and CAF. The supplied SIG PDF/XLSX confirms the desired dynamic report/export style: company header, VAT number, period, page numbering, filter sheet and typed numeric balances. The supplied tax workbook confirms the desired declaration-support pattern: official-style VAT sections and boxes, period, company filter, balance and adjustment columns.

Impact: Milestone 13 reporting cannot stop at ledger controls or raw CSV/PDF artifacts. The product must generate readable, reviewable accounting packages and guide the user through official declaration values.

Required work: implement human-readable dynamic report screens and exports, then add declaration guidance and closing-package workflows.

## Current progress summary

### Complete enough to preserve

- isolated accounting source restore service
- source extraction and private snapshot generation
- clean target reset/import pipeline
- posted ledger replay into a source-traced target
- target validation and comparison artifacts for the posted ledger slice
- imported source report catalogue preservation
- preliminary report artifact generation
- FEC generation/validation harness artifact
- accountant/review evidence models
- Diataxis user docs and Odoo docs browser

### Partial and not yet acceptable

- user-facing reports
- report PDF/XLSX presentation
- Odoo Online-style report interaction
- reconciliation user experience
- tax-return workflow UX
- official non-test FEC access policy
- broader accounting menu hierarchy and daily workflow naming
- settings behavior with cash-basis taxes

### Not yet complete

- Level 4 report parity for mandatory reports
- full statutory French statements with accountant acceptance
- final CA12 and tax package review
- accountant-reviewed FEC dossier
- full accountant access testing
- second clean reconstruction after the latest UI/docs state
- final closure evidence package

## Checklist left to complete

### Immediate

- [x] Add a clear Accounting app/menu entry that opens the accounting dashboard directly.
- [ ] Make module refresh/upgrade instructions and UI refresh behavior visible in the dev guide.
- [ ] Diagnose the bank journal transaction view and reconcile it with imported `account.bank.statement.line` records.
- [ ] Decide the product target for historical reconciliation: native Community flow, custom review workbench, or both.
- [ ] Include customer invoices, credit notes, vendor bills, supplier refunds and expenses in the user-facing reconstruction scope.
- [ ] Diagnose and fix the FEC permission path for accountant and finance operator roles.
- [ ] Diagnose the Settings cash-basis tax error without changing tax meaning.
- [ ] Reorganize menus around CEO/accountant workflows.

### Short term

- [ ] Replace the current machine-oriented PDF output with readable, accountant-ready templates.
- [ ] Replace or augment the wizard flow with dynamic interactive report screens.
- [ ] Preserve the current CSV/XLSX evidence exports as audit artifacts, but distinguish them from user-facing reports.
- [ ] Add CFS Pro and Portailpro declaration guidance views with field, value, source, calculation, warning and reviewer state.
- [ ] Add declaration schedule reminders for the French SASU closing workflow.
- [ ] Add drill-down from report lines to journal items and evidence in the normal UI.
- [ ] Update user docs after the menu and reporting UX are redesigned.
- [ ] Add realistic user-role tests for Valentin, accountant, finance operator and read-only reviewer.

### Milestone 13 core

- [ ] Prove all mandatory reports at Level 4 parity.
- [ ] Complete French statutory report semantics.
- [ ] Complete VAT, CA12 and tax-package mapping review.
- [ ] Complete fixed-asset and depreciation reconciliation to statements and tax mappings.
- [ ] Validate lock-date behavior in UI and programmatic actions.
- [ ] Validate sequence and chronology behavior.
- [ ] Validate full and partial reconciliation behavior through a user-facing review path.
- [ ] Complete official FEC validation and accountant review dossier.
- [ ] Run a second clean reconstruction and compare deterministic outputs.
- [ ] Resolve or formally accept every P0/P1 discrepancy.

### Later roadmap

- [ ] Keep payment providers out of the Milestone 13 required scope.
- [ ] Keep bank synchronization as a later roadmap topic after the historical accounting core is trustworthy.

## Remaining questions and doubts

- Validation question: do the selected OCA report screens produce USL/Odoo Online-equivalent interactive results once mapped to the imported ledger, or do specific reports still need a custom USL implementation?
- Validation question: does the OCA reconciliation workbench correctly perform operational matching, write-offs and partial reconciliations on imported statement lines without damaging historical source-traced reconciliation evidence?
- Accounting question: which cash-basis VAT behavior in the source is legally required for USL, and which behavior is only a side effect of imported localization configuration?
- Accounting question: which generated statutory PDFs/XLSX must match the accountant benchmark visual structure, and which can remain machine-oriented evidence exports under Advanced Audit?
- Access question: which exact source documents or attachments should be visible to the accountant as evidence versus restricted accounting evidence?
- What exact accountant review workflow is required before Milestone 13 can close?

## Bottom line

Current implementation progress is mainly the reconstruction pipeline, source-traced imported accounting data, validation artifacts, and evidence-oriented Odoo views.

It is not yet an Enterprise-comparable accounting reporting, audit and review product. The largest remaining gap is the user-facing accounting UX: app entry, reconciliation workbench, dynamic reports, readable templated exports, permissions, and accountant-ready review flows.
