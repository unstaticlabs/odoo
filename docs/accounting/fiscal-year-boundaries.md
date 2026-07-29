# Fiscal-year boundary contract

## Product invariant

Every Accounting feature that asks for the fiscal year containing a reference
date must resolve the same company-governed boundaries.

For Unstatic Labs:

- the exceptional first exercise is `10/01/2024`–`30/09/2025`;
- the recurring cadence is 1 October–30 September from `01/10/2025`;
- an explicit custom period remains exactly what the user selected and is not
  snapped to a fiscal year.

This invariant applies to calculations and scope, not only to labels. Screen,
PDF, XLSX, FEC defaults, declarations, closing workspaces and drill-down
domains must therefore tell the same period story.

## Configuration

The recurring closing day and month remain standard Odoo company settings.
The exceptional first start and end are company fields under
**French Declaration Profile**. Both exceptional boundaries are required
together and the start cannot follow the end.

`res.company.compute_fiscalyear_dates(reference_date)` is the canonical
runtime contract. The custom company extension returns the exceptional first
exercise when the reference date falls within it and delegates to standard
Odoo behavior otherwise. Existing databases that have not yet stored the
explicit first end use their matching fiscal lock date only as an upgrade
fallback.

## Covered consumers

| Consumer | Governed behavior |
| --- | --- |
| Interactive reports | Fiscal Year and Fiscal Year to Date presets |
| PDF and XLSX | Current report session dates and metadata |
| Accounting Overview | Report launcher defaults |
| FEC | Standard wizard default dates |
| Declarations and Closing | Fiscal instances and workspaces |
| Cash and IS projection | Year-to-date ledger scope and irregular-year ceiling |
| Revenue/spending and analytics | Current-fiscal-year domains |
| Native cumulative balances | Profit-and-loss reset boundary |
| Accounting spreadsheets | Fiscal-date service and year/day formula domains |
| Journal sequences and resequencing | Initial prefixes, `year_range` grouping and forced year labels |

Explicit benchmark actions retained for reconstruction evidence remain fixed
at `10/01/2024`–`30/09/2025` by design. Custom-date report filters and
as-of/open-item cutoffs are user-selected periods, not fiscal-year
calculations.

## Implementation decision

Three approaches were reviewed:

1. adjust report/PDF labels only—rejected because ledger domains, exports and
   other Accounting features would remain inconsistent;
2. patch every consumer independently—rejected because new features could
   silently return to recurring-cadence-only calculations;
3. extend the standard company fiscal-year API and route exceptional direct
   consumers through it—selected because native and custom features share one
   configuration-driven contract.

The native initial-sequence and resequencing methods contained direct
fiscal-year calculations without extension hooks. Narrow upstream-adjacent
patches make both call the standard company API. Companies without exceptional
boundaries retain unchanged Odoo behavior.

## Regression protection

`TestRebuildAccountMigration` protects:

- the first exercise on the Bilan screen and in extracted PDF text;
- Accounting Overview, FEC, cash/IS, analytics and revenue/spending scopes;
- spreadsheet fiscal dates and formula boundaries;
- journal sequence prefixes and resequencing behavior on both sides of
  1 October 2024;
- the recurring 2025–2026 exercise after the exception;
- absence of direct `get_fiscal_year` bypasses in custom Accounting models;
- presence of the governed company API in the two unavoidable native sequence
  integration points.

Any new Accounting feature that derives a fiscal year must call
`company.compute_fiscalyear_dates(reference_date)` or the tuple adapter
`company.rebuild_compute_fiscalyear_dates(reference_date)`. A code review or
test failure must block a new direct recurring-cadence calculation.
