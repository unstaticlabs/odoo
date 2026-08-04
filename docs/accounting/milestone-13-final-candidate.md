# Milestone 13 final Accounting v1 candidate

Status date: 26 July 2026

This is retained as historical reconstruction evidence. For the current
electronic-invoice product and activation state, use
`french-electronic-invoicing-readiness.md` and
`../operations/activate-french-electronic-invoicing.md`; their verified
readiness terms supersede the snapshot wording in this record.

Branch: `saas-19.2-usl-feat-accounting`

Upstream baseline: `8a44ecc8da96e341ac472fec27352d138ed2edd7`

Product candidate database: `odoo_dev`

Source database: `odoo_online_source_saas_19_2` (read-only)

Source snapshot: `source-ee6d9789224a`

Source dump SHA-256:
`ee6d9789224a7a8ba1d9048c813939a41ffed77e13fad3b65be246cfc3f83c9e`

## Release decision

The clean final replay passes the source/target parity, retry-idempotence,
native trace, product count, balance, report, attachment and local FEC gates.
There is no P0 or P1 migration defect. Migration placeholder records are not
an accepted representation of source accounting truth and none of the former
move, move-line, payment, document-regeneration or reconciliation review
models or tables exists.

The complete reconciliation graph is imported because all former boundary
endpoints belong to native draft documents in the scoped companies. The
remaining source-data advisory is 16 source sequence gaps and 106 source
date-order decreases, preserved exactly without introducing a target-only
chronology exception.

The current Online source natively records and reconciles the EUR 942 DGFiP
VAT refund on account 445670. The distribution preserves that accounting and
its reconciliation links unchanged; no target-only correction entry or
bank-line normalization remains. For unnamed source drafts, the native Odoo
display sentinel `/` represents the source SQL `NULL`; no posted number or
sequence is normalized.

Professional approval, live filing and production cutover are operational
decisions outside this engineering release.

## Clean reconstruction and parity

The final release harness restored the current source, completed independent
exact and native validation tracks, and reconstructed the product candidate
from scratch. `dev-validate-status.json` was generated at
`2026-07-26T17:05:39Z`.

| Object | Verified count |
| --- | ---: |
| Accounting moves | 5,044 |
| Posted / draft / cancelled moves | 4,849 / 193 / 2 |
| Native move lines, all states | 11,871 |
| Business documents | 348 |
| Native expenses | 360 |
| Native payments, including no-entry historical records | 110 |
| Bank statement lines | 3,046 |
| Partial / full reconciliations | 2,584 / 1,260 |
| Historical currency rates | 1,889 |
| Analytic lines | 632 |
| Assets / schedule lines / posted depreciation links | 3 / 91 / 28 |
| Deferred schedule lines / posted entries | 110 / 37 |
| Accounting attachments reconstructed / readable | 704 / 704 |

For 1 October 2025 through 30 June 2026, source and target both contain
2,694 posted moves and 6,319 journal items with EUR 1,708,270.52 debit and
credit. Account differences: 0. Journal differences: 0. The historical
benchmark through 30 September 2025 likewise matches exactly: 2,046 posted
moves, 4,809 journal items, and EUR 1,064,045.02 debit and credit.

The clean `odoo_dev` replay independently passed expenses, commercial
documents, assets, deferrals, complete reconciliation, bank lines, historical
currencies, analytics and attachments. The separate validation databases are
disposable proofs, not product ledgers.

## Product acceptance

- Overview is the daily cockpit; Journals retains native journal cards.
- Transactions, Bank Matching, General Reconciliation and Matched Items/Undo
  are separate, purpose-specific journeys.
- Accounting Hygiene and Closing use the same 21 configurable, versioned
  controls. Reports and Declarations are also governed definitions under
  Accounting Configuration.
- The canonical report workbench uses professional French accounting
  terminology and provides period/comparison filters, compact
  hierarchy, search, fold/unfold, journal-item drill-down and screen-consistent
  PDF/XLSX. All 38 source report definitions have accepted level-4 technical
  evidence; unused association reports are explicitly excluded.
- Analyse analytique uses native list, pivot and graph views over 632
  analytic lines. Analytic Profit and Loss remains the designed financial
  statement; Revenue vs Spending shows net contribution as a derived measure.
- The Accounting Manager retains operational actions. The scoped accountant
  can inspect, filter, drill down and export but cannot create, post, match,
  undo, configure, lock or suspend services.
- French electronic-invoice reception now covers UBL, CII and Factur-X
  invoices and credit notes, duplicates, malformed/rejected documents,
  retained evidence and visible retry recovery. The current readiness screen
  uses **Configuration incomplete**, **Not yet verified**, **Test passed**,
  **Ready but inactive** and **Production activation required**. Provider
  eligibility, live registration, directory activation, scheduled reception
  and e-reporting remain inactive pending the production runbook.
- Import and comparison infrastructure is not exposed in normal Accounting
  navigation.

## FEC and reports

The benchmark FEC contains 4,781 data rows and balances at
EUR 1,064,045.02 debit and credit. SHA-256:
`46dfd0a9b3708087309c05875b018f18365724e5bf9f72cd1373e7e6db1707aa`.
Local structural preflight reports zero invalid rows, chronology decreases or
unbalanced entries. Running the current official external DGFiP validator and
professional acceptance remains deliberately outside this engineering pass.

Dynamic report smoke checks, drill-downs and PDF/XLSX exports pass. Trusted
French statement anchors include EUR 69,680.16 total assets/passif,
EUR 56,222.98 net result, EUR 66,180.70 operating result and
EUR 57,899.03 CAF. These are technical ledger-derived results, not an
unsupported claim of filed or professionally approved statements.

## Validation and evidence

| Gate | Command | Evidence |
| --- | --- | --- |
| Clean exact reconstruction | `make accounting-validation-exact-reset`; `make accounting-validation-exact-import`; `make accounting-validation-exact-validate` | `validation-exact-*-status.json` |
| Retry safety and obsolete-model absence | `make accounting-validation-exact-idempotence`; `make accounting-validation-exact-failure-tests` | `validation-exact-idempotence-status.json`, `validation-exact-failure-tests-status.json` |
| Clean product reconstruction | `make accounting-dev-reset`; `make accounting-dev-import`; `make accounting-dev-attachments`; `make accounting-dev-validate` | `dev-*-status.json` |
| Candidate parity | `make accounting-compare` | `compare-status.json`, `dev-validate-status.json` |
| Reports and exports | `make accounting-reports` | `reports-status.json`, `parity-matrix-v1.json`, generated report files |
| Currency provider | `make accounting-currency-rate-provider` | `currency-rate-provider-status.json` |
| FEC | `make accounting-fec`; `make accounting-fec-preflight` | `fec-status.json`, `fec-structural-preflight.json` |
| Add-on regression | `make accounting-addon-tests` | Passed on a fresh disposable test database |
| Harness regression | `python3 -m unittest discover -s accounting_compat/tests -v` | 6 tests passed |
| Role access | report-suite manager/reviewer probes | `reports-status.json` |
| Attachment reconstruction | `make accounting-dev-attachments`; `make accounting-attachment-audit` | `dev-attachment-replay-status.json`, `attachment-reconstruction-status.json` |

Private evidence is under `artifacts/accounting-compat/private/` and remains
uncommitted. It contains production-derived identifiers and must not be
published.

## Explicitly deferred

- professional sign-off and live tax/electronic filing;
- selection and activation of a production approved e-invoicing platform;
- live bank synchronization and provider ingestion;
- probabilistic/AI matching and autonomous posting;
- production deployment and cutover from the disposable `odoo_dev`
  environment.

These are the complete remaining gaps. Source chronology anomalies and the
native draft-name sentinel are preserved/declared source semantics, not
unfinished migration work. There is no remaining P0/P1 data, reconciliation,
attachment, report-definition or obsolete-model gap.
