# Immediate settlement accounting

## Purpose

Immediate Settlement is an opt-in extension of Odoo's native outstanding
credit and debit workflow. It applies when a foreign-currency document and its
payment are one economic event and the payment supplies both authoritative
facts: the exact foreign amount and the actual company-currency amount.

The native **Add** action is unchanged. It remains the default for foreign
receivables and payables held over time and may create Odoo's normal exchange
difference. **Settle** is offered separately only when the server can prove
that an executed-rate settlement is deterministic and within policy.

## Posting model

The implementation considered three compatible approaches. Standard Odoo/OCA
reconciliation is retained unchanged as **Add**, because it is correct for a
genuine outstanding foreign debt. Rewriting posted document or payment lines
was rejected because it hides a material accounting change and conflicts with
secure-entry controls. A document-specific currency-rate override would also
alter valuation semantics beyond the settled portion. The selected explicit
adjustment move is narrow, inspectable, reversible, and keeps native
reconciliation responsible for residual creation and clearing.

The engine never changes posted invoice, payment, or bank statement journal
items. It creates an auditable move in the company's **Immediate Settlements**
general journal:

- a zero-foreign-amount receivable or payable valuation line adjusts the
  document's settled company-currency value;
- balancing lines use the original economic accounts in proportion to their
  posted balances and preserve analytic distributions;
- tax, liquidity, receivable/payable, suspense, fee, and unsupported
  mixed-sign lines are excluded from economic allocation;
- no tax IDs, tax tags, repartition metadata, or tax-base metadata are copied;
- a bank statement candidate on a reconcilable suspense account receives
  explicit suspense-clearing and receivable/payable bridge lines.

The controlled reconciliation calls native Odoo with exchange-difference
creation disabled. That context is private to **Settle**; the **Add** route
continues to use the standard reconciliation path.

For a partial settlement, only the exact foreign amount represented by the
selected payment uses the executed rate. The remaining foreign residual stays
on the original document terms and retains normal Odoo valuation.

## Eligibility and trust boundary

`account.move._get_immediate_settlement_eligibility(payment_line)` is the
authoritative server check. It requires compatible posted records, one
company, the same non-company currency, coherent signs, real foreign and
company amounts, a deterministic residual or payment term, an explained bank
transaction, an eligible economic allocation, an unlocked period, accounting
permissions, and compliance with the effective date and rate policies.

`account.move.js_settle_outstanding_line(line_id)` is the only widget RPC. The
client never sends an amount, rate, or trust flag. The method locks the
document and payment moves, rechecks eligibility, and is idempotent for repeat
clicks.

A future installed integration may override
`_get_immediate_settlement_source_facts(payment_line)` to provide exact source
facts and provenance. A trusted source can replace generic date inference but
cannot bypass currency, amount, discrepancy, rate, lock, company, or
permission checks. No platform-specific model is part of this implementation.

## Audit and reversal

`account.immediate.settlement` stores the source document and journal item,
payment and bank statement links, exact currency pair, executed and reference
rates, deviation, dates, provenance, user, adjustment, allocations, and
partial reconciliations.

Removing a linked partial reconciliation reverses the whole settlement
atomically. The adjustment is reversed, all settlement partials are removed,
and the original document and payment residuals are restored. Settlement
adjustments cannot be drafted, cancelled, edited, or deleted directly.
Original and reversal audit records remain available.

## Configuration and migration

Company defaults are three calendar days and a 3% maximum deviation from the
document reference rate. A journal may override both thresholds. Configured
fee accounts are kept separate from the executed settlement and do not enter
the rate calculation.

Installation and the `saas~19.2.1.1.0` migration create one dedicated general
journal per existing company and initialize field defaults without changing
historical entries or reconciliations. New companies receive their own
journal. Deployment must update both `usl_accounting` and
`rebuild_account_migration`.
