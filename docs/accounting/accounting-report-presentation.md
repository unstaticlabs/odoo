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

**Compte de résultat** is the one canonical French performance statement. It
uses the governed PCG presentation with products, charges, intermediate
results, financial result and result for the year. The former detailed entry
is a deprecated compatibility alias that resolves to this same report and is
not displayed in the Reporting menu.

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

For official-document rendering, three credible options were compared:

1. standard Odoo QWeb reports with `web.external_layout`, which provide native
   company branding but would introduce a second statement renderer and a
   separate calculation/template contract;
2. OCA QWeb/XLSX report outputs, which remain appropriate for their specialist
   wizards but do not cover the configured USL, French and management
   statements as one product;
3. the existing deterministic ReportLab/XLSX exporters driven by the same
   report session and hierarchy as the interactive client.

The third option remains the canonical exporter. Its visual tokens are no
longer hardcoded per renderer: the resolved report definition supplies an
official A4 template key, primary/muted colors, section background/text colors
and footer label. The defaults follow the supplied USL LaTeX conventions:
sans-serif typography, restrained black/gray hierarchy, compact tables,
company legal identity in the repeated header and a quiet document footer.
Company overrides are validated for six-digit hexadecimal colors and a minimum
4.5:1 section contrast ratio.

For navigation, retaining Odoo's four broad report families was also compared
with one flat list and with purpose-based families. A flat list made common
books and statements compete with specialist outputs. The selected structure
uses six stable purposes—accounts and journals, financial statements, partners
and ageing, tax, management, and assets/periods—while preserving the configured
actions behind every canonical report.

## Interaction rules

- Results and the principal result/control appear before filter configuration.
- Headline monetary results repeat the selected display unit in their label;
  for example, `Résultat net de l’exercice (€)`.
- Preset periods expose one reference date; custom periods expose explicit
  start and end dates. The two concepts are not shown simultaneously.
- A custom comparison defaults to the same dates in the prior year and remains
  editable.
- Less common journal, account, partner and analytic filters use progressive
  disclosure and remain visible as removable active-scope chips.
- The display unit is a first-level choice. Units, thousands and millions use
  the selected company's currency symbol and scale company-currency figures;
  an original foreign-currency amount remains in its own unscaled currency.
- French statement variants are resolved by the configured definition for the
  selected company and period. The client presents that resolved variant
  instead of asking users to choose an inapplicable historical ruleset.
- Client action state retains filters and collapsed groups while drilling into
  journal items and returning through the action stack.
- Material values retain direct source drill-down.
- Accounting statements use French date and number conventions independently
  from the user's general Odoo interface language, with tabular figures,
  restrained negative emphasis and de-emphasized zeros.
- Portrait statements render inside a centered A4-like reading surface;
  column-heavy ledgers use a bounded landscape surface with local horizontal
  scrolling rather than stretching across the entire application window.
- Principal sections use a light configured background and dark configured
  text. Hover retains the same contrast instead of applying the generic detail
  row hover color.

## Export rules

PDF and XLSX carry the same company, resolved dates, posted/draft scope,
comparison, filters, grouping, search and report variant as the screen.
Hierarchy roles determine shading, weight, indentation and total rules in both
formats. Folding is part of the displayed statement state: a download contains
the same visible hierarchy, and its metadata records the collapsed group keys.
Display-unit scaling applies to the readable statement while the XLSX `Audit
Data` sheet remains intentionally raw and machine oriented. The `Report` sheet
is the accountant-readable statement.

PDF pages repeat company identity, registry/VAT context, address, reporting
date, official-document label and page number. Column headers and section rows
use high-contrast light fills with dark text; final totals retain formal
accounting rules rather than decorative saturated fills.

An export is provisional accounting evidence. It does not replace statutory
review, filing or a recorded closing decision.
