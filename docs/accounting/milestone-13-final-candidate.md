# Milestone 13 final Accounting v1 candidate

Status date: 25 July 2026

Branch: `saas-19.2-usl-feat-accounting`

Upstream baseline: `8a44ecc8da96e341ac472fec27352d138ed2edd7`

Product candidate database: `odoo_dev`

Source database: `odoo_online_source_saas_19_2` (read-only)

Source snapshot: `source-ee6d9789224a`

Source dump SHA-256:
`ee6d9789224a7a8ba1d9048c813939a41ffed77e13fad3b65be246cfc3f83c9e`

## Release decision

Accounting v1 is technically ready for daily operations and merge. The final
readiness classification is
`TECHNICAL_PRODUCT_GATES_PASSED_ADVISORY_REVIEWS_REMAIN`: there are no
engineering blockers, P0 defects or unclassified target accounting
differences.

Two source/accountant advisories remain visible by design:

1. 75 cross-boundary reconciliation relationships remain review evidence
   because the other source endpoint is draft or outside the exact posted
   replay boundary. A rollback-only native partial-reconciliation probe passed.
2. 16 source sequence gaps and 104 source date-order decreases are preserved
   exactly. The target introduces no additional chronology exception.

Professional approval, live filing and production cutover are operational
decisions outside this engineering release.

## Clean reconstruction and parity

The final release harness restored the current source, completed independent
exact and native validation tracks, and reconstructed the product candidate
from scratch. `dev-validate-status.json` was generated at
`2026-07-25T21:02:13Z`.

| Object | Verified count |
| --- | ---: |
| Accounting moves | 5,039 |
| Posted / draft moves | 4,849 / 189 |
| Imported posted move lines | 11,404 |
| Business documents | 344 |
| Native expenses | 360 |
| Payments / payment-evidence records | 97 / 13 |
| Bank statement lines | 3,046 |
| Partial / full reconciliations | 2,531 / 1,210 |
| Historical currency rates | 1,889 |
| Analytic lines | 632 |
| Assets / schedule lines / posted depreciation links | 3 / 91 / 28 |
| Deferred schedule lines / posted entries | 110 / 37 |
| Imported / main attachments | 332 / 224 |

For 1 October 2025 through 30 June 2026, source and target both contain
2,694 posted moves and 6,319 journal items with EUR 1,708,270.52 debit and
credit. Account differences: 0. Journal differences: 0. The historical
benchmark through 30 September 2025 likewise matches exactly: 2,046 posted
moves, 4,809 journal items, and EUR 1,064,045.02 debit and credit.

The native replay independently passed expenses, commercial documents,
settlements, assets, deferrals, general and bank reconciliation, external bank
flows, historical currencies and analytics. It is proof of reproducibility in
`odoo_saas_19_2_validation_native`, not a second product ledger.

## Product acceptance

- Overview is the daily cockpit; Journals retains native journal cards.
- Transactions, Bank Matching, General Reconciliation and Matched Items/Undo
  are separate, purpose-specific journeys.
- Accounting Hygiene and Closing use the same 21 configurable, versioned
  controls. Reports and Declarations are also governed definitions under
  Accounting Configuration.
- The canonical report workbench provides period/comparison filters, compact
  hierarchy, search, fold/unfold, journal-item drill-down and screen-consistent
  PDF/XLSX. All 38 source report definitions have accepted level-4 technical
  evidence; unused association reports are explicitly excluded.
- Analytical Reporting uses native list, pivot and graph views over 632
  analytic lines. Analytic Profit and Loss remains the designed financial
  statement; Revenue vs Spending shows net contribution as a derived measure.
- The Accounting Manager retains operational actions. The scoped accountant
  can inspect, filter, drill down and export but cannot create, post, match,
  undo, configure, lock or suspend services.
- French electronic-invoice reception is implemented and representative
  valid, duplicate, malformed and rejected documents are regression-tested.
  The readiness screen says **Implemented and Validated**,
  **Configuration Required** and **Not Connected**. Production provider
  registration, endpoints and scheduled exchange remain inactive.
- Reconstruction, import and comparison objects are not exposed in normal
  Accounting navigation.

## FEC and reports

The benchmark FEC contains 4,781 data rows and balances at
EUR 1,064,045.02 debit and credit. SHA-256:
`05c8e064307bc0ff1387695625059179951aadd5b5dc9a126258abcb1a857fe0`.
Local structural preflight reports zero invalid rows, chronology decreases or
unbalanced entries. The official DGFiP Test Compta Demat source validator
exited 0 with zero blocking logs.

Dynamic report smoke checks, drill-downs and CSV/PDF/XLSX exports pass. Trusted
French statement anchors include EUR 69,680.16 total assets/passif,
EUR 56,222.98 net result, EUR 66,180.70 operating result and
EUR 57,899.03 CAF. These are technical ledger-derived results, not an
unsupported claim of filed or professionally approved statements.

## Validation and evidence

| Gate | Command | Evidence |
| --- | --- | --- |
| Clean exact and native reconstruction | `make accounting-compat` | `source-*-status.json`, `validation-exact-*-status.json`, `validation-native-*-status.json`, `dev-*-status.json` |
| Candidate parity | `make accounting-compare` | `compare-status.json`, `dev-validate-status.json` |
| Reports and exports | `make accounting-reports` | `reports-status.json`, `parity-matrix-v1.json`, generated report files |
| Currency provider | `make accounting-currency-rate-provider` | `currency-rate-provider-status.json` |
| FEC | `make accounting-fec`; `make accounting-fec-preflight`; `make accounting-fec-validate` | `fec-status.json`, `fec-structural-preflight.json`, `fec-validation-status.json`, `fec-dgfip-source-validation/` |
| Add-on regression | `make accounting-addon-tests` | 104 tests passed |
| Harness regression | `python3 -m unittest discover -s accounting_compat/tests -v` | 10 tests passed |
| Role browser acceptance | focused manager/reviewer walkthrough | `replacement-browser-status.json` |
| Release gate | `make accounting-readiness`; `make accounting-evidence` | `readiness-assessment.json`, `readiness-assessment.md`, `evidence-index.json` |

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
