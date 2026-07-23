# Milestone 13 reporting and closing UX target

Last updated: 2026-07-23

Audience: product owner, accountant, finance operator, and implementation agents.

This document records the desired end-user reporting and year-end closing experience for Unstatic Labs. It is based on the supplied reference report samples and on the current Milestone 13 feedback.

It defines the product target before further implementation. It does not authorize hard-coding benchmark values.

## Reference material reviewed

The following local files were inspected as reporting references:

- `Plaquette UNSTATIC LABS.pdf`
- `sig_-_soldes_intermediaires_de_gestion_usl_2024-2025_unstatic_labs.pdf`
- `sig_-_soldes_intermediaires_de_gestion_usl_2024-2025_unstatic_labs.xlsx`
- `rapport_de_taxes_janv._2025_aout_2026_unstatic_labs.xlsx`

The annual accounts PDF is a 22-page A4 package with:

- cover page
- summary
- accountant attestation
- Bilan Actif
- Bilan Passif
- Compte de resultat
- detailed balance sheet accounts
- detailed profit and loss accounts
- accounting rules and methods
- ratios
- Soldes Intermediaires de Gestion
- Capacite d'autofinancement

The SIG PDF is a 2-page A4 report generated with `wkhtmltopdf`. It includes company header, VAT number, period label, page numbers, report date, hierarchy, subtotals and formulas.

The SIG workbook includes:

- report sheet
- filter sheet
- period label
- company filter
- typed numeric balances
- formula-like row labels such as value added, commercial margin, production, EBE, operating result, current result before tax, exceptional result, net result and corporate-income-tax charge

The tax workbook includes:

- report sheet
- filter sheet
- period label
- company filter
- VAT-style sections and boxes
- columns for balance and adjustment
- official-style labels such as taxable operations, non-taxed operations, gross VAT, deductible VAT, VAT credit, credit to report, net VAT due and total to pay

## Product goal

Odoo must become the daily accounting closing workbench, not just a repository of imported accounting data.

The final user experience must support two rhythms:

- Daily or weekly preparation: reconcile, review, correct drafts, inspect invoices, bills, refunds, expenses, journal entries, tax issues and missing evidence.
- Period and year-end closing: verify readiness, produce reports, guide declaration fields, export the accountant package, archive evidence and lock the period.

The UI should make the frequent routines easy for Valentin and the accountant. Detailed technical evidence remains available, but it should be behind an "Advanced Audit" area.

## Daily workbench target

The Accounting app now opens directly to a company-scoped Accounting Home. It
combines bank and cash positions, unmatched transactions, daily document queues,
open balances, closing readiness, declarations and prepared actions. The
standard Odoo journal dashboard remains available through `Dashboard` and the
Home header.

Primary paths should be two clicks away:

- Accounting -> Home
- Accounting -> Dashboard
- Accounting -> Customers -> Invoices
- Accounting -> Customers -> Credit Notes
- Accounting -> Vendors -> Bills
- Accounting -> Vendors -> Refunds
- Accounting -> Expenses
- Accounting -> Bank -> Reconcile
- Accounting -> Bank -> Bank Statements
- Accounting -> Journal Entries
- Accounting -> Review -> Issues
- Accounting -> Review -> Missing Evidence
- Accounting -> Reports
- Accounting -> Tax and Declarations
- Accounting -> FEC

The Community top-level app label `Invoicing` was not sufficient for Milestone
13. The implemented `Accounting` entry now opens the operational Home directly,
while retaining native journal cards and direct journal access.

## Reconcile and review target

The final product must provide an accounting reconciliation experience for historical and ongoing bank data.

Required user outcomes:

- see bank journals and balances
- see imported bank statement lines
- see unreconciled, partially reconciled and fully reconciled items
- open the source journal items involved
- see suggested matches only as suggestions unless explicitly approved
- see write-offs, bank fees, internal transfers and foreign-currency differences
- understand why an item is blocked

The current implementation preserves reconciliation data as imported evidence, but the bank journal transaction view is not yet a satisfying reconciliation workbench.

## Customer, vendor and expense scope

Milestone 13 should now include customer and vendor business objects in addition to posted ledger replay, where the source dump contains usable processed objects.

Target coverage:

- customer invoices
- customer credit notes
- customer payments and residuals
- vendor bills
- supplier refunds
- supplier payments and residuals
- expense accounting records
- links from documents to journal entries
- links from journal entries to evidence
- filters for draft, posted, paid, unpaid, overdue and locked-period records

Exact posted ledger replay remains the statutory baseline. Business-document reconstruction is a separate layer used for usability, review and future operations.

## Reporting UX target

The current custom wizard is not the final target. It is acceptable as an evidence export, but not as the user-facing report product.

Reports must feel like accounting reports:

- dynamic screen first
- filters visible on screen
- period/date controls
- company selection
- posted/all scope where meaningful
- account, journal, partner and analytic filters where meaningful
- folded and unfolded hierarchy
- drill-down to entries
- drill-down to documents and attachments
- annotations or review notes where useful
- export as seen to PDF and XLSX
- CSV or machine export available as an advanced/audit option

Official Odoo 19 documentation describes dynamic accounting reports with expand/drill-down behavior, period comparison and PDF/XLSX export:

- https://www.odoo.com/documentation/19.0/applications/finance/accounting/reporting.html

The final USL product should match that interaction model where it matters for USL, while adding USL-specific French SASU guidance.

## PDF and XLSX report target

PDF exports should be human-readable, printable and accountant-ready.

They must include:

- company name
- company address and identifiers where appropriate
- VAT number where appropriate
- period
- report generation date
- page numbers
- clear title
- clear sections
- consistent numeric alignment
- totals and subtotals
- currency
- applied filters
- source/audit reference where appropriate

XLSX exports should be templated, readable and useful for review:

- one report sheet
- one filters/metadata sheet
- typed numeric values
- formulas where useful
- frozen headers where useful
- clear number formats
- grouped sections where useful
- no clipped labels
- no raw technical IDs unless in an audit/export sheet

Machine-oriented CSV exports may remain, but they should be labelled as audit/detail exports rather than the main report.

## Annual accounts target

The annual accounts package must be assembled from controlled components.

System-generated:

- cover metadata
- balance sheet assets
- balance sheet liabilities
- profit and loss
- detailed balance sheet accounts
- detailed profit and loss accounts
- fixed-asset tables
- ratios
- SIG
- CAF
- appendix data tables where derivable

Controlled narrative:

- accounting methods
- comments and explanations
- closing notes

Accountant-authored or accountant-approved:

- attestation
- professional opinion
- final approval

The system must not fabricate accountant attestation text or imply accountant approval before it exists.

## French declarations guidance target

The product should help prepare official declarations, not submit them automatically during this milestone.

The user needs guided views for what to enter in official portals:

- https://cfspro.impots.gouv.fr/mire/accueil.do
- https://portailpro.gouv.fr/

The guidance must show:

- declaration
- form or portal step
- field or box
- value
- source of value
- calculation
- contributing accounts
- contributing journal items
- external/manual values
- missing inputs
- warning status
- reviewer
- period
- company

Official deadlines and portal behavior change. Deadline and declaration guidance must be refreshed from official sources before each filing cycle. The official impots.gouv.fr professional fiscal calendar is the primary reference:

- https://www.impots.gouv.fr/professionnel/calendrier-fiscal

## Declaration schedule target

Accounting Home should show upcoming and open declaration work.

For each declaration task:

- company
- period
- due date
- portal
- status
- required inputs
- blocking issues
- review owner
- accountant review status
- last generated package
- archive link after close

Suggested statuses:

- To Prepare
- Data Missing
- Ready for Internal Review
- Ready for Accountant Review
- Accountant Reviewed
- Filed Externally
- Paid
- Archived
- Blocked

No task should claim "Filed" unless the external filing has actually happened and evidence is attached.

## Closing package target

At close, Odoo should generate one archived review package containing:

- what period was closed
- source/import identity
- ledger controls
- report bundle
- declaration field mappings
- FEC
- fixed-asset register
- depreciation schedule
- VAT and tax reports
- open discrepancies
- accepted differences
- evidence index
- accountant review record
- final lock dates

The package should answer both:

- What does the company financial position mean?
- What values should be used to complete the mandatory French declarations, and why?

## Report families to implement

Priority 1:

- Accounting Overview
- Reconciliation Workbench
- Journal Entries
- Customer Invoices
- Customer Credit Notes
- Vendor Bills
- Supplier Refunds
- Expenses
- Trial Balance
- General Ledger
- Partner Ledger
- Aged Receivable
- Aged Payable
- Balance Sheet
- Profit and Loss
- VAT / Tax Report
- FEC

Priority 2:

- French Annual Accounts
- Detailed Balance Sheet
- Detailed Profit and Loss
- Fixed Asset Register
- Depreciation Schedule
- SIG
- CAF
- Ratios
- Corporate income-tax package mapping
- CA12 guidance

Priority 3:

- EC Sales List where actual transactions require it
- OSS reports where actual transactions require it
- deferred expense/revenue reports where actual records require it
- bank synchronization readiness after historical accounting is trustworthy

Out of Milestone 13 scope:

- payment-provider product support
- automatic filing
- autonomous reconciliation
- autonomous posting
- automatic accountant attestation

## Acceptance checklist

- [x] A user can open Accounting from the app launcher without knowing `/odoo/accounting`.
- [x] A user can reach the main daily workflows in two clicks.
- [x] Bank transactions and reconciliation state are visible in a coherent workbench.
- [ ] Customer invoices and refunds are usable as business documents where source data supports them.
- [ ] Vendor bills and refunds are usable as business documents where source data supports them.
- [x] Expenses, direct commercial-document settlement, General Reconciliation and all bank transactions are reconstructed and explicitly classified: isolated Track B validates all `325` current-period source expenses, all `97` company-payment transitions and all `95` employee reimbursements; `233` bank transactions and `339` edges directly linked to current commercial documents; all `111` non-bank document partials and `40` native exchange partials; `1,415` direct/source-open bank transactions; and the final `95` external-endpoint transactions with `125` exact counterpart lines. The settlement stages preserve `67` exact outside-only bank counterparts, allowing all five posted cross-bank edges and the related exchange edge to settle natively. Native bank coverage is `1,841/1,841`, and a disposable hybrid candidate now combines it with exact benchmark history; cutoff-boundary acceptance, replacement report/role/browser validation, professional acceptance of the EUR `2.64` native exchange profit-and-loss difference and promotion remain separate items.
- [x] Reports open as dynamic views before export.
- [ ] PDFs are readable, aligned and accountant-ready.
- [ ] XLSX exports are templated and reviewable.
- [x] Report lines drill down to journal items and evidence.
- [x] Declaration guidance shows official form/box mappings and sources.
- [x] Declaration reminders are visible on Accounting Home.
- [x] FEC can be generated by the intended authorized user.
- [ ] Accountant review state is explicit.
- [ ] Machine evidence exports remain available but are not confused with user-facing reports.
