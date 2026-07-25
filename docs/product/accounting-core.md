# Accounting core

## Outcome

The Community target must preserve and operate the accounting reality used by USL and its accountant. It must reproduce accepted Odoo Online accounting and reporting results from approved source exports before production replacement is considered.

## Required functional perimeter

The target supports, where used:

- French chart of accounts and localization;
- customer invoices, vendor bills and credit notes;
- payments, expenses and reimbursements;
- journals, entries, sequences and lock dates;
- bank statements and reconciliation without requiring live banking;
- multi-currency transactions and exchange differences;
- taxes, fiscal positions, VAT and CA12-relevant values;
- analytic accounting;
- shareholder and intercompany accounts;
- assets, amortization and deferred items;
- supporting documents and audit history;
- statutory, management and accountant reports;
- valid FEC generation.

## User experience

Valentin and the accountant must be able to:

- understand the accounting state without developer assistance;
- trace report values to journal items and evidence;
- identify unresolved discrepancies;
- distinguish drafts, posted records, corrections and locks;
- review company-specific records without exposure to unrelated private material.

## Product rules

- Standard accounting records form the authoritative ledger.
- Custom workflows orchestrate accounting; they do not create parallel ledgers.
- Posted history is never silently altered or deleted.
- Accounting differences are visible, classified and evidence-backed.
- Legal compliance is professionally reviewed, not inferred from passing software tests.
- Live bank connectivity is optional and outside the initial parity gate; historical bank accounting is not.
- Accounting Hygiene and Closing use one visible, company-scoped control
  catalogue. Business policy is configurable; advanced evaluators are
  whitelisted module extensions rather than arbitrary code.
- A technical control failure is not reported as an accounting failure and
  cannot produce a false Ready conclusion.

Detailed invariants and parity standards live under `docs/accounting/`.
