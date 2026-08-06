# Multi-company Accounting

## Product contract

The Distribution uses Odoo's native company selector and company-aware
records. An authorized user can select several allowed companies for combined
reading, but creates and changes Accounting records in one active company at a
time.

The one-off Online migration restores both in-scope legal companies with their
complete charts of accounts, journals, taxes and fiscal settings, including
inactive configuration required to understand history. Every imported move,
payment, reconciliation, control result and declaration remains company
scoped.

The canonical Online dump currently contains two EUR companies. The verified
reconstruction restores **Unstatic Labs** with its source expense journal and
creates an idempotent native expense journal for **USL MEDIA**, whose source
has none. All payment-method lines used by source payments and expenses map to
native Community methods. Unused Enterprise-only batch, ISO 20022 and SEPA
methods are classified and not imitated by inert custom configuration.

## Reports

Interactive reports have their own **Companies** selector because a saved
report must preserve its scope independently from later global-selector
changes. A new report starts with the companies currently selected in Odoo's
global selector.

- Balance générale, Journal comptable, Bilan, Compte de résultat, Cash Flow,
  Executive Summary, grouped assets and French management statements combine
  equivalent rows when all selected companies use the same company currency.
  Each combined row retains company contributions and drills into all selected
  source lines.
- Detail reports keep company-specific rows so journal-item identity, running
  balances, currencies and reconciliation evidence are not blurred.
- PDF and XLSX use the same selected-company scope and calculations as the
  screen.
- FEC, French tax packages and closing packages remain one-company outputs.
- Companies with different company currencies must be reported separately.

The Accounting Overview, Hygiene, Declarations and Closing workspaces remain
focused on the active company. This keeps operational actions, deadlines and
readiness decisions unambiguous; combined reading belongs in the reports.

These combined views are management totals, not legal consolidation. The
Distribution does not currently implement consolidation account mapping,
eliminations, multi-ledgers or currency-translation adjustments. Those require
a separately approved group-consolidation design. This boundary follows Odoo's
distinction between ordinary multi-company reporting and its full
consolidation toolset.

## Access and operating rules

- Allowed Companies on the user record is the hard access boundary.
- The highlighted company in the global selector is the active company used
  for new accounting records and company-dependent configuration.
- Selecting several companies broadens permitted reading; it does not make a
  write operation cross-company.
- Accounting Manager and read-only accountant permissions remain identical in
  each allowed company. The reviewer can inspect combined reports but cannot
  post, reconcile, configure or close.
- A report rejects an unauthorized company even if its identifier is supplied
  directly to the report API.

## Regression evidence

Automated coverage protects company-scoped SQL report models, reviewer record
rules, same-currency aggregation, contribution evidence, multi-company
drill-down, different-currency rejection, complete source configuration replay
and repeated migration idempotence.

The latest full-dump proof reconstructed 5,067 moves and 11,941 lines with no
unbalanced posted move or configuration mismatch. Per-company acceptance also
posts invoices, credit notes, bills, refunds, a general entry, a payment, a
bank transaction and an employee expense for USL MEDIA, then deliberately
rolls those temporary records back. It verifies that the scoped reviewer
cannot read the second company's Accounting or custom operational records.
Run it against a reconstructed disposable target with:

```bash
make accounting-multicompany-acceptance COMPOSE_PROJECT=<project>
```

This proves ordinary multi-company operation and same-currency management
aggregation. It is not a claim of Enterprise legal-consolidation parity: the
source contains no consolidation setup, and the Distribution does not ship
consolidation mappings, eliminations, translation adjustments or Enterprise
payment-batch transports.

Official reference:
[Odoo 19 multi-company Accounting](https://www.odoo.com/documentation/19.0/applications/finance/accounting.html#multi-company)
and [Odoo 19 consolidation](https://www.odoo.com/documentation/19.0/applications/finance/accounting/get_started/consolidation.html).
