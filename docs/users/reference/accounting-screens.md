# Accounting Menu and Screen Reference

Audience: all accounting users.

This reference lists the main user-facing screens added for accounting reconstruction and review.

## Main Review Entry

```text
Accounting > Review Issues
```

Purpose: company-level dashboard for import status, ledger totals, discrepancies, review decisions, external values and report evidence.

Use it first when reviewing a reconstructed database.

## Evidence and Control Screens

```text
Accounting > Review and Audit > Advanced Audit > Import Runs
```

Shows source and target import metadata.

```text
Accounting > Review and Audit > Advanced Audit > Discrepancies
```

Shows open, investigating, accepted and resolved accounting differences or review gates.

```text
Accounting > Review and Audit > Advanced Audit > Review Decisions
```

Shows accountant, Valentin, operator or joint review decisions.

```text
Accounting > Review and Audit > Advanced Audit > External Report Values
```

Shows manual or externally supplied report values, especially for tax-package review.

## Source Report Evidence

```text
Accounting > Review and Audit > Advanced Audit > Source Report Catalogue
Accounting > Review and Audit > Advanced Audit > Source Report Lines
Accounting > Review and Audit > Advanced Audit > Source Report Expressions
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
Accounting > Review Issues > Report Export
```

Also available through Accounting reporting launchers.

Purpose: preview and export supported reports.

## Priority Workflows

The Accounting app exposes these first-level destinations for frequent work:

- `Review Issues`
- `Reconcile Bank Transactions`
- `Customers`
- `Suppliers and Expenses`
- `Reports and Declarations`

`Reconcile Bank Transactions` currently opens a list of unreconciled imported bank statement lines. Use it to review transactions, amounts, partners and running balances. The final operational reconciliation workbench is still under implementation.

Technical source mappings, raw imported report rows and comparison evidence are intentionally grouped under `Review and Audit > Advanced Audit`.
