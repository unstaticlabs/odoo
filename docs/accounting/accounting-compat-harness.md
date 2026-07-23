# Accounting compatibility harness

## Purpose

The accounting compatibility harness turns an Odoo Online backup into repeatable technical evidence. The source PostgreSQL dump remains a source format only; extraction and comparison use PostgreSQL and the target Odoo ORM boundary rather than parsing business data from SQL text.

## Commands

```bash
make accounting-source-package-validate
make accounting-source-validate
make accounting-source-restore
make accounting-source-inspect
make accounting-extract
make accounting-failure-tests
make accounting-target-reset
make accounting-target-import
make accounting-target-validate
make accounting-target-idempotence
make accounting-target-failure-tests
make accounting-document-regeneration
make accounting-track-b-reset
make accounting-track-b-expenses
make accounting-track-b-documents
make accounting-track-b-assets
make accounting-track-b-deferrals
make accounting-track-b-expense-settlement
make accounting-track-b-document-settlement
make accounting-track-b-general-reconciliation
make accounting-track-b-bank-categorization
make accounting-track-b-bank-external
make accounting-track-b-analytics
make accounting-target-reconciliation-probe
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

The live `odoo19` database is not reset or modified by these stages. Exact reconstruction uses the disposable `odoo_rebuild_accounting_test` database. Native-engine Track B proof uses a second disposable database, `odoo_rebuild_accounting_track_b`, so recomputed current-period documents cannot alter the exact historical replay.

`make accounting-failure-tests` validates six non-destructive source-package guardrails: missing source directory, missing `dump.sql`, missing filestore directory, filestore path that is not a directory, unsupported dump format and a minimal valid plain-SQL source package. These tests use temporary private packages under `artifacts/accounting-compat/private/failure-tests/` and never mutate the real source backup.

## Minimal Imported-Data Pipeline

Use this shorter sequence when the immediate goal is to open the imported accounting data in Odoo:

```bash
make oca-addons-sync
make accounting-source-restore
make accounting-extract
make accounting-target-reset
make accounting-target-import
make accounting-target-validate
make accounting-reports
```

Run these commands from the host shell, not from inside the Dev Container. The harness currently calls `docker compose`; the Dev Container runs Odoo but does not include the Docker CLI.

`make oca-addons-sync` fetches pinned OCA 19.0 add-ons into ignored local directories. The target reset stage requires these add-ons because the disposable imported target now initializes the OCA reporting, MIS, bank statement and reconciliation foundation.

The sequence has two live PostgreSQL services:

| Service | Role |
| --- | --- |
| `accounting-source-db` | Isolated PostgreSQL service containing the restored Odoo Online backup. |
| `db` | Normal Odoo PostgreSQL service containing the disposable target database. |

The important databases are:

| Database | Role |
| --- | --- |
| `odoo_online_source_saas_19_2` | Read-only source database restored from `usl-online-dump/dump.sql`. |
| `odoo_rebuild_accounting_test` | Clean target Odoo database rebuilt from the extracted snapshot. |
| `odoo_rebuild_accounting_track_b` | Separate clean target for native current-period business-document recomputation. |
| `odoo19` | Normal local demo/development database. It is not the imported accounting target. |

Stage dependencies:

| Command | Reads | Writes | Why it must run here |
| --- | --- | --- | --- |
| `make accounting-source-restore` | `usl-online-dump/dump.sql`, `usl-online-dump/filestore/` | `odoo_online_source_saas_19_2` in `accounting-source-db`; source restore status artifacts | It creates the source database that every later source read depends on. |
| `make accounting-extract` | Restored source database through read-only SQL | Private canonical snapshot and extraction artifacts | It converts the physical SaaS database into the durable transfer package used by the target importer. |
| `make accounting-target-reset` | Compose target PostgreSQL service `db` | Fresh `odoo_rebuild_accounting_test` database | It removes old target state so the import is deterministic and not mixed with previous attempts. |
| `make accounting-target-import` | Canonical snapshot; source database for source metadata; clean target database | Target Odoo records and source-trace metadata | It reconstructs accounting evidence through the target Odoo ORM. |
| `make accounting-target-validate` | Imported target database | Target validation artifacts and discrepancy records | It proves the imported target is internally consistent before report checks run. |
| `make accounting-track-b-reset` | Installed target/OCA add-ons | Fresh `odoo_rebuild_accounting_track_b` database | It creates a clean, neutralized proof environment without touching the exact replay target. |
| `make accounting-track-b-expenses` | Read-only restored source expense/business fields, verified source filestore binaries and Track B configuration | Native employees, products, expenses, company payments, employee receipts and source-traced receipt attachments in the Track B database; private proof artifact | It uses normal expense submission, approval/refusal, receipt preparation and payment posting APIs, then compares every expense and generated accounting effect to source. It verifies every source receipt checksum/size and preserves the source-designated main attachment. Run it before the document stage so expense-generated receipts can be reused. |
| `make accounting-track-b-documents` | Read-only restored source business fields, verified source filestore binaries and Track B configuration | Native posted invoices, bills, supplier refunds and receipts with source-traced document attachments in the Track B database; private proof artifact | It calls normal Odoo draft creation and `action_post`, compares headers, due dates and per-account effects to source, then verifies every business-document binary and source-designated main attachment. |
| `make accounting-track-b-assets` | Read-only source asset master data, depreciation schedules and Track B configuration | OCA assets, profiles, depreciation-board lines and native posted depreciation entries; private proof artifact | It seeds the source business schedule into maintained OCA `account_asset_management`, lets OCA create and post every in-period entry, leaves future schedule lines unposted and compares date, amount and account effects exactly. |
| `make accounting-track-b-deferrals` | Native Track B documents plus read-only source deferred relationships and schedule decisions | Operational deferred-expense records, posted recognition entries, future schedule lines and a traced opening boundary entry; private proof artifact | It creates a focused schedule workflow backed by standard `account.move` posting, validates every posted and future source relationship, and keeps the reviewer surface read-only. |
| `make accounting-track-b-expense-settlement` | Native Track B expenses plus the read-only source bank/reconciliation graph | Native bank transactions, OCA-generated partial reconciliations, paid company payments and paid employee expenses; private proof artifact | It runs after expenses/documents, replays source operator allocations chronologically, and keeps mixed-transfer non-expense balances explicit for General Reconciliation. |
| `make accounting-track-b-document-settlement` | Native Track B documents, expense settlement and the read-only source bank/reconciliation graph | Native commercial-document bank transactions, exact OCA-generated partial reconciliations and bounded residuals for General Reconciliation; private proof artifact | It reuses overlapping expense bank lines, creates the remaining bank transactions, applies every direct document/bank edge and validates company/transaction-currency partials plus due-line residuals. |
| `make accounting-track-b-general-reconciliation` | Native Track B documents and direct bank settlement plus the read-only source non-bank reconciliation graph | Native posted manual entries, document netting, General Reconciliation partials and traced Odoo/OCA exchange-difference moves; private proof artifact | It posts shareholder-current-account and clearing entries through standard journal APIs, reconciles them with documents, and classifies native timing and one-cent exchange differences without copying finalized source journal rows. |
| `make accounting-track-b-bank-categorization` | Track B through General Reconciliation plus source bank transactions without external partial endpoints | Native OCA-categorized interest, fees, transfers and account allocations plus source-open transactions retained for review; private proof artifact | It replays the operator's account, partner, analytic and currency inputs for direct categorizations and deliberately leaves source-unreconciled transactions open. |
| `make accounting-track-b-bank-external` | Track B through direct categorization plus the remaining source bank/external-reconciliation graph | Exact multi-line OCA bank categorizations, posted payroll/tax/clearing entries, native General Reconciliation and explicit cutoff boundaries; private proof artifact | It completes all current-period bank transactions while keeping draft/post-cutoff documents as prepayments and identifying five aggregates from earlier bounded settlement stages that still need refinement. |
| `make accounting-track-b-analytics` | Completed Track B native stages plus source expense decisions, finalized analytic distributions and analytic lines | Explicit analytic-correction audit records and direct source/target reconciliation across both analytic plans; private proof artifact | It runs last, after every posting stage. Native business objects remain the accounting input; the stage applies only source post-posting analytic classifications through Odoo's supported distribution write, then compares both theoretical allocations and actual analytic lines. |
| `make accounting-replacement-reset` | Completed and validated Track B native state | Fresh `odoo_rebuild_accounting_replacement` clone with the current migration add-on upgraded | It refuses to clone incomplete Track B state and preserves both the exact-replay baseline and the isolated native proof. |
| `make accounting-replacement-import` | Canonical historical snapshot plus the replacement clone | Exact benchmark history in the replacement database, reusing only four checksum/shape-validated native move representations | It adds the locked historical ledger without duplicating the four current native moves that already represent source history. |
| `make accounting-replacement-validate` | Source database and completed replacement candidate | Historical parity, current-period difference decomposition and promotion-gate evidence | It requires exact benchmark parity, balanced/unique native state and a classification for every current journal and account-balance difference. A classified difference can still require professional acceptance. |
| `make accounting-currency-rate-provider` | Imported target company configuration and the official ECB daily XML feed | Native future-dated `res.currency.rate` rows plus provider, retrieval, cron and idempotence evidence | It runs after historical replay. It must never replace a source-traced historical rate, and its reference rows remain separate from transaction-specific bank or platform conversion evidence. |
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
odoo_online_source_saas_19_2
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
artifacts/accounting-compat/private/target-import-status.json
artifacts/accounting-compat/private/target-validate-status.json
artifacts/accounting-compat/private/target-idempotence-status.json
artifacts/accounting-compat/private/target-failure-tests-status.json
artifacts/accounting-compat/private/track-b-reset-status.json
artifacts/accounting-compat/private/track-b-expenses-status.json
artifacts/accounting-compat/private/track-b-documents-status.json
artifacts/accounting-compat/private/track-b-expense-settlement-status.json
artifacts/accounting-compat/private/track-b-document-settlement-status.json
artifacts/accounting-compat/private/track-b-general-reconciliation-status.json
artifacts/accounting-compat/private/track-b-bank-categorization-status.json
artifacts/accounting-compat/private/track-b-bank-external-status.json
artifacts/accounting-compat/private/target-reconciliation-probe-status.json
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

As of the latest clean rehearsal on 2026-07-23:

- source dump SHA-256: `bf16ce18965e4ce1b23d7b79930b6e43ca7f510339ac6d2db280231f91d1449f`;
- replay mode: exact posted-ledger replay;
- source companies: Unstatic Labs (`1`) and USL Media (`8`);
- benchmark period: `2024-01-10` through `2025-09-30`;
- imported posted moves through source snapshot: `4,843`;
- imported accounting move lines: `11,392`;
- imported non-posted source move workflow review records: `194` (`189` ready document-regeneration candidates, `2` cancelled records and `3` zero-line draft records marked review-only/not-applicable);
- imported source move-line workflow review records: `467` (`466` non-posted source accounting lines and `1` posted non-account display line);
- imported move-backed payments: `97`;
- imported source payment workflow review records without journal entries: `13`;
- imported cross-boundary source reconciliation review records: `75` (`39` partial and `36` full);
- imported source accounting report catalogue records: `38` active source `account.report` records (`23` mandatory parity, `10` operational parity, `3` accountant-requested and `2` removed-as-unused association reports by current rule set);
- source report import-time parity levels: `23` Level 3 semantic partial, `10` Level 2 ledger-control, `3` Level 1 availability and `2` scope-excluded association reports before report evidence is generated;
- imported deferred expense/revenue schedule review rows: `110` (`37` linked to imported posted entries and `73` source draft forecast rows);
- imported bank statement lines: `3,040`;
- imported analytic plans: `2`;
- imported analytic accounts: `14`;
- imported analytic lines: `632` (`577` linked to imported journal items and `55` standalone source analytic lines);
- imported scoped accounting attachments: `332`;
- imported assets: `3`;
- imported source asset depreciation schedule lines: `91`;
- benchmark source and target debit: `1,064,045.02`;
- benchmark source and target credit: `1,064,045.02`;
- benchmark source and target move count: `2,046`;
- benchmark source and target accounting-line count: `4,809`.

## Track B native business-document proof

Track B is deliberately separate from the exact replay. Three approaches were considered:

1. create recomputed documents inside `odoo_rebuild_accounting_test`, which would mix generated current-period effects with the historical-truth baseline;
2. create a dedicated clean database with the same Community/OCA configuration and replay source business fields through native Odoo posting;
3. calculate expected taxes, currencies and due lines in a custom migration engine and write the resulting journal entries.

Option 2 is selected. It preserves the exact target as an audit baseline and proves the target product through Odoo's own engines. Option 1 makes later parity controls ambiguous. Option 3 would duplicate the accounting engine and would be another exact-line importer rather than a native workflow proof.

`make accounting-track-b-expenses` first reconstructs all `325` source `hr.expense` records dated `2025-10-01` through `2026-06-30`. Three credible treatments were compared:

1. replay employee, product and expense business fields through native `hr.expense` submission, approval/refusal, receipt and company-payment APIs in the isolated Track B database;
2. preserve only finalized source expense ledger/payment rows in the exact target, which retains historical accounting truth but does not prove the replacement expense workflow;
3. calculate expense accounting in a custom migration engine or duplicate the source's Enterprise implementation.

Option 1 is selected for product proof, while the existing exact-target ledger remains the historical-truth baseline. Option 2 alone cannot prove native daily use. Option 3 would duplicate accounting logic and create an upgrade-sensitive parallel workflow.

The clean expense run validates `325/325` expenses and all `176` generated moves with `0` blocked cases and `0` mismatches. The source state distribution is `192` paid, `125` approved, `3` draft and `5` refused; payment modes are `97` company-account and `228` own-account. Native reconstruction creates `97` company payments and `79` grouped employee receipts for the `95` paid own-account expenses. It preserves accounts, taxes, analytics, employees, vendors, dates, quantities, historical unit prices, currencies and all monetary fields. A repeated run after document reconstruction reuses all `325` expenses, `97` payments and `79` receipts without changing payment-method identities or creating duplicates.

At this expense-document checkpoint, state transitions that depend on settlement remain explicit rather than forged: the `97` source-reconciled company payments stay in process until their bank transactions are matched, and the `95` source-paid own-account expenses stay posted until employee reimbursement replay. A legacy source destination-payable hint is retained as classification evidence; the current native company-expense payment's posted outstanding account is the accounting effect validated to source.

`make accounting-track-b-documents` then reconstructs all `284` posted source business documents for the same period: `36` customer invoices, `161` vendor bills, `3` supplier refunds and `84` purchase receipts. It reuses the `79` receipts already produced by the expense workflow and creates the remaining `205` documents from commercial lines, accounts, quantities, unit prices, discounts, taxes, analytic distributions, fiscal positions, payment terms, partners, dates and the source transaction currency rate, then calls normal `action_post`. The latest clean run validates `284/284`, with `0` blocked cases and `0` mismatches. Coverage is `170` EUR and `114` USD documents.

Validation compares untaxed, tax and total amounts, due dates, and debit/credit/balance/amount-currency aggregates by source account. Finalized source journal lines are never passed to document creation. After expense-generated receipts are reused, two remaining source documents whose stored tax/base totals cannot be derived from their price fields are replayed through Odoo's native `extra_tax_data` manual-tax mechanism. That path is guarded to one unambiguous taxable product line and recorded as `supported_native_manual_tax_override`; ambiguous multi-line allocations remain mismatches rather than guesses.

`make accounting-track-b-expense-settlement` then proves the bank/payment transition for this expense slice. Three credible approaches were compared:

1. create native `account.bank.statement.line` records and replay the source operator's selected current-expense candidates through maintained OCA `reconcile_bank_line()` behavior;
2. copy the source statement moves and finalized partial-reconciliation rows, which would preserve history but would not prove the target bank-matching product;
3. implement a custom matching/reimbursement engine, which would duplicate Odoo/OCA accounting logic and create an upgrade-sensitive parallel workflow.

Option 1 is selected. The exact target continues to provide historical truth, while Track B demonstrates native behavior from bank transaction through reconciliation. Option 2 remains appropriate only for the exact replay. Option 3 is rejected because OCA already supplies the maintained Community reconciliation engine.

The bounded expense-settlement run creates `106` native bank transactions: `98` company-account card/bank lines and `8` grouped employee reimbursement transfers. It replays `181` source partial-reconciliation choices against `176` native outstanding/payable lines, including one company payment split across two bank transactions. All source partial amounts match the OCA-generated target partials, all `97` company payments become paid, and all `95` employee-paid expenses become paid. OCA's native exact-reference behavior automatically matches one unambiguous €46.50 line at statement creation; the replay detects and traces that native result instead of adding a duplicate candidate.

All `98` company-account bank lines and `2` reimbursement transfers contain only current-period expense allocations. The other `6` reimbursement transfers also settle older or non-expense shareholder-account items in the source. Track B replays the source edges backed by current-period native expenses and preserves all `19` outside-only source counterpart lines through the same OCA reconciliation payload, including exact account, partner, currency, company amount, transaction amount and analytic distribution. Only source lines themselves split across perimeters retain an aggregate residual. Validation derives the outside balance from all bank counterpart lines minus the traced current-expense partials, so it remains stable after downstream reconciliation. This retains source detail without inventing an endpoint or copying finalized journal rows.

`make accounting-track-b-document-settlement` next proves the direct bank transition for all current-period commercial documents. Three credible approaches were compared:

1. create standard bank transactions and replay the source operator's exact document candidates through OCA Bank Matching, using supported transaction countervalues plus a narrow custom-rate adapter where OCA would otherwise replace the historical company/foreign amount pair;
2. copy finalized source bank journal items and partial-reconciliation rows into the exact target;
3. implement a project-specific matching and foreign-exchange engine.

Option 1 is selected. It preserves the native statement/OCA workflow and uses Odoo's supported foreign-currency countervalue on `34` newly created foreign-journal transactions whose historical EUR countervalue no longer equals the current date rate. The adapter removes only OCA's proposed exchange candidate and retains the source operator's exact company/transaction-currency candidate pair; OCA still creates the bank journal items and partial reconciliations. Option 2 remains the historical truth path but cannot prove an operational workflow. Option 3 is rejected because it would duplicate maintained Odoo/OCA matching behavior.

The clean stage covers `233` source bank transactions and `339` direct document/bank partial-reconciliation edges against `241` native receivable/payable lines. It reuses `8` bank lines and `83` partials already proved by expense settlement, creates the remaining `225` bank lines, creates `256` additional partials, and extends one prior mixed employee transfer through native General Reconciliation. All `339` source company-currency and transaction-currency partial amounts and endpoints match; all `241` due-line transaction-currency residuals match; a rerun reuses all `233` bank lines and `339` document edges with no duplicate trace. `170` bank lines contain only current document allocations. Across the other `63`, OCA preserves `48` outside-only source counterpart lines exactly; only source lines themselves split across perimeters retain a bounded residual.

`make accounting-track-b-general-reconciliation` then proves every current-period non-bank document reconciliation. Three credible approaches were compared:

1. post the `CCAVV` shareholder-current-account and `MISC` compensation/clearing entries from journal-entry inputs, then use native General Reconciliation for those document edges and document-to-document netting while retaining Odoo/OCA-generated exchange differences;
2. copy the finalized source manual-entry, partial-reconciliation and exchange-difference rows, which preserves history but does not prove the target operational workflow;
3. implement project-specific reconciliation and exchange-difference engines.

Option 1 is selected. Standard draft `account.move` creation and `action_post` prove the manual-entry workflow, and native `reconcile()` behavior proves the reconciliation transition. Option 2 remains the historical-truth path in the exact target. Option 3 is rejected because it would duplicate maintained Odoo/OCA accounting behavior.

The clean run covers all `111` source non-bank partial reconciliations and their `114` current-document endpoints. It posts `21` manual entries with `72` exact accounting-input lines: `18` reconciliation-edge entries plus `3` standalone source operator entries whose balanced accounting effect is not owned by a downstream bank stage. It creates `68` manual-entry/document partials plus `3` document-netting partials and traces all `40` native exchange-difference partials. All manual moves are posted and balanced, all source transaction-currency partial amounts and endpoints match, and the final payment-state distribution exactly equals source: `159` paid and `2` reversed vendor bills, `84` paid purchase receipts, `1` paid and `2` partially paid supplier refunds, and `36` paid customer invoices. Odoo's native reconciliation produces two bounded one-cent company-currency differences; one case also has an extra one-cent exchange segment. These are retained as explicit rounding evidence rather than rewriting posted entries. A rerun creates nothing and reuses all `21` moves, `71` input partials and `40` exchange partials without duplicate traces.

`make accounting-track-b-bank-categorization` next covers every source bank transaction that has no external partial-reconciliation endpoint. Three credible approaches were compared:

1. recreate each bank transaction and replay the source operator's account, partner, analytic and transaction-currency categorization through OCA Bank Matching, while leaving source-unreconciled transactions open;
2. copy finalized source bank journal lines, which preserves history but does not prove operational categorization;
3. write counterpart journal lines directly through a project-specific categorization engine.

Option 1 is selected. The clean run creates `1,415` additional bank transactions, categorizes `1,229` through OCA and retains the source's `186` open transactions as open. The categorized population consists of interest, bank fees, internal-transfer allocations and bounded payable, shareholder, corporate-tax and investment-account allocations; exactly `5` carry analytic distributions. `908` foreign-journal transactions use Odoo's supported explicit company-currency countervalue while retaining the source journal-currency amount. Every bank header, liquidity effect and categorized counterpart account/partner/currency/analytic effect matches; all moves balance; there are no duplicate traces. A rerun creates or categorizes nothing and reuses all `1,415` bank lines and `1,229` categorizations.

`make accounting-track-b-bank-external` then covers the final `95` current-period bank transactions and their `125` source counterpart lines. Three credible approaches were compared:

1. reconstruct every bank move through OCA, post source payroll/tax/clearing inputs as standard manual journal entries, and reconcile only valid posted endpoints;
2. copy finalized source bank and endpoint journal lines, which preserves history but does not prove the replacement workflow;
3. force draft or post-cutoff document partials into the current graph, which would misstate Odoo's posting and period-cutoff rules.

Option 1 is selected. The clean run creates all `95` bank transactions and `125` exact account/partner/currency counterpart lines, posts or reuses `17` manual endpoint moves with `151` balanced lines and `46` analytic allocations, creates or reuses `75` native input partials, and traces `12` Odoo-generated exchange partials. Five input partials reuse exact source counterparts preserved by the expense/document settlement stages; the associated USD relationship also reuses its native exchange edge. When source account `471000` is both the company suspense account and the exact source allocation, the stage temporarily assigns an empty untraced `TBSUSP` account to the relevant source bank journal so OCA can perform a real categorization transition, then restores `471000`. Validation requires the staging account to have zero lines and zero balance before and after the pass. This brings native current-period bank coverage to `1,841/1,841`. A rerun creates nothing and reuses all `95` categorizations, `17` manual moves, `75` input partials and `12` exchange partials.

The remaining `48` source relationships are boundaries, not missing bank transactions: `37` draft-document edges and `2` draft exchange-entry edges remain open prepayments, while `9` receipts against three July 1 customer invoices remain post-cutoff prepayments. The five formerly bounded cross-bank edges now reconcile through exact traced counterparts, so no `preexisting_bounded_bank_aggregate` or `exchange_of_bounded_input` classification remains. No draft document is posted or reconciled to manufacture parity.

`make accounting-track-b-assets` proves the native fixed-asset workflow. Three credible approaches were compared:

1. use maintained OCA `account_asset_management`, seed its native depreciation board from the source business schedule and let OCA post each due entry;
2. recompute a fresh OCA schedule from acquisition values, which changes historical monthly amounts because the source already contains imported depreciation and prorata decisions;
3. copy the finalized source journal entries or keep only the read-only `rebuild.account.asset` evidence surface.

Option 1 is selected. It preserves the operator-facing OCA asset lifecycle and the source business decisions without copying finalized journal rows. The clean run creates `3` native assets, `2` account-specific profiles and `91` depreciation-board lines. OCA posts the `28` source-period depreciation entries; every date, amount and account total matches. The `63` future source schedule rows remain unposted native lines. A rerun creates nothing and reuses all `3` assets, `91` schedule lines and `28` moves. The manager browser journey opens the three assets and their posted/future board actions. The reviewer journey sees the same assets and posted move links but no create, recompute, confirm or reverse controls; server ACLs and a combined-view regression test enforce the boundary.

`make accounting-track-b-deferrals` proves the operational deferred-expense workflow. Three credible approaches were compared:

1. add a focused schedule model whose due lines post standard balanced `account.move` entries through `action_post`;
2. adopt or port OCA `account_spread_cost_revenue`;
3. retain only the existing imported schedule evidence or copy finalized source journal rows.

Option 1 is selected because no maintained OCA 19.0 deferral module is available in the pinned add-on set; the spread module is available only on older OCA branches and a milestone-time port would be migration-sensitive. The evidence-only alternative cannot support daily scheduling or controlled posting. The clean replay creates `5` deferred-expense records with `82` schedule lines, reuses/posts `34` current-period moves, retains `48` future lines, and represents one opening-boundary reversal. Every date, amount, account and analytic distribution matches, and a rerun reuses all records without duplication. The manager can create schedules and post due or individual lines. The reviewer can inspect schedules and linked entries but has no create or post control.

`make accounting-track-b-analytics` then runs after all Track B posting stages. Two materially different source values exist for `29` expense lines: the business-time expense distribution and a later finalized journal-item classification. Three treatments were compared:

1. replay the expense workflow from its business inputs, then apply only the source's explicit post-posting analytic correction through Odoo's supported analytic-distribution write and retain a read-only audit record;
2. pass the finalized journal-item distribution into expense creation, which would erase the distinction between business input and later classification;
3. copy analytic lines directly, which would bypass Odoo's allocation engine.

Option 1 is selected. The audit represents all `29` corrections and is idempotent. Across `13` source analytic accounts, source and target move-line allocation totals match exactly, source and target actual analytic-line totals match exactly, `324` directly traced analytic lines have no mismatch, and no analytic line is unmapped. Odoo's per-line currency rounding leaves a theoretical `+0.01/-0.01` pair between two analytic accounts; this is within company-currency precision and the actual analytic-line totals reconcile exactly to source. The browser journey verifies all `621` target analytic lines in the native list, multi-plan `Projet` and `Epic` values, pivot/XLSX and graph views, plus a read-only correction audit for manager and reviewer.

This proves current-period native expense approval/refusal/posting, invoice/bill/refund/receipt posting, expense-related bank matching, partial reimbursement allocation, direct commercial-document bank matching, non-bank document netting/manual-entry reconciliation, exchange-difference generation, all `1,841` bank transactions, native asset depreciation, operational deferrals, multi-plan analytics and final current-document/payment state. The selected design improves the earlier stages rather than creating duplicate manual moves; copying finalized source rows and direct journal-line surgery remain rejected because they would not prove the operational workflow. Remaining Track B work is deliberate draft/post-cutoff acceptance, undo behavior and closing acceptance.

## Hybrid replacement candidate

The exact historical target and Track B answer different questions, so neither is promoted in place. Three integration approaches were considered:

1. generate current native documents inside the exact-replay database, which would mix recomputed accounting effects with the historical audit baseline;
2. promote Track B alone, which would leave the exact pre-cutoff ledger outside the operational candidate;
3. clone the completed Track B state into a third disposable database and exact-import the benchmark history, reusing only native records whose source identity and accounting shape are validated.

Option 3 is selected. `make accounting-replacement-reset` creates `odoo_rebuild_accounting_replacement` only after Track B proves `284` documents, `325` expenses, `1,841` bank transactions, zero unbalanced posted moves and zero duplicate source move representations. `make accounting-replacement-import` then adds the `2024-01-10` through `2025-09-30` benchmark. It imports `2,046` moves and `4,809` lines while reusing exactly four validated native move aliases (`6727`, `6728`, `6730` and `6735`); a duplicate or shape mismatch blocks the import.

Historical validation is exact: source and target both contain `2,046` moves, `4,809` lines and debit/credit of `1,064,045.02`. The combined candidate contains `4,541` posted moves and `10,727` posted lines, with zero unbalanced moves and zero duplicate source identities.

Historical move identity is now blocking evidence, not only a count and amount
comparison. Three treatments were considered for the four reused native move
aliases: retain their newly generated native names, duplicate the source moves,
or keep the validated native accounting representation and restore its source
entry reference through Odoo's ORM. Retaining the generated names would
silently renumber history, while duplication would repeat accounting effects;
direct SQL mutation was also rejected because it bypasses Odoo's sequence and
lock machinery. The selected ORM path uses Odoo's scoped lock-check bypass for
this migration-only normalization and verifies the exact source name, date,
journal, sequence prefix and sequence number afterwards. The four aliases now
retain `OD000000003`, `OD000000004`, `OD000000006` and `OD000000011`.

Source and target sequence/chronology profiles now match exactly across all
`2,046` benchmark moves: no blank entry references, no duplicate names, no
duplicate sequence numbers, `2` source sequence gaps and `3` source
date-order decreases. The gaps and decreases already exist in the locked source
ledger; they are preserved rather than repaired and are exposed as a P2
accountant-owned source-anomaly discrepancy. Technical parity is complete, but
the accountant still needs to explain or accept those five source exceptions.

Current-period reconstruction remains native rather than exact-line replay. Relative to the source, it contains `199` fewer moves, `401` fewer lines and EUR `8,866.06` less gross debit/credit. Every difference is classified: Odoo's `EXCH` journal aggregates exchange effects differently; `CABA` uses native cash-basis timing and aggregation; and `SHINE`, `REVEU` and `REVUS` segment OCA bank allocations differently. The `12` differing account balances net to EUR `0.00`, with no unclassified journal or account difference. The remaining profit-and-loss balance difference is EUR `2.64`, attributable to native exchange timing.

The focused clean replacement sequence was repeated after the final FEC role
and UI refresh changes without restoring or re-extracting the unchanged
source. The runs beginning at `2026-07-23T10:57:27Z` and
`2026-07-23T12:01:40Z` reproduced the same Track B clone signature, exact
historical counts and totals, combined posted counts, four native aliases,
12 classified account differences, zero net account difference, zero
unclassified difference and EUR `2.64` profit-and-loss difference. This is the
deterministic comparison required for the second clean hybrid reconstruction.

The validation status is deliberately `partial`, classified as `HYBRID_REPLACEMENT_TARGET_EXPLAINED_NATIVE_DIFFERENCES`. Manager/reviewer report, bill and expense browser journeys now pass on the combined candidate. Professional acceptance of the EUR `2.64` difference and an explicit promotion decision remain required. The disposable candidate does not replace `odoo_rebuild_accounting_test` or authorize production migration.

`make accounting-target-validate` also proves:

- no unbalanced imported posted moves;
- no duplicate source traces for moves, move review records, document-regeneration case records, move lines, move-line review records, reconciliations, reconciliation review records, source report catalogue records, payments, payment review records, bank statement lines, analytic plans/accounts/lines, attachments, taxes, tax tags or assets;
- preserved USL lock dates, with a rollback-only protected-write check blocked by Odoo for a locked posted move dated `2024-01-10`.

`make accounting-target-idempotence` now proves accidental repeated import safety for the disposable target. The stage snapshots source-traced accounting consequence counts, posted-ledger debit/credit/balance totals, generated-draft totals, discrepancy counts and duplicate-trace invariants; reruns `make accounting-target-import` against the already processed target; reruns target validation; and then compares the same signature. The importer also preserves post-generation document-regeneration state, so a repeated import after `make accounting-document-regeneration` keeps the `189` validated candidate drafts validated, keeps the `5` review-only cases marked not applicable, and reintroduces `0` document-regeneration blockers. The latest clean rehearsal passed with `signature_matches = true`, `observed_import_run_delta = 1`, `target_validate_status = passed` and no duplicate-trace failures. The additional import-run row is expected audit evidence and is not an accounting consequence.

`make accounting-target-failure-tests` now provides rollback-only target conflict and invariant failure injections. It creates a duplicate source-trace record in `rebuild.account.move.review` inside a savepoint, verifies that the duplicate-trace detector sees one injected duplicate group, rolls the savepoint back, and then verifies both duplicate count and target signature returned to baseline. It also temporarily perturbs one imported posted journal item so a posted move becomes unbalanced, verifies the unbalanced-move detector sees the injected group, rolls back and verifies the target is clean. The same stage now injects an invalid `account_move_line.account_id`, an invalid `account_move_line_account_tax_rel.account_tax_id`, an invalid `account_partial_reconcile.credit_move_id`, corrupted checksum metadata on one imported accounting attachment and source attachment metadata pointing to a missing filestore file. It verifies PostgreSQL rejects the three missing-reference conditions through the target schema, verifies no orphan account, tax relation or reconciliation endpoint remains after rollback, verifies attachment checksum/store metadata mismatch detection fires while injected, verifies source-metadata-driven missing-file discrepancy creation fires while injected, and verifies both evidence probes disappear after rollback. The latest run passed with `baseline_duplicate_groups = 0`, `injected_duplicate_groups = 1`, `final_duplicate_groups = 0`, `baseline_unbalanced_groups = 0`, `injected_unbalanced_groups = 1`, `final_unbalanced_groups = 0`, `baseline_missing_account_lines = 0`, `final_missing_account_lines = 0`, `baseline_missing_tax_relations = 0`, `final_missing_tax_relations = 0`, `baseline_incomplete_reconciliations = 0`, `final_incomplete_reconciliations = 0`, `baseline_attachment_metadata_mismatches = 0`, `injected_attachment_metadata_mismatches = 1`, `final_attachment_metadata_mismatches = 0`, `baseline_missing_file_discrepancies = 0`, `injected_missing_file_discrepancies = 1`, `final_missing_file_discrepancies = 0` and `signature_matches_after_rollback = true`.

## Future reference-rate provider

Historical reconstruction and future automation deliberately use different paths. Three credible alternatives were assessed:

1. rely on the absent Enterprise live-currency module;
2. install a maintained OCA 19 currency updater;
3. add a focused adapter that writes official ECB reference rates through Odoo's native `res.currency.rate` model.

The checked Community/OCA dependency set contains no deployable automatic updater, so option 3 is selected for future reference rates. Historical source-traced rows remain immutable; the adapter skips any same-date row carrying a source trace. It parses the official ECB XML with entity and network resolution disabled, validates one date and positive finite rates, calculates cross-rates for a non-EUR company currency, limits the response size, and stores `ecb` plus the retrieval timestamp on native rate rows.

Accounting Managers configure and run it under:

```text
Accounting > Configuration > Currency Rate Automation
```

The daily cron is enabled and scheduled after the ECB's normal publication window. ECB rates are reference information: when a bank, card processor or platform conversion defines a transaction, preserve that actual conversion instead of replacing it with the reference rate.

`make accounting-currency-rate-provider` performs two live updates on `odoo_rebuild_accounting_test`, proves the second creates no row, checks the daily cron and provider trace, and verifies the source-traced historical-rate count is unchanged. The 2026-07-23 proof retrieved the 2026-07-22 feed, wrote GBP `0.8534` and USD `1.1408` per EUR, found no duplicate currency/date row, and preserved all `1,877` imported source rates. The manager browser journey passed, while the accountant-reviewer persona received the expected Accounting Administrator access error.

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

These read-only views are the audit/evidence layer under `Review > Advanced Audit`. Normal users reach reports through the canonical Accounting Report Workbench. The reports stage records both audit-view controls and live workbench controls in `reports-status.json`, including sampled source actions proving that ledger-backed report rows open contributing `account.move.line` records and analytic report rows open `account.analytic.line`.

The local Community code keeps the licensing boundary explicit: `addons/account/models/account_report.py` provides the shared `account.report` data model and tax-report expression machinery, while `addons/account/views/res_config_settings_views.xml` exposes `module_account_reports` as an `upgrade_boolean` labelled Dynamic Reports. There is no `account_reports` addon in the current Community fork. Three alternatives were assessed: expose OCA report wizards directly, depend on the absent proprietary Enterprise application, or build an original Community-compatible workbench while retaining OCA as a maintained accounting foundation. The third option is implemented. No Enterprise code is copied, and importing source `account.report` rows remains evidence preservation rather than executable-code reconstruction.

The addon exposes normal Accounting > Reporting launcher actions for the mandatory report families. The latest validation database update created `32` `rebuild.account.report.export.wizard` launch actions and `31` launcher menus under Odoo's standard legal statement, partner, management and tax/fiscal reporting menu groups. These actions preselect the relevant report type, benchmark dates and export format for Trial Balance, General Ledger, Journal Report, Partner Ledger, Customer Statement, Open Items, Aged Receivable, Aged Payable, Balance Sheet, Profit and Loss, VAT/Tax Report, tax grouping by account/tax, tax grouping by tax/account, French tax package/CA12 mapping, FEC, French annual statements, French Balance Sheet (2024 PCG), French Profit and Loss (2024 PCG), SIG and CAF (2024 PCG), bank reconciliation, currency report, cash flow, executive summary, analytic report, fixed assets, fixed assets by account, depreciation schedule, deferred schedule, EC Sales List, OSS Sales and OSS Imports. All primary launchers open the same full-page workbench with `target=current`; competing OCA/MIS report menus are hidden from normal navigation but their technical actions are retained.

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
`artifacts/accounting-compat/private/track-b-native-attachments-browser-status.json`.
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

The addon now exposes an Accounting Reconstruction Review summary under Accounting > Review > Rebuild Evidence. This read-only Odoo view aggregates each imported company's latest import run, imported posted-ledger totals, open discrepancy counts, pending/recorded review-decision counts, pending external report-value counts, document-regeneration case counts, source report coverage, source report parity levels and review-only workflow records, with buttons to open the latest import run, open discrepancies, review decisions, external values, document-regeneration cases, imported journal items, source report catalogue and report export wizard. The latest accountant-access harness check validates that the reviewer can open the Unstatic Labs summary, sees `blocked` readiness, `1` open P0 discrepancy, `1` open P1 discrepancy, `44` active pending review decisions, `2` pending external report values, `194` document-regeneration cases, `5` review-only document cases, `0` blocked document cases, all `38` source reports at Level 4 evidence partial, `11,386` visible imported USL move lines, `1,053` workflow/review records and no visible USL Media journal items under the USL-only reviewer company scope. It also runs a rollback-only recorded-decision propagation probe proving that an accountant reviewer can record an accepted-with-difference decision which updates the linked source report to `level_4_accepted`, marks the external value `accepted_with_difference` and marks the linked discrepancy `accepted`, while rolling back the probe records and preserving imported accounting evidence.

The addon also exposes `rebuild.account.review.decision` records under Accounting > Review > Rebuild Evidence > Review Decisions. These records are the durable acceptance surface for report parity, FEC validation, external tax values, discrepancy acceptance, deliberate scope exclusions and milestone closure gates. Discrepancies, source report catalogue records and external report values have `Record Review Decision` actions that prefill the gate, evidence key, period, source value, target value, difference, remaining risk and next action. Marking a decision as recorded now requires a non-pending conclusion and propagates that conclusion to the linked review evidence: accepted report reviews become `level_4_accepted`, accepted external values update their review status, and accepted discrepancy decisions update the discrepancy status and approver. Recorded and superseded review decisions are immutable; changed conclusions require superseding the old record and creating a new decision. Accountant reviewers can create and update draft review-decision records and external report-value evidence, but the harness and permanent addon tests continue to prove that they cannot mutate imported posted accounting evidence or directly edit discrepancy records.

The reports stage now seeds pending review-decision records after generating the technical report evidence. The current seed artifact, `artifacts/accounting-compat/private/review-decision-seed-status.json`, records `38` linked source-report review records, `2` linked open-discrepancy review records, `2` linked external-value review records, one FEC official-validation review record and one Milestone 13 closure review record. It also supersedes stale draft decisions linked to resolved discrepancy records while preserving any recorded decisions. The latest full clean target has no superseded review-decision rows and surfaces `44` active pending records. They create a durable review queue and do not constitute accountant or stakeholder acceptance.

The report-suite blocker is now refreshed from post-export evidence rather than import-time catalogue counts. When every active source report has a target equivalent and Level 4 technical evidence, the open P0 is classified as `legal_or_accounting_uncertainty`, with target value `38 Level 4 technical evidence packages; 0 accountant-accepted reports; 0 missing target equivalents`. This keeps closure blocked while accurately stating that the remaining report-suite gate is accountant/stakeholder acceptance of semantics, PCG/legal-form variants and deliberate scope exclusions rather than a currently missing target report family.

The discrepancy importer is idempotent for recurring end-of-run blockers. Re-running `make accounting-target-import` upserts the current blocker record and marks stale duplicates as `resolved`. The current clean target contains `2` open discrepancies (`1` P0 and `1` P1) plus resolved DGFiP FEC-validation, VAT CA12-clearing and document-regeneration discrepancies. The VAT CA12-clearing evidence now reconciles in a clean run and does not create a source-to-target discrepancy. Review decisions linked to resolved discrepancies are automatically superseded by the report-seed stage while recorded decisions are preserved.

`make accounting-readiness` now writes a durable Milestone 13 readiness assessment after comparison and before the evidence index. The JSON and Markdown artifacts summarize technical gate status, source and target identities, open discrepancies, review-decision queues, source-report parity evidence and the closure recommendation. The current readiness gate is expected to remain `blocked` while open P0/P1 discrepancies or draft accountant/stakeholder review decisions remain; it is a closure-control artifact, not a way to mark professional acceptance.

The same export wizard now exposes a FEC export backed by Odoo `l10n_fr_account`. The current harness generates `983982950FEC20250930.txt` through the Odoo UI wizard model in FEC test mode, with `4,781` data rows, debit `1,064,045.02`, credit `1,064,045.02` and SHA-256 `38d99b33b0f2864637a0506f61a52d33e73cd58ecd3ca9cdf6a6f69b740c53b1`. The generated exports identify company, source company id, dates, posted/draft scope, selected filters, format and row count.

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

The EC/OSS review view is ledger-derived and explicitly review-only. The closed benchmark period has no EC Sales, OSS Sales or OSS Imports rows. The current-period EC Sales List view validates `4` rows for partner `ARTEMISA 3000 TECH SOLUTIONS SL.`, country code `ES` derived from the VAT prefix when the partner country is missing, taxable amount `37,555.12`, and journal/account breakdown across `INV`, `CABA` and account `706000`. OSS Sales and OSS Imports currently export as explicit empty reports for the current source corpus. These reports are target equivalents for the active source EC/OSS report definitions; they are not accepted tax filings.

The French annual-statement view currently validates the benchmark anchors for:

- total gross assets, depreciation and net assets;
- total passif and current-year result;
- turnover, operating result, current result before tax and net result;
- value added, gross operating surplus and cash-flow capacity.

The generated private export is:

```text
artifacts/accounting-compat/private/french-annual-statements-2025-09-30.json
artifacts/accounting-compat/private/french-annual-statements-2025-09-30.csv
```

The mapping is still USL-specific reconstruction evidence. Full statutory/French report semantics, complete Enterprise-style report behaviour, statutory PDF layouts, form-specific exports and accountant approval remain open.

The imported depreciation schedule preserves the source `account_asset` schedule evidence reconstructed from source asset-linked depreciation moves. The current clean rehearsal compares `91` source schedule rows to `91` target rows with no missing, extra or mismatched rows. The schedule spans the source depreciation plan through `2028-06-30`; posted entries inside the replay scope remain imported as journal entries, while future draft schedule rows are review evidence rather than posted accounting effects.

The imported deferred schedule view preserves source `account_move_deferred_rel` evidence. The current clean rehearsal imports and compares all `110` source relations: all are deferred-expense rows, `37` link to imported posted entries and `73` are source draft forecast rows. No source deferred-revenue rows are present in the current corpus.

The importer now represents all `194` non-posted source `account.move` records as `rebuild.account.move.review` records. These include draft entries, draft supplier documents, one draft receipt and cancelled entries from the approved source companies from `2024-01-10` onward. They preserve source state, move type, journal, partner, currency, source name/ref, dates, monetary totals, source line counts, source line debit/credit/balance totals and trace metadata, but intentionally create no posted target journal entry. The target validation compares all `194` source rows to all `194` target review records with no missing, extra or mismatched rows.

The importer also derives `194` `rebuild.account.document.regeneration.case` records from those non-posted source moves. These records provide an audited Odoo workbench for the separate document-regeneration mode without creating native draft target documents in the exact replay baseline. The current target validation compares all `194` source-derived expected cases to all `194` target cases with no missing, extra or mismatched rows. The current classification is `189` ready candidates and `5` review-only/not-applicable cases: `37` draft business documents, `152` draft journal entries, `2` cancelled source records and `3` zero-line draft records without accounting effect. `make accounting-document-regeneration` now exercises all `189` candidate-ready cases in an isolated native draft-generation pass: `37` draft business documents and `152` draft journal entries are generated or reused as target `account.move` drafts, validated against preserved source line counts and debit/credit totals, and kept out of posted ledger controls. The latest artifact records `189` validated generated drafts, `5` review-only not-applicable cases, `0` blockers, `0` mismatches, `0` not-generated candidate cases and `0` posted generated moves; the previous document-generation P2 discrepancy is resolved.

The importer also represents all `467` source move-line workflow review rows as `rebuild.account.move.line.review` records. These include `466` non-posted source move lines and the single posted source display line with no `account_id`. The non-posted rows preserve source state, move, sequence, account, display type, partner, debit, credit, balance, amount in currency, tax base, source tax IDs, source tax tag IDs and trace metadata while intentionally creating no posted target journal item. The posted display line preserves the source move, sequence, display type, label and source trace while intentionally creating no target journal item. The target validation compares all `467` source rows to all `467` target review rows with no missing, extra or mismatched rows. In Odoo, a source move review has a `Source Lines` smart button that opens the preserved source move-line review rows without mixing them into the posted ledger baseline.

The importer now represents all `13` source `account.payment` workflow records that have no source journal entry (`move_id IS NULL`) as `rebuild.account.payment.review` records. These records preserve source company, journal, partner, amount, payment type, state, raw nullable source flags and source trace metadata, but intentionally create no debit or credit because the source has no accounting move to replay. The target validation compares all `13` source rows to the `13` target review records with no missing, extra or mismatched rows.

The importer now represents `75` cross-boundary source reconciliation relationships as `rebuild.account.reconciliation.review` records. These are the `39` partial reconciliations and `36` full reconciliations that touch at least one imported source journal item and at least one source endpoint outside the selected posted replay boundary. The current breakdown is `39` posted-to-draft partial reconciliations totaling `4,082.49`; `34` full reconciliations with one missing draft endpoint totaling `2,141.81`; and `2` full reconciliations with two missing draft endpoints totaling `940.68`. They preserve source partial/full reconciliation identity, source endpoint line and move IDs where applicable, missing endpoint source move IDs, source move states, source move dates, imported/missing endpoint counts, amounts, max date, company scope and trace metadata. After `make accounting-document-regeneration`, every review row has `all_generated_draft` endpoint coverage: `77` missing source-line mentions resolve to generated target draft lines traced as `account.move.line.document_regeneration`. The review form exposes both imported posted endpoints and generated draft endpoints for inspection. They intentionally do not complete the native target reconciliation graph while those generated endpoints remain draft records. The target validation compares all `75` source review rows to all `75` target review rows with no missing, extra or mismatched rows.

`make accounting-target-reconciliation-probe` adds a rollback-only technical probe for this boundary. It samples a posted-to-draft cross-boundary partial reconciliation, resolves the imported posted endpoint and generated draft endpoint, attempts to create a native `account.partial.reconcile` inside an Odoo savepoint, forces rollback and verifies the native partial count returns to baseline. The stage updates the cross-boundary reconciliation discrepancy with the probe result. Passing this probe proves capability for one representative native partial; it does not authorize applying draft-endpoint reconciliations in the exact posted replay baseline, because that would alter residual/matching presentation outside the posted-ledger replay scope.

The reconciliation review form now exposes a controlled native-application workflow for partial boundary rows. `Preview Native Partial` opens the exact imported/generated endpoint journal items that would be reconciled. `Record Decision` creates a review decision linked to the specific `rebuild.account.reconciliation.review` row. `Apply Native Partial` remains blocked unless the user is an Accounting Manager, all missing endpoints have generated draft coverage, and a recorded review decision linked to that boundary row has conclusion `accepted` or `accepted_with_difference`. When those gates pass, the action creates or reuses one source-traced `account.partial.reconcile` and marks the review row `native_reconciliation_applied`; repeated application is idempotent. Permanent addon tests cover the unauthorized-user block, missing-decision block, endpoint preview, recorded-decision requirement and no-duplicate behaviour. The full harness still leaves these rows review-only by default.

The user-facing queue now defaults to `Pending Policy Review`, which keeps all
`75` represented rows visible after document generation instead of applying an
empty `Review Required` filter. The evidence list and form disable direct
create/edit/delete controls. `Preview Full Scope` resolves and opens every
imported/generated source line for a full boundary in one read-only action.
Full-row decision copy explicitly offers review-only acceptance or a separately
authorized full-graph workflow; it does not claim that one partial application
can recreate a full reconciliation. The `Apply Native Partial` button is
visible only to Accounting Managers.

The read-only manager/reviewer browser journey passed on
`odoo_rebuild_accounting_test`: both roles saw `39` partial and `36` full rows
under the default filter, opened the balanced EUR `47.72` imported/generated
pair for source partial `3056` and full reconciliation `1391`, and reached the
decision surface. The reviewer saw preview/decision controls but no apply,
configuration or direct-edit control. The manager's unsaved full-row decision
showed the review-only/separate-workflow choice and was discarded. Database
counts remained `2,534` native partials, `0` linked decisions and `0` applied
boundary rows. Private evidence is in
`reconciliation-review-browser-status.json`.

The importer now represents all `38` active source `account.report` records as `rebuild.account.source.report` catalogue records. These records preserve source report identity, English and French names, country, root report, custom handler model, source filter flags, line/column/expression/external-value counts, line-code samples, expression-engine summary, parity decision, target-equivalent status, target evidence key, parity level and trace metadata. The current rule set classifies `23` reports as `MANDATORY_PARITY`, `10` as `OPERATIONAL_PARITY`, `3` as `ACCOUNTANT_REQUESTED` and `2` as `REMOVED_AS_UNUSED` association reports. All `38` reports now have a partial target equivalent or explicit legal-form scope decision, and `0` active source reports are missing an assigned target treatment. The importer also preserves the source report structure as Odoo evidence records: `702` report lines, `1,227` expressions and `141` columns. Target validation and the source-target comparison artifact compare all source report catalogue, line, expression and column rows with no missing, extra or mismatched records. This is a review catalogue, not a copy of Enterprise report code.

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

The imported French tax-package mapping is a review surface, not an automatic filing engine. It exposes `31` ledger-derived and evidence-derived lines for 2065-SD, 2033-A/B/C/D and 3517-S-SD/CA12. The current harness checks key values including taxable-profit review amount `66,144.98`, total net assets `69,680.16`, net result `56,222.98`, fixed-asset gross value `10,430.49`, accumulated depreciation `1,676.05`, VAT collected `459.00`, VAT credit carryover `3,442.00` and deductible VAT on goods/services CA12 clearing amount `1,960.00`, while preserving gross account `445660` turnover of `3,014.09` as ledger evidence.

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
- read imported non-posted source move workflow review records;
- read imported source move-line workflow review records;
- read imported source payment workflow review records;
- read imported source reconciliation boundary review records;
- read the imported source accounting report catalogue;
- open the review-decision queue from the Accounting Reconstruction Review summary;
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

It exited with status `0` and ran the addon-scoped post-install test class tagged `rebuild_account_migration_unit`. The tests lock down accountant read-only ACL behavior, external report-value review/provenance handling, imported accounting attachment readability, private technical attachment blocking, report export metadata, in-wizard report preview metadata, report-launcher action contexts, FEC/statutory filter guardrails, FEC preview guardrails, reconciliation review drill-down domains, non-posted source move-line review preservation and document-regeneration case classification/access. This is a first permanent regression layer; it does not replace the private production-derived harness comparisons or accountant review.

## Current FEC status

The harness generates a benchmark FEC through Odoo `l10n_fr_account` in test mode:

- path: `artifacts/accounting-compat/private/fec-usl-2025-09-30.txt`;
- data rows excluding header: `4,781`;
- debit and credit totals: `1,064,045.02`;
- current SHA-256: `38d99b33b0f2864637a0506f61a52d33e73cd58ecd3ca9cdf6a6f69b740c53b1`.

The FEC export reconciles to the imported target ledger. The Odoo-facing export wizard produces the same file hash for the benchmark period. It has passed the current DGFiP Test Compta Demat source validation route described below, but it has not been accepted by the accountant.

`make accounting-fec-preflight` runs a deterministic local structural preflight derived from article A47 A-1 of the Livre des procédures fiscales. The current preflight passes and records:

- separator: pipe;
- required first 18 fields: present and ordered;
- entry groups by journal and entry number: `2,032`;
- invalid row/date/amount/account counts: `0`;
- chronology decreases: `0`;
- unbalanced entry groups: `0`;
- rows with lettering: `2,239`;
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

## Remaining blockers

The target import deliberately remains `partial` while these items are open:

- the canonical Community-compatible workbench is technically validated for native-ledger filters, comparisons, grouping, folding, drilldowns and screen-consistent CSV/XLSX/PDF output, but it is not the proprietary Enterprise `account_reports` application and its accounting semantics still require independent acceptance;
- the source report catalogue and source report line/expression/column structure are now represented in Odoo and all `38` active source reports have Level 4 evidence-partial technical packages or explicit legal-form scope-exclusion evidence, but no report has Level 4 accepted parity and the 2024 PCG/accountant/legal-form decisions still need final acceptance;
- `3` accountant-requested source grouping reports now have target grouped actions, export packages and preview source-action evidence, but still need stakeholder acceptance before final parity;
- non-posted source moves and their source lines are represented as workflow review records, and all `189` candidate-ready cases are regenerated as target draft moves with matching preserved accounting lines; the remaining `5` cancelled or line-incomplete cases need review-only acceptance or explicit scope decisions;
- source payment records without journal entries are represented as workflow review records and are deliberately not replayed as accounting effects;
- some reconciliation relationships cross the selected replay boundary and are represented as review records with generated draft endpoint coverage, but are not yet applied to the native target reconciliation graph while those endpoints remain draft records;
- repeated exact-ledger import no longer duplicates accounting consequences, and rollback-only duplicate source-trace, unbalanced posted-move, missing-account FK, missing-tax FK, incomplete-reconciliation FK, imported attachment checksum-metadata corruption detection and source-metadata-driven missing-file discrepancy creation are covered; malformed source filestore directory detection is also covered;
- French tax-package mapping is still a ledger-derived review surface; exact official box mapping, reduced-rate IS eligibility/ceiling, reintegrations, deductions, deficits and external declaration values remain open.
- FEC structural validation now passes through the DGFiP source validator route, but the FEC still needs accountant review and final professional acceptance;
- accountant access is technically checked for read-only accounting review and sampled attachment privacy, but real external-accountant onboarding and accountant workflow acceptance remain open.

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
