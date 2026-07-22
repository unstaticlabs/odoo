# Milestone 13 checkpoint - 2026-07-22

Audience: Odoo Rebuild Product Manager, Valentin, USL finance operators, accountant reviewers, and implementation agents.

This checkpoint records verified current reality for Milestone 13. It is not a closure report and it does not continue the build. The checkpoint used the existing imported target database, targeted harness validation, database/registry checks, and a browser smoke test. A full clean reconstruction was inspected through existing artifacts from 2026-07-22 but was not rerun during this checkpoint.

## Executive summary

Milestone 13 is substantially progressed but not complete. The strongest verified achievement is that the USL closed benchmark posted-ledger slice can be reconstructed in `odoo_rebuild_accounting_test` with source/target parity for moves, move lines, debit, credit, full and partial reconciliations, bank statement lines, scoped attachments, assets, depreciation evidence, deferred schedule evidence and analytic lines.

The largest remaining product gap is now the rest of the user-facing accounting lifecycle: native current-period invoices/bills/refunds/receipts pass in Track B, while payments, bank matching, reconciliation, historical expenses, dynamic report semantics, declaration preparation and accountant acceptance remain incomplete.

Engineering can continue autonomously on implementation increments that have already been decided: native Track B payments/reconciliation, historical expense reconstruction, OCA-first interactive reporting, the USL closing/declaration layer, FEC acceptance workflow, and accountant review packages. Product/accountant input is still required for VAT exigibility treatment, accepted statutory presentation, attachment visibility rules, and final FEC/declaration acceptance.

## Repository and runtime baseline

| Item | Verified value | Evidence |
| --- | --- | --- |
| Branch | `19-usl-feat-accounting` | `git status --short --branch` |
| Starting commit | `92d0195140f211acbfe38e058cb841f5140d1279` | `git rev-parse HEAD` before doc edits |
| Working tree at start | clean | `git status --short --branch` |
| Previous documented checkpoint | `023061c319c docs(accounting): define milestone 13 reporting workflow` | `git log 023061c319c..HEAD` |
| Odoo runtime | Odoo Community `19.0` | `docker compose exec -T odoo odoo --version` in prior baseline and current shell logs |
| Python runtime | `3.12.13` | current runtime baseline |
| PostgreSQL runtime | PostgreSQL `16.14` | current runtime baseline |
| Odoo config used for shell checks | `/etc/odoo/odoo.conf` | failed unconfigured shell check, corrected configured shell check |
| Add-on paths | core Odoo, `/mnt/custom-addons`, `/mnt/oca-addons` | configured `odoo shell` logs |
| Running services | `accounting-source-db`, `db`, `devcontainer`, `odoo` healthy/up | `docker compose ps` |
| Source DB | `odoo_online_source_saas_19_2` | `source-restore-status.json`, `source-manifest.json` |
| Target DB | `odoo_rebuild_accounting_test` | `target-validate-status.json`, configured Odoo shell |
| Synthetic/dev DB | `odoo19` | database inventory; not used as parity evidence |

Relevant commits since `023061c319c` include OCA reporting/reconciliation foundation, Accounting app routing, report menu routing, cash-basis settings preservation, reviewer FEC test access, MIS financial statements, OCA aged-report shortcuts, reconciliation kanban stabilization, native expense workspace dependency, and cron-thread disabling for local parity runtime.

## Validation mode used

This checkpoint used:

- existing imported target database: yes, `odoo_rebuild_accounting_test`;
- partial validation rerun: yes, `scripts/accounting-compat target-validate` and `scripts/accounting-compat reports`;
- browser smoke test: yes, login to `odoo_rebuild_accounting_test`, navigation to `/odoo/accounting`, and `/usl/user-docs`;
- registry/menu checks: yes, configured `odoo shell`;
- full clean reconstruction: not rerun during the checkpoint;
- prior clean-rehearsal evidence inspected: yes, private artifacts under `artifacts/accounting-compat/private/`.

The checkpoint deliberately did not run source restore, extraction, target reset or target import again because this was a documentation and status checkpoint and current artifacts were recent, internally consistent and sufficient for the claims being checked.

## Source evidence

`artifacts/accounting-compat/private/source-manifest.json` currently records:

- source declaration: Odoo Online Enterprise `saas~19.2`;
- source database: `odoo_online_source_saas_19_2`;
- database UUID: `8528a66d-20ba-4a84-bc9c-8427f932f69a`;
- database create date: `2025-12-04 17:50:55`;
- dump format: `postgres_plain_sql`;
- dump path: `usl-online-dump/dump.sql`;
- dump SHA-256: `bf16ce18965e4ce1b23d7b79930b6e43ca7f510339ac6d2db280231f91d1449f`;
- dump size: `100,966,570` bytes;
- filestore exists with `1,763` files;
- source companies: Unstatic Labs (`1`) and USL MEDIA (`8`);
- USL fiscal, tax, sale and purchase lock dates: `2025-09-30`;
- USL Media has no lock dates in the source manifest.

`artifacts/accounting-compat/private/source-controls.json` records source controls generated on `2026-07-22T10:32:40+00:00`, including no posted unbalanced moves and source reconciliation counts of `2,584` partial reconciliations and `1,369` full reconciliations.

## Installed accounting modules

Configured Odoo shell on `odoo_rebuild_accounting_test` confirms the relevant installed modules:

- Odoo Community/accounting: `account`, `account_payment`, `analytic`, `hr_expense`, `l10n_fr`, `l10n_fr_account`, `spreadsheet_account`, `spreadsheet_dashboard_account`;
- Odoo electronic-invoicing/connectivity modules present but not milestone targets: `account_edi_proxy_client`, `account_edi_ubl_cii`, `account_peppol`, `account_peppol_response`, `l10n_fr_pdp`, `snailmail_account`;
- OCA reporting/reconciliation foundation: `account_financial_report`, `account_reconcile_oca`, `account_statement_base`, `account_statement_import_base`, `account_statement_import_file`, `account_statement_import_file_reconcile_oca`, `account_tax_balance`, `mis_builder`, `partner_statement`;
- USL custom module: `rebuild_account_migration`.

`l10n_fr_account` provides the French FEC wizard model used by the test export path. The Enterprise `account_reports` add-on is not installed as a local runtime module.

## Reconstruction completeness

Current target database counts from configured Odoo shell:

| Record category | Target count | Status |
| --- | ---: | --- |
| Companies | 2 | represented |
| Accounts | 179 Odoo shell count; 136 imported by latest import run | represented with target/bootstrap context |
| Journals | 32 Odoo shell count; 31 imported by latest import run | represented with target/bootstrap context |
| Taxes | 82 Odoo shell count; 145 imported/source tax configuration count in import statistics | partial semantic validation |
| Fiscal positions | 2 | represented |
| Currencies | 3 active Odoo shell count; 170 table rows in import status | represented |
| Currency rates | 0 active target records | gap for currency-rate reconstruction/validation |
| Posted moves | 4,843 | imported posted replay through snapshot |
| Move lines | 11,392 | imported posted replay through snapshot |
| Partial reconciliations | 2,531 | imported within replay scope |
| Full reconciliations | 1,210 | imported within replay scope |
| Payments | 97 | move-backed payments imported |
| Payment review records | 13 | no-entry source payments represented as review evidence |
| Bank statement lines | 3,040 | imported; statement headers not invented |
| Assets | 3 | imported as USL review assets |
| Depreciation schedule lines | 91 | imported as evidence |
| Deferred schedule lines | 110 | imported as evidence |
| Analytic lines | 632 | imported |
| Scoped accounting attachments | 332 imported by latest import run; 362 Odoo shell visible attachments | scoped evidence represented |
| Source report definitions | 38 | preserved as source report evidence |
| Source report lines | 702 | preserved |
| Source report columns | 141 | preserved |
| Source report expressions | 1,227 | preserved |
| Document-regeneration cases | 194 | `189` validated exact-target drafts plus `5` review-only/not-applicable cases |
| Native `hr.expense` records | 0 | workspace installed, historical expense reconstruction not complete |

Current exact-target import status is `partial`, classified as `POSTED_SOURCE_REPLAY_THROUGH_SNAPSHOT`. The reason is product scope, not a failed ledger replay: posted replay and the separate native Track B document proof work, but payments/reconciliation, historical expenses, declaration workflows, full report semantics and professional acceptance are not complete.

## Source-versus-target benchmark controls

For USL benchmark period `2024-01-10` through `2025-09-30`, `target-validate-status.json` generated on `2026-07-22T15:57:29+00:00` reports:

| Control | Source | Target | Difference | Status |
| --- | ---: | ---: | ---: | --- |
| Posted moves | 2,046 | 2,046 | 0 | passed |
| Posted move lines | 4,809 | 4,809 | 0 | passed |
| Debit | 1,064,045.02 | 1,064,045.02 | 0.00 | passed |
| Credit | 1,064,045.02 | 1,064,045.02 | 0.00 | passed |
| Balance | 0.00 | 0.00 | 0.00 | passed |
| Partial reconciliations | 1,563 | 1,563 | 0 | passed |
| Full reconciliations | 663 | 663 | 0 | passed |
| Bank statement lines | 3,040 | 3,040 | 0 | passed |
| Move-backed payments | 97 | 97 | 0 | passed |
| Assets | 3 | 3 | 0 | passed |
| Asset depreciation schedule lines | 91 | 91 | 0 | passed |
| Deferred schedule lines | 110 | 110 | 0 | passed |
| Analytic lines | 632 | 632 | 0 | passed |
| Scoped accounting attachments | 332 | 332 | 0 | passed |

Lock enforcement is also validated for the benchmark: Odoo blocks a rollback-only write to a locked posted move dated `2024-01-10` with `You cannot add/modify entries prior to and inclusive of: Global Lock Date (09/30/2025).`

## Source moves versus imported posted moves

The source controls show:

- USL benchmark period: `2,047` source moves, of which `2,046` are posted and `1` is cancelled;
- USL current period from `2025-10-01`: `2,987` source moves, of which `2,794` are posted, `192` draft and `1` cancelled;
- USL Media current period: `3` posted moves.

The target import represents `4,843` posted source moves. The apparent difference from `5,037` total source moves is classified, not ignored:

- `4,843` posted moves are replayed as accounting moves;
- `194` non-posted/cancelled/no-line source moves are represented as `rebuild.account.document.regeneration.case` and `rebuild.account.move.review` records;
- `189` candidate-ready cases generate validated exact-target drafts, while the separate Track B proof recomputes current-period commercial documents through native posting;
- `5` are review-only/not applicable (`2` cancelled source records and `3` zero-line draft records);
- `467` non-posted or display-only move lines are represented as move-line review records.

This classification is enough for audit visibility, but not enough for final user-facing parity because native invoice, bill, refund and expense reconstruction remains incomplete.

## User-facing Accounting experience

Browser smoke test on `odoo_rebuild_accounting_test` verified:

- `http://localhost:8069/odoo/accounting` loads a visible Accounting area;
- visible first-level labels include `Accounting`, `Review Issues`, `Reconcile Bank Transactions`, `Customers`, `Suppliers and Expenses`, and `Reports and Declarations`;
- `http://localhost:8069/usl/user-docs` loads the interactive user guide with navigation for tutorials, how-to guides, reference and explanations.

Configured registry checks verified these menu bindings:

| Menu | Complete path/action |
| --- | --- |
| Accounting root | `account.menu_finance`, named `Accounting`, action `account.open_account_journal_dashboard_kanban` |
| Review Issues | `Accounting/Review Issues`, action `ir.actions.act_window(312,)` |
| Reconcile Bank Transactions | `Accounting/Reconcile Bank Transactions`, action `ir.actions.act_window(467,)` |
| Reports and Declarations | `Accounting/Reports and Declarations` |
| Suppliers and Expenses | `Accounting/Suppliers and Expenses` |
| Advanced Audit | `Accounting/Review and Audit/Advanced Audit` |
| User Guide | `Accounting/Review and Audit/Advanced Audit/User Guide`, action URL |

Direct browser navigation to action URLs for Review/Reconcile returned the Odoo web shell without enough visible content in this checkpoint, so the user-flow validation for those screens is registry/evidence-based rather than full browser interaction proof.

## Report and export parity

`scripts/accounting-compat reports` was rerun and produced `reports-status.json` with status `partial`, classification `HARNESS_AND_ODOO_REPORT_ARTIFACTS_PARTIAL`.

Verified current report facts:

- Odoo report view smoke checks passed for Trial Balance, General Ledger, Journal Report, Open Items, Aged Receivable/Payable support, Balance Sheet, Profit and Loss, VAT/tax reports, currency report, bank reconciliation evidence, asset/depreciation evidence, EC/OSS evidence, and French tax package checks.
- Odoo drill-down smoke checks passed for Trial Balance, General Ledger, Journal Report, Partner Ledger, Open Items, Balance Sheet, Profit and Loss, French Balance Sheet, French Profit and Loss, VAT tax report, bank reconciliation, currency report, analytic report, SIG/CAF, French tax package, cash flow and executive summary.
- The custom export wizard generated CSV/PDF/XLSX payloads for the main benchmark reports.
- The report artifact stage remains partial because the final Odoo Online-like interactive `account.report` behavior, accountant-ready templates, official declaration views and professional acceptance are incomplete.

| Mandatory report | Availability | Interactive usability | Correctness evidence | Drill-down | PDF/XLSX | Benchmark status |
| --- | --- | --- | --- | --- | --- | --- |
| Trial Balance | available | OCA wizard plus custom evidence | debit/credit match `1,064,045.02` | smoke passed | generated | technical evidence passed |
| General Ledger | available | OCA wizard plus custom evidence | `4,809` rows | smoke passed | generated | technical evidence passed |
| Journal Report | available | OCA/custom evidence | balanced journal checks | smoke passed | generated | partial |
| Partner Ledger | available | OCA/custom evidence | 68 export rows | smoke passed | generated | partial |
| Open Items | available | OCA/custom evidence | 1 export row | smoke passed | generated | partial |
| Aged Receivable | available | OCA shortcut/custom evidence | 1 export row | smoke passed | generated | partial |
| Aged Payable | available | OCA shortcut/custom evidence | 0 export rows in benchmark | smoke/export passed | generated | partial |
| Balance Sheet | available | MIS/custom evidence | benchmark French checks pass | smoke passed | generated | technical evidence passed, semantic acceptance pending |
| Profit and Loss | available | MIS/custom evidence | net result check passes | smoke passed | generated | technical evidence passed, semantic acceptance pending |
| Detailed Balance Sheet/P&L | partial | custom/MIS evidence only | account-detail work partly enabled | partial | generated for annual package | accountant-readable template pending |
| Tax/VAT reports | available | OCA/custom evidence | tax package checks pass with review flags | smoke passed | generated | declaration acceptance pending |
| Asset register | available | custom evidence | 3 assets, gross `10,430.49`, imported period net value `8,754.44` | evidence drill-down | generated | technical evidence passed |
| Depreciation schedule | available | custom evidence | 91 rows | evidence drill-down | generated | reconciliation/acceptance pending |
| Currency report | available | custom evidence | 14 rows | smoke passed | generated | partial |
| SIG | available | custom/MIS-style evidence | value added/EBE/CAF checks pass | smoke passed | generated | accountant-readable template pending |
| CAF | available | custom evidence | `57,899.03` | smoke passed | generated | accountant acceptance pending |
| Management ratios | partial | target documented | not fully validated in checkpoint | partial | not complete | partial |

The generated PDFs are readable as technical artifacts but not yet accepted as accountant-ready templates comparable to the supplied annual-account/SIG/tax samples. Raw technical exports should remain under Advanced Audit.

## 30 September 2025 benchmark status

The benchmark close does not yet pass full Milestone 13 acceptance, but the current technical evidence reproduces the principal benchmark values from imported ledger/report rules:

| Benchmark value | Verified target value | Difference | Current classification |
| --- | ---: | ---: | --- |
| Total gross assets | 71,356.21 | 0.00 | technical evidence passed |
| Accumulated depreciation/provisions | 1,676.05 | 0.00 | technical evidence passed |
| Total net assets | 69,680.16 | 0.00 | technical evidence passed |
| Gross fixed assets | 10,430.49 | 0.00 | technical evidence passed |
| Net fixed assets | 8,754.44 | 0.00 | technical evidence passed |
| Total passif | 69,680.16 | 0.00 | technical evidence passed |
| Net result | 56,222.98 | 0.00 | technical evidence passed |
| Turnover | 129,188.62 | 0.00 | technical evidence passed |
| Operating result | 66,180.70 | 0.00 | technical evidence passed |
| Current result before tax | 66,144.98 | 0.00 | technical evidence passed |
| Value added | 85,322.21 | 0.00 | technical evidence passed |
| EBE | 67,856.75 | 0.00 | technical evidence passed |
| CAF | 57,899.03 | 0.00 | technical evidence passed |
| FEC debit | 1,064,045.02 | 0.00 | technical evidence passed |
| FEC credit | 1,064,045.02 | 0.00 | technical evidence passed |

Remaining material benchmark gaps:

- VAT deductible goods/services has explicit review/difference flags in the French tax package checks; the source ledger, CA12 clearing evidence and benchmark/external values require accountant review.
- Taxable result, reduced-rate treatment, 2065/2033 box mapping and CA12 final filing values are preparation evidence, not accepted declaration outputs.
- The formal annual-account package presentation and accountant-authored narrative/attestation are not generated as final accountant-ready documents.
- Management ratios are not yet fully validated as a complete report set.

## FEC readiness

`fec-status.json` reports `passed`, classification `FEC_GENERATED_TEST_MODE`. Current verified FEC evidence:

- generated file path: `artifacts/accounting-compat/private/fec-usl-2025-09-30.txt`;
- SHA-256: `38d99b33b0f2864637a0506f61a52d33e73cd58ecd3ca9cdf6a6f69b740c53b1`;
- rows excluding header: `4,781`;
- debit and credit: `1,064,045.02`;
- generated through Odoo `l10n_fr_account` in test mode;
- local structural preflight passed with no errors or warnings;
- `fec-validation-status.json` records status `passed`, classification `OFFICIAL_DGFIP_SOURCE_VALIDATION_PASSED`.

Important limitations:

- The FEC is a private generated artifact and must not be committed.
- Odoo omits `28` zero-debit/zero-credit move lines from the FEC; this is classified as a presentation difference because FEC amount rows reconcile to target/source amount rows.
- The checkpoint did not rerun the official DGFiP tool; it inspected the existing validation artifact.
- Final FEC acceptance still requires accountant review, checksum retention, reconciliation to accepted statements, and the accepted lock/freeze workflow.

## French declaration readiness

Current state is preparation-only:

- French annual statements evidence exists for Bilan Actif, Bilan Passif, Compte de resultat, detailed lines, SIG and CAF.
- French tax package mapping evidence exists with 31 rows and review flags.
- Two external report values are imported/represented.
- VAT benchmark investigation exists and confirms the `445660` source/target ledger match.
- Declaration lifecycle states, deadlines, CFS Pro/Portailpro user guidance, CERFA/DGFiP box workflow, filing references and payment/refund tracking are not complete user-facing workflows.

No electronic filing capability is implemented or claimed.

## Accountant workflow

Current accountant-facing checks in `reports-status.json` passed for:

- reviewer group visibility to USL move lines and review records;
- no visible USL Media move lines for the restricted reviewer scope;
- write blocked for review user;
- privacy probe blocked with `AccessError`;
- access to attachments/deferred schedules/source reports/discrepancies/review decisions;
- trial balance XLSX export via the custom export path;
- review-decision probe rolled back after exercising acceptance fields.

Current gaps:

- accountant cannot complete full review without developer-prepared context because declaration mappings, final report templates and FEC acceptance workflow are not finished;
- attachment classification into accounting evidence, restricted accounting evidence and non-accounting private material needs final policy and tests;
- official non-test FEC access is still intentionally restricted;
- no full browser walkthrough of the accountant role was completed in this checkpoint.

## Product capability scorecard

| Capability | Verified status | Evidence | Remaining gap | Blocking? |
| --- | --- | --- | --- | --- |
| Source restore | Functionally complete but not rerun in checkpoint | `source-restore-status.json` passed | full clean rehearsal before closure | P1 |
| Source extraction | Functionally complete but not rerun in checkpoint | `source-manifest.json`, `source-controls.json` | extraction for all non-ledger semantics needs continued validation | P1 |
| Target reconstruction | Partial | exact target plus separate passing Track B artifact | payments/reconciliation, expenses, reports and declarations | P0 |
| Posted ledger replay | Verified complete for benchmark slice | `target-validate-status.json` passed | broaden final acceptance beyond posted slice | P0 if regresses |
| Companies | Verified represented | Odoo shell: 2 companies | USL Media empty readiness/user reports | P1 |
| Accounts/journals | Functionally complete but acceptance incomplete | import/run counts, validation comparisons | bootstrap/import count distinction needs clearer user evidence | P1 |
| Taxes/fiscal positions | Partial | tax config imported, tax reports generated | VAT exigibility and declaration acceptance | P0 |
| Currencies/rates | Functionally complete for restored history | `1,877/1,877` exact traced rates | future automatic provider remains separate | P1 |
| Moves/move lines | Verified complete for posted replay | exact source/target counts | non-posted workflow reconstruction | P0 |
| Invoices/bills/refunds/receipts | Native Track B proof complete | `284/284` posted and exact, `0` blocked/mismatched | integrate with later payment/reconciliation proof | P1 |
| Expenses | Implemented but not user-data complete | `hr_expense` installed, 0 records | historical expense reconstruction | P1 |
| Payments | Partial | 97 imported, 13 review records | no-entry payment workflow native UX | P1 |
| Bank/reconciliation | Partial | statement lines/reconciliations imported; OCA menu present | operational reconciliation UX not fully validated | P0 |
| Assets/depreciation | Functionally complete but acceptance incomplete | 3 assets, 91 schedule rows | statement/tax reconciliation acceptance | P1 |
| Deferred schedules | Evidence only | 110 rows | user-facing workflow/report acceptance | P2 |
| Analytics | Partial | 632 lines | analytic report semantics and UI acceptance | P2 |
| Attachments/evidence | Partial | 332 scoped attachments | final privacy classification | P1 |
| Lock dates | Verified for benchmark write block | lock enforcement check passed | UI/user-role lock tests | P1 |
| Traceability | Functionally complete | no duplicate trace invariant failures | user-facing clarity | P1 |
| Idempotence/failure handling | Functionally complete in prior clean rehearsal | harness docs and private artifacts | not rerun in checkpoint | P1 |
| General reports | Partial | reports artifact partial, OCA/MIS installed | Level 4 user/report parity | P0 |
| French statutory reports | Partial | annual statement checks pass | accountant-ready presentation/acceptance | P0 |
| Declarations | Not complete | tax package mapping evidence | lifecycle/deadlines/CERFA workflow | P0 |
| FEC | Functionally complete but acceptance incomplete | test-mode FEC, preflight, validation artifact | final acceptance workflow/accountant approval | P0 |
| Accountant workflow | Partial | access probes passed | full user walkthrough and evidence policy | P1 |

## Documentation audit

Authoritative current documents:

- Product target: `docs/product/accounting-core.md`, `docs/accounting/milestone-13-reporting-and-closing-ux-target.md`;
- Current technical harness: `docs/accounting/accounting-compat-harness.md`;
- Current formal status: this checkpoint;
- Current progress narrative: `docs/accounting/milestone-13-current-progress-report.md`;
- Development workflow: `docs/operations/accounting-development-workflow.md`;
- Imported-data run guide: `docs/operations/run-imported-accounting-dev.md`;
- User guide entry point: `docs/users/README.md`.

Documentation issues corrected in this checkpoint:

- `ROADMAP.md` had checked implementation items under "What is not complete"; it now points to this dated checkpoint and separates verified complete, functionally complete, partial, evidence-only and not-started/deferred work.
- `docs/accounting/README.md` now lists this checkpoint.
- `docs/accounting/milestone-13-current-progress-report.md` now explicitly defers to this checkpoint for current status and fixes the stale user-guide menu path and cash-basis wording.
- `docs/users/README.md` now states that the native Expenses workspace is available but historical expense records are not yet reconstructed.

Remaining documentation debt:

- consolidate repeated report status language across progress report, harness doc and user docs after the next implementation increment;
- add a concise public/private artifact map for non-developer reviewers;
- update user guides after final menu/report UX stabilizes.

## Risks and blockers

### P0 milestone blockers

- Native business-document reconstruction is incomplete for customer invoices, vendor bills, refunds and expenses.
- Mandatory reports have technical evidence and OCA/MIS surfaces, but not full Level 4 user-facing/accountant-accepted parity.
- French declaration workflows for CA12, 2065/2033 and CFS Pro/Portailpro guidance are not complete.
- VAT deductible/exigibility treatment still needs accountant validation.
- Operational reconciliation workflow is not validated end to end.
- FEC is generated and structurally checked, but final accountant-reviewed FEC acceptance workflow is incomplete.

### P1 material risks

- Currency-rate reconstruction is not sufficiently proven.
- Attachment privacy/access classification is incomplete.
- Accountant browser walkthrough is incomplete.
- USL Media is represented but its empty-company readiness and multi-company reporting UX need validation.
- Idempotence and failure guardrails were inspected from prior artifacts, not rerun during this checkpoint.

### P2 improvements

- Menu labels and grouping need another polish pass after the remaining workflows land.
- Generated PDFs/XLSX need accountant-ready templates and clearer separation from Advanced Audit exports.
- Documentation still repeats status in several places.

## Recommended next increments

1. **Native payments, reconciliation and historical expenses**
   - User outcome: the proven invoices, bills, refunds and receipts participate in normal payment and bank workflows, while historical employee expenses are readable as native Odoo expenses where source data permits.
   - Acceptance: source payment/reconciliation/expense counts are classified and native accounting effects match source without altering the exact target.
   - Dependency: Track B payment, statement, reconciliation and expense field mapping.
   - Decision needed: accountant/product approval for source records that cannot be safely regenerated.

2. **Operational reconciliation workbench validation**
   - User outcome: finance users can reconcile bank transactions and understand imported historical reconciliation state.
   - Acceptance: match, write-off, partial reconciliation and evidence views work without corrupting replayed history.
   - Dependency: OCA reconciliation behavior tests.
   - Decision needed: none unless historical relationships need transformation.

3. **Level 4 general report parity**
   - User outcome: Trial Balance, General Ledger, Journal Report, Partner Ledger, Open Items, Aged Receivable/Payable, Balance Sheet and Profit and Loss are daily-use reports.
   - Acceptance: interactive filters, drill-down, PDF/XLSX exports and benchmark reconciliation pass.
   - Dependency: OCA/MIS mapping and export templates.
   - Decision needed: report presentation acceptance from accountant/Product Manager.

4. **French statutory statement package**
   - User outcome: benchmark annual accounts can be generated as accountant-readable package components.
   - Acceptance: Bilan, P&L, detailed accounts, SIG and CAF reconcile to ledger and benchmark.
   - Dependency: report templates and mappings.
   - Decision needed: accountant approval for presentation and narrative boundaries.

5. **VAT, CA12 and declaration workflow**
   - User outcome: user can see what must be entered on CFS Pro/Portailpro and why.
   - Acceptance: box mappings, external values, statuses, deadlines and evidence are reviewable.
   - Dependency: declaration spec files and Odoo models.
   - Decision needed: accountant validation of VAT exigibility and external values.

6. **FEC acceptance workflow**
   - User outcome: reviewer can generate, validate, checksum, freeze and retrieve accepted FEC dossier.
   - Acceptance: test and final-candidate FECs reconcile and have recorded review status.
   - Dependency: permission workflow and official validator run.
   - Decision needed: who may approve/freeze final FEC.

7. **Accountant access walkthrough**
   - User outcome: accountant can review records/evidence/reports without developer help.
   - Acceptance: browser walkthrough under accountant role and access tests pass.
   - Dependency: final report/declaration screens.
   - Decision needed: attachment visibility policy.

8. **Two clean rehearsals**
   - User outcome: reproducible product evidence from the original dump.
   - Acceptance: two clean target reconstructions produce the same accounting/report/FEC outcomes.
   - Dependency: above accounting/report/declaration increments.
   - Decision needed: none unless discrepancies appear.

## Decisions required

1. **VAT exigibility and CA12 treatment**
   - Evidence: imported taxes include cash-basis behavior; settings inconsistency was fixed without changing tax definitions; tax package has review flags.
   - Options: preserve source behavior pending accountant review; document an option for VAT on debits if evidence exists; configure company/date-specific mixed treatment if needed.
   - Recommendation: preserve source behavior and require accountant approval before any rule change.
   - Consequence of delay: declaration workflow can be built, but final CA12 acceptance remains blocked.

2. **Accountant-ready report presentation standard**
   - Evidence: current PDFs/XLSX generate but are technical; supplied annual accounts/SIG/tax samples define higher readability expectations.
   - Options: OCA/MIS native exports accepted with light USL templates; custom QWeb/XLSX templates matching benchmark structure; external accountant package assembly outside Odoo.
   - Recommendation: OCA/MIS dynamic reports plus USL QWeb/XLSX templates for annual/tax packages.
   - Consequence of delay: Level 4 report acceptance and accountant workflow remain blocked.

3. **Attachment visibility policy**
   - Evidence: accountant access probes pass for scoped attachments, but final three-class evidence policy is not complete.
   - Options: broad accountant access; metadata-only for restricted evidence; curated accounting copies for sensitive evidence.
   - Recommendation: accounting evidence, restricted accounting evidence, non-accounting private material, with curated copies where needed.
   - Consequence of delay: accountant readiness and privacy acceptance remain blocked.

4. **Final FEC approval authority**
   - Evidence: test-mode FEC works; official non-test FEC remains restricted.
   - Options: accountant reviewer approves; accounting manager approves; CEO approves after accountant review.
   - Recommendation: accountant reviewer validates, accounting manager freezes, CEO can download accepted package.
   - Consequence of delay: FEC can be tested but cannot be accepted for closure.

## Conclusion

**Substantially progressed, implementation continues.**

The rebuild now has a credible Layer 1 posted-ledger foundation, exact native current-period business-document proof, and meaningful Odoo-facing evidence/report surfaces. It is not Milestone 13 complete because payments and operational reconciliation, historical expenses, Level 4 reports, French declaration workflows, final FEC acceptance and the accountant-ready review experience remain unfinished.
