# Exact foreign-amount settlement

## Purpose

**Settle** fixes one narrow bank-matching problem: Odoo can invent a foreign
amount when an imported company-currency bank transaction does not contain
one. For example, a Shine card debit may contain only `€4.40`. Odoo converts
that amount at its reference rate and suggests `$5.03`, although the selected
Cloudflare bill is exactly `$5.00`.

The authoritative facts in this workflow are:

- the `€4.40` imported bank debit;
- the selected bill's exact `$5.00` residual.

The `$5.03` value is retained only as a discarded Odoo estimate. It is not
treated as bank evidence and it does not leave a `$0.03` residual.

**Add** is unchanged. It remains the normal matching action for every existing
payment and for candidates that do not meet the stricter Settle policy.

## Accounting model

Two implementation approaches were considered:

1. revalue the posted document at the bank's executed rate and suppress the
   company-currency difference;
2. correct only the missing foreign amount and then use standard OCA/Odoo
   reconciliation.

The second approach is used. Revaluing the document would be a separate
accounting policy affecting expense or revenue valuation and potentially tax
bases. Exact settlement does not change the bill, invoice, taxes, analytics,
global rates, or imported liquidity amount.

For a bill carried as `€4.38 / $5.00` and a bank debit of `€4.40`, Settle:

1. marks the statement foreign amount as `$5.00`, derived from the selected
   document rather than reported by the bank;
2. asks the pinned OCA editable reconciliation engine to prepare
   `€4.40 / $5.00` on the payable counterpart;
3. lets native Odoo reconciliation clear the `$5.00` payable;
4. lets native Odoo create its normal `€0.02` EXCH loss using the configured
   exchange journal and account.

The bank suspense and document foreign residual are both zero afterward.
There is no dedicated settlement journal and no custom adjustment move.

## Eligibility and trust boundary

`account.move._get_immediate_settlement_eligibility(payment_line)` is the
authoritative server check. Settle is available only for a posted, open
foreign-currency invoice, bill, refund, or receipt and a posted imported bank
transaction that:

- belongs to the same company and has a coherent commercial direction;
- uses a company-currency bank journal in OCA editable reconciliation mode;
- is still one unallocated suspense line with no fee or withholding line;
- has no bank-reported or otherwise authoritative foreign amount;
- identifies either the full document residual or one unique payment term;
- is within the effective date and reference-rate deviation policy;
- is not locked, hashed, reconciled, or protected by the restrictive audit
  trail;
- is writable by an Accounting user.

The default policy is three calendar days and 3% maximum deviation. A bank
journal may override both values. Ambiguity always falls back to Add.

`account.move.js_settle_outstanding_line(line_id)` is the only widget RPC. The
browser sends no amount, rate, provenance, or trust flag. The method locks the
document, bank move, statement line, source suspense line, and relevant
payment terms, then recomputes eligibility. A repeated or stale click is
idempotent.

An installed integration may override
`_get_immediate_settlement_source_facts(payment_line)` to supply a trusted
transaction date and provenance or mark a foreign fact authoritative,
conflicting, or combined with a fee/withholding. Trusted date provenance may
replace generic date inference. It never bypasses currency, amount, rate,
company, lock, discrepancy, or permission checks.

## Audit and security

`account.immediate.settlement` records:

- document, selected terms, statement line, bank move, and original suggestion
  line ID;
- imported company amount, invoice-derived foreign amount, carrying value,
  and discarded synthetic estimate;
- executed and reference rates, deviation, and settlement dates;
- previewed/actual gain or loss, native EXCH move references, exchange account
  and lines, and reconciliation links;
- provenance, acting user, state, and reversal details.

The statement line has a separate source marker so its inferred `$5.00` is
never represented as bank-reported data. Accounting users can read audit
records but cannot create, edit, or delete them through RPC. Only validated
server code creates them with elevated access.

## Reversal and migration

Unreconciling a settlement-linked partial or using **Reverse Settlement**
routes through the native OCA bank-line undo flow. It removes the native
reconciliation and EXCH result, restores the suspense and document residuals,
and restores the statement's original missing-foreign-amount state. The audit
record remains, including the inferred amount and EXCH reference snapshot.

The `saas~19.2.1.2.0` migration does not create a journal or alter historical
reconciliations. It marks preview-era adjustment settlements as legacy,
preserves used preview journals and entries, and archives only unused journals
that were linked by the preview configuration. Legacy records remain
inspectable and reversible.

Deployment must update both `usl_accounting` and
`rebuild_account_migration`. “Use payment rate” document revaluation is
explicitly out of scope.
