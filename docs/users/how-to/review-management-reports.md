# How To Review Management Reports

Audience: CEO, accountant, finance operator.

Use this guide when you need a management view of the reconstructed accounting period, including intermediate management balances, cash-flow capacity, cash flow, executive summary and analytic allocation.

Management reports help explain performance. They do not replace the statutory ledger, balance sheet, profit and loss, tax reports or FEC.

## Open the Management Report Launchers

Use the Accounting reporting menu:

```text
Accounting > Reporting > SIG and CAF (2024 PCG)
Accounting > Reporting > Cash Flow Statement
Accounting > Reporting > Executive Summary
Accounting > Reporting > Analytic Report
```

If you start from the reconstruction summary, open:

```text
Accounting > Review > Rebuild Evidence > Accounting Reconstruction Review
```

Then click `Report Export` and select the report type.

## Review SIG and CAF

For the benchmark closed period, choose:

```text
Report Type: SIG and CAF (2024 PCG)
Start Date: 2024-01-10
End Date: 2025-09-30
Target Move: Posted Entries Only
Export Format: PDF
```

Review these lines:

- commercial margin;
- production for the period;
- value added;
- gross operating surplus;
- operating result;
- financial result;
- current result before tax;
- exceptional result where present;
- net result;
- cash-flow capacity.

The values should reconcile to the accepted profit and loss and French annual statements.

## Review Cash Flow and Executive Summary

Use:

```text
Report Type: Cash Flow Statement
Report Type: Executive Summary
```

Check:

- company;
- selected period;
- opening and closing cash-related values;
- net result;
- depreciation and non-cash adjustments where shown;
- metadata.

These reports are designed for review and decision support. Material differences from the statutory reports must be investigated and classified.

## Review Analytic Allocation

Use:

```text
Report Type: Analytic Distribution
```

For current operational review, the default launcher starts at:

```text
Start Date: 2025-10-01
```

Analytic data helps review brands, projects and activities. It must not be interpreted as a separate legal company.

When reviewing analytic lines, check:

- analytic account;
- company;
- source journal item;
- amount;
- partner where present;
- date;
- activity or project context.

For the native current-period view, use:

```text
Accounting > Accounting > Analytic Items
```

The list exposes each configured analytic-plan column, including `Projet` and
`Epic`. Use the Pivot and Graph view buttons to aggregate the same underlying
analytic lines; the Pivot toolbar also downloads XLSX.

Source post-posting classification changes are visible as a read-only audit
under:

```text
Accounting > Review > Advanced Audit > Native Analytic Corrections
```

That audit explains why a finalized journal-item distribution can differ from
the original expense business input. It is not a second analytic ledger.

## Drill Down

Click `Preview`, then use the external-link icon or `Open Journal Items`.

For analytic reports, drill-down opens analytic lines. For other management reports, drill-down opens the related accounting items where available.

## Before Using Management Figures

Before using management figures in a decision or investor/accountant package, check:

- the statutory profit and loss for the same period;
- the French annual statements for the same period;
- discrepancy status;
- whether any external value contributes;
- whether accountant review is still pending.
