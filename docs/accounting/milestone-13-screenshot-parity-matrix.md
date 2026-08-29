# Milestone 13 screenshot parity and journey scorecard

Status date: 26 July 2026.

The supplied Odoo Online screenshots are functional acceptance references. The
target intentionally uses Community/OCA-native layouts where they are clearer;
pixel-for-pixel reproduction is not required.

| # | Online reference | Accounting v1 equivalent | Final status | Current evidence |
| --- | --- | --- | --- | --- |
| 1 | Accounting dashboard and configuration | **Overview** is the daily cockpit; **Journals** retains the native journal cards; Customers, Vendors, Accounting, Review, Reporting, Declarations and Configuration remain one application. | Equivalent and improved | Final `odoo_dev` manager/reviewer walkthrough. Overview exposes bank matching, draft documents, open items, Hygiene and closing/declaration priorities. Configuration is absent for the reviewer. |
| 2 | Dynamic Profit and Loss | Dedicated interactive **Compte de résultat** page with fiscal/custom dates, comparison, analytic filters, hierarchy, drill-down and direct PDF/XLSX. | Implemented | Report controls pass; the shared **Balance générale** browser journey proves the same interaction contract with 113 live rows and a 598-line account drill-down. |
| 3 | General Ledger and report navigation | One canonical interactive **Grand livre** and **Balance générale**; OCA remains a supporting implementation dependency, not a competing menu. | Implemented | Screen/export controls pass. Browser Back restores the report period, grouping and rows after drill-down. |
| 4 | General Reconciliation | General Reconciliation shows All, Unreconciled and Reconciled states with debit, credit, remaining amount, status, lettrage reference, Match/Open, partial/full state and Undo where permitted. | Equivalent and improved | The manager has matching actions. The reviewer can inspect the route but sees no Match, Mark for Review or Undo action. Exact and native reconciliation probes pass. |
| 5 | Tax Returns timeline | Permanent Declarations list/calendar with version, legal period basis, trigger, deadline, status, validation state, unresolved count and field drill-down. | Equivalent and improved | Final browser journey must prove the period-aware USL and USL MEDIA schedules, one 2065 dossier with 2033 annexes, first-year 2571 exemption, split long-period CA12, transaction-triggered obligations and the 1 October 2027 CA3 transition. Electronic filing is deliberately deferred. |
| 6 | Bank Matching | Journal **Transactions** is a full-width transaction browser; **Bank Matching** opens only the unreconciled operational queue. The panel offers **Reconcile**, **Categorize** and **Chatter**. Categorize keeps direct accounting treatment and transaction details visible together; no separate Other Info tab remains. | Equivalent and improved | Manager matching and Undo are available; the scoped reviewer has inspection only. Reviewer journal cards expose neither Reconcile nor Import. |
| 7 | Vendor bill with source evidence | Standard Odoo bill form with native business lines, taxes, journal items, payment/reconciliation state and imported attachment evidence. | Equivalent | The current candidate contains 349 business documents and 710 reconstructed Accounting attachments. All 710 are readable and source-traced; native replay validates document creation, posting, evidence and settlement without duplicate accounting. |
| 8 | Expenses and receipts | Standard Odoo Expenses list/form with native workflow state, accounting link, analytic allocation and receipts. | Equivalent | The current candidate contains all 363 source expenses; native replay validates creation, posting and settlement without a parallel expense representation. |

## Role scorecard

| Journey | Accounting Manager | Read-only accountant |
| --- | --- | --- |
| Daily cockpit and journals | Full operational actions | Same accounting visibility; no New, Import, Reconcile or Configuration actions |
| Documents | Create, review, post, pay and inspect | Read lines, taxes, journal items, payments and evidence; no Send, Credit Note, Reset or create controls |
| Reconciliation | Match, partially reconcile, continue residuals and Undo | Inspect transactions, residuals and lettrage; no Match or Undo |
| Reports | Filter, unfold, drill down and export | Same read/filter/drill-down and PDF/XLSX |
| Declarations, closing and FEC | Prepare and update operational state | Inspect and download permitted test evidence; no close/configure action |

## Deliberate boundaries

- Live approved-platform activation, electronic filing, live bank
  synchronization and probabilistic/AI matching are deferred. Electronic
  invoice reception is implemented and tested but not connected.
- The source has no customer-credit-note case in the bounded native replay;
  normal Odoo credit-note creation remains available and tested by module
  behavior, while three supplier refunds are production-derived.
- The complete source reconciliation graph is represented by native partial
  and full reconciliation records; no boundary placeholder remains.
  review evidence because a missing endpoint is draft or outside the exact
  posted slice. They do not change posted totals.
- Sixteen source sequence gaps and 104 source date-order decreases are
  preserved rather than silently resequenced.

Private browser evidence is
`artifacts/accounting-compat/private/replacement-browser-status.json`.
