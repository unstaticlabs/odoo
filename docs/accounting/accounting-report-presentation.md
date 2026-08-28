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

Account labels use native, translated `account.account.name` values. A former
report-only override model was retired because it made the Chart of Accounts
and reports disagree and its six installed records had no company- or
report-specific scope. Account code and ledger relationships remain the stable
identity; the one-shot restore records source evidence externally and applies
the six verified naming corrections to the target Chart of Accounts in French
and English. This makes every native screen, report, PDF, XLSX and FEC consume
one governed master-data label. Accounting Managers maintain names through
**Configuration > Accounting > Chart of Accounts** and Odoo's standard
translations.

For statement drill-down, three credible approaches were compared:

1. switch the whole statement to **Regrouper par > Compte**, which exposes
   account totals but replaces the legal statement structure with a trial
   balance;
2. add account rows only in the browser, which would diverge from PDF, XLSX,
   comparison and source-domain behavior;
3. expand each configured source line through native `account.group` ancestors
   to the contributing account numbers in the shared report row tree.

The third approach is used. A source line is unfoldable only when the signed
sum of its contributing trial-balance accounts reconciles to the displayed
line within one cent. Derived totals and conditionally filtered formulas stay
calculation rows unless their exact contribution rule is available. This
prevents a plausible-looking but false account breakdown. Native PCG group
prefix ranges and parent relationships remain company-configurable; the
statement engine does not hardcode a second chart hierarchy.

For official-document rendering, three credible options were compared:

1. standard Odoo QWeb reports with `web.external_layout`, which provide native
   company branding but would introduce a second statement renderer and a
   separate calculation/template contract;
2. OCA QWeb/XLSX report outputs, which remain appropriate for their specialist
   wizards but do not cover the configured USL, French and management
   statements as one product;
3. the governed `accounting_statement.v2` LaTeX renderer driven by the same
   report session and hierarchy as the interactive client, while preserving
   the existing XLSX exporter.

The third option is the canonical PDF exporter. It receives the report
session's exact selected rows, hierarchy, filters, display unit and rounding;
it performs no accounting calculation. The resolved report definition maps
safe specialist theme overrides into the shared A4 template. The defaults use
embedded Lato, restrained black/gray hierarchy, compact tables, company legal
identity in the repeated header and a quiet document footer. Company overrides
remain validated for six-digit hexadecimal colors and a minimum 4.5:1 section
contrast ratio. XLSX remains unchanged and consumes the same session truth.

Version 2 replaces the former flat columns/rows payload with allow-listed
semantic columns, ordered sections, hierarchy roles, page-break policy, totals
and controls. Version 1 remains registered only for immutable historical
attachments. Current bindings and previews resolve to v2. Neither version
accepts formulas, raw LaTeX or arbitrary template paths.

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
- Reference, custom and comparison fields use Odoo's calendar input with an
  explicit `DD/MM/YYYY` display contract. Browser-native date inputs are
  excluded because they can render month-first independently of Odoo.
- A custom comparison defaults to the same dates in the prior year and remains
  editable.
- Less common journal, account, partner and analytic filters use progressive
  disclosure and remain visible as removable active-scope chips.
- Applicable reports offer **Masquer les lignes à zéro**. It removes only
  detail lines and accounts whose displayed current, comparison and activity
  columns are all zero; offset debit/credit activity is not mistaken for an
  empty account, and empty PCG branches are pruned. Structural sections,
  statement totals and balance controls remain; zero detail and intermediate
  subtotal rows may be omitted.
- The display unit is a first-level choice. Units, thousands and millions use
  the selected company's currency symbol and scale company-currency figures;
  an original foreign-currency amount remains in its own unscaled currency.
- Presentation rounding is a separate first-level choice. Financial
  statements, fiscal reports and management summaries default to the nearest
  euro; reconciliation-oriented ledgers, partner reports and schedules default
  to cents. The active report definition governs that default and the user can
  switch it without changing any accounting value.
- French statement variants are resolved by the configured definition for the
  selected company and period. The client presents that resolved variant
  instead of asking users to choose an inapplicable historical ruleset.
- Client action state retains filters and collapsed groups while drilling into
  journal items and returning through the action stack.
- Source statement lines initially stay folded to preserve a compact financial
  statement. Unfolding reveals French PCG group codes and names, their native
  subgroups where configured, then the full account number and account label.
- Material values retain direct source drill-down.
- Calculated parents such as total equity do not repeat the same account
  subtree already exposed by their contributing result line. An account leaf
  appears once under its reconciling statement source line.
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
The export call receives the client's current filter object and refreshes the
same wizard session before rendering, preventing a download from using a stale
pre-filter state. The zero-line choice appears in export metadata and as a
concise document-context label when active. The export response returns the
refreshed preview identifiers to the client, preventing a subsequent fold or
drill-down from referring to lines replaced during export synchronization.
Readable periods, row dates, metadata, headers and generation timestamps are
day-first in both formats. ISO dates remain confined to machine metadata and
the XLSX raw audit sheet.
Hierarchy roles determine shading, weight, indentation and total rules in both
formats. Folding is part of the displayed statement state: a download contains
the same visible hierarchy, including visible PCG group codes and account
numbers, and its metadata records the collapsed group keys.
Display-unit scaling and the selected rounding apply identically to the
screen, PDF and readable XLSX `Report` sheet. Whole-euro presentation uses
commercial half-up rounding: a fraction of `0.50` is rounded to the next euro.
Calculations continue from exact ledger values and rounding occurs only after
each report expression has been evaluated. The XLSX `Audit Data` sheet remains
intentionally exact, unscaled and machine oriented.

PDF pages repeat company identity, registry/VAT context, address, reporting
date, official-document label and page number. Column headers and section rows
use high-contrast light fills with dark text; final totals retain formal
accounting rules rather than decorative saturated fills. Physical top and
bottom safety areas are balanced for office printing. Semantic section and
total rows reserve enough remaining page space to avoid orphaned headings or
detached totals; final totals use a bounded rule-and-wash treatment that stays
recognisable in grayscale.

The report-specific hierarchy is part of the shared session, not a PDF-only
decoration. In particular:

- the Balance générale uses PCG classes 1–8, exact class subtotals and a final
  debit/credit equality control;
- the Grand livre uses one account block with opening, movements and closing;
- the Journal comptable groups journals by native journal type before journal;
- the Grand livre auxiliaire nests account blocks below each partner;
- open items separate customers and suppliers and ageing reports use their
  real `1–30`, `31–60`, `61–90` and `> 90` buckets;
- the Bilan and detailed Bilan emit separate Actif and Passif sections with a
  deterministic page break, distinct equity, exact totals and an unnetted
  balance control. Closing balances, including the closing class 6/7 result,
  are authoritative for the statement-at-date; aggregate Actif, Passif and
  equality values are repeated in a compact summary before the detailed sides;
- the asset register labels each account section and places its grand total
  after every account subtotal;
- management, tax, analytic and schedule reports preserve their specialist
  section keys and exact subtotal rows;
- the analytic pivot PDF request contains only allow-listed axes, measures,
  ordering, domain and safe context. The server reapplies active-company access
  and recomputes the matrix with ORM aggregation. Wide matrices are split into
  bounded landscape segments with the row header repeated.

The French annual package adds deterministic cover, contents and preparation
status pages before the canonical statements, then SIG/CAF and defined
management ratios. It says that it was prepared by the company and is not
professionally attested. The product never generates the previous
accountant's attestation or implies professional approval. Controlled
accounting-method narratives remain a closing input; the generated status note
does not fabricate policies that have not been reviewed.

PCG classification rules used by the canonical statements include:

- `701`–`706` in production sold and `707` (net of `7097`) in merchandise
  sales and commercial margin;
- `455` associate current accounts in financial/associate debt, not supplier
  payables;
- visible other operating products and explicit total products/total charges;
- ratios calculated from the same statement rows, with `€`/display-unit,
  `jours` or `x` shown beside every value and no denominator-zero result
  presented as a real zero.

An export is provisional accounting evidence. It does not replace statutory
review, filing or a recorded closing decision.

## References checked

- Odoo 19 Chart of Accounts and account-group reporting behavior:
  https://www.odoo.com/documentation/19.0/applications/finance/accounting/get_started/chart_of_accounts.html
- Odoo 19 report-line hierarchy, folding and actions:
  https://www.odoo.com/documentation/19.0/de/developer/reference/standard_modules/account/account_report_line.html
- Odoo 19 account-prefix and grouping report expressions:
  https://www.odoo.com/documentation/19.0/applications/finance/accounting/reporting/customize.html
- Autorité des normes comptables, recueil PCG au 1er janvier 2026:
  https://www.anc.gouv.fr/files/anc/files/1_Normes_fran%C3%A7aises/recueil/2026/Recueil-PCG-Janvier-2026.pdf
- Code de commerce, composition et structure des comptes annuels:
  https://www.legifrance.gouv.fr/loda/id/LEGISCTA000034161774
- CGI, article 1649 undecies, fiscal bases rounded to the nearest euro:
  https://www.legifrance.gouv.fr/codes/section_lc/LEGITEXT000006069577/LEGISCTA000006147278/
- Code des impositions sur les biens et services, articles L131-1 and L131-2:
  https://www.legifrance.gouv.fr/codes/section_lc/LEGITEXT000044595989/LEGISCTA000044598033/
- Bpifrance Création, formules des soldes intermédiaires de gestion:
  https://bpifrance-creation.fr/encyclopedie/piloter-lentreprise/finance-pilotage-economique/comprendre-calculer-soldes
