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
- Smart recommendations expose their scored source facts and intended ledger
  effects. Low-confidence partner inference may inform a harmless suggestion,
  but never silently authorizes an accounting action. A user-approved bank
  match may correct the transaction partner and suspense account only when the
  UI discloses those changes; native reconciliation remains authoritative and
  chatter records the evidence used.
- Posted history is never silently altered or deleted.
- Accounting differences are visible, classified and evidence-backed.
- Legal compliance is professionally reviewed, not inferred from passing software tests.
- Live bank connectivity is optional and outside the initial parity gate; historical bank accounting is not.
- Accounting Hygiene and Closing use one visible, company-scoped control
  catalogue. Business policy is configurable; advanced evaluators are
  whitelisted module extensions rather than arbitrary code.
- A technical control failure is not reported as an accounting failure and
  cannot produce a false Ready conclusion.
- Interactive reports are statement-first: one shared filter and interaction
  system presents explicit sections, groups, details, subtotals, totals and
  controls consistently on screen, in PDF and in the readable XLSX sheet.
- Each accounting need has one canonical end-user report. **Compte de
  résultat** is the single French performance statement; historical generic
  and detailed aliases may remain for migration compatibility but are not
  separate menu choices.
- Designed statements use a restrained A4-like reading width on screen.
  Headline figures state their unit, and every section/hover state must retain
  accessible contrast.
- Report definitions also govern the official document template, primary and
  muted colors, section colors and footer label. Screen, PDF and readable XLSX
  consume that configuration; company overrides must satisfy a minimum 4.5:1
  section contrast ratio.
- Exploratory analysis uses Odoo's native pivot/list/graph framework over
  authoritative analytic items. It remains distinct from designed financial
  statements while reconciling to the same accounting population.
- French electronic-invoice reception uses Odoo's maintained approved-platform
  and UBL/CII capabilities behind an explicit production activation gate.
  Readiness, live connection and scheduled exchange are separate states;
  received payloads retain immutable processing evidence and enter the normal
  native vendor-bill workflow.
- Controls, Reports and Declarations are governed definitions with shared
  origin, lifecycle, company, effective-date and version semantics. Runtime
  issues, report sessions/exports and filing instances freeze the definition
  provenance that produced them.
- Upgrade-managed definitions are adapted through company overrides; ordinary
  Accounting configuration never executes arbitrary Python, SQL or JavaScript.

Detailed invariants and parity standards live under `docs/accounting/`.
