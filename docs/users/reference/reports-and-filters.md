# Reports and Filters

Every normal report opens directly as a compact financial statement. The
selected period and principal result or control appear first. Filters recompute
the page in place, and PDF or XLSX downloads the same report scope.

## Canonical reports

| Need | Report |
| --- | --- |
| Account totals | Balance générale |
| Detailed account movements | Grand livre |
| Movements by journal | Journal comptable |
| Customer or supplier history | Grand livre auxiliaire |
| Outstanding entries | Écritures ouvertes |
| Due-date analysis | Balance âgée clients / Balance âgée fournisseurs |
| Financial position | Bilan / Bilan détaillé |
| Performance | Compte de résultat / Compte de résultat détaillé |
| French tax | TVA et taxes / Liasse fiscale française |
| Fixed assets | Registre des immobilisations / Plan d’amortissement |
| Management analysis | SIG / CAF / Management Ratios |
| Designed analytical statement | Compte de résultat analytique / Revenue vs Spending |
| Free-form analytic exploration | Reporting > Analyse analytique |

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
when the user's general Odoo interface language is different. The same
day-first date and French accounting-number convention applies throughout the
normal application; English
human-readable dates use `10 Jun` in the current year and include the year for
any other year.

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

Each report resolves a governed definition for the selected company and period.
Its version and origin are retained in the report session and export metadata.
Accounting Managers inspect or adapt these definitions under **Configuration >
Reports**.

## Analytic pivot

**Analyse analytique** under Reporting deliberately uses Odoo's native pivot
rather than the statement layout above. **Compte de résultat analytique** under
Reporting is the designed financial statement for a governed period and
hierarchy; use the pivot for free-form exploration.

Its default is the current company fiscal year, revenue and expense accounts,
analytic activity by month and Net Contribution.

The pivot supports nested row/column dimensions, fiscal year/quarter/month/week/
day intervals, several simultaneous measures, expand/collapse, axis flipping,
cell drill-down, list/graph switching and native XLSX download. New analytic
plans appear as grouping dimensions automatically. The same search domain
drives every view.

Revenue is income-side analytic amount, Spending shows normal expense
consumption as positive, and Net Contribution equals Revenue minus Spending.
The native Count measure remains available. Spreadsheet insertion is displayed
only when the installation supplies a compatible writable destination.
