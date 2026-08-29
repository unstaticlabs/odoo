# Accounting compatibility harness

## Purpose

The accounting compatibility harness turns an Odoo Online backup into repeatable technical evidence. The source PostgreSQL dump remains a source format only; extraction and comparison use PostgreSQL and the target Odoo ORM boundary rather than parsing business data from SQL text.

## Commands

```bash
make accounting-source-package-validate
make accounting-source-validate
make accounting-source-restore
make accounting-source-inspect
make accounting-attachment-audit
make accounting-extract
make accounting-failure-tests
make accounting-validation-exact-reset
make accounting-validation-exact-import
make accounting-validation-exact-validate
make accounting-validation-exact-idempotence
make accounting-validation-exact-failure-tests
make accounting-validation-native-reset
make accounting-validation-native-expenses
make accounting-validation-native-documents
make accounting-validation-native-assets
make accounting-validation-native-deferrals
make accounting-validation-native-expense-settlement
make accounting-validation-native-document-settlement
make accounting-validation-native-general-reconciliation
make accounting-validation-native-bank-categorization
make accounting-validation-native-bank-external
make accounting-validation-native-analytics
make accounting-currency-rate-provider
make accounting-reports
make accounting-fec
make accounting-fec-preflight
make accounting-fec-validate
make accounting-compare
make accounting-readiness
make accounting-evidence
```

The top-level command is:

```bash
make accounting-compat
```

The current implementation runs a full clean rehearsal:

```text
source package validation
→ non-destructive source-package failure guardrails
→ isolated source restore
→ source inspection and controls
→ complete filestore and Accounting attachment audit
→ private accounting extract
→ disposable target reset
→ exact posted-ledger replay through the target ORM
→ target controls
→ repeated target import idempotence guardrail
→ rollback-only target conflict failure guardrail
→ isolated native draft-generation checks for candidate-ready non-posted source moves
→ dedicated Track B database reset
→ native expense approval, refusal and posting reconstruction for 2025-10-01 through 2026-06-30
→ native business-document reconstruction and posting for 2025-10-01 through 2026-06-30
→ native asset depreciation and deferred-expense schedules
→ native bank matching and employee-expense settlement for the current-period expense slice
→ native bank matching for current-period commercial documents
→ native General Reconciliation and full bank categorization
→ cross-stage multi-plan analytic reconciliation
→ rollback-only native reconciliation probe for generated draft endpoints
→ live ECB reference-rate retrieval and idempotence check
→ Odoo-facing report view, drill-down and export-wizard checks
→ FEC test-mode export
→ local FEC structural preflight
→ official FEC validator status gate
→ source-target comparison
→ Milestone 13 readiness assessment
→ evidence index
```

The normal `odoo_dev` database is reset only by explicit development rebuild
stages. Validation commands use their own temporary databases, so ordinary
module updates do not require additional development database names.
Exact reconstruction uses `odoo_saas_19_3_validation_exact`. Native-engine
Track B proof uses
`odoo_saas_19_3_validation_native`, so recomputed current-period documents
cannot alter the exact historical replay.

`make accounting-failure-tests` validates six non-destructive source-package guardrails: missing source directory, missing `dump.sql`, missing filestore directory, filestore path that is not a directory, unsupported dump format and a minimal valid plain-SQL source package. These tests use temporary private packages under `artifacts/accounting-compat/private/failure-tests/` and never mutate the real source backup.

## Minimal Imported-Data Pipeline

Use this shorter sequence when the immediate goal is to open the imported accounting data in Odoo:

```bash
make oca-addons-sync
make accounting-source-restore
make accounting-extract
make accounting-validation-exact-reset
make accounting-validation-exact-import
make accounting-validation-exact-validate
make accounting-reports
```

Run these commands from the host shell, not from inside the Dev Container. The harness currently calls `docker compose`; the Dev Container runs Odoo but does not include the Docker CLI.

`make oca-addons-sync` fetches pinned OCA 19.0 add-ons into ignored local directories. The target reset stage requires these add-ons because the product and validation databases use maintained OCA reporting, bank-statement and reconciliation components. MIS is no longer installed or exposed: the canonical custom report workbench covers the retained USL reporting requirements without a second report engine.

The sequence has two live PostgreSQL services:

| Service | Role |
| --- | --- |
| `accounting-source-db` | Isolated PostgreSQL service containing the restored Odoo Online backup. |
| `db` | Normal Odoo PostgreSQL service containing the disposable target database. |

The important databases are:

| Database | Role |
| --- | --- |
| `odoo_online_source_saas_19_3` | Read-only source database restored from `usl-online-dump/dump.sql`. |
| `odoo_saas_19_3_validation_exact` | Clean target Odoo database rebuilt from the extracted snapshot. |
| `odoo_saas_19_3_validation_native` | Separate clean target for native current-period business-document recomputation. |
| `odoo_dev` | Disposable SaaS developer/QA product database. A clean reset and import reproduce the complete source Accounting state here. |

Stage dependencies:

| Command | Reads | Writes | Why it must run here |
| --- | --- | --- | --- |
| `make accounting-source-restore` | `usl-online-dump/dump.sql`, `usl-online-dump/filestore/` | `odoo_online_source_saas_19_3` in `accounting-source-db`; source restore status artifacts | It creates the source database that every later source read depends on. |
| `make accounting-attachment-audit` | Restored source attachment metadata, complete source filestore and reconstructed `odoo_dev` | Private source/target integrity, relationship, chatter and exclusion evidence | It verifies every referenced source blob, reads every imported target binary through Odoo storage and blocks material Accounting omissions. See [Accounting attachment reconstruction](attachment-reconstruction.md). |
| `make accounting-extract` | Restored source database through read-only SQL | Private canonical snapshot and extraction artifacts | It converts the physical SaaS database into the durable transfer package used by the target importer. The accepted `csv_v1` contract is bound to the source-dump SHA-256 and records a SHA-256 for every exported file; Parquet is deliberately not a second supported migration path. |
| `make accounting-validation-exact-reset` | Compose target PostgreSQL service `db` | Fresh `odoo_saas_19_3_validation_exact` database | It removes old target state so the import is deterministic and not mixed with previous attempts. |
| `make accounting-validation-exact-import` | Canonical snapshot; source database for source metadata; clean target database | Target Odoo records and source-trace metadata | It reconstructs the complete source Accounting state through the target Odoo ORM. |
| `make accounting-validation-exact-validate` | Imported target database | Target validation artifacts and discrepancy records | It proves the imported target is internally consistent before report checks run. |
| `make accounting-validation-native-reset` | Installed target/OCA add-ons | Fresh `odoo_saas_19_3_validation_native` database | It creates a clean, neutralized proof environment without touching the exact replay target. |
| `make accounting-validation-native-expenses` | Read-only restored source expense/business fields, verified source filestore binaries and Track B configuration | Native employees, products, expenses, company payments, employee receipts and source-traced receipt attachments in the Track B database; private proof artifact | It uses normal expense submission, approval/refusal, receipt preparation and payment posting APIs, then compares every expense and generated accounting effect to source. It verifies every source receipt checksum/size and preserves the source-designated main attachment. Run it before the document stage so expense-generated receipts can be reused. |
| `make accounting-validation-native-documents` | Read-only restored source business fields, verified source filestore binaries and Track B configuration | Native posted invoices, bills, supplier refunds and receipts with source-traced document attachments in the Track B database; private proof artifact | It calls normal Odoo draft creation and `action_post`, compares headers, due dates and per-account effects to source, then verifies every business-document binary and source-designated main attachment. |
| `make accounting-validation-native-assets` | Read-only source asset master data, depreciation schedules and Track B configuration | OCA assets, profiles, depreciation-board lines and native posted depreciation entries; private proof artifact | It seeds the source business schedule into maintained OCA `account_asset_management`, lets OCA create and post every in-period entry, leaves future schedule lines unposted and compares date, amount and account effects exactly. |
| `make accounting-validation-native-deferrals` | Native Track B documents plus read-only source deferred relationships and schedule decisions | Operational deferred-expense records, posted recognition entries, future schedule lines and a traced opening boundary entry; private proof artifact | It creates a focused schedule workflow backed by standard `account.move` posting, validates every posted and future source relationship, and keeps the reviewer surface read-only. |
| `make accounting-validation-native-expense-settlement` | Native Track B expenses plus the read-only source bank/reconciliation graph | Native bank transactions, OCA-generated partial reconciliations, paid company payments and paid employee expenses; private proof artifact | It runs after expenses/documents, replays source operator allocations chronologically, and keeps mixed-transfer non-expense balances explicit for General Reconciliation. |
| `make accounting-validation-native-document-settlement` | Native Track B documents, expense settlement and the read-only source bank/reconciliation graph | Native commercial-document bank transactions, exact OCA-generated partial reconciliations and bounded residuals for General Reconciliation; private proof artifact | It reuses overlapping expense bank lines, creates the remaining bank transactions, applies every direct document/bank edge and validates company/transaction-currency partials plus due-line residuals. |
| `make accounting-validation-native-general-reconciliation` | Native Track B documents and direct bank settlement plus the read-only source non-bank reconciliation graph | Native posted manual entries, document netting, General Reconciliation partials and traced Odoo/OCA exchange-difference moves; private proof artifact | It posts shareholder-current-account and clearing entries through standard journal APIs, reconciles them with documents, and classifies native timing and one-cent exchange differences without copying finalized source journal rows. |
| `make accounting-validation-native-bank-categorization` | Track B through General Reconciliation plus source bank transactions without external partial endpoints | Native OCA-categorized interest, fees, transfers and account allocations plus source-open transactions retained for review; private proof artifact | It replays the operator's account, partner, analytic and currency inputs for direct categorizations and deliberately leaves source-unreconciled transactions open. |
| `make accounting-validation-native-bank-external` | Track B through direct categorization plus the remaining source bank/external-reconciliation graph | Exact multi-line OCA bank categorizations, posted payroll/tax/clearing entries, native General Reconciliation and explicit cutoff boundaries; private proof artifact | It completes all current-period bank transactions while keeping draft/post-cutoff documents as prepayments and identifying five aggregates from earlier bounded settlement stages that still need refinement. |
| `make accounting-validation-native-analytics` | Completed Track B native stages plus source expense decisions, finalized analytic distributions and analytic lines | Explicit analytic-correction audit records and direct source/target reconciliation across both analytic plans; private proof artifact | It runs last, after every posting stage. Native business objects remain the accounting input; the stage applies only source post-posting analytic classifications through Odoo's supported distribution write, then compares both theoretical allocations and actual analytic lines. |
| `make accounting-dev-reset` | Current target source and pinned module set | Empty, disposable `odoo_dev` product database | It initializes the supported Community/OCA/USL dependency closure without source business data. It never opens the Online dump directly with target code. |
| `make accounting-dev-import` | Clean replacement database plus the read-only restored Online source | Complete source-faithful Accounting product state, including native expenses and their evidence | It blocks unless every source expense passes field-by-field native validation. The gate covers workflow state, monetary fields, account move and line links, company, employee, department, manager, payment method, analytics, split-expense history, notification/approval history and all direct expense attachments, including URL evidence. |
| `make accounting-dev-validate` | Source database and completed replacement candidate | Historical parity, current-period difference decomposition and promotion-gate evidence | It requires exact benchmark parity, balanced/unique native state and a classification for every current journal and account-balance difference. A classified difference can still require professional acceptance. |
| `make accounting-currency-rate-provider` | Imported target company configuration and the official ECB publication-history XML feed | Native `res.currency.rate` rows for every missing published day, plus provider, retrieval, cron and idempotence evidence | It runs after restored rates are loaded. It never replaces a restored or manager-entered rate, and its reference rows remain separate from transaction-specific bank or platform conversion evidence. |
| `make accounting-reports` | Imported and validated target database | Report preview/export/drill-down evidence artifacts | It proves the user-facing report surfaces can generate and trace values. |

Do not stop `accounting-source-db` after `accounting-source-restore`. Later stages still query it for source metadata, snapshot dates, controls and comparisons. If a later stage fails with `service "accounting-source-db" is not running`, restart it:

```bash
docker compose --profile accounting-compat up -d accounting-source-db
```

Then rerun the failed stage. If several stages have failed or the state is unclear, rerun the minimal pipeline from `make accounting-source-restore`.

## Source restore

Default source:

```text
usl-online-dump/dump.sql
usl-online-dump/filestore/
```

Default source database:

```text
odoo_online_source_saas_19_3
```

The restore stage:

- detects PostgreSQL plain SQL versus custom `PGDMP`;
- computes SHA-256 and source package inventory;
- starts the Compose PostgreSQL service with `pgvector/pgvector:pg16-bookworm`;
- recreates only the isolated source database;
- pre-creates `pg_trgm`, `unaccent` and `vector`;
- marks `public.unaccent(text)` and `public.unaccent(regdictionary,text)` immutable before replaying the dump so exported expression indexes restore under PostgreSQL 16;
- creates the read-only extraction role `accounting_source_ro`;
- writes a private strict restore log.

The `unaccent` volatility change is a physical restore compatibility shim. It is not a business-data transformation.

## Private artifacts

Production-derived outputs are ignored by Git:

```text
accounting_compat/private/
artifacts/accounting-compat/private/
```

Important private files:

```text
artifacts/accounting-compat/private/source-package-validation.json
artifacts/accounting-compat/private/source-restore-status.json
artifacts/accounting-compat/private/source-manifest.json
artifacts/accounting-compat/private/source-controls.json
artifacts/accounting-compat/private/report-catalogue-v1.json
artifacts/accounting-compat/private/parity-matrix-v1.json
artifacts/accounting-compat/private/failure-tests-status.json
artifacts/accounting-compat/private/validation-exact-import-status.json
artifacts/accounting-compat/private/validation-exact-validate-status.json
artifacts/accounting-compat/private/validation-exact-idempotence-status.json
artifacts/accounting-compat/private/validation-exact-failure-tests-status.json
artifacts/accounting-compat/private/validation-native-reset-status.json
artifacts/accounting-compat/private/validation-native-expenses-status.json
artifacts/accounting-compat/private/validation-native-documents-status.json
artifacts/accounting-compat/private/validation-native-expense-settlement-status.json
artifacts/accounting-compat/private/validation-native-document-settlement-status.json
artifacts/accounting-compat/private/validation-native-general-reconciliation-status.json
artifacts/accounting-compat/private/validation-native-bank-categorization-status.json
artifacts/accounting-compat/private/validation-native-bank-external-status.json
artifacts/accounting-compat/private/currency-rate-provider-status.json
artifacts/accounting-compat/private/currency-rate-provider-browser-status.json
artifacts/accounting-compat/private/reports-status.json
artifacts/accounting-compat/private/fec-status.json
artifacts/accounting-compat/private/fec-structural-preflight.json
artifacts/accounting-compat/private/fec-validation-status.json
artifacts/accounting-compat/private/fec-dgfip-source-validation/
artifacts/accounting-compat/private/vat-benchmark-investigation-2025-09-30.json
artifacts/accounting-compat/private/compare-status.json
artifacts/accounting-compat/private/readiness-assessment.json
artifacts/accounting-compat/private/readiness-assessment.md
artifacts/accounting-compat/private/evidence-index.json
accounting_compat/private/snapshots/<snapshot-id>/
```

## Current validated replay scope

As of the clean 18 August 2026 rehearsal against source dump `395cc8b950b592035fed41dedf0072f3487e18f10b4010f939331a5e5b51e69f`, the importer materializes the complete scoped source-company population as native Odoo records:

- `5,401` source `account.move` records: `5,190` posted, `209` draft and `2` cancelled;
- `12,989` source move lines, including source display/note lines without an account;
- `113` native payments, including historical workflow payments whose source `move_id` is null;
- `3,087` bank statement lines;
- `2,883` partial and `1,354` source full reconciliations, with zero missing endpoints;
- `1,937` historical currency rates;
- `982` analytic lines;
- `3` assets and `91` depreciation schedule lines;
- `110` deferred-schedule relations, all linked to native source-traced moves;
- every Accounting-scope attachment in exact replay, with zero missing files,
  checksum mismatches, duplicate traces or unmapped targets.

The target contains no move, move-line, payment, document-regeneration or reconciliation review models. Draft and cancelled documents remain native `account.move` records; no-entry payments remain native immutable `account.payment` records; reconciliation links point directly to native journal items. Posted benchmark totals and sequence identity remain exact.

For `175` native draft moves whose source SQL name is `NULL`, validation
compares Odoo's standard draft sentinel `/`. This is the only representation
normalization in the complete move identity comparison; it does not change a
posted number, sequence, state, date, amount or relationship.

## Track B native business-document proof

Track B is deliberately separate from the exact replay. Three approaches were considered:

1. create recomputed documents inside `odoo_saas_19_3_validation_exact`, which would mix generated current-period effects with the historical-truth baseline;
2. create a dedicated clean database with the same Community/OCA configuration and replay source business fields through native Odoo posting;
3. calculate expected taxes, currencies and due lines in a custom migration engine and write the resulting journal entries.

Option 2 is selected. It preserves the exact target as an audit baseline and proves the target product through Odoo's own engines. Option 1 makes later parity controls ambiguous. Option 3 would duplicate the accounting engine and would be another exact-line importer rather than a native workflow proof.

`make accounting-validation-native-expenses` first reconstructs all `325` source `hr.expense` records dated `2025-10-01` through `2026-06-30`. Three credible treatments were compared:

1. replay employee, product and expense business fields through native `hr.expense` submission, approval/refusal, receipt and company-payment APIs in the isolated Track B database;
2. preserve only finalized source expense ledger/payment rows in the exact target, which retains historical accounting truth but does not prove the replacement expense workflow;
3. calculate expense accounting in a custom migration engine or duplicate the source's Enterprise implementation.

Option 1 is selected for product proof, while the existing exact-target ledger remains the historical-truth baseline. Option 2 alone cannot prove native daily use. Option 3 would duplicate accounting logic and create an upgrade-sensitive parallel workflow.

The clean expense run validates `325/325` expenses and all `176` generated moves with `0` blocked cases and `0` mismatches. The source state distribution is `192` paid, `125` approved, `3` draft and `5` refused; payment modes are `97` company-account and `228` own-account. Native reconstruction creates `97` company payments and `79` grouped employee receipts for the `95` paid own-account expenses. It preserves accounts, taxes, analytics, employees, vendors, dates, quantities, historical unit prices, currencies and all monetary fields. A repeated run after document reconstruction reuses all `325` expenses, `97` payments and `79` receipts without changing payment-method identities or creating duplicates.

At this expense-document checkpoint, state transitions that depend on settlement remain explicit rather than forged: the `97` source-reconciled company payments stay in process until their bank transactions are matched, and the `95` source-paid own-account expenses stay posted until employee reimbursement replay. A legacy source destination-payable hint is retained as classification evidence; the current native company-expense payment's posted outstanding account is the accounting effect validated to source.

`make accounting-validation-native-documents` then reconstructs all `284` posted source business documents for the same period: `36` customer invoices, `161` vendor bills, `3` supplier refunds and `84` purchase receipts. It reuses the `79` receipts already produced by the expense workflow and creates the remaining `205` documents from commercial lines, accounts, quantities, unit prices, discounts, taxes, analytic distributions, fiscal positions, payment terms, partners, dates and the source transaction currency rate, then calls normal `action_post`. The latest clean run validates `284/284`, with `0` blocked cases and `0` mismatches. Coverage is `170` EUR and `114` USD documents.

Validation compares untaxed, tax and total amounts, due dates, and debit/credit/balance/amount-currency aggregates by source account. Finalized source journal lines are never passed to document creation. After expense-generated receipts are reused, two remaining source documents whose stored tax/base totals cannot be derived from their price fields are replayed through Odoo's native `extra_tax_data` manual-tax mechanism. That path is guarded to one unambiguous taxable product line and recorded as `supported_native_manual_tax_override`; ambiguous multi-line allocations remain mismatches rather than guesses.

`make accounting-validation-native-expense-settlement` then proves the bank/payment transition for this expense slice. Three credible approaches were compared:

1. create native `account.bank.statement.line` records and replay the source operator's selected current-expense candidates through maintained OCA `reconcile_bank_line()` behavior;
2. copy the source statement moves and finalized partial-reconciliation rows, which would preserve history but would not prove the target bank-matching product;
3. implement a custom matching/reimbursement engine, which would duplicate Odoo/OCA accounting logic and create an upgrade-sensitive parallel workflow.

Option 1 is selected. The exact target continues to provide historical truth, while Track B demonstrates native behavior from bank transaction through reconciliation. Option 2 remains appropriate only for the exact replay. Option 3 is rejected because OCA already supplies the maintained Community reconciliation engine.

The bounded expense-settlement run creates `106` native bank transactions: `98` company-account card/bank lines and `8` grouped employee reimbursement transfers. It replays `181` source partial-reconciliation choices against `176` native outstanding/payable lines, including one company payment split across two bank transactions. All source partial amounts match the OCA-generated target partials, all `97` company payments become paid, and all `95` employee-paid expenses become paid. OCA's native exact-reference behavior automatically matches one unambiguous €46.50 line at statement creation; the replay detects and traces that native result instead of adding a duplicate candidate.

All `98` company-account bank lines and `2` reimbursement transfers contain only current-period expense allocations. The other `6` reimbursement transfers also settle older or non-expense shareholder-account items in the source. Track B replays the source edges backed by current-period native expenses and preserves all `19` outside-only source counterpart lines through the same OCA reconciliation payload, including exact account, partner, currency, company amount, transaction amount and analytic distribution. Only source lines themselves split across perimeters retain an aggregate residual. Validation derives the outside balance from all bank counterpart lines minus the traced current-expense partials, so it remains stable after downstream reconciliation. This retains source detail without inventing an endpoint or copying finalized journal rows.

`make accounting-validation-native-document-settlement` next proves the direct bank transition for all current-period commercial documents. Three credible approaches were compared:

1. create standard bank transactions and replay the source operator's exact document candidates through OCA Bank Matching, using supported transaction countervalues plus a narrow custom-rate adapter where OCA would otherwise replace the historical company/foreign amount pair;
2. copy finalized source bank journal items and partial-reconciliation rows into the exact target;
3. implement a project-specific matching and foreign-exchange engine.

Option 1 is selected. It preserves the native statement/OCA workflow and uses Odoo's supported foreign-currency countervalue on `34` newly created foreign-journal transactions whose historical EUR countervalue no longer equals the current date rate. The adapter removes only OCA's proposed exchange candidate and retains the source operator's exact company/transaction-currency candidate pair; OCA still creates the bank journal items and partial reconciliations. Option 2 remains the historical truth path but cannot prove an operational workflow. Option 3 is rejected because it would duplicate maintained Odoo/OCA matching behavior.

The clean stage covers `233` source bank transactions and `339` direct document/bank partial-reconciliation edges against `241` native receivable/payable lines. It reuses `8` bank lines and `83` partials already proved by expense settlement, creates the remaining `225` bank lines, creates `256` additional partials, and extends one prior mixed employee transfer through native General Reconciliation. All `339` source company-currency and transaction-currency partial amounts and endpoints match; all `241` due-line transaction-currency residuals match; a rerun reuses all `233` bank lines and `339` document edges with no duplicate trace. `170` bank lines contain only current document allocations. Across the other `63`, OCA preserves `48` outside-only source counterpart lines exactly; only source lines themselves split across perimeters retain a bounded residual.

`make accounting-validation-native-general-reconciliation` then proves every current-period non-bank document reconciliation. Three credible approaches were compared:

1. post the `CCAVV` shareholder-current-account and `MISC` compensation/clearing entries from journal-entry inputs, then use native General Reconciliation for those document edges and document-to-document netting while retaining Odoo/OCA-generated exchange differences;
2. copy the finalized source manual-entry, partial-reconciliation and exchange-difference rows, which preserves history but does not prove the target operational workflow;
3. implement project-specific reconciliation and exchange-difference engines.

Option 1 is selected. Standard draft `account.move` creation and `action_post` prove the manual-entry workflow, and native `reconcile()` behavior proves the reconciliation transition. Option 2 remains the historical-truth path in the exact target. Option 3 is rejected because it would duplicate maintained Odoo/OCA accounting behavior.

The clean run covers all `111` source non-bank partial reconciliations and their `114` current-document endpoints. It posts `21` manual entries with `72` exact accounting-input lines: `18` reconciliation-edge entries plus `3` standalone source operator entries whose balanced accounting effect is not owned by a downstream bank stage. It creates `68` manual-entry/document partials plus `3` document-netting partials and traces all `40` native exchange-difference partials. All manual moves are posted and balanced, all source transaction-currency partial amounts and endpoints match, and the final payment-state distribution exactly equals source: `159` paid and `2` reversed vendor bills, `84` paid purchase receipts, `1` paid and `2` partially paid supplier refunds, and `36` paid customer invoices. Odoo's native reconciliation produces two bounded one-cent company-currency differences; one case also has an extra one-cent exchange segment. These are retained as explicit rounding evidence rather than rewriting posted entries. A rerun creates nothing and reuses all `21` moves, `71` input partials and `40` exchange partials without duplicate traces.

`make accounting-validation-native-bank-categorization` next covers every source bank transaction that has no external partial-reconciliation endpoint. Three credible approaches were compared:

1. recreate each bank transaction and replay the source operator's account, partner, analytic and transaction-currency categorization through OCA Bank Matching, while leaving source-unreconciled transactions open;
2. copy finalized source bank journal lines, which preserves history but does not prove operational categorization;
3. write counterpart journal lines directly through a project-specific categorization engine.

Option 1 is selected. The clean run creates `1,415` additional bank transactions, categorizes `1,229` through OCA and retains the source's `186` open transactions as open. The categorized population consists of interest, bank fees, internal-transfer allocations and bounded payable, shareholder, corporate-tax and investment-account allocations; exactly `5` carry analytic distributions. `908` foreign-journal transactions use Odoo's supported explicit company-currency countervalue while retaining the source journal-currency amount. Every bank header, liquidity effect and categorized counterpart account/partner/currency/analytic effect matches; all moves balance; there are no duplicate traces. A rerun creates or categorizes nothing and reuses all `1,415` bank lines and `1,229` categorizations.

`make accounting-validation-native-bank-external` then covers the final `95` current-period bank transactions and their `125` source counterpart lines. Three credible approaches were compared:

1. reconstruct every bank move through OCA, post source payroll/tax/clearing inputs as standard manual journal entries, and reconcile only valid posted endpoints;
2. copy finalized source bank and endpoint journal lines, which preserves history but does not prove the replacement workflow;
3. force draft or post-cutoff document partials into the current graph, which would misstate Odoo's posting and period-cutoff rules.

Option 1 is selected. The clean run creates all `95` bank transactions and `125` exact account/partner/currency counterpart lines, posts or reuses `17` manual endpoint moves with `151` balanced lines and `46` analytic allocations, creates or reuses `75` native input partials, and traces `12` Odoo-generated exchange partials. Five input partials reuse exact source counterparts preserved by the expense/document settlement stages; the associated USD relationship also reuses its native exchange edge. When source account `471000` is both the company suspense account and the exact source allocation, the stage temporarily assigns an empty untraced `TBSUSP` account to the relevant source bank journal so OCA can perform a real categorization transition, then restores `471000`. Validation requires the staging account to have zero lines and zero balance before and after the pass. This brings native current-period bank coverage to `1,841/1,841`. A rerun creates nothing and reuses all `95` categorizations, `17` manual moves, `75` input partials and `12` exchange partials.

The remaining `48` source relationships are boundaries, not missing bank transactions: `37` draft-document edges and `2` draft exchange-entry edges remain open prepayments, while `9` receipts against three July 1 customer invoices remain post-cutoff prepayments. The five formerly bounded cross-bank edges now reconcile through exact traced counterparts, so no `preexisting_bounded_bank_aggregate` or `exchange_of_bounded_input` classification remains. No draft document is posted or reconciled to manufacture parity.

`make accounting-validation-native-assets` proves the native fixed-asset workflow. Three credible approaches were compared:

1. use maintained OCA `account_asset_management`, seed its native depreciation board from the source business schedule and let OCA post each due entry;
2. recompute a fresh OCA schedule from acquisition values, which changes historical monthly amounts because the source already contains imported depreciation and prorata decisions;
3. copy the finalized source journal entries or keep only the read-only `rebuild.account.asset` evidence surface.

Option 1 is selected. It preserves the operator-facing OCA asset lifecycle and the source business decisions without copying finalized journal rows. The clean run creates `3` native assets, `2` account-specific profiles and `91` depreciation-board lines. OCA posts the `28` source-period depreciation entries; every date, amount and account total matches. The `63` future source schedule rows remain unposted native lines. A rerun creates nothing and reuses all `3` assets, `91` schedule lines and `28` moves. The manager browser journey opens the three assets and their posted/future board actions. The reviewer journey sees the same assets and posted move links but no create, recompute, confirm or reverse controls; server ACLs and a combined-view regression test enforce the boundary.

`make accounting-validation-native-deferrals` proves the operational deferred-expense workflow. Three credible approaches were compared:

1. add a focused schedule model whose due lines post standard balanced `account.move` entries through `action_post`;
2. adopt or port OCA `account_spread_cost_revenue`;
3. retain only the existing imported schedule evidence or copy finalized source journal rows.

Option 1 is selected because no maintained OCA 19.0 deferral module is available in the pinned add-on set; the spread module is available only on older OCA branches and a milestone-time port would be migration-sensitive. The evidence-only alternative cannot support daily scheduling or controlled posting. The clean replay creates `5` deferred-expense records with `82` schedule lines, reuses/posts `34` current-period moves, retains `48` future lines, and represents one opening-boundary reversal. Every date, amount, account and analytic distribution matches, and a rerun reuses all records without duplication. The manager can create schedules and post due or individual lines. The reviewer can inspect schedules and linked entries but has no create or post control.

`make accounting-validation-native-analytics` then runs after all Track B posting stages. Two materially different source values exist for `29` expense lines: the business-time expense distribution and a later finalized journal-item classification. Three treatments were compared:

1. replay the expense workflow from its business inputs, then apply only the source's explicit post-posting analytic correction through Odoo's supported analytic-distribution write and retain a read-only audit record;
2. pass the finalized journal-item distribution into expense creation, which would erase the distinction between business input and later classification;
3. copy analytic lines directly, which would bypass Odoo's allocation engine.

Option 1 is selected. The audit represents all `29` corrections and is idempotent. Across `13` source analytic accounts, source and target move-line allocation totals match exactly, source and target actual analytic-line totals match exactly, `324` directly traced analytic lines have no mismatch, and no analytic line is unmapped. Odoo's per-line currency rounding leaves a theoretical `+0.01/-0.01` pair between two analytic accounts; this is within company-currency precision and the actual analytic-line totals reconcile exactly to source. The browser journey verifies all `621` target analytic lines in the native list, multi-plan `Projet` and `Epic` values, pivot/XLSX and graph views, plus a read-only correction audit for manager and reviewer.

This proves current-period native expense approval/refusal/posting, invoice/bill/refund/receipt posting, expense-related bank matching, partial reimbursement allocation, direct commercial-document bank matching, non-bank document netting/manual-entry reconciliation, exchange-difference generation, all `1,841` bank transactions, native asset depreciation, operational deferrals, multi-plan analytics and final current-document/payment state. The selected design improves the earlier stages rather than creating duplicate manual moves; copying finalized source rows and direct journal-line surgery remain rejected because they would not prove the operational workflow. Remaining Track B work is deliberate draft/post-cutoff acceptance, undo behavior and closing acceptance.

### Product expense reconstruction gate

The product import validates the complete source expense population independently
of the bounded native-workflow rehearsal. A successful
`make accounting-dev-import` requires all source `hr.expense` rows to exist as
native expenses with no blocked or mismatched record. Validation compares the
business fields and relationships used by daily work, including employee and
manager identity, department, workflow and approval history, payment method,
linked journal entry and journal items, taxes, currency amounts, analytic
distribution, split-expense origin and source evidence.

Direct expense evidence is part of the blocking gate. Binary files are verified
against the restored filestore checksum and size; URL attachments retain their
native type and URL instead of being discarded as non-binary records. Chatter
attachments retain their source message relationship metadata. Notification
The early Accounting attachment pass creates temporary source-aware file links;
the final Collaboration stage replaces its generated attachment notes with the
original source messages, tracking history, mapped internal followers and
verified attachment relationships. Delivery notifications and sent queue rows
are deliberately excluded so reconstruction cannot resend historical mail.

The import is safe to repeat. Existing source-traced expenses and evidence are
revalidated and reused. Payments already linked to expenses and depreciation
lines already linked to assets are treated as native immutable records: an
identical relationship is left untouched, while a conflicting source/target
identity blocks the replay instead of bypassing Odoo or OCA write protections.

## Product replacement candidate

The product candidate is reconstructed from an empty target through the target
ORM. Three approaches were considered: opening the Enterprise dump directly,
promoting the bounded native-workflow proof database, or replaying the complete
source truth into a clean Community/OCA/USL target. Direct opening is
unsupported across editions, while the bounded proof database intentionally
does not contain the complete source. The selected clean replay keeps one
canonical product flow and leaves Track B as isolated engine evidence.

The current source snapshot contains `5,401` moves, `12,989` move lines, `412`
expenses and `3` assets. `make accounting-dev-import` preserves each source
move identity, imports every expense as a native record, restores its direct
evidence and creates no duplicate accounting representation. Historical
sequence gaps and chronology decreases already present in the Online source are
preserved and reported; the importer does not silently resequence history.

The exact target validates the posted replay through the `2026-08-18` source
snapshot. Source and target have the same `13` sequence gaps and `128`
sequence-ordered date decreases. This proves exact preservation; the
accountant-owned P2 explanation/acceptance gate remains explicit.

The final product database is an exact source-state reconstruction; Track B is
an isolated native-engine proof and is not mixed into the candidate. The
historical Odoo 19 `odoo_dev` run beginning at `2026-07-25T07:26:43Z` completed import at
`07:29:37Z` and final validation at `07:50:03Z`. For
`2025-10-01` through `2026-06-30`, source and product contain `2,694` posted
moves and `6,319` journal items with debit and credit both
EUR `1,708,270.52`; account and journal differences are zero.

The isolated Track B run completed every native stage for the same period:
`325` expenses, `284` commercial documents, `3` assets, deferrals, document
and expense settlements, general reconciliation, direct bank transactions,
external-endpoint reconciliation and analytic distributions. It is supporting
engine evidence, not a hybrid product candidate or an alternative product
database.

`make accounting-validation-exact-validate` also proves:

- no unbalanced imported posted moves;
- no duplicate source traces for native moves, move lines, payments, partial/full reconciliations, bank statement lines, source report catalogue records, analytic plans/accounts/lines, attachments, taxes, tax tags or assets;
- preserved USL lock dates, with a rollback-only protected-write check blocked by Odoo for a locked posted move dated `2024-01-10`.

`make accounting-validation-exact-idempotence` proves accidental repeated-import safety. It snapshots native source-traced accounting counts and posted-ledger totals, reruns the complete import, reruns parity validation and requires the same accounting signature. The additional import-run audit row is expected and is not an accounting consequence.

`make accounting-validation-exact-failure-tests` checks duplicate trace invariants on the native move, move-line, payment and reconciliation models. Review-model fallbacks are not part of the accepted schema.

## Native reference-rate automation

There is one runtime currency-rate truth: Odoo's native
`res.currency.rate`. Restored source rows, manual manager entries and automated
ECB rows carry provenance on that same model; there is no parallel historical
rate implementation. Three credible alternatives were assessed:

1. rely on the absent Enterprise live-currency module;
2. install a maintained OCA 19 currency updater;
3. add a focused adapter that writes official ECB reference rates through Odoo's native `res.currency.rate` model.

The checked Community/OCA dependency set contains no deployable automatic
updater, so option 3 is selected. The adapter establishes an explicit coverage
boundary after the latest restored or manual foreign-currency rate. A manager
run reads the official ECB history and fills every missing published business
day through the latest reference date. The daily cron reads the recent
90-day history, so an interrupted run is recovered automatically. Restored and
manual rows are immutable; an existing ECB row is corrected only if the
official value changed.

The parser disables entity and network resolution, validates unique dates and
positive finite rates, calculates cross-rates for a non-EUR company currency,
limits response size and stores `ecb` plus the retrieval timestamp on native
rate rows. EUR is USL's company currency and remains Odoo's implicit rate
`1.0`; automation only creates rows for active foreign currencies.

Accounting Managers configure and run it under:

```text
Accounting > Configuration > Currency Rate Automation
```

The daily cron is enabled and scheduled after the ECB's normal publication
window. The long-running Compose service has one cron thread; init, test and
reconstruction helpers keep cron disabled. ECB rates are reference
information: when a bank, card processor or platform conversion defines a
transaction, preserve that actual conversion instead of replacing it with the
reference rate.

`make accounting-currency-rate-provider` performs two live updates on
`odoo_validation_exact`, proves the second creates or corrects no row, checks
the daily cron and provider trace, and verifies the restored source-rate count
is unchanged.

## Current Odoo-facing report views

The `rebuild_account_migration` addon exposes read-only report views under Accounting > Reporting and Review > Rebuild Evidence:

- Imported Trial Balance;
- Imported General Ledger;
- Imported Journal Report;
- Imported Partner Ledger;
- Imported Open Items;
- Imported Aged Receivable;
- Imported Aged Payable;
- Imported Balance Sheet;
- Imported Profit and Loss;
- Imported Bank Reconciliation;
- Imported Currency Gain/Loss;
- Imported Cash Flow Statement;
- Imported Executive Summary;
- Imported Analytic Distribution;
- Imported French Annual Statements, with Bilan Actif, Bilan Passif, Compte de résultat, SIG and CAF lines;
- Imported VAT and Tax Report;
- Imported Tax Report by Account then Tax;
- Imported Tax Report by Tax then Account;
- Imported EC Sales List;
- Imported OSS Sales;
- Imported OSS Imports;
- Fixed Asset Register;
- Fixed Asset Register by Account;
- Imported Depreciation Schedule;
- Imported Deferred Expense and Revenue Schedule;
- Imported French Tax Package Mapping for 2065-SD, 2033-SD and 3517-S-SD/CA12 review values;
- Source Accounting Report Catalogue;
- Review Decisions for accountant and stakeholder acceptance gates;
- External Report Values for declaration values, carryovers and benchmark anchors that must remain separate from the ledger;
- Imported Accounting Report Export wizard;
- Import Runs and Discrepancies.

These read-only views are the audit/evidence layer formerly grouped under `Review > Advanced Audit`. The product menu is inactive, and every child is additionally restricted to the explicitly assigned Technical Features group. Accounting managers, ordinary administrators and read-only accountants reach reports through the canonical Accounting report pages and never see reconstruction objects in their normal menus. The report harness invokes the underlying actions directly and records both audit-view controls and live workbench controls in `reports-status.json`, including sampled source actions proving that ledger-backed report rows open contributing `account.move.line` records and analytic report rows open `account.analytic.line`.

The local Community code keeps the licensing boundary explicit: `addons/account/models/account_report.py` provides the shared `account.report` data model and tax-report expression machinery, while `addons/account/views/res_config_settings_views.xml` exposes `module_account_reports` as an `upgrade_boolean` labelled Dynamic Reports. There is no `account_reports` addon in the current Community fork. Three alternatives were assessed: expose OCA report wizards directly, depend on the absent proprietary Enterprise application, or build an original Community-compatible workbench while retaining OCA as a maintained accounting foundation. The third option is implemented. No Enterprise code is copied, and importing source `account.report` rows remains evidence preservation rather than executable-code reconstruction.

The addon exposes dedicated interactive Accounting report pages for every
mandatory report family. Each menu opens the report immediately with its
relevant defaults, dynamic filters, hierarchy, drill-down and direct PDF/XLSX
downloads. The generic `rebuild.account.report.export.wizard` is retained only
as an Advanced Audit implementation detail and is not a normal report
experience. Duplicate OCA launchers are hidden from normal navigation and MIS
has been removed from the dependency/runtime surface.

`action_preview_report()` materializes transient `rebuild.account.report.preview.line` rows inside the full-page workbench. The workbench supports:

- native-ledger scope by default and an explicit imported-only audit scope;
- one or several allowed companies, with single-company statutory/FEC guards;
- month, quarter, fiscal year, year-to-date and custom period presets;
- previous-period, previous-year and custom comparisons;
- posted-only or all-entry scope with a visible included/excluded draft count;
- journal, account, partner, analytic-plan and analytic-account filters;
- grouping by section, account, partner, journal, month or analytic account;
- search, whole-report expand/collapse and per-group toggles;
- screen metadata carrying current period, comparison period, difference, filters and row counts.

Each preview row has an `Open Sources` action. Analytic report rows open contributing `account.analytic.line` records; other rows open the relevant native `account.move.line` domain using stable source IDs, account codes, partners, journal codes, months, tax tags or French statement account prefixes. Trial Balance computes opening balance from all eligible prior entries, period debit/credit and movement from the selected dates, and closing balance through the end date. Its drilldown therefore follows the closing balance through all entries up to the end date rather than showing only the period movement.

The latest production-derived dynamic probe used October 2025 with an October 2024 comparison, grouped by account. It passed with `4` excluded draft entries, `90` group rows, `180` expanded visible rows, fewer rows when collapsed, a successful search refresh, a matching `180`-row XLSX and passed canonical/competing-menu checks. The permanent add-on suite also exercises repeated preview cleanup under the read-only reviewer ACL. FEC remains a generated file workflow because acceptance depends on file export, ledger reconciliation and official structural validation.

The complementary live browser artifact is `artifacts/accounting-compat/private/dynamic-report-browser-status.json`. It records the final Accounting Manager and disposable read-only reviewer journeys after the module update: visible Trial Balance opening/debit/credit/movement/closing columns, repeat refresh without an ACL error, account search, native-scope XLSX metadata, closing-balance source drilldown, and no create/edit controls for the reviewer. The disposable reviewer identity is removed after the check.

The operational landing-page artifact is
`artifacts/accounting-compat/private/accounting-home-browser-status.json`.
Three alternatives were compared: retain only the standard journal dashboard,
build a parallel OWL dashboard and data model, or extend the existing
company-scoped SQL review summary as an operational Home while retaining native
journal cards. The third option is implemented because it keeps the state
queryable for users and a future accounting agent without introducing a second
ledger or UI-only domain. The artifact records manager and disposable
read-only-reviewer journeys, the active-company Home counts, direct report and
journal-dashboard routes, refresh/back stability, company isolation and hidden
configuration controls for the reviewer. Readiness requires this artifact to
remain `passed`.

The daily-control artifact is
`artifacts/accounting-compat/private/accounting-hygiene-browser-status.json`.
Three designs were compared: a second hygiene issue model, exposing only the
period-closing control list, or extending the existing company-scoped review
summary with operational buckets and native-record actions. The third option is
implemented. It avoids duplicate state while covering both ongoing work and the
current monthly/quarterly/annual controls.

Analytic-allocation results reuse Odoo's native editable
`account.move.line.analytic_distribution` widget and list multi-edit contract.
The result action pins a scoped journal-item list with the field visible, while
writable Accounting users inherit native Analytic Accounting access. This was
chosen over a custom batch wizard because the native widget already validates
plans, percentages, company scope and analytic-line synchronization. The
read-only reviewer does not inherit this writable group.

`Accounting > Review > Control > Accounting Hygiene` now exposes unmatched bank
transactions, incomplete documents, supplier documents without main evidence,
expenses without receipts, document/expense work older than 30 days, open
balances, closing and declaration state, P0/P1 issues and the decision queues
owned separately by Valentin and Prosper. The current target is correctly
`blocked`; after the manager refreshed controls it showed `354` attention
items, `207` bank transactions to match, `37` supplier documents without main
evidence, `37` stale drafts, `7` unusual account balances totalling EUR
`50,860.26`, `15` overdue declarations, `4` blocking and `7` warning closing
controls, `2` Valentin actions and `44` Prosper actions.

The manager browser journey refreshed the current controls and drilled into all
`37` supplier-evidence records plus the seven-account unusual-balance queue.
The refreshed current control set contains `14` rows. The
single-company reviewer saw the same queues without Configuration, refresh,
create or upload controls. During the first reviewer pass, the standard account
move list still exposed an `Upload` button despite server-side create denial.
The add-on now gates that shared frontend control with Odoo's standard
`account.group_account_invoice`; the repeat browser pass proved it remains
available for the manager and is absent for the reviewer. The disposable
reviewer was removed. Readiness requires this browser artifact to remain
`passed`.

For unusual balances, three implementations were compared:

1. rely on Trial Balance/manual review, which provides no continuous Hygiene
   signal;
2. create durable issue copies for each account, which would duplicate and
   eventually drift from the native ledger;
3. add a live period-closing control that aggregates posted native journal
   items, applies a configurable natural-balance policy and drills directly
   back to those items.

The third option is implemented. Balance-sheet accounts use posted history
through the selected close date; income and expense accounts use the company
fiscal-year start through that close date. Automatic policy follows native
account types and common French contra-account families (`28`, `29`, `39`,
`49`, `59`, `609`, `619`, `629`, `709`) while treating variable inventory
families and current-year earnings as two-sided. Accounting Managers can
override a documented account to debit, credit or either-side expectations.
The control is a warning/review queue and never posts an automatic correction.

The native business-document evidence artifact is
`artifacts/accounting-compat/private/validation-native-native-attachments-browser-status.json`.
Three alternatives were compared: keep binaries only on exact-ledger evidence
records, link the native records to the separately operated source filestore,
or replay checksum-verified binaries onto their source-traced native records.
The third option is implemented because it gives bills and expenses usable
evidence through standard Odoo ACLs without coupling the replacement to the
private source service.

The current Track B document replay preserves `215/215` business-document
binaries and `202/202` source-designated main attachments. The expense replay
preserves `263/263` binaries and `245/245` main attachments. Both stages report
zero missing files, unmapped targets, checksum mismatches, duplicate attachment
traces or main-selection mismatches. The browser artifact records the manager
vendor-bill/PDF and expense/receipt journeys and the permanent reviewer
attachment-read regression. Community uses the standard chatter attachment
workbench, thumbnail and PDF viewer instead of the Enterprise split-pane
presentation; this is classified as an equivalent replacement, not pixel
parity. Readiness requires the artifact to remain `passed`.

The workbench generates CSV, XLSX and PDF files from the same filtered result shown on screen. Every format carries company, period, comparison, posted/draft scope, data scope, filters, grouping, search and row-count metadata. XLSX uses typed numeric cells and structured headers; PDF uses structured document tables and repeatable report headers. Fixed-asset reports accept account filters only, bank reconciliation accepts journal and partner filters, and the French tax-package mapping rejects ledger filters because it is a statutory benchmark review mapping. The harness validates report metadata, row counts, workbook structure, PDF structure and screen/export consistency. It also verifies a filtered General Ledger export by journal: the current control uses journal `MISC1`, produces `471` rows, and proves that exported rows and drill-down metadata carry the selected journal filter. The accountant-requested grouping reports retain their technical evidence: both tax grouping exports contain `6` rows with debit `9,168.27`, credit `5,726.27` and balance `3,442.00`; the fixed-asset account grouping contains `2` rows with gross value `10,430.49` and imported net value `8,754.44`.

`rebuild.account.overview` is the company-scoped operational cockpit used by Overview and Accounting Hygiene. It aggregates native accounting state, controls, deadlines and assurance status; it is not a migration-review model and exposes no reconstruction workbench. `rebuild.account.assurance.decision` remains a durable business approval record for reports, declarations and closing gates that still require accountable human acceptance. The scoped accountant can read evidence and exports and can record an audited declaration-review decision for an allowed company; direct accounting mutation, closing approval, filing/payment actions and cross-company access remain denied.

The report-suite status is refreshed from post-export evidence rather than
import-time catalogue counts. Every active source report has a target
equivalent and Level 4 technical evidence: `38` evidence packages and
`0` missing target equivalents. Professional review remains an operational
follow-up, not an engineering completion gate.

Import-time integrity failures now stop the reconstruction instead of creating review-only placeholders. The only chronology advisory is source truth preserved verbatim: `16` source sequence gaps and `104` source date-order decreases, with no target-only exception. Complete report evidence is assessed by the report stage rather than inserted as a standing P1 discrepancy.

`make accounting-readiness` writes the durable Milestone 13 readiness
assessment after comparison and before the evidence index. The JSON and
Markdown artifacts summarize technical gates, source and target identities,
advisories, review-decision records, source-report parity evidence and the
release recommendation. The final assessment is
`ready_with_documented_assumptions`, with zero technical failures and zero
engineering blockers. The `45` draft decisions are audit/acceptance records
(`36` report, `3` discrepancy, `2` scope, `2` external tax value, `1` FEC and
`1` closure); they are not accounting records and do not block engineering
completion.

`parity-matrix-v1.json` now has an explicit two-stage lifecycle. Source
inspection writes the discovery baseline, and the reports stage must replace
it with final evidence-backed classifications or fail its own technical gate.
The current matrix contains all `56` rows: `12` implemented, `39` technically
complete but professionally partial, `4` not applicable and `1` explicitly
deferred. It contains `0` discovery rows and `0` technical gaps. Each base
capability records its required private artifacts, and every one of the `38`
source reports records its Level 4 technical-evidence state and remaining
acceptance gate.

The report stage also validates the native monthly
`Revenue versus Spending Trend`. The SQL view normalizes revenue, spending and
net contribution into graph/pivot/list rows and retains journal-item
drill-down. For October 2025–June 2026 the exact target must return `27` rows
with EUR `176,928.45` revenue, EUR `101,215.69` spending and EUR `75,712.76`
net contribution.

Closing-package acceptance has a separate persistence contract. Manager
generation attaches the XLSX/PDF to the selected closing. Recording an accepted
decision copies its bytes and SHA-256 plus the decision/reviewer context into
immutable snapshot rows; tests deny write/unlink and require a snapshot before
standard locks can advance. This proves the accepted artifact without relying
on a mutable attachment reference.

The same export wizard now exposes a FEC export backed by Odoo `l10n_fr_account`. The current harness generates `983982950FEC20250930.txt` through the Odoo UI wizard model in FEC test mode, with `4,781` data rows, debit `1,064,045.02`, credit `1,064,045.02` and SHA-256 `95652b3f3a7c66e25a6f2aa0d56cf860777364606b5a9519090f2d48e5657efa`. The generated exports identify company, source company id, dates, posted/draft scope, selected filters, format and row count.

The benchmark report bundle now includes historical bank reconciliation, currency, analytic and EC/OSS evidence:

```text
artifacts/accounting-compat/private/bank-reconciliation-2025-09-30.json
artifacts/accounting-compat/private/bank-reconciliation-2025-09-30.csv
artifacts/accounting-compat/private/currency-gain-loss-exposure-2025-09-30.json
artifacts/accounting-compat/private/currency-gain-loss-exposure-2025-09-30.csv
artifacts/accounting-compat/private/analytic-distribution-2025-09-30.json
artifacts/accounting-compat/private/analytic-distribution-2025-09-30.csv
artifacts/accounting-compat/private/analytic-distribution-current.json
artifacts/accounting-compat/private/analytic-distribution-current.csv
```

Current controls for the benchmark slice validate `1,164` imported bank statement lines, total statement amount `56,170.11`, zero statement residual, and drill-down from a sampled bank line to its two journal items. The currency report validates `14` grouped rows covering the foreign-currency ledger and realized exchange gain/loss sections. The source has no `account_analytic_line` records in the closed benchmark slice, so the benchmark analytic report is available but empty and explicitly recorded with zero allocated debit, credit and balance. The current-period analytic report validates `53` grouped rows over `632` imported source analytic lines, allocated debit `310,175.76`, allocated credit `208,694.53` and allocated balance `101,481.23`, with drill-down to contributing analytic lines.

The EC/OSS analysis view is ledger-derived and explicitly preparatory. The closed benchmark period has no EC Sales, OSS Sales or OSS Imports rows. The current-period EC Sales List view validates `4` rows for partner `ARTEMISA 3000 TECH SOLUTIONS SL.`, country code `ES` derived from the VAT prefix when the partner country is missing, taxable amount `37,555.12`, and journal/account breakdown across `INV`, `CABA` and account `706000`. OSS Sales and OSS Imports currently export as explicit empty reports for the current source corpus. These reports are target equivalents for the active source EC/OSS report definitions; they are not accepted tax filings.

The French annual-statement view currently validates the benchmark anchors for:

- gross assets `71,356.21`, depreciation `1,676.05` and net assets
  `69,680.16`;
- total passif `69,680.16`, equity `57,222.98`, financial/associate debt
  `156.26` and total debt `12,457.18`;
- turnover `129,188.62`, operating products `129,190.02`, operating charges
  `63,009.32`, operating result `66,180.70`, current result before tax
  `66,144.98`, total products `129,270.65`, total charges `73,047.67` and net
  result `56,222.98`;
- commercial margin `-6,288.77`, production `129,188.62`, value added
  `85,322.30`, gross operating surplus `67,856.84` and cash-flow capacity
  `57,899.03`;
- structure and profitability ratios `6.55`, `370.53`, `0.53`, `0.44`,
  `6.42`, `0.98` and operating working-capital importance `0.01`.

The statement mapping follows the PCG distinction between account `701`
production and account `707` merchandise sales. Associate current account
`455100` is presented as financial/associate debt instead of supplier debt.
Other operating products and total products/charges are explicit statement
lines. The restore applies six verified French and English naming corrections
to the native Chart of Accounts. Account codes and ledger relationships remain
unchanged, source evidence stays outside the product database, and native
views, screen reports, PDF, XLSX and FEC all use the same translated label.

The generated private export is:

```text
artifacts/accounting-compat/private/french-annual-statements-2025-09-30.json
artifacts/accounting-compat/private/french-annual-statements-2025-09-30.csv
```

The USL report mappings, interactive behavior, drill-downs and current PDF/XLSX
outputs pass the automated product controls for the imported source corpus.
Professional approval of statutory interpretation and live filing remains
deliberately deferred; it is not represented by a hidden runtime review model.
The annual PDF is company-prepared and explicitly non-attested; professional
attestation remains outside generated scope.

The depreciation and deferral schedules link directly to native source-traced moves, including future drafts. All `182` non-posted source moves are native records with their original draft/cancelled state. All `13` source payments without a journal entry are native immutable payments that preserve source workflow state and invoice links without generating duplicate ledger entries. All reconciliation endpoints are present: the target contains `2,595` source-traced partial reconciliations and `1,267` source-traced full reconciliations, with no boundary queue or policy-review fallback.

The importer now represents all `38` active source `account.report` records as `rebuild.account.source.report` catalogue records. These records preserve source report identity, English and French names, country, root report, custom handler model, source filter flags, line/column/expression/external-value counts, line-code samples, expression-engine summary, parity decision, target-equivalent status, target evidence key, parity level and trace metadata. The current rule set classifies `23` reports as `MANDATORY_PARITY`, `10` as `OPERATIONAL_PARITY`, `3` as `ACCOUNTANT_REQUESTED` and `2` as `REMOVED_AS_UNUSED` association reports. All `38` reports now have a partial target equivalent or explicit legal-form scope decision, and `0` active source reports are missing an assigned target treatment. The importer also preserves the source report structure as Odoo evidence records: `702` report lines, `1,227` expressions and `141` columns. Target validation and the source-target comparison artifact compare all source report catalogue, line, expression and column rows with no missing, extra or mismatched records. This is a technical compatibility catalogue, not a copy of Enterprise report code.

The reports stage now updates each source report with post-export evidence in Odoo and writes:

```text
artifacts/accounting-compat/private/source-report-parity-status.json
```

The latest report-stage parity distribution is:

- `38` Level 4 evidence partial;
- `0` Level 3 semantic partial;
- `0` Level 2 ledger-control;
- `0` Level 1 availability;
- `0` Level 4 accepted.

The `38` Level 4 evidence-partial reports have passed current technical availability, dynamic filtering, export and sampled drill-down checks for their mapped report family or explicit scope-exclusion evidence. Their source line hierarchy, columns and expressions are preserved and compared as Odoo evidence. They are not final accepted parity because accountant approval of formulas, PCG variants, statutory interpretations and deliberate scope exclusions remains open.

The three `2024` French variants now have explicit Odoo wizard actions and exports: `French Balance Sheet (2024 PCG)`, `French Profit and Loss (2024 PCG)` and `SIG and CAF (2024 PCG)`. Their export metadata records `pcg_2024_pre_2025_opening_year`, based on ANC règlement 2022-06 applying to financial years opened from `2025-01-01` while the USL benchmark year opened on `2024-01-10`. The two association reports remain catalogued but are classified as `REMOVED_AS_UNUSED` for the USL SASU target scope; this deliberate non-parity still needs stakeholder/accountant acceptance.

The imported French tax-package mapping is a review surface, not an automatic filing engine. It exposes `31` ledger-derived and evidence-derived lines for 2065-SD, 2033-A/B/C/D and 3517-S-SD/CA12. The current harness checks key values including taxable-profit review amount `66,144.98`, total net assets `69,680.16`, net result `56,222.98`, fixed-asset gross value `10,430.49`, accumulated depreciation `1,676.05`, VAT collected `459.00`, VAT credit carryover `3,442.00` and deductible VAT on goods/services CA12 clearing amount `1,960.00`, while preserving gross account `445660` turnover of `3,014.09` as ledger evidence. The asset register, annual-statement and 2033-A/2033-C rows now reconcile at `10,430.49` gross, `1,676.05` accumulated depreciation and `8,754.44` net. Count-only 2033-C evidence uses typed quantity/value columns (`3` assets and `91` preserved schedule rows) and carries no misleading monetary amount; coercing those counts into currency fields was rejected.

Three fields remain review-required or manual-value-required. The deductible VAT on goods/services benchmark value is stored as two `rebuild.account.external.report.value` records for the 2033-D and CA12 review fields, so the SQL view reads an explicit Odoo evidence record rather than a hidden report constant. The restored source also contains the same `1,960.00` value in posted journal entry `OD000000009` on `2025-09-30`, line name `ca12`, as a credit to account `445660`. The Odoo tax-package line and CSV/XLSX/PDF exports now show amount `1,960.00`, benchmark amount `1,960.00`, ledger amount `1,960.00`, zero difference and no `EXTERNAL_VALUE_DIFFERENCE` classification, while the line evidence text preserves gross `445660` debit turnover `3,014.09`. The previous P1 VAT discrepancy is resolved with a decision noting that the benchmark matches the imported source CA12 clearing entry.

The generated VAT investigation artifact is:

```text
artifacts/accounting-compat/private/vat-benchmark-investigation-2025-09-30.json
```

It records that source and target account `445660` match exactly for the benchmark period: `253` lines, debit `3,014.09`, credit `3,014.09` and balance `0.00`. It also records the source and target CA12 clearing lines for VAT accounts `445201`, `445620`, `445660`, `445663`, `445670` and `445700`, including the `445660` credit of `1,960.00` on `OD000000009`. The SaaS source has `0` `account_report_external_value` rows, the French tax report source expression for box `20 - Other goods and services` is tag-based, and no benchmark-period source move lines were tagged `20`; final VAT/CA12 acceptance remains an accountant review item, but this specific amount is no longer an open ledger mismatch.

## Current accountant access status

The addon defines `rebuild_account_migration.group_rebuild_accountant_reviewer` as a USL-specific review role that implies Odoo's `account.group_account_readonly`.

The report stage creates a deterministic disposable target user, `accountant.review@example.invalid`, and validates that this user can:

- read imported USL report views and French statement lines;
- read the imported discrepancy register;
- read native draft/cancelled documents, historical payments and complete reconciliation evidence;
- read the imported source accounting report catalogue;
- open assurance decisions from the Accounting Overview;
- read and open pending external report values for CA12/2033-D review;
- read imported accounting attachment metadata and one sampled binary evidence file;
- be blocked with `AccessError` from a rollback-only private technical attachment linked outside accounting;
- create and update a review-decision note for accountant/stakeholder acceptance without changing imported accounting evidence;
- generate a Trial Balance XLSX export through the Odoo wizard;
- see imported USL accounting move lines;
- see zero imported USL Media move lines when only Unstatic Labs is assigned as an allowed company;
- fail with `AccessError` when attempting to write an imported posted accounting move.

This is a technical accountant-access check for the disposable target. It proves the current USL-only reviewer can read imported accounting evidence while a rollback-only private technical attachment remains inaccessible. It does not yet prove external accountant onboarding, complete source-document review workflow or accountant acceptance.

The addon now also has permanent Odoo transaction tests under `custom-addons/rebuild_account_migration/tests/`. They can be run with `make accounting-addon-tests`, which defaults to a timestamped disposable database through `ACCOUNTING_TEST_DB`. The current scoped test run used a disposable database and:

```bash
docker compose --profile init run --rm -e ODOO_INIT_DB=odoo_rebuild_accounting_unit_20260722102008 init-db odoo --config=/etc/odoo/odoo.conf --database=odoo_rebuild_accounting_unit_20260722102008 --init=rebuild_account_migration --without-demo=true --test-enable --test-tags=rebuild_account_migration_unit --stop-after-init --log-level=warn
```

It exited with status `0` and ran the addon-scoped post-install test class tagged `rebuild_account_migration_unit`. The tests lock down accountant read-only ACL behavior, external report-value provenance, imported attachment readability, private technical attachment blocking, report export metadata, report-launcher contexts, FEC guardrails, native reconciliation drill-down, complete native source-document representation and immutable historical payments without journal entries. This permanent regression layer complements, but does not replace, the private production-derived source/target comparisons.

## Current FEC status

The harness generates a benchmark FEC through Odoo `l10n_fr_account` in test mode:

- path: `artifacts/accounting-compat/private/fec-usl-2025-09-30.txt`;
- data rows excluding header: `4,781`;
- debit and credit totals: `1,064,045.02`;
- current SHA-256: `95652b3f3a7c66e25a6f2aa0d56cf860777364606b5a9519090f2d48e5657efa`.

The FEC export reconciles to the imported target ledger. The Odoo-facing export wizard produces the same file hash for the benchmark period. It has passed the current DGFiP Test Compta Demat source validation route described below, but it has not been accepted by the accountant.

`make accounting-fec-preflight` runs a deterministic local structural preflight derived from article A47 A-1 of the Livre des procédures fiscales. The current preflight passes and records:

- separator: pipe;
- required first 18 fields: present and ordered;
- entry groups by journal and entry number: `2,032`;
- invalid row/date/amount/account counts: `0`;
- chronology decreases: `0`;
- unbalanced entry groups: `0`;
- rows with lettering: `2,240`;
- rows with currency values: `4,781`.

This is useful defect prevention evidence only. It is separate from the DGFiP source-validation result and does not replace accountant review.

`make accounting-fec-validate` records the official-validation gate in:

```text
artifacts/accounting-compat/private/fec-validation-status.json
```

The harness checked the current DGFiP source on `2026-07-22`: the official free structural test tool is `Test Compta Demat`, published by the Direction générale des Finances publiques as version `1.00.10b`, for FEC files governed by article A.47 A-1 of the Livre des procédures fiscales. The source page is:

```text
https://www.economie.gouv.fr/dgfip/outil-de-test-des-fichiers-des-ecritures-comptables-fec
```

The DGFiP source page links to the DGFiP GitHub release `1.00.10b`. That release publishes `Testeur_1_00_10b_win_x86.exe`, `Testeur_1_00_10b_win_x86_64.exe` and `Notice.Test.Compta.Demat_maj.2021.pdf`. The repository also exposes CeCILL-licensed source under `src/`. On the current `Darwin arm64` validation host, neither `wine` nor `wine64` is installed and the host Perl lacks `Tk.pm`, so the harness runs the DGFiP source validator in an isolated Debian Bookworm container when no `FEC_VALIDATOR_COMMAND` is configured.

The current validation status is `passed` with classification `OFFICIAL_DGFIP_SOURCE_VALIDATION_PASSED`. The archived validator artifact is:

```text
artifacts/accounting-compat/private/fec-dgfip-source-validation/
```

The run mounted `/private/tmp/Test-Compta-Demat-1.00.10b/src/testeur` read-only, initialized the SQLite log tables and header mapping normally produced by the GUI bootstrap, invoked DGFiP `trt_txt.pl`, and archived the SQLite log database plus generated PDF. The result records `validator_exit_code = 0`, `blocking_log_count = 0`, DGFiP log counts `{"I": 24}`, FEC debit `1,064,045.02`, FEC credit `1,064,045.02`, and report PDF SHA-256 `9ef6f1a3a243359981c174f7a53fda9d2dd8796d63d2cc3da7001618ba12a339`.

The container uses runtime-only compatibility shims because the official source is GUI-oriented and old: it installs Perl/Tk dependencies and patches Debian `PDF::Table` so a missing `text_opt` option defaults to an empty hash during PDF rendering. The DGFiP source tree is mounted read-only and not modified. This is structural validator evidence and still does not replace accountant review or ledger reconciliation.

## Documented assumptions and deferred items

The complete source reconciliation graph is native and has no boundary queue.
The only source anomaly is chronology evidence (`16` sequence gaps and `104`
date-order decreases), preserved exactly rather than silently repaired.

The five deliberate deferrals are:

1. professional approval and live tax/electronic filing;
2. production approved-platform selection and activation for e-invoicing;
3. live bank synchronization/provider ingestion;
4. probabilistic or AI matching and autonomous posting;
5. production deployment and cutover from disposable `odoo_dev`.
- the official DGFiP source validator passes structurally; actual filing and
  professional use remain company operations.

## Current authoritative checks

The following official sources were checked on 2026-07-21 before documenting French accounting risk areas:

- DGFiP/BOFiP FEC format: https://bofip.impots.gouv.fr/bofip/9028-PGP.html/identifiant%3DBOI-CF-IOR-60-40-20-20131213
- Légifrance article A47 A-1, Livre des procédures fiscales: https://www.legifrance.gouv.fr/codes/article_lc/LEGIARTI000027804775
- impots.gouv.fr FEC standard files/XSD page: https://www.impots.gouv.fr/fichiers-standards-des-ecritures-comptables-art-l-47-1-du-lpf
- ANC PCG consolidated versions: https://www.anc.gouv.fr/plan-comptable-general-0
- economie.gouv.fr e-invoicing reform calendar: https://www.economie.gouv.fr/tout-savoir-sur-la-facturation-electronique-pour-les-entreprises
- BOFiP cash-register software perimeter: https://bofip.impots.gouv.fr/bofip/10691-PGP.html/identifiant%3DBOI-TVA-DECLA-30-10-30-20250416
- impots.gouv.fr 2065-SD form page: https://www.impots.gouv.fr/formulaire/2065-sd/impot-sur-les-societes
- impots.gouv.fr 2033-SD simplified BIC/IS package page: https://www.impots.gouv.fr/formulaire/2033-sd/liasse-bicsi-regime-rsi-tableaux-ndeg-2033-sd-2033-g-sd
- impots.gouv.fr 3517-S-SD CA12/CA12E page: https://www.impots.gouv.fr/formulaire/3517-s-sd/tva-et-taxes-assimilees-et-regime-simplifie
- impots.gouv.fr VAT simplified-regime deadlines and obligations: https://www.impots.gouv.fr/professionnel/les-regimes-dimposition-la-tva
- impots.gouv.fr IS/RSI filing perimeter: https://www.impots.gouv.fr/professionnel/resultat-imposable-limpot-sur-le-revenu-ir-ou-limpot-sur-les-societes

The DGFiP `Test Compta Demat` FEC tester page was checked on 2026-07-22:

- DGFiP FEC structural test tool: https://www.economie.gouv.fr/dgfip/outil-de-test-des-fichiers-des-ecritures-comptables-fec

The ECB reference-rate publication and API guidance were checked on
2026-07-23:

- ECB euro reference rates and publication policy: https://www.ecb.europa.eu/stats/policy_and_exchange_rates/euro_reference_exchange_rates/html/index.en.html
- ECB Data Portal API overview: https://data.ecb.europa.eu/help/api/overview
- ECB Data Portal data guidance: https://data.ecb.europa.eu/help/api/data

These checks do not replace accountant, legal or compliance review.
