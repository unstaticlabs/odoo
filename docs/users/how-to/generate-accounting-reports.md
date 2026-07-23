# Generate, Explore and Export Accounting Reports

Audience: CEO, accountant, finance operator.

Use this guide for a Trial Balance, General Ledger, partner report, French statement, management report or another accounting export. FEC uses the same launcher but has stricter rules.

## Open the Accounting Report Workbench

Open the report you need directly from `Accounting > Reporting`, for example:

```text
Accounting > Reporting > Trial Balance
Accounting > Reporting > General Ledger
Accounting > Reporting > French Annual Statements
Accounting > Reporting > FEC
```

You can also open `Accounting > Review > Control > Issues`, select a company and use `Report Export`.

All supported launchers open the same full-page Accounting Report Workbench. The launcher preselects the report family; you do not need to learn a different wizard for every report.

## Select the accounting scope

Set:

- `Company` and, for non-statutory reports, optional `Companies`;
- `Data Scope`;
- `Target Move`;
- a period;
- optional comparison and filters.

Keep `Data Scope` on `All Native Accounting` for normal work. This queries the current native Odoo ledger, including valid native activity created after reconstruction. Use `Imported Accounting Only` only for source-reconstruction audit work.

Use `Posted Entries Only` for formal reporting. If draft entries exist in the period, the workbench states how many are excluded. When `All Entries` is selected, the warning states that they are included.

FEC and statutory/declaration reports require one company. Odoo blocks a multi-company or otherwise misleading statutory scope.

## Choose a period

Choose a `Period Preset`:

- `Month`;
- `Quarter`;
- `Fiscal Year`;
- `Year to Date`;
- `Custom Dates`.

For a preset, set the anchor date and click `Apply Period`. For the first closed USL benchmark, use custom dates:

```text
Date From: 2024-01-10
Date To: 2025-09-30
Target Move: Posted Entries Only
```

## Add a comparison

Choose:

- `No Comparison`;
- `Previous Period`;
- `Previous Year`;
- `Custom Comparison`.

The report displays the selected-period value, comparison value and difference. The exact dates are recorded in preview and export metadata.

## Filter and organize the report

Optional filters include:

- journals;
- accounts;
- partners;
- analytic plans and analytic accounts for the analytic report.

Use `Group By` to organize rows by section, account, partner, journal, month or analytic account. Use `Search Report` to find an account code, partner, journal entry or label.

Click `Refresh` after changing filters. Use:

- `Expand All` to show group details;
- `Collapse All` to show only group totals;
- the group-toggle icon to expand or collapse one group.

Some report families reject filters that would change their legal or accounting meaning. Remove the incompatible filter if Odoo explains that the selected scope is invalid.

## Read the Trial Balance

The Trial Balance shows:

- `Opening`: eligible balance before the start date;
- `Debit` and `Credit`: activity inside the selected period;
- `Movement`: debit minus credit for the period;
- `Closing`: opening plus movement through the end date.

Comparison values use the corresponding closing balance for the comparison period. The Trial Balance source action includes entries up to the report end date so the closing balance can be reconstructed.

## Drill down to sources

Click the external-link icon on a report row.

- Ledger-backed rows open the contributing native journal items.
- Analytic rows open the contributing analytic lines.
- Grouped rows carry their company/account/partner/journal/month context into the source domain.

The drilldown is read-only for accountant reviewers. If a row depends on external evidence or has no more precise stable key, Odoo opens the safest filtered source scope rather than pretending to have a more exact link.

## Export the current result

Choose:

- `CSV` for machine-readable detail;
- `XLSX` for spreadsheet review;
- `PDF` for a printable review package;
- `FEC TXT` only for FEC.

Click `Generate Export`, open the `Download` tab and download the file.

The export is generated from the same filters and grouping as the visible result. Its metadata records the company or companies, native/imported scope, period, comparison, posted/draft scope, filters, grouping, search and row count.

## Before sharing a report

Check:

- company and legal entity;
- report and comparison dates;
- native versus imported-only scope;
- posted-only versus all entries;
- draft-entry warning;
- filters, grouping and search;
- row-level source drilldown;
- whether accountant acceptance is recorded.

Technical validation does not itself mean that an accountant has accepted a French report variant, statutory interpretation, declaration value or FEC.

## Supported report families

The workbench covers:

- Trial Balance, General Ledger and Journal Report;
- Partner Ledger, Customer Statement, Open Items and aged receivable/payable;
- Balance Sheet, Profit and Loss, Cash Flow and Executive Summary;
- VAT/tax reports, EC Sales, OSS and French tax-package mapping;
- bank reconciliation, currency exposure and analytic distribution;
- fixed assets, depreciation and deferred schedules;
- French annual statements, 2024 PCG variants, SIG and CAF;
- FEC.
