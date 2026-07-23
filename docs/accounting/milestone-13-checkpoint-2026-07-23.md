# Milestone 13 checkpoint - 2026-07-23

Status: **technical rehearsal extended; professional acceptance remains pending**.

This checkpoint supersedes the 2026-07-22 checkpoint. It is not a milestone closure or a production-migration authorization. The remaining P0/P1 gates require Valentin and/or accountant decisions; they must not be accepted by an implementation agent.

## Outcome

The disposable `odoo_rebuild_accounting_test` target provides the broad Milestone 13 rehearsal:

- source-traced historical reconstruction and benchmark parity;
- automatic future ECB reference rates kept separate from historical replay;
- isolated Track B native document, expense, payment, bank, reconciliation, asset, deferral and multi-plan analytic replay;
- distinct Bank Matching and General Reconciliation workbenches;
- canonical report navigation, interactive OCA reports, drill-down and PDF/XLSX exports;
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

- Accounting dashboard and journal cards opened;
- Bank Matching opened the imported transaction history;
- General Reconciliation opened account-grouped residual items;
- the declaration schedule and CA12-E source fields opened;
- the historical annual closing workspace and its controls opened;
- the closing PDF/XLSX wizard produced a `78`-row preview;
- the OCA Trial Balance opened with date, posted/draft, hierarchy, partner, journal and account filters;
- clicking the EUR 1,000 Trial Balance amount drilled into the exact journal item `BQ1000000001`.

Prosper/read-only journey used a disposable browser user assigned only to Unstatic Labs:

- Accounting dashboard opened without create controls;
- Trial Balance filters and interactive report opened;
- Declarations and Closing opened;
- the historical close was visible without a create button;
- no USL Media accounting scope was assigned;
- write access to `account.bank.statement.line` and `account.move.line` raised `AccessError`;
- Bank Matching retained the read-only `View move` route while hiding validate, reset and check-state mutation controls;
- the disposable user was deleted after the walkthrough.

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

## Commands and validation

Passing validation includes:

```text
python3 -m unittest accounting_compat.tests.test_report_evidence accounting_compat.tests.test_fec_preflight
python3 -m py_compile accounting_compat/cli.py accounting_compat/tests/test_report_evidence.py custom-addons/rebuild_account_migration/tests/test_rebuild_account_migration.py
docker compose --profile devcontainer run --rm devcontainer ruff check accounting_compat/tests/test_report_evidence.py custom-addons/rebuild_account_migration/tests/test_rebuild_account_migration.py
git diff --check
make accounting-target-reset
make accounting-target-import
make accounting-target-validate
make accounting-document-regeneration
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
```

The scoped declaration/closing Odoo tests and the broader `TestRebuildAccountMigration` suite also pass. The fresh isolated run included the three ECB provider/idempotence/access tests and completed with exit code `0`; targeted Ruff also passed. Module initialization emitted the existing docutils indentation warnings but no test failure or error.

`ruff` is not installed on the host or long-running Odoo container, so the repository's devcontainer was used. The changed tests pass targeted Ruff validation; a whole-file check of the historical `accounting_compat/cli.py` still reports its pre-existing baseline warnings.

## Remaining P0/P1 decisions

### P0 — report acceptance

All 38 technical packages await a recorded accountant/stakeholder decision. Required action: Prosper reviews mandatory report semantics and exports; Valentin accepts product scope and the two SASU exclusions. The agent must not self-accept these records.

### P1 — cross-boundary reconciliation policy

The `75` source reconciliation relationships crossing into draft or future endpoints remain review-only. A native partial probe proves the mechanism is available. Required action: accept review-only historical treatment, or authorize a separate draft-endpoint application workflow after accountant/product review.

## Closure rule

Do not close Milestone 13 or authorize production migration until the two decisions above, FEC acceptance, declaration acceptance and final milestone approval are recorded by their named authorities.
