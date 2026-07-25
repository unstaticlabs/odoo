# Accounting Report Presentation

## Product contract

Canonical interactive reports use one statement presentation contract while
preserving each report's professional columns and terminology. The report
engine returns explicit hierarchy roles:

- **section** for the principal statement divisions;
- **group** for account, partner, journal or analytic groups;
- **detail** for contributing lines;
- **subtotal** for intermediate totals;
- **total** for final statement results;
- **control** for reconciliation or validation conclusions.

The browser, PDF and readable XLSX sheet consume the same roles. Calculation
rows, filters, grouping, comparison values and drill-down domains remain the
authoritative shared source.

## Architecture decision

Two credible approaches were considered:

1. expose maintained OCA report wizards and their generated outputs directly;
2. retain the canonical USL client and use Odoo/OCA accounting models as its
   calculation and drill-down foundation.

OCA remains installed where it supplies maintained accounting behavior, but
its report wizards do not provide one consistent direct-opening client across
all USL, French, asset and management statements. The shared client is retained
as the lower-maintenance product surface because it already centralizes
filters, source domains and PDF/XLSX generation. Presentation semantics are
kept in the report engine rather than inferred independently in JavaScript,
PDF and XLSX.

No core Odoo patch is required.

## Interaction rules

- Results and the principal result/control appear before filter configuration.
- Preset periods expose one reference date; custom periods expose explicit
  start and end dates. The two concepts are not shown simultaneously.
- A custom comparison defaults to the same dates in the prior year and remains
  editable.
- Less common journal, account, partner and analytic filters use progressive
  disclosure and remain visible as removable active-scope chips.
- Client action state retains filters and collapsed groups while drilling into
  journal items and returning through the action stack.
- Material values retain direct source drill-down.
- Accounting statements use French date and number conventions independently
  from the user's general Odoo interface language, with tabular figures,
  restrained negative emphasis and de-emphasized zeros.

## Export rules

PDF and XLSX carry the same company, resolved dates, posted/draft scope,
comparison, filters, grouping, search and report variant as the screen.
Hierarchy roles determine shading, weight, indentation and total rules in both
formats. The XLSX `Audit Data` sheet remains intentionally raw and machine
oriented; the `Report` sheet is the accountant-readable statement.

An export is provisional accounting evidence. It does not replace statutory
review, filing or a recorded closing decision.
