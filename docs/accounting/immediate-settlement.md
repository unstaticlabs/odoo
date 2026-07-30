# Three-action foreign-currency settlement

## Purpose

An imported company-currency bank transaction may have no authoritative
foreign amount. Odoo then converts the bank balance at its reference rate. A
`€4.40` card debit can appear as `$5.03` beside a `$5.00` Cloudflare bill.

This feature separates three accounting decisions:

| Action | Foreign amount | Company-currency treatment |
| --- | --- | --- |
| **Add** | Existing Odoo candidate | Existing native behavior |
| **Settle** | Exact selected document residual | Native Odoo FX |
| **Use payment rate** | Exact selected document residual | Safe economic-account allocation; no FX |

Add is not renamed, removed, or intercepted. Facturations Plateformes and
automatic posted-document revaluation remain out of scope.

## Design decision

Three credible mechanisms were evaluated:

1. mutate or revalue the posted invoice;
2. create a dedicated adjustment journal and move;
3. use the existing editable OCA bank move.

The third mechanism is used. The posted document, imported liquidity line,
global currency rates, and tax lines remain unchanged. The bank move is already
the native location where OCA prepares the statement counterpart and is
reversible through the standard bank-line undo path. No IMST journal is
created.

Odoo's standard delayed-settlement FX remains the Settle policy, consistent
with [Odoo's multi-currency model](https://www.odoo.com/documentation/19.0/applications/finance/accounting/get_started/multi_currency.html).
Payment-rate treatment is limited to immediate economic events. It never
copies VAT metadata because foreign-currency VAT valuation follows its own
tax-exigibility rules; see the
[BOFiP foreign-currency VAT guidance](https://bofip.impots.gouv.fr/bofip/1475-PGP.html/identifiant=BOI-TVA-BASE-10-20-40-20-20120912).

## Shared validation

`account.move._get_foreign_settlement_context(payment_line)` is the common
server-side gate. It requires:

- a posted, open foreign-currency invoice, bill, refund, or receipt;
- one editable company-currency OCA bank statement candidate;
- one unallocated, reconcilable suspense line;
- the same company, coherent partner and commercial direction;
- one deterministic full residual or unique payment term;
- no conflicting currency fact, combined fee, withholding, lock, secure hash,
  or immutable audit-trail protection;
- Accounting User permission and write access.

The browser sends only the candidate line ID. Both RPCs lock the document, bank
move, statement line, selected term lines, and source line, then re-run the
gate. Repeated clicks return the existing audit record; stale cross-policy
clicks are rejected.

An installed integration may override
`_get_immediate_settlement_source_facts(payment_line)` to provide a transaction
date, provenance, and authoritative/conflicting foreign facts. Trusted
provenance may replace generic date inference for payment-rate timing. It
cannot bypass amount, currency, rate, discrepancy, permission, lock, or company
checks.

## Settle: exact foreign amount with native FX

`account.move.js_settle_outstanding_line(line_id)` ignores a synthetic Odoo
foreign estimate and assigns the exact selected document residual to the
statement, marked `document_residual`.

For a bill carried as `€4.38 / $5.00` and a `€4.40` bank debit:

1. the statement becomes `€4.40 / $5.00`;
2. OCA prepares the exact payable counterpart on the bank move;
3. native reconciliation clears the payable and suspense;
4. Odoo records the legitimate `€0.02` carrying-value FX loss.

Date distance and reference-rate deviation are warnings, not Settle blockers.
Settle is hidden when Add already uses an authoritative exact foreign amount.

## Use payment rate: immediate-event valuation

`account.move.js_use_payment_rate_outstanding_line(line_id)` adds these stricter
conditions:

- transaction and document are within the effective date policy, default three
  calendar days, unless a trusted integration supplies the date;
- the bank/document executed rate is within the effective reference-rate
  policy, default 3%;
- the original economic lines are safe to adjust.

The OCA bank preparation is reused with exchange preparation disabled only
inside this internal service call. It creates:

- an exact foreign receivable/payable counterpart at the document carrying
  value;
- company-currency-only balancing lines on the existing bank move, allocated
  proportionally to the original eligible economic accounts.

The allocation copies analytic distributions only. It does not copy tax IDs,
tax tags, tax repartition data, tax bases, products, fee data, asset/deferred
metadata, or stock valuation metadata. Automatic stock valuation, fixed
assets, deferrals, mixed signs, reconcilable subledgers, and unsupported
complexity are blocked. Add and Settle remain available where their own gates
pass.

Postconditions are checked in the same transaction:

- imported liquidity is unchanged;
- the selected company and foreign residuals are zero;
- bank suspense is zero;
- original tax metadata is unchanged;
- no exchange move or exchange line exists;
- generated economic balances equal the bank-versus-carrying difference.

Any failed assertion rolls back the request.

## Audit and security

`account.immediate.settlement` records the exact/native-FX, `payment_rate`, or
preserved `legacy_adjustment` mechanism, including source amounts, discarded
estimate, carrying value, dates, rates, policy warning, provenance, generated
lines, reconciliation, native FX, economic allocation, user, and reversal
state.

`account.immediate.settlement.allocation` stores immutable account, amount,
proportion, analytic, and generated-line snapshots. After reversal the
generated line relation is cleared but its ID, name, account, amount, and
analytic snapshot remain.

Accounting users have read-only ACLs. Model create/write also require both
elevated server access and an in-process Python object token. A serialized RPC
context cannot reproduce object identity, so even a superuser RPC cannot
fabricate an audit record or generated line. Active generated lines and bank
moves cannot be directly edited, drafted, cancelled, deleted, or unlinked.

## Reversal and migration

Unreconciling a linked partial or choosing **Reverse Settlement** routes through
OCA's native `unreconcile_bank_line()` path. It removes reconciliation and
generated bank counterpart/economic lines, restores suspense and the document
residual, and restores the statement's original foreign currency, amount, and
source state. Audit snapshots remain immutable.

The `saas~19.2.1.2.0` migration preserves preview-era adjustment records as
`legacy_adjustment`, keeps used entries inspectable, and archives only unused
auto-created IMST journals. The `saas~19.2.1.3.0` migration backfills exact
settlement source facts, preview differences, and immutable allocation
snapshots. No new journal or historical settlement is created.

Deployment updates `usl_accounting` followed by
`rebuild_account_migration`.
