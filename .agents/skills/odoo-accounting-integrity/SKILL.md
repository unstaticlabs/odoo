---
name: odoo-accounting-integrity
description: Protect financial meaning when changing Odoo accounting data, currencies, reconciliation, taxes, reports, lock dates, or multi-company accounting.
---

# Preserve accounting meaning

- Define the ledger invariant before editing. Never change posted entries,
  account types, or reconciliation merely to make a report match.
- Distinguish ledger balances from derived analysis fields. For foreign-currency
  documents, validate company-currency lines, `amount_currency`, the document's
  historical rate, residuals, and the report that consumes them.
- Preserve journal sequences, tax exigibility, analytic dimensions, full and
  partial reconciliations, evidence, chatter, and company ownership.
- Treat lock dates as controls. A temporary Odoo lock-date exception is an
  explicit, audited authorization—not a general bypass.
- Test with realistic dates, currencies, journals, tax states, and at least two
  companies. Odoo may display several companies together while an accounting
  operation still belongs to one active company.
- For stored-data changes, prove the module upgrade on a representative restored
  database and compare ledger-level controls before and after. State any fiscal
  ambiguity for accountant approval; do not encode a guess.

References: [Odoo accounting](https://www.odoo.com/documentation/19.0/applications/finance/accounting.html), [year-end and lock dates](https://www.odoo.com/documentation/19.0/applications/finance/accounting/reporting/year_end.html).
