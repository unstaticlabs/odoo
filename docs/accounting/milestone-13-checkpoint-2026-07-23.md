# Milestone 13 checkpoint - 2026-07-23

Status: **technical rehearsal extended; professional acceptance remains pending**.

This checkpoint supersedes the 2026-07-22 checkpoint. It is not a milestone closure or a production-migration authorization. The remaining P0/P1 gates require Valentin and/or accountant decisions; they must not be accepted by an implementation agent.

## Outcome

The disposable `odoo_rebuild_accounting_test` target provides the broad Milestone 13 rehearsal:

- source-traced historical reconstruction and benchmark parity;
- automatic future ECB reference rates kept separate from historical replay;
- isolated Track B native document, expense, payment, bank, reconciliation, asset, deferral and multi-plan analytic replay, including checksum-verified bill and expense evidence;
- a company-scoped operational Accounting Home for cash/bank, daily queues, open balances, closing, declarations and prepared actions, with the native journal dashboard retained;
- distinct Bank Matching and General Reconciliation workbenches;
- canonical Community-compatible dynamic report navigation, drill-down and PDF/XLSX exports;
- versioned French declaration preparation with traceable fields and filing/payment state;
- monthly, quarterly and annual closing workspaces with reviewer and lock-date gates;
- source-traced correction and reconciliation of the EUR 942 DGFiP refund;
- accountant-scoped read access and write denial;
- a DGFiP-source-validated FEC;
- a polished 13-page PDF and three-sheet XLSX closing review package;
- two clean reconstruction rehearsals.

The most recent pre-audit readiness classification was:

```text
TECHNICAL_REHEARSAL_PASSED_PROFESSIONAL_ACCEPTANCE_PENDING
```

That artifact reported `0` technical failures against the gates then encoded.
A full objective audit subsequently identified missing direct Track B proof for
deferral schedules and cross-stage analytics, plus the absent future reference-
rate provider. Those gates are now implemented, passed and included in
readiness/evidence generation. This does not convert the remaining professional
decisions into technical acceptances.

## Architecture decision

The selected design is a thin USL workflow layer on standard Odoo lock dates and maintained OCA reporting/reconciliation modules. It preserves standard accounting records and exposes structured declaration/closing state without creating a second ledger.

Alternatives rejected for this milestone:

1. OCA fiscal-year/cutoff modules alone: useful foundations, but insufficient for the required declaration, filing, reviewer and closing-package lifecycle.
2. A custom tax engine or electronic filing client: duplicates official compliance logic and creates unnecessary legal, maintenance and migration risk.

Electronic submission is therefore explicitly outside this implementation; accurate preparation and external filing tracking remain in scope.

For the Accounting landing page, three alternatives were compared:

1. keep only the standard journal-card dashboard, which does not expose closing,
   declaration or prepared-decision state;
2. build a parallel OWL dashboard and model, which would duplicate a structured
   domain and make agent/API access dependent on UI logic;
3. extend the existing company-scoped SQL review summary as an operational Home
   and retain the standard journal dashboard as a direct child route.

The third option is implemented. A minimal client action routes the Accounting
launcher to the active company's queryable Home; it does not implement a second
ledger or a parallel accounting engine.

For Accounting Hygiene, three alternatives were compared:

1. create a second hygiene issue model, duplicating states already held by
   native records, closing controls and review decisions;
2. expose only closing controls, omitting daily queues that remain relevant
   between period reviews;
3. extend the company-scoped review summary with live operational buckets and
   direct actions while reusing the existing closing and review domains.

The third option is implemented. It creates no issue copies or notification
stream and keeps every count traceable to a native record or durable decision.

For native document evidence, three alternatives were compared:

1. retain binaries only on exact-ledger evidence records, leaving operational
   bills and expenses without their source documents;
2. link native records to the separately operated private source filestore,
   coupling day-to-day accounting to that service and its authorization model;
3. replay checksum-verified binaries onto the source-traced native records and
   preserve the source-designated main attachment.

The third option is implemented in Track B. Missing files, unmapped native
targets, checksum mismatches, duplicate traces and main-selection mismatches
are blocking attachment defects. Standard Odoo record access governs the
result; no unverified source link is exposed.

For future exchange rates, the absent Enterprise live-currency module and the
lack of a deployable updater in the checked OCA 19 dependency set were compared
with a focused native-rate adapter. The selected ECB adapter does not regenerate
history: it skips source-traced rows, records provider/retrieval metadata and
runs daily after the normal publication window. ECB values are informational
reference rates; actual bank or platform conversions remain authoritative for
transactions where they define the conversion.

## Reconstruction evidence

The latest clean target rehearsal passed:

- source dump SHA-256: `bf16ce18965e4ce1b23d7b79930b6e43ca7f510339ac6d2db280231f91d1449f`;
- benchmark: 10 January 2024 through 30 September 2025;
- Track A: `2,046` moves and `4,809` lines;
- Track A debit and credit: `1,064,045.02` each;
- full imported target: `4,843` source moves plus one source-traced correction;
- target invariants: no unbalanced posted move and no source/target parity failure;
- document regeneration: `189` candidates validated, `5` deliberately review-only;
- cross-boundary reconciliation evidence: `75/75` rows have generated draft endpoint coverage;
- rollback-only native partial-reconciliation probe: passed.

Clean rehearsal A ran reset, import, validation and idempotence before the declaration/closing checkpoint. Clean rehearsal B reran reset, import and validation after those changes. Both reproduced the same Track A counts and totals. The latest target idempotence artifact remains passed.

## Track B native engine

The following current-period technical gates are passed:

- native business documents;
- native expenses;
- expense settlement;
- document settlement;
- General Reconciliation;
- direct bank categorization;
- external bank replay;
- native asset depreciation;
- native deferred-expense scheduling and posting;
- cross-stage multi-plan analytic reconciliation.

The target retains deliberate draft/post-cutoff boundaries instead of forging reconciliations across the accepted posted-ledger scope.

Native document evidence is also complete inside Track B:

- business-document binaries: `215/215`;
- business-document main selections: `202/202`;
- expense binaries: `263/263`;
- expense main selections: `245/245`;
- missing files, unmapped targets, checksum mismatches, duplicate traces and
  main-selection mismatches: `0`.

The standard Community vendor-bill form exposes the original PDF through its
attachment workbench, rendered thumbnail and PDF viewer rather than copying the
Enterprise split-pane layout. The standard expense form exposes the source
receipt filename, count and thumbnail. This is an equivalent native evidence
path, not a pixel-parity claim. The records are now integrated with exact
benchmark history in a disposable hybrid candidate. Manager/reviewer report,
bill and expense browser journeys pass there; professional acceptance and
promotion remain deliberate open boundaries.

The native deferral replay represents all `5` source originals as operational
schedules with `82` lines: `34` posted current-period entries and `48` future
lines, plus one traced opening-boundary reversal. All five schedules remain
running because future dates remain, and a repeat replay creates nothing.

The analytic replay represents `29` explicit source post-posting corrections.
Source and target allocation totals match across `13` analytic accounts;
source and target actual analytic-line totals also match, all `324` directly
traced analytic lines match, and no line is unmapped. Odoo's per-line rounding
creates only a theoretical `+0.01/-0.01` pair, within company-currency
precision; actual analytic-line totals reconcile exactly to source.

## Hybrid replacement candidate

`odoo_rebuild_accounting_replacement` is a third disposable database, separate
from both the exact-replay baseline and Track B. It clones only a completed
Track B state and then exact-imports the `2024-01-10` through `2025-09-30`
benchmark. Four native moves pass source-identity and accounting-shape alias
validation and are reused instead of duplicated.

Historical parity is exact at `2,046` moves, `4,809` lines and debit/credit
`1,064,045.02`. The combined candidate has `4,541` posted moves and `10,727`
posted lines, with no unbalanced posted move or duplicate source identity.

Every current-period journal and account-balance difference is classified as
native cash-basis timing/aggregation, native exchange timing/aggregation or OCA
bank-allocation segmentation. The `12` account differences net to EUR `0.00`;
the remaining profit-and-loss difference is EUR `2.64` and is attributable to
native exchange timing. Validation therefore remains `partial`, classified as
`HYBRID_REPLACEMENT_TARGET_EXPLAINED_NATIVE_DIFFERENCES`. Professional
acceptance and an explicit promotion decision remain open. Replacement
report/role/browser journeys now pass for both Accounting Manager and the
single-company accountant reviewer.

The first clean native rebuild after adding standalone operator entries failed
the external-bank stage with `40` blocked cases and `46` mismatches. The
standalone selector had claimed `12` payroll/tax moves that belong to the
downstream bank stage, and OCA could not categorize a source allocation to the
same `471000` account currently configured as suspense. Ownership is now
derived from the external-bank edge graph, and the bank stage uses and restores
an empty `TBSUSP` staging suspense with zero ending lines and balance. The clean
rebuild and required idempotence reruns then passed.

Earlier replacement-harness attempts also exposed and corrected a statement
date query against a nonexistent column, stale cloned module schema, a July
refund action outside the native replay period, invalid latent discrepancy
classifications and a legacy-account-code `GROUP BY` typo in the validator.
These were harness defects, not accepted accounting differences.

## Future reference rates

The live target provider gate retrieved the official ECB daily feed twice. The
latest reference date was `2026-07-22`, with GBP `0.8534` and USD `1.1408` per
EUR. The idempotent rerun created `0` rows and updated the same `2`; all `1,877`
source-traced historical rates remained unchanged, no duplicate currency/date
row appeared, and the daily cron is active.

The Accounting Manager browser journey opened `Accounting > Configuration >
Currency Rate Automation`, showed the retrieved state and opened both native
rate rows. A USL accountant-reviewer persona received the expected Accounting
Administrator access error. The disposable browser user was removed after the
check.

## Report evidence

All `38` active source reports now have:

- a mapped target treatment or explicit SASU scope exclusion;
- a passing export package for the mapped report family;
- a passing sampled drill-down when rows exist;
- an explicit `not_applicable_empty_scope` classification when a valid report has no row to drill into;
- Level 4 technical evidence pending professional acceptance.

Current distribution:

| Decision | Count | Technical level |
| --- | ---: | --- |
| Mandatory parity | 23 | Level 4 evidence partial |
| Operational parity | 10 | Level 4 evidence partial |
| Accountant requested | 3 | Level 4 evidence partial |
| Removed as unused for SASU | 2 | Level 4 evidence partial |

The empty benchmark Open Items and Aged Receivable reports are not treated as failures: export and view scope are valid, the ledger residual is zero, and no row-level drill-down is applicable. The harness retains passed evidence per report family even if another family fails, preventing one aggregate false negative from erasing unrelated evidence.

No report is professionally accepted yet. Level 4 technical evidence is not accountant acceptance.

The normal reporting menus use one native-ledger-first Community-compatible
workbench. OCA remains a maintained foundation where appropriate, but the
dynamic report product does not depend on an absent proprietary report module
and does not expose competing report implementations in normal navigation.

## VAT and declarations

The 2025 and 2026 rule sets cover the applicable or conditional 2571, 2572, 2065/2065-bis, 2033 A-G, 2069-RCI, 2777, 3517/CA12-E and 3514 workflows.

The CA12-E workspace exposes ledger-derived fields and confirmed facts, including:

- account 445670 debit: EUR 3,442;
- accepted/reimbursed VAT refund: EUR 2,500;
- later DGFiP refund: EUR 942;
- VAT collected: EUR 459;
- deductible VAT clearing value: EUR 1,960;
- remaining VAT credit: EUR 0;
- instalments paid: EUR 0.

The EUR 942 refund is corrected through a source-traced debit to 471 and credit to 445670, and both sides reconcile. Independent validation classifies the transformation as `CONFIRMED_SOURCE_TRACED_TRANSFORMATION`.

Missing corporate-tax information remains visibly blocked. This is correct preparation behavior, not an invented filing value.

## Closing, exports and FEC

The historical annual close shows `3` blocking controls, `1` warning and `8` passed controls. The blockers are professional acceptance gates for reports, FEC and declarations; standard Odoo lock dates already protect the benchmark period.

Closing review package:

- PDF: `output/pdf/usl-closing-package-2025-09-30.pdf`;
- PDF pages: `13`;
- PDF SHA-256: `abd012c25affdabce3d567e71ff3f6f10445ce02e06a9857ab129d878f955c59`;
- XLSX: `output/pdf/usl-closing-package-2025-09-30.xlsx`;
- XLSX sheets: `Metadata`, `Report`, `Audit Data`;
- XLSX SHA-256: `adb10d682824952ae666327ab27cee3ac5066d35216b6ca089a7cc071ca900bc`.

The PDF has repeated company/period headers, page numbers and bounded page content. The XLSX was parsed and rendered through both the artifact workbook runtime and LibreOffice; the legal identifier remains text, not scientific notation.

FEC:

- rows: `4,781`;
- debit and credit: `1,064,045.02` each;
- latest file SHA-256: `95652b3f3a7c66e25a6f2aa0d56cf860777364606b5a9519090f2d48e5657efa`;
- local structural preflight: passed;
- official DGFiP Test Compta Demat source validation: passed with `0` blocking logs.

## Browser walkthrough

The in-app browser was used against the clean target after restarting the HTTP service on the current registry.

Valentin/Accounting Manager journey:

- Accounting opened on `Unstatic Labs — Accounting Home`;
- the Home showed `3,037` bank transactions, `207` unmatched, `31` journals,
  `12` cash journals and a bank/cash balance of EUR `91,477.07`;
- daily/open-balance state showed `37` draft vendor documents and `74` open
  payables totalling EUR `31,749.82`;
- the earlier Home capture showed the current 30 June 2026 close blocked by
  `2` controls; the later Hygiene refresh recomputed `4` blockers. The next
  visible declaration deadline was 24 July 2024, `15` obligations were overdue
  and `1` was within 45 days;
- prepared-action counts were `2` for Valentin and `44` for Prosper;
- the report route opened Trial Balance with native scope and fiscal
  year-to-date defaults;
- refresh, browser back and a direct Home route preserved the correct title and
  form;
- the retained native journal Dashboard opened with `28` visible cards;
- Bank Matching opened the imported transaction history;
- General Reconciliation opened account-grouped residual items;
- the declaration schedule and CA12-E source fields opened;
- the historical annual closing workspace and its controls opened;
- the closing PDF/XLSX wizard produced a `78`-row preview;
- the OCA Trial Balance opened with date, posted/draft, hierarchy, partner, journal and account filters;
- clicking the EUR 1,000 Trial Balance amount drilled into the exact journal item `BQ1000000001`.

Prosper/read-only journey used a disposable browser user assigned only to Unstatic Labs:

- Accounting opened on the same Home without create or configuration controls;
- the database contained two companies while the reviewer Home pager remained
  `1/1` for Unstatic Labs;
- the Configuration menu and Accounting Settings button were hidden;
- the native-scope report workbench opened without an access error;
- Trial Balance filters and interactive report opened;
- Declarations and Closing opened;
- the historical close was visible without a create button;
- no USL Media accounting scope was assigned;
- write access to `account.bank.statement.line` and `account.move.line` raised `AccessError`;
- Bank Matching retained the read-only `View move` route while hiding validate, reset and check-state mutation controls;
- the disposable user was deleted after the walkthrough.

Accounting Hygiene journey:

- the manager opened the dedicated company-scoped workbench and refreshed the
  current controls without an access error;
- the final state showed `354` attention items: `207` bank transactions to
  match, `37` supplier documents without main evidence, `37` stale drafts,
  `7` unusual account balances, `15` overdue declarations, `4` current closing
  blockers and `7` warnings;
- the unusual-balance queue represented EUR `50,860.26` across supplier
  advances, customer credits, shareholder/tax debit balances, two small
  foreign-currency cash overdrafts and a wrong-way exchange-income balance;
- the live control used posted history through 30 June 2026 for balance-sheet
  accounts and the configured 1 October 2025 fiscal-year start for profit and
  loss accounts. French contra accounts and documented two-sided policies were
  not treated as errors;
- the workbench separated `2` decisions prepared for Valentin from `44`
  prepared for Prosper and retained the open `1` P0 / `1` P1 evidence;
- the current period control set contained all `14` accounting, document,
  bank, tax, payroll, asset, currency, analytic, issue, report, FEC and lock
  controls;
- the manager opened the account-grouped seven-account journal-item drilldown
  and saw the configurable `Hygiene Balance Policy` on the Chart of Accounts;
- the supplier-evidence drilldown opened exactly `37` draft bills;
- the first reviewer pass exposed a misleading standard `Upload` control even
  though create access was denied; the shared account-move frontend now gates
  that control with `account.group_account_invoice`;
- the repeat reviewer pass retained read access to the `37` records while
  hiding refresh, Configuration, New and Upload controls;
- the reviewer opened the same seven-account unusual-balance drilldown without
  refresh or balance-policy configuration;
- the manager retained New and Upload, and the disposable reviewer was removed;
- private proof:
  `artifacts/accounting-compat/private/accounting-hygiene-browser-status.json`.

Private Accounting Home proof:
`artifacts/accounting-compat/private/accounting-home-browser-status.json`.

The Bank Matching control restriction is enforced both by server ACLs and by the combined Odoo form architecture. The corresponding regression test verifies every OCA mutation button, including both reconcile variants.

Native asset journey:

- the Accounting > Assets list opened on the Track B target with all `3` source assets;
- the manager opened the MBP 16 depreciation board, its `9` posted current-period move links and its future unposted schedule;
- the reviewer opened the same list and board with `9` read-only move links;
- the reviewer saw `0` create-move, recompute or reverse controls after the view hardening update;
- the disposable asset-review user was deleted after the walkthrough;
- private proof: `artifacts/accounting-compat/private/track-b-assets-browser-status.json`.

Native deferral journey:

- the manager opened all `5` running deferrals, the linked original/posted entries and the full posted/future schedule;
- manager-only `New`, `Post Due Entries` and individual `Post` controls were visible;
- the reviewer opened the same records with `0` create or post controls;
- the disposable reviewer was deleted after the walkthrough;
- private proof: `artifacts/accounting-compat/private/track-b-deferrals-browser-status.json`.

Native analytic journey:

- the native Analytic Items list opened with `621` lines and both `Projet` and `Epic` plan columns;
- current examples showed source-replayed Canada, Australia and Pride classifications;
- native pivot view, XLSX download and graph view rendered;
- the read-only correction audit opened with all `29` records for manager and reviewer and no create action;
- private proof: `artifacts/accounting-compat/private/track-b-analytics-browser-status.json`.

Native document-evidence journey:

- the manager opened a standard Track B vendor bill with its posted/paid state;
- its source PDF was visible under the original filename with a rendered
  thumbnail and a native PDF viewer route;
- the manager opened a standard Track B expense with its approval/accounting
  state and source JPEG receipt thumbnail;
- the permanent add-on regression proves that the single-company accountant
  reviewer can read the verified native accounting attachment binary;
- private proof:
  `artifacts/accounting-compat/private/track-b-native-attachments-browser-status.json`.

Hybrid replacement journey:

- Accounting Home opened on the combined candidate with `3,005` bank
  transactions, `186` to match, `0` draft customer/vendor documents, `128`
  expenses to process, `30` open receivables and `122` open payables;
- the native Trial Balance refreshed for All Native Accounting and fiscal
  year-to-date `2025-10-01` through `2026-07-23`, producing `105` preview rows;
- the manager opened `245` vendor documents (`161` bills and `84` receipts),
  a paid OpenAI bill with its source attachment, and all `325` native expenses;
- the reviewer opened the same Home, `105`-row report, `245` documents and
  `325` expenses without Configuration, Accounting Settings, New, Upload,
  credit-note, reset, expense-submit or expense-receipt mutation controls;
- the first expense pass exposed two standard-boundary defects: base internal
  access rendered New/Upload, and migration-only employee provenance fields
  crossed into `hr.employee.public`. The final implementation makes native
  expenses read-only in both server methods and combined view architecture,
  gates list/kanban upload controls on create access, and restricts private
  employee trace fields to `hr.group_hr_user`;
- granting the reviewer private `hr.employee` access was rejected because it
  would expose unrelated HR data. The final expense form links Valentin
  through `hr.employee.public`, retains the source receipt/accounting evidence,
  and shows zero New, Upload, Submit or Attach Receipt controls;
- the temporary reviewer and temporary shell scripts were removed;
- private proof:
  `artifacts/accounting-compat/private/replacement-browser-status.json`.

The full screenshot mapping, user-journey scorecard and permission matrix are
recorded in
[Milestone 13 screenshot parity and user-journey scorecard](milestone-13-screenshot-parity-matrix.md).

## Commands and validation

Passing validation includes:

```text
python3 -m unittest accounting_compat.tests.test_report_evidence accounting_compat.tests.test_fec_preflight
python3 -m py_compile accounting_compat/cli.py custom-addons/rebuild_account_migration/models/closing.py custom-addons/rebuild_account_migration/models/review_summary.py custom-addons/rebuild_account_migration/tests/test_declaration_closing.py custom-addons/rebuild_account_migration/tests/test_rebuild_account_migration.py
docker compose --profile devcontainer run --rm devcontainer ruff check --ignore EM101 custom-addons/rebuild_account_migration/models/closing.py custom-addons/rebuild_account_migration/models/review_summary.py custom-addons/rebuild_account_migration/tests/test_declaration_closing.py custom-addons/rebuild_account_migration/tests/test_rebuild_account_migration.py
odoo --config=/etc/odoo/odoo.conf --addons-path=/workspace/odoo/addons,/workspace/odoo/odoo/addons,/workspace/odoo/custom-addons,/workspace/odoo/oca-addons --database=odoo_rebuild_accounting_test --update=rebuild_account_migration --stop-after-init
/tmp/odoo-m13-docs-venv/bin/python -m mkdocs build --config-file mkdocs.yml
git diff --check
make accounting-target-reset
make accounting-replacement-reset
make accounting-replacement-import
make accounting-replacement-validate
make accounting-target-import
make accounting-target-validate
make accounting-document-regeneration
make accounting-track-b-expenses
make accounting-track-b-documents
make accounting-track-b-deferrals
make accounting-track-b-analytics
make accounting-target-reconciliation-probe
make accounting-currency-rate-provider
make accounting-reports
make accounting-fec
make accounting-fec-validate
make accounting-compare
make accounting-readiness
make accounting-evidence
make accounting-addon-tests ACCOUNTING_TEST_DB=odoo_m13_unusual_balance_unit_20260723_2
make accounting-addon-tests ACCOUNTING_TEST_DB=odoo_m13_replacement_unit_20260723_7
jq empty artifacts/accounting-compat/private/accounting-hygiene-browser-status.json artifacts/accounting-compat/private/replacement-browser-status.json artifacts/accounting-compat/private/readiness-assessment.json artifacts/accounting-compat/private/evidence-index.json
```

The scoped declaration/closing Odoo tests and the broader
`TestRebuildAccountMigration` suite also pass. The latest fresh isolated run
completed with `0` failures and `0` errors, including Accounting Home and
Hygiene routing, operational aggregation, reviewer company isolation,
manager-only refresh, configurable natural-balance rules, current-fiscal-year
scope, unusual-balance drilldown, frontend asset registration and
checksum/main-selection preservation for a native document attachment. The
latest replacement-focused run also covers reviewer access to all
company-scoped expenses, employee provenance privacy, disabled create/edit/
delete architecture, hidden header mutation controls, server-side create/
write/unlink denial and backend asset registration. The suite also includes
the three ECB provider/idempotence/access tests. Module initialization emitted
the existing docutils indentation warnings but no test failure or error. The
disposable unit databases were dropped after validation.

The first document replay after introducing the gate correctly returned
`partial`: `74` attachments belonged to valid expense-generated receipt moves
whose native trace class was not yet allowed. The target resolver was expanded
to that explicit trace class and the repeat replay passed `215/215`. The first
expense repeat after downstream settlement also returned `partial`: `95`
own-account expenses and `97` company payments were already in the later
`paid` state. Stage validation now accepts the expected intermediate state or
that monotonic downstream state; the repeat replay passed `325/325` expenses and
`263/263` attachments.

`ruff` is not installed on the host or long-running Odoo container, so the
repository's devcontainer was used. The changed model and test pass targeted
Ruff with `EM101` ignored. A non-ignored pass found three pre-existing `EM101`
literal-message findings in `review_summary.py`; the two new occurrences were
fixed, while the historical three remain outside this scoped patch.

The host `make user-docs-build` command initially failed because MkDocs was not
installed. A temporary virtual environment was populated from
`requirements-docs.txt`; two direct MkDocs builds then passed. The first
reviewer browser pass also revealed the standard supplier-list `Upload`
control. Adding a QWeb group condition did not alter the rendered inherited
controller, so the final implementation gates that controller state through
the accountant group; the repeat manager and reviewer journeys passed.
The host also has no `ruff` executable, so replacement-role lint ran in the
repository devcontainer and passed. The final replacement-role change was
validated with Python compilation, focused Ruff, XML parsing,
`git diff --check`, a fresh full tagged Odoo suite, a module-only update on the
replacement candidate and the repeated manager/reviewer browser journey.

## Remaining P0/P1 decisions

### P0 — report acceptance

All 38 technical packages await a recorded accountant/stakeholder decision. Required action: Prosper reviews mandatory report semantics and exports; Valentin accepts product scope and the two SASU exclusions. The agent must not self-accept these records.

### P1 — cross-boundary reconciliation policy

The `75` source reconciliation relationships crossing into draft or future endpoints remain review-only. A native partial probe proves the mechanism is available. Required action: accept review-only historical treatment, or authorize a separate draft-endpoint application workflow after accountant/product review.

## Closure rule

Do not close Milestone 13 or authorize production migration until the two decisions above, FEC acceptance, declaration acceptance and final milestone approval are recorded by their named authorities.
