# Accounting core

## Outcome

The production Distribution preserves and operates the accounting reality used
by USL and its accountant. Accepted Odoo Online accounting and reporting
results remain historical parity evidence; the current production database is
authoritative for every upgrade and recovery.

## Required functional perimeter

The Distribution supports, where used:

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
- Every Accounting feature resolves a fiscal year through the same
  company-governed boundary contract. The exceptional first USL exercise is
  10/01/2024–30/09/2025; recurring exercises then run from 1 October to
  30 September. Reports, exports, FEC defaults, cash/tax projections,
  analytics, spreadsheets, declarations, closings and fiscal sequence ranges
  must agree for the same reference date.
- Multi-company convenience never merges legal records. Provider-controlled
  ECB rates are synchronized across companies with the same base currency,
  while restored/manual rates remain company-specific. A person may submit
  expenses in several allowed companies through one company-specific employee
  profile per company; HR, payroll, approval and accounting data stay isolated.
  An imported bank-only company receives the minimal native operational
  journals needed for invoices, bills, entries and expenses, without changing
  source journals or historical accounting.
- Financial statements and declaration-oriented reports open with whole-euro
  presentation; reconciliation-oriented ledgers retain cents by default. The
  user may change presentation rounding, but screen, PDF and readable XLSX
  must agree while calculations and audit data retain exact ledger amounts.
- Smart recommendations expose their scored source facts and intended ledger
  effects. Low-confidence partner inference may inform a harmless suggestion,
  but never silently authorizes an accounting action. A user-approved bank
  match may correct the transaction partner and suspense account only when the
  UI discloses those changes; native reconciliation remains authoritative and
  chatter records the evidence used.
- Posted history is never silently altered or deleted.
- Accounting differences are visible, classified and evidence-backed.
- Legal compliance is professionally reviewed, not inferred from passing software tests.
- Live bank connectivity is optional; complete historical bank accounting is
  not.
- Accounting Hygiene and Closing use one visible, company-scoped control
  catalogue. Business policy is configurable; advanced evaluators are
  whitelisted module extensions rather than arbitrary code.
- Dismissing Hygiene acknowledges only the current detected occurrence. It
  never disables the Control: unchanged evidence stays dismissed, while new
  records or materially changed evidence make the result actionable again and
  retain the prior dismissal in its audit history.
- A technical control failure is not reported as an accounting failure and
  cannot produce a false Ready conclusion.
- Interactive reports are statement-first: one shared filter and interaction
  system presents explicit sections, groups, details, subtotals, totals and
  controls consistently on screen, in PDF and in the readable XLSX sheet.
- Financial statement source lines unfold through the company's native PCG
  `account.group` hierarchy to full account numbers. An account breakdown is
  displayed only when its signed contributions reconcile to the statement
  value; screen, source drill-down, PDF and readable XLSX retain the same
  codes, labels and amounts.
- Applicable reports can hide fully zero-valued detail and account rows
  without hiding accounts that have debit, credit or comparison activity.
  Sections, subtotals and totals remain visible. The setting is part of the
  shared report session, visible scope and export metadata; PDF/XLSX generation
  refreshes from the current client filters before rendering.
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
  and UBL/CII/Factur-X capabilities behind an explicit production activation
  gate. Company configuration, safe-test verification, production onboarding,
  live connection and scheduled reception are separate states. Received
  payloads retain company-scoped original-file and duplicate/retry evidence,
  then enter the normal native vendor-bill, posting, payment and reconciliation
  workflow. Reception activation never enables e-reporting.
- Controls, Reports and Declarations are governed definitions with shared
  origin, lifecycle, company, effective-date and version semantics. Runtime
  issues, report sessions/exports and filing instances freeze the definition
  provenance that produced them.
- Upgrade-managed definitions are adapted through company overrides; ordinary
  Accounting configuration never executes arbitrary Python, SQL or JavaScript.

Detailed invariants and parity standards live under `docs/accounting/`.
