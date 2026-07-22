# How To Drill Down from a Report to Accounting Sources

Audience: accountant, CEO, finance operator.

Use this guide when you need to understand what makes up a report total.

## Start from a Report Preview

1. Open `Imported Accounting Report Export`.
2. Choose a report.
3. Set company and dates.
4. Click `Preview`.

The preview table shows report rows.

## Open Sources for One Row

1. Find the row you want to inspect.
2. Click the external-link icon.

Odoo opens the source records behind the row.

For ledger-backed reports, this usually opens `account.move.line` journal items.

For analytic reports, this opens `account.analytic.line` records.

## What to Check in Journal Items

When journal items open, check:

- date;
- journal;
- entry number;
- account;
- partner;
- label;
- debit;
- credit;
- balance;
- currency;
- source trace fields;
- related move;
- attachments where available.

## Use Drill-Down to Validate a Report

A report is not proven by its total alone. Use drill-down to confirm:

- the right company is included;
- the right period is included;
- only posted entries are included when required;
- the right accounts contribute to the row;
- the amounts match the ledger;
- taxes and partners make sense;
- evidence is available for important documents.

## When Drill-Down Is Not Journal Items

Some reports are not pure journal-item reports:

- Fixed Asset Register opens asset evidence.
- Depreciation Schedule opens imported schedule evidence.
- French Tax Package Mapping may include ledger-derived rows and external report values.
- Source Report Catalogue opens source report definitions, lines, columns and expressions.
- Reconciliation Boundary Review opens imported and generated endpoint lines.

These records are still accounting evidence. They are not hidden developer logs.

