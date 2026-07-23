# Tutorial: Prosper Accounting Acceptance Walkthrough

Audience: Prosper, Valentin, and the administrator preparing the external
accountant's first formal review.

Goal: let Prosper independently inspect the USL accounting reconstruction,
reports, statutory evidence, reconciliation boundaries, closing package and
FEC, then record only the decisions for which he has professional authority.

Time needed: 60–90 minutes for the first pass, plus any detailed report review.

This walkthrough is an acceptance package, not an acceptance result. Completing
the screens does not approve their accounting meaning. Conclusions must be
recorded in Odoo by the named authority.

## 1. Administrator preflight

Before Prosper signs in, an administrator must verify:

1. Prosper has a personal Odoo user. Do not share Valentin's account.
2. Allowed Companies contains `Unstatic Labs` only.
3. The active company is `Unstatic Labs`.
4. The user has:
   - Odoo Accounting read-only access;
   - `USL Accountant Reviewer`.
5. The user does not have Accounting Manager, Settings, system administration
   or another company's access.
6. The latest Milestone 13 readiness record reports no technical failures.
7. The latest reports, comparison, FEC validation and evidence-index artifacts
   exist.

At the current checkpoint, the review queue contains `45` draft records:

- `38` source-report reviews;
- `3` discrepancy reviews;
- `2` external tax-value reviews;
- `1` FEC review;
- `1` milestone-closure review.

These counts are a checkpoint aid. If the source snapshot or implementation
changes, use the current Odoo queue and readiness record instead of forcing the
old count.

## 2. Verify the access boundary

Sign in as Prosper and open Accounting.

Confirm:

- Accounting Home opens for Unstatic Labs;
- the company pager does not expose USL Media;
- Configuration and Accounting Settings are absent;
- journal, document, report, declaration, closing and review records are
  readable;
- create, edit, post, reconcile, reset and lock-date controls are absent.

Stop and report an access defect if another company or a mutation control is
visible. Do not continue by exporting data through a more privileged account.

## 3. Review operational state

Follow [First Accounting Review](first-accounting-review.md) through Accounting
Home and Accounting Hygiene.

Inspect:

- cash and bank balances;
- unmatched bank transactions;
- draft documents and missing evidence;
- open receivables and payables;
- unusual balances;
- declaration deadlines;
- closing blockers and warnings;
- actions prepared for Valentin and Prosper.

Operational queues are not automatically accounting errors. Open the
underlying records before reaching a conclusion.

## 4. Review the ledger and canonical reports

Use [Generate, Preview and Export Accounting Reports](../how-to/generate-accounting-reports.md).

For the locked benchmark `2024-01-10` through `2025-09-30`, review at least:

1. Trial Balance;
2. General Ledger;
3. Journal Report;
4. Balance Sheet and Profit and Loss;
5. French annual statements, SIG and CAF;
6. Partner Ledger, Open Items and aged balances;
7. bank reconciliation and currency reports;
8. analytic reporting;
9. fixed-asset register and depreciation schedule;
10. French tax-package and CA12 mapping.

For each material report:

- confirm company, period, scope and filters;
- preview before export;
- drill into at least one material line;
- compare screen, XLSX and PDF values;
- inspect the source report catalogue and technical evidence;
- record an objection if formula, classification, legal-form variant,
  presentation or drill-down membership is not acceptable.

The technical asset evidence currently reconciles at EUR `10,430.49` gross,
EUR `1,676.05` accumulated depreciation and EUR `8,754.44` net. The 2033-C
evidence separately exposes `3` assets and `91` schedule rows as quantities.

## 5. Review reconciliation boundaries

Use [Review Reconciliation Boundaries](../how-to/review-reconciliation-boundaries.md).

The exact replay exposes `39` partial and `36` full relationships crossing from
posted history into draft/future endpoints.

Prosper should:

- inspect imported and generated endpoints;
- preview a representative partial pair;
- preview a representative full scope;
- confirm that debit and credit endpoints are understandable;
- state whether review-only treatment is acceptable or whether a separately
  authorized application workflow is required.

Prosper must not apply or manufacture reconciliations from the read-only role.
Valentin owns the product/action decision after accounting advice.

## 6. Review assets, deferrals and evidence

Use [Review Assets and Deferred Schedules](../how-to/review-assets-and-deferred.md)
and [Review Customer and Supplier Accounting](../how-to/review-customer-and-supplier-accounting.md).

Confirm:

- three source assets and their depreciation evidence are visible;
- posted and forecast schedule rows are distinguishable;
- native current-period asset and deferral moves open from their records;
- supplier documents and expense receipts open through their native parent;
- unrelated private technical attachments remain inaccessible.

## 7. Review declarations and tax evidence

Use [Review French Tax and CA12](../how-to/review-french-tax-and-ca12.md).

Check:

- the USL legal/tax profile and custom fiscal-year dates;
- form applicability and deadline;
- the EUR `2,500` refund represented once;
- the EUR `942` refund corrected and not retained as VAT credit;
- zero VAT instalment deduction;
- source accounts and journal-item drill-down;
- explicit external values and their evidence;
- remaining review-required or manual fields.

Electronic submission is not part of Accounting v1. The review covers
preparation, portal guidance and external filing/payment/refund state.

## 8. Review closing and the FEC

Open the historical annual closing workspace and inspect every control,
warning, blocker and package row.

Then use [Generate and Review the FEC](../how-to/generate-and-review-fec.md).

As Prosper:

- generate the complete posted benchmark FEC in locked test mode;
- keep the default complete journal scope;
- download `983982950FEC20250930.txt`;
- reconcile its debit and credit totals to EUR `1,064,045.02`;
- inspect the structural-preflight and DGFiP source-validation evidence;
- confirm the period and company identity.

Expected denial:

- Prosper cannot generate an official non-test FEC;
- Prosper cannot exclude journals;
- Prosper cannot advance the fiscal lock date.

Valentin remains the manager responsible for the official/freeze action after
accountant review.

## 9. Record review decisions

Use [Review Discrepancies and Record Decisions](../how-to/review-discrepancies-and-decisions.md)
and [Review Source Report Evidence](../how-to/review-source-report-evidence.md).

Record one of the available non-pending conclusions only after reviewing its
evidence. Add a concise rationale and any remaining risk.

Authority split:

- Prosper records accounting conclusions or objections for report semantics,
  statutory/tax evidence, FEC and source accounting anomalies.
- Valentin records product scope, hybrid-candidate promotion and final
  milestone approval.
- The cross-boundary reconciliation policy combines Prosper's accounting
  advice with Valentin's product/action decision.
- Association-report exclusions and other legal-form variants require the
  named stakeholder/accountant conclusion shown by their review records.

Do not bulk-accept the queue. A rejected or accepted-with-difference conclusion
is valid evidence when it accurately records the review.

## 10. Finish or escalate

The named-user walkthrough is complete only when:

- Prosper performed the review using his own scoped account;
- every report requiring his conclusion is recorded or has a documented
  objection;
- FEC and declaration conclusions are recorded;
- access defects are absent or logged;
- the reconciliation-policy advice is recorded;
- Valentin has a clear queue of decisions that remain his responsibility;
- a fresh readiness assessment is generated after the decisions.

Milestone 13 must remain blocked while an open P0/P1 or draft professional
decision remains. The implementation agent must not record approvals on
Prosper's or Valentin's behalf.

