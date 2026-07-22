# How To Generate, Preview and Export Accounting Reports

Audience: CEO, accountant, finance operator.

Use this guide when you need a Trial Balance, General Ledger, French annual statement, FEC or another accounting export from the reconstructed ledger.

## Open the Report Wizard

You can open reports in two ways.

From the review summary:

```text
Accounting > Review > Rebuild Evidence > Accounting Reconstruction Review
```

Open the company and click `Report Export`.

Or open a direct report launcher from the Accounting Reporting menu, for example:

```text
Accounting > Reporting > Trial Balance
Accounting > Reporting > General Ledger
Accounting > Reporting > French Annual Statements
Accounting > Reporting > FEC
```

The exact menu group depends on the report family, but all supported reports open the same `Imported Accounting Report Export` wizard.

## Choose the Report

In the wizard, choose:

- `Report Type`
- `Company`
- `Start Date`
- `End Date`
- `Target Move`
- `Export Format`

For the benchmark closed period, use:

```text
Start Date: 2024-01-10
End Date: 2025-09-30
Target Move: Posted Entries Only
```

## Preview Before Exporting

Click `Preview`.

Use preview when:

- you want to confirm the report has rows;
- you want to inspect totals before downloading;
- you want to drill down from a row;
- you want to confirm the selected filters.

The preview includes metadata. Review it before sending a report to someone else.

## Export the Report

Choose an export format:

- `CSV` for machine-readable detail;
- `XLSX` for spreadsheet review;
- `PDF` for printable review;
- `FEC TXT` only for FEC.

Click `Generate Export`.

Odoo will show:

- file name;
- downloadable file;
- export metadata.

## Use Optional Filters

The wizard supports optional ledger filters where meaningful:

- journals;
- accounts;
- partners.

Typical examples:

- filter General Ledger by one journal;
- filter Partner Ledger by one partner;
- filter Trial Balance by selected accounts;
- filter Bank Reconciliation by journal or partner.

Some reports deliberately reject filters that would make the result misleading:

- French tax-package mapping does not accept ledger filters;
- fixed-asset reports do not accept journal or partner filters;
- bank reconciliation does not accept account filters;
- FEC has its own strict export rules.

If Odoo blocks a filter, remove that filter and regenerate the report.

## Supported Report Types

The wizard currently supports:

- Trial Balance
- General Ledger
- Journal Report
- Partner Ledger
- Customer Statement
- Open Items
- Aged Receivable
- Aged Payable
- Balance Sheet
- Profit and Loss
- VAT and Tax Report
- Tax Report by Account then Tax
- Tax Report by Tax then Account
- EC Sales List
- OSS Sales
- OSS Imports
- Bank Reconciliation
- Currency Gain, Loss and Exposure
- Cash Flow Statement
- Executive Summary
- Analytic Distribution
- Fixed Asset Register
- Fixed Asset Register by Account
- Depreciation Schedule
- Deferred Expense and Revenue Schedule
- French Annual Statements
- French Balance Sheet (2024 PCG)
- French Profit and Loss (2024 PCG)
- SIG and CAF (2024 PCG)
- French Tax Package Mapping
- FEC

## Before Sending an Export

Check:

- company;
- period;
- report type;
- posted-only versus draft-inclusive scope;
- selected filters;
- export format;
- whether the report is technically accepted or still pending review.

For accountant review, include the export metadata or send the file generated directly by Odoo.

