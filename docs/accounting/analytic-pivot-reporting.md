# Analytic Pivot Reporting

## Product contract

**Accounting > Analysis > Analytic Reporting** is the exploratory complement
to the configured Analytic Distribution statement and Revenue vs Spending
chart. It uses `account.analytic.line` directly, so pivot, list and graph views
share one company-scoped population and every aggregate can drill into the
contributing analytic items.

The default opens on:

- the active company's current fiscal year, including non-calendar fiscal
  years;
- revenue and expense financial accounts;
- the primary analytic plan in rows;
- month in columns;
- Net Contribution as the measure.

Users may replace those defaults with any combination of date interval,
configured analytic plan/account, financial account/group/category, partner,
product, journal or company. Odoo's analytic-plan view patch adds newly
configured plan fields to the pivot and search groupings without another USL
report implementation.

## Architecture decision

Two credible approaches were considered:

1. extend the configured USL report client with another fixed hierarchy;
2. expose Odoo's native `account.analytic.line` pivot, adding only missing
   business measures and accounting dimensions.

The second approach is implemented. The fixed report remains appropriate for
a designed financial statement and PDF export; the native pivot supplies
nested dimensions, axis flipping, expansion, cell drill-down, graph/list
switching and XLSX export without duplicating the web client or aggregation
engine.

No SQL reporting view or copied analytic ledger is introduced. The underlying
analytic items remain Odoo-generated records linked to journal items and
business documents.

## Measures and signs

The stored measures follow native analytic signs:

| Measure | Meaning |
| --- | --- |
| Accounting Amount | Native `account.analytic.line.amount` |
| Revenue | Income-side amount; normal revenue is positive and reversals reduce it |
| Spending | Expense-side amount with normal consumption shown positive and reversals reducing it |
| Net Contribution | Revenue minus Spending; exactly equal to Accounting Amount for the revenue/expense population |
| Count | Native pivot record count |

For the candidate database after the module update, the 631 analytic lines
linked to revenue/expense accounts produce:

- Accounting Amount and Net Contribution: `101,481.23`;
- Revenue: `277,126.60`;
- Spending: `175,645.37`;
- `Revenue - Spending - Net Contribution = 0.00`.

This is a report-layer sign control, not a replacement for the native analytic
reconstruction parity controls.

## Security and spreadsheet boundary

Existing analytic-line access and the global multi-company record rule apply to
all three views. Accounting Managers and read-only accountants can analyze and
drill down; the reporting action does not grant write access.

Native pivot XLSX download is available. Odoo only displays **Insert in
Spreadsheet** when an installed destination module enables
`can_insert_in_spreadsheet`. The Community spreadsheet engine is installed in
the current build, but it does not provide that writable Documents destination;
the product does not imitate the missing Enterprise destination with custom
code. If a maintained compatible destination is installed later, the native
pivot becomes insertable without changing this report.
