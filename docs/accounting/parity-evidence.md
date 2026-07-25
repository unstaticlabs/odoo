# Accounting parity evidence

## Evidence package

Each parity run records:

- source environment, version and extraction date;
- companies and periods covered;
- source artefact manifest;
- target release and import run;
- included, transformed, excluded and failed records;
- record counts and control totals;
- report parameters and source/target outputs;
- line-by-line differences;
- attachment completeness;
- FEC and validator results;
- warnings, decisions and approvals.

## Required scenarios

The maintained reference corpus includes, where present:

- a closed fiscal year and following opening;
- current-year activity;
- USL and USL Media;
- invoices, bills, credit notes and manual entries;
- payments and bank statement lines;
- full, partial and absent reconciliation;
- USD transactions settled in EUR;
- realized and unrealized exchange differences;
- VAT, carryovers and external adjustments;
- payroll entries;
- shareholder and intercompany accounts;
- assets and deferred items;
- supporting attachments.

## Evidence quality

The following do not prove parity alone:

- screenshots;
- application startup;
- a single total;
- one happy-path document;
- synthetic-only data;
- undocumented manual corrections;
- reports without drill-down;
- an unvalidated FEC.

## Repeatability

The approved source package imported into separate clean targets must produce the same accounting consequences, reports, FEC and discrepancy classifications. Reprocessing must not silently duplicate business consequences.
