# Accounting Menu and Screen Reference

Audience: all accounting users.

This reference lists the main user-facing screens added for accounting reconstruction and review.

## Accounting Home

```text
Accounting
```

Purpose: company-scoped operational overview for bank and cash balances,
unmatched transactions, draft or incomplete daily work, open receivables and
payables, closing readiness, declaration deadlines, and actions prepared for
Valentin or the accountant.

Use the header and statistic buttons to open the native journal dashboard,
transactions, Bank Matching, documents, expenses, balances, reports, closing,
declarations and prepared decision queues. The standard Odoo `Dashboard` remains
available as a child menu for journal cards and direct journal access.

## Accounting Hygiene

```text
Accounting > Review > Control > Accounting Hygiene
```

Purpose: company-scoped daily review for unmatched bank transactions,
incomplete or stale documents and expenses, missing supplier/receipt evidence,
unusual aggregate account balances, closing/declaration warnings and prepared
decisions.

Each count opens the native record or durable evidence behind it. Accounting
Managers can refresh the current closing controls; the accountant reviewer has
the same scoped read path without the refresh or accounting mutation control.
The reconstruction-only summary remains under `Review > Advanced Audit`.

## Evidence and Control Screens

```text
Accounting > Review > Advanced Audit > Import Runs
```

Shows source and target import metadata.

```text
Accounting > Review > Advanced Audit > Discrepancies
```

Shows open, investigating, accepted and resolved accounting differences or review gates.

```text
Accounting > Review > Advanced Audit > Review Decisions
```

Shows accountant, Valentin, operator or joint review decisions.

```text
Accounting > Review > Advanced Audit > External Report Values
```

Shows manual or externally supplied report values, especially for tax-package review.

## Source Report Evidence

```text
Accounting > Review > Advanced Audit > Source Report Catalogue
Accounting > Review > Advanced Audit > Source Report Lines
Accounting > Review > Advanced Audit > Source Report Expressions
```

Use these screens to inspect the source Odoo Online report definitions as evidence for parity review. They do not copy Enterprise report code.

## Imported Report Views

Screens include:

- Imported Trial Balance
- Imported General Ledger
- Imported Journal Report
- Imported Partner Ledger
- Imported Open Items
- Imported Balance Sheet
- Imported Profit and Loss
- Imported VAT and Tax Report
- Imported EC/OSS Tax Review
- Imported Bank Reconciliation
- Imported Currency Gain/Loss
- Imported Cash Flow and Executive Summary
- Imported Analytic Distribution
- Imported French Annual Statements
- Imported French Tax Package Mapping

These are read-only review views over imported evidence.

## Workflow Review Screens

Screens include:

- Source Move Workflow Review
- Document Regeneration Cases
- Source Move Line Workflow Review
- Source Payment Workflow Review
- Source Reconciliation Boundary Review

Use these to inspect source records that are not simply posted ledger lines.

## Asset and Schedule Screens

Screens include:

- Fixed Asset Register
- Imported Depreciation Schedule
- Imported Deferred Schedule
- Native Analytic Corrections

Use these to inspect asset, depreciation and deferred evidence.

Operational deferred schedules live under `Accounting > Closing > Deferrals`.
Native analytic lines, pivot and graph views live under
`Accounting > Accounting > Analytic Items`.

## Currency Rate Automation

```text
Accounting > Configuration > Currency Rate Automation
```

Accounting Managers use this workspace to configure daily ECB reference rates,
retrieve a rate immediately, inspect the latest status and open the native
currency-rate rows. Imported source-traced historical rates are protected from
provider updates. Accountant reviewers cannot open this configuration action.

## Accounting Report Workbench

```text
Accounting > Review > Advanced Audit > Accounting Reconstruction Review > Generate Reports
```

Also available through Accounting reporting launchers.

Purpose: preview and export supported reports.

Normal report launchers under `Accounting > Reporting` open this full-page workbench. It defaults to all native accounting and supports period presets, comparisons, journal/account/partner/analytic filters, grouping, search, expand/collapse, draft warnings, source drilldown and CSV/XLSX/PDF export. Imported-only scope is an advanced reconstruction-audit option.

## Priority Workflows

The Accounting app exposes the standard seven-area navigation:

- `Dashboard`
- `Customers`
- `Vendors`
- `Accounting`
- `Review`
- `Reporting`
- `Configuration`

Use the three transaction/reconciliation paths for different purposes:

- A journal card's `Transactions` button opens the complete transaction history.
- `Accounting > Transactions > Bank Matching` opens unreconciled bank statement lines across journals. A journal card's `Reconcile … Items` button opens the same workbench scoped to that journal.
- `Accounting > Closing > General Reconciliation` handles receivable, payable, suspense, tax, shareholder, payroll and other clearing accounts by account and partner.

Bank Matching shows the statement line, suspense entry, candidate journal items,
manual operation, chatter and validation controls. General Reconciliation shows
account/partner groups and their residual journal items. Executable tests cover
native matching, write-off, partial-reconciliation and undo effects; the source
boundary classifications still require professional acceptance.

Technical source mappings, raw imported report rows and comparison evidence are intentionally grouped under `Review > Advanced Audit`.

## Matched Items and Undo

```text
Accounting > Closing > Matched Items and Undo
```

This screen shows posted, reconciled journal items on reconcilable accounts.
Finance operators can select a line and use `Action > Unreconcile` to invoke
Odoo's native full/partial reconciliation removal. The accountant reviewer can
inspect the same scoped list but cannot run the mutation.

Native match, partial-match and undo accounting effects have executable tests.
The professional decision for source cross-boundary matches and write-offs
remains separate from this technical capability.

## Email Ingestion

Purchase journals expose their bill alias under the journal's advanced email
settings. Expense ingestion is configured under
`Settings > Expenses > Incoming Emails`.

The native gateway creates a draft record, identifies a known supplier or
employee where possible, and retains the incoming message and attachment.
Self-hosted delivery is not active until an administrator configures a
controlled alias domain, provider/DNS route and incoming mail server. See
[Route Bills and Expenses by Email](../how-to/route-bills-and-expenses-by-email.md).

## Accounting Configuration

Accounting Managers have explicit routes for the retained configuration:

| Configuration | Route or implementation |
| --- | --- |
| Chart of accounts | `Configuration > Accounting > Chart of Accounts` |
| Account groups | `Configuration > Accounting > Account Groups` |
| Taxes and tax groups | `Configuration > Accounting > Taxes` and `Tax Groups` |
| Accounting/tax tags | `Configuration > Accounting > Accounting and Tax Tags` |
| Journals | `Configuration > Accounting > Journals` |
| Reconciliation rules | `Configuration > Accounting > Reconciliation Models` |
| Currencies and rates | `Configuration > Currencies` and `Currency Rate Automation` |
| Payment terms | `Configuration > Invoicing > Payment Terms` |
| Incoterms | `Configuration > Invoicing > Incoterms` |
| Asset profiles | `Configuration > Assets` |
| Analytic plans/accounts | `Configuration > Analytic Accounting` |
| Multi-ledger configuration | `Configuration > Multi-Ledgers` |

The source contains no tax-unit rows and no installed budget application, so
those are retained as explicit not-applicable capabilities rather than empty
custom screens. French declarations use the dedicated declaration workspace
and rule catalogue instead of an Enterprise tax-unit model.
