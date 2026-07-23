# USL Odoo User Guide

This guide is for people who use the USL Odoo fork to inspect accounting, review migration evidence, generate reports, and prepare accountant-ready exports.

The main readers are:

- Valentin, as CEO of Unstatic Labs, who needs to understand whether the accounting state is trustworthy and what still needs a decision.
- USL's accountant, who needs to inspect ledgers, reports, tax evidence, FEC output, supporting documents, discrepancies, and review decisions.
- Finance operators, who need to navigate accounting records and produce review packages without using developer tools.

This is user documentation. It explains what to do in Odoo and what the screens mean. It does not explain how to restore databases, run the migration harness, or change code.

## How to Open This Guide

From Odoo, open:

```text
Accounting > Review > Advanced Audit > User Guide
```

You can also open the guide from the `User Guide` button on the Accounting Reconstruction Review screen.
The same button is available in the header of Accounting Home.

The Odoo guide opens in the browser with a navigation sidebar and search field. It uses the same Markdown source files as this `docs/users` directory.

When a dedicated documentation site is running, the same guide can also be viewed through the MkDocs site used by the project team.

## Current Scope

The implemented user-facing accounting features are centered on reconstructed accounting evidence imported from the Odoo Online backup. In Odoo, authorized users can currently:

- open Accounting directly from the application menu onto a company-scoped Accounting Home;
- review bank and cash balances, unmatched transactions, daily document queues, open receivables and payables, closing readiness, declaration deadlines, and prepared actions from that Home;
- use the seven accounting areas: Dashboard, Customers, Vendors, Accounting, Review, Reporting and Configuration;
- open Review > Control > Accounting Hygiene to review daily queues, missing evidence, current controls and assigned decisions;
- open Accounting > Transactions > Bank Matching to review imported bank statement lines in the bank workbench;
- open Accounting > Closing > General Reconciliation to reconcile open accounting items independently of bank matching;
- open the native Expenses workspace from the supplier accounting area;
- use one canonical Accounting Report Workbench for Trial Balance, General Ledger, partner, statement, tax, management, asset and analytic reports;
- open a reconstruction summary for each imported company;
- inspect imported posted journal items;
- inspect source-traced accounting reports and report evidence;
- filter, compare, group, search, fold, drill down and export supported accounting reports;
- generate a FEC export for the benchmark period;
- inspect French annual statement and tax-package mappings;
- inspect fixed assets, depreciation schedules, deferred schedules, bank reconciliation, currency exposure, analytic reporting, EC/OSS evidence and tax reports;
- inspect discrepancies and pending review decisions;
- record review decisions where accountant or stakeholder approval is required;
- inspect source report lines and expressions used as parity evidence;
- inspect non-posted source workflow records and document-regeneration cases;
- inspect cross-boundary reconciliation reviews before any native reconciliation decision is applied.
- let Accounting Managers retrieve daily ECB reference rates without changing source-traced historical rates.

The system deliberately distinguishes technical evidence from professional acceptance. A report can have technical evidence and still require accountant approval before Milestone 13 can close.

Expense reconstruction status: a separate, disposable Track B proof reconstructs all `325` source expenses from `2025-10-01` through `2026-06-30` as source-traced native `hr.expense` records and validates their approval/refusal states and accounting effects without mismatches. This includes `97` company payments and `79` grouped receipts for `95` employee-paid expenses. Its settlement stages replay `106` expense-related bank transactions and `181` source allocations plus all `233` bank transactions directly linked to current commercial documents and their `339` source reconciliation edges. A third disposable hybrid candidate now combines those native current workflows with the exact historical ledger. It is not the promoted replacement: its classified EUR `2.64` current-period exchange profit-and-loss difference still needs professional acceptance, and its report, role and browser journeys still need validation. Continue to use the normal imported target as the historical audit baseline until that explicit promotion decision.

## Documentation Structure

These docs use the Diataxis framework: tutorials for learning, how-to guides for tasks, reference for lookup, and explanation for background. Diataxis describes four documentation needs: tutorials, how-to guides, reference, and explanation. See [diataxis.fr](https://diataxis.fr/).

### Start Here

- [Tutorial: First Accounting Review](tutorials/first-accounting-review.md)

Use this if this is your first time opening the rebuilt accounting evidence in Odoo.

### How-To Guides

- [Check the Reconstruction Status](how-to/check-reconstruction-status.md)
- [Review Accounting Hygiene](how-to/review-accounting-hygiene.md)
- [Generate, Preview and Export Accounting Reports](how-to/generate-accounting-reports.md)
- [Review Customer and Supplier Accounting](how-to/review-customer-and-supplier-accounting.md)
- [Drill Down from a Report to Accounting Sources](how-to/drill-down-to-sources.md)
- [Review Discrepancies and Record Decisions](how-to/review-discrepancies-and-decisions.md)
- [Review Source Report Evidence](how-to/review-source-report-evidence.md)
- [Review French VAT, CA12 and Tax-Package Values](how-to/review-french-tax-and-ca12.md)
- [Review Management Reports](how-to/review-management-reports.md)
- [Generate and Review the FEC](how-to/generate-and-review-fec.md)
- [Review Fixed Assets, Depreciation and Deferred Schedules](how-to/review-assets-and-deferred.md)
- [Review Reconciliation Boundary Cases](how-to/review-reconciliation-boundaries.md)
- [Manage Currency Rate Automation](how-to/manage-currency-rate-automation.md)
- [Use Accountant Access Safely](how-to/use-accountant-access.md)

### Reference

- [Accounting Menu and Screen Reference](reference/accounting-screens.md)
- [Report Reference](reference/reports.md)
- [Review Status and Decision Reference](reference/review-statuses.md)
- [Access and Role Reference](reference/access-roles.md)

### Explanations

- [How the Rebuilt Accounting Evidence Works](explanation/rebuilt-accounting-evidence.md)
- [Imported Ledger, Draft Regeneration and Review-Only Records](explanation/imported-vs-generated-accounting.md)
- [Why Some Items Still Require Accountant Review](explanation/accountant-review-boundaries.md)
- [French SASU Accounting Context](explanation/french-sasu-accounting-context.md)

## Important Safety Notes

- Do not treat technical parity evidence as accountant approval.
- Do not manually change imported posted accounting entries unless an authorized correction process exists.
- Do not record acceptance decisions unless you have reviewed the evidence and have the authority to approve.
- Do not apply native cross-boundary reconciliations without an accepted review decision.
- Do not replace an actual bank or platform conversion with an ECB reference rate when the actual conversion defines the transaction.
- Do not treat access to the reconciliation workbench as proof that every reconciliation behavior is accepted. Matching, write-off, partial-reconciliation and historical-reconciliation behavior still require validation before Milestone 13 closure.
- Do not use synthetic bootstrap data as production accounting evidence.

## Normal Starting Point in Odoo

Open the Accounting app. It lands on Accounting Home for the active company and
shows the operational state before you choose a workflow.

For reconstruction controls and technical evidence, open:

```text
Accounting > Review > Control > Accounting Hygiene
```

Accounting Hygiene shows unmatched bank work, incomplete or stale documents and
expenses, missing evidence, unusual account balances, closing/declaration
warnings and prepared decisions.
For source-backup and import details, open
`Review > Advanced Audit > Accounting Reconstruction Review`.
