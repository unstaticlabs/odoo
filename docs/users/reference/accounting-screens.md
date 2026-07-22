# Accounting Menu and Screen Reference

Audience: all accounting users.

This reference lists the main user-facing screens added for accounting reconstruction and review.

## Main Review Entry

```text
Accounting > Review > Control > Issues
```

Purpose: company-level dashboard for import status, ledger totals, discrepancies, review decisions, external values and report evidence.

Use it first when reviewing a reconstructed database.

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

Use these to inspect asset, depreciation and deferred evidence.

## Report Export Wizard

```text
Accounting > Review > Control > Issues > Report Export
```

Also available through Accounting reporting launchers.

Purpose: preview and export supported reports.

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

Bank Matching shows the statement line, suspense entry, candidate journal items, manual operation, chatter and validation controls. General Reconciliation shows account/partner groups and their residual journal items. Full accounting-effect validation of matching, write-offs, partial reconciliation and undo remains in progress.

Technical source mappings, raw imported report rows and comparison evidence are intentionally grouped under `Review > Advanced Audit`.
