# Milestone 13 final Accounting v1 candidate

Status date: 25 July 2026
Branch: `19-usl-feat-accounting`
Implementation commit under verification: `412798935b8`
Product database: `odoo_dev`
Source snapshot: `source-bf16ce18965e`
Source dump SHA-256:
`bf16ce18965e4ce1b23d7b79930b6e43ca7f510339ac6d2db280231f91d1449f`

## Release decision

Accounting v1 is **ready for internal daily use with documented assumptions**.
The automated readiness classification is
`TECHNICAL_PRODUCT_GATES_PASSED_ADVISORY_REVIEWS_REMAIN`: no technical gate,
P0 or engineering P1 blocker remains. Professional approval and external
filing are outside the engineering milestone.

The two remaining advisories are source/scope facts:

1. 75 cross-boundary reconciliation references are retained as review records
   because their missing endpoint is draft or outside the exact posted slice.
2. 16 source sequence gaps and 104 source date-order decreases are preserved
   exactly rather than silently resequenced.

## Clean product reconstruction

The final clean reset started at `2026-07-25T07:26:43Z`; import completed at
`2026-07-25T07:29:37Z`; final validation ran at
`2026-07-25T07:50:03Z`.

`odoo_dev` contains:

| Object | Count |
| --- | ---: |
| Accounting moves | 5,033 |
| Posted / draft moves | 4,843 / 189 |
| Imported posted move lines | 11,392 |
| Business documents | 344 |
| Customer invoices visible in normal UI | 40 |
| Vendor documents visible in normal UI | 301 |
| Native expenses | 360 |
| Payments plus payment-evidence records | 97 + 13 |
| Bank statement lines | 3,040 |
| Partial / full reconciliations | 2,531 / 1,210 |
| Historical currency rates | 1,877 |
| Rate currencies and coverage | EUR, GBP, USD; 2024-01-01 to 2026-07-20 |
| Analytic lines | 632 |
| Assets / schedules / linked posted depreciation moves | 3 / 91 / 28 |
| Deferred schedule lines / posted entries | 110 / 37 |
| Imported attachments / main attachments | 332 / 224 |

All 190 eligible non-posted regeneration cases became validated native drafts.
The other four of the historical 194 cases are accepted review-only records:
one cancelled source record and three records without accounting lines. There
are zero blocked, incomplete or mismatched cases.

For 1 October 2025 through 30 June 2026 the source and target both contain
2,694 posted moves and 6,319 lines, with debit and credit of
EUR 1,708,270.52. Account differences: 0. Journal differences: 0.

## Native Track B replay

Final status: **complete for the bounded 2025-10-01 through 2026-06-30
native-replay scope**. The result is isolated in `odoo_validation_native`; it
is a genuine native-engine proof, not the product database and not a hybrid
candidate.

| Category | Native treatment | Result |
| --- | --- | --- |
| Customer invoices | Draft objects created and posted through ORM | 36 |
| Customer credit notes | No eligible source case | 0 |
| Vendor bills | Draft objects created and posted through ORM | 161 |
| Vendor refunds | Draft objects created and posted through ORM | 3 |
| Purchase receipts | Draft objects created and posted through ORM | 84 |
| Expenses | Native `hr.expense` workflow | 325 |
| Payments | Native payment/state and settlement workflows | 97 company payments; 95 employee expenses paid |
| Bank statement lines | Chronological native statement-line reconstruction and categorization | 1,841/1,841 bounded lines represented |
| Assets | Native OCA assets and schedules | 3 assets; 28 posted depreciation moves; 91 schedule lines |
| Deferrals | Native deferral/opening workflow | 5 deferrals; 34 posted deferral moves; 1 opening boundary |
| Miscellaneous entries | Normal ORM-created journal entries | 21 manual entries |

Source journal entries corresponding to replayed business documents were
excluded from native creation. Source identities and account totals were used
as comparison evidence; they were not imported a second time. This prevents
double accounting.

Native documents: 284 eligible, 284 passed, 0 partial, 0 failed, 0 blocked.
Of those, 205 were newly created and 79 reused an already-created native
representation. Document types are 36 customer invoices, 161 vendor bills,
84 purchase receipts and 3 supplier refunds. Posting used normal Odoo ORM
actions, not direct SQL final-state insertion.

Settlement/reconciliation proof includes 106 expense bank lines and 181
expense edges; 233 commercial-document bank lines and 256 reconciliation
edges; 71 input partials, 40 traced exchange partials and 21 manual entries;
1,229 directly categorized bank lines, 186 deliberately open lines; and 95
external bank lines with 73 created partials plus 12 exchange partials.
Every stage reports zero material mismatch. The only generated rounding
segments are balanced +EUR 0.01/-EUR 0.01 pairs.

Historical rates were imported from the Online dump, not substituted by a
current provider. Native USD/GBP valuation therefore uses the dated Odoo rate
records. Representative posted invoices expose their historical conversion
rate, payment allocations and exchange-difference entries in the normal UI.

## Canonical product experience

- Overview is the operational cockpit; Journals is the native journal
  dashboard.
- Transactions and Bank Matching are separate. Transactions opens without a
  side panel. Bank Matching defaults to unreconciled items.
- The reconciliation panel defaults to Chatter. Reconcile uses the removable
  `Closest amount OR Closest date` filter and does not show a Difference
  column.
- General Reconciliation supports All, Unreconciled and Reconciled review,
  residuals, lettrage, partial/full state, Match, continued residual matching
  and Undo.
- Every normal report opens as a dedicated interactive page. The generic
  export wizard is confined to Advanced Audit.
- MIS is no longer installed or depended upon. It had no remaining unique
  product capability after the canonical reports replaced the two historical
  templates. OCA remains a supporting library where appropriate, not a
  competing reporting menu.
- Revenue vs Spending provides graph, pivot and list views with fiscal-year
  default, revenue, spending and separately selectable net contribution.
- Declarations is a permanent versioned schedule. Closing Workspaces use 14
  user-configurable control definitions exposed under Accounting
  Configuration.

All mandatory financial report launchers use the custom dedicated interactive
report action and shared filter/export contract. Assets and depreciation read
the native OCA asset models; Revenue vs Spending uses an Odoo analytical view;
FEC uses the French/native ledger export service plus USL access and test-mode
guards. Competing OCA report menus and the removed MIS templates are not
visible.

## Browser and role acceptance

The final manager/reviewer artifact is
`artifacts/accounting-compat/private/replacement-browser-status.json`.

Manager checks passed for:

- Overview, Journals, 777-line Shine Transactions and 63-item Bank Matching;
- reconciliation tab order/default and closest amount/date filter;
- 340-row General Reconciliation;
- 113-line fiscal-year Trial Balance, 598-line account drill-down, Back state,
  PDF and XLSX actions;
- 40 customer invoices, sample invoice lines/taxes/journal items/payments,
  USD rate and exchange differences;
- 301 vendor documents and all 360 expenses;
- Revenue vs Spending graph/pivot/list and fiscal-year default;
- 22 open declaration records, 16 closing workspaces and 14 configurable
  closing controls.

The exact scoped reviewer can inspect journals, documents, reports,
reconciliations, declarations and closing evidence. The live walkthrough
showed 0 New, 0 Import Statement, 0 journal Reconcile, 0 general Match/Undo,
0 document Send/Credit Note/Reset and no Configuration menu. PDF/XLSX and
report drill-down remain available. The manager retained all intended actions.

## Commands and evidence

| Claim | Exact command | Artifact |
| --- | --- | --- |
| Clean product reset/import/parity | `make accounting-dev-reset`; `make accounting-dev-import`; `make accounting-dev-validate` | `dev-reset-status.json`, `dev-import-status.json`, `dev-validate-status.json` |
| Independent exact replay | `make accounting-validation-exact-reset`; `make accounting-validation-exact-import`; `make accounting-validation-exact-validate`; `make accounting-compare` | `validation-exact-*-status.json`, `compare-status.json` |
| Native replay stages | `make accounting-validation-native-reset` followed by every `accounting-validation-native-*` target | `validation-native-*-status.json` |
| Reports and exports | `make accounting-reports` | `reports-status.json` and generated PDF/XLSX files |
| FEC generation and official source validation | `make accounting-fec`; `make accounting-fec-validate` | `fec-status.json`, `fec-validation-status.json`, `fec-dgfip-source-validation/` |
| Add-on regression | `make accounting-addon-tests` | 94 tests passed |
| Harness unit regression | `python3 -m unittest discover -s accounting_compat/tests -v` | 10 tests passed |
| Release gate | `make accounting-readiness`; `make accounting-evidence` | `readiness-assessment.json`, `readiness-assessment.md`, `evidence-index.json` |

The generated benchmark FEC contains 4,781 rows and balanced debit/credit of
EUR 1,064,045.02. Its SHA-256 is
`05c8e064307bc0ff1387695625059179951aadd5b5dc9a126258abcb1a857fe0`;
the official DGFiP source validator exited 0 with no blocking log.

The 45 “draft decisions” are audit/acceptance records, not accounting entries:
36 report-parity records, 3 discrepancy classifications, 2 scope exclusions,
2 external tax-value records, 1 FEC-validation record and 1 milestone-closure
record. They do not block engineering readiness and do not change the ledger.

## Explicitly deferred

- professional sign-off and external filing;
- Peppol and electronic tax filing;
- live bank synchronization;
- probabilistic/AI matching and autonomous posting;
- production cutover.

No confidential dump or private generated output is committed.
