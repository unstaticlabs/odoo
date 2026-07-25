# Reports and Filters

Every normal report opens directly as a compact financial statement. The
selected period and principal result or control appear first. Filters recompute
the page in place, and PDF or XLSX downloads the same report scope.

## Canonical reports

| Need | Report |
| --- | --- |
| Account totals | Trial Balance |
| Detailed account movements | General Ledger |
| Movements by journal | Journal Report |
| Customer or supplier history | Partner Ledger |
| Outstanding entries | Open Items |
| Due-date analysis | Aged Receivable / Aged Payable |
| Financial position | Balance Sheet / Detailed Balance Sheet |
| Performance | Profit and Loss / Detailed Profit and Loss |
| French tax | Tax Report / French VAT |
| Fixed assets | Asset Register / Depreciation Schedule |
| Management analysis | SIG / CAF / Management Ratios |
| Analytical dimensions | Revenue vs Spending and Analytical Reporting |

## Common filters

The available subset depends on the report:

- company;
- month, quarter, fiscal year or arbitrary dates—a preset uses one reference
  date, while Custom Dates exposes start and end dates;
- previous period, previous year or custom comparison dates;
- journals, accounts and partners;
- analytic plan and account;
- posted entries only or drafts included;
- variant, currency and display unit;
- text search.

Less common journal, account, partner and analytic choices are under
**Filtres**. Active choices appear as removable pills and **Effacer** removes
optional filters without losing the company or selected period. Accounting
statements consistently use `DD/MM/YYYY` and French number separators, even
when the user's general Odoo interface language is different.

## Reading results

Dark section rows identify the statement's principal divisions. Shaded group
rows contain accounts, partners or journals; indented rows are details; a
single rule marks subtotals and a double rule marks final totals. Control rows
show a validation conclusion separately.

Use fold/unfold to move from sections to account groups and accounts. Select
the drill-down icon on a material line to inspect journal items, then open the
original invoice, bill, payment or entry. Browser Back returns to the same
report session with its filters and opened groups.

Draft warnings mean the displayed period includes unposted accounting that can still change.

## Screen and exports

The screen, PDF and readable XLSX `Report` sheet share the resolved period,
filters, grouping, hierarchy, calculations and totals. XLSX also contains a raw
`Audit Data` sheet for analysis; it is not the presentation reference.
