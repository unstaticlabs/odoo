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
| **Use payment rate** | Exact full document residual | Reprice the document through draft/post; no FX |

Add is not renamed, removed, or intercepted. Facturations Plateformes remains
out of scope.

## Design decision

Three credible mechanisms were evaluated:

1. reset the document to draft, apply its bank-derived rate, and repost it;
2. create a dedicated adjustment journal and move;
3. add balancing economic-account lines to the editable OCA bank move.

The first mechanism is used for **Use payment rate**, but only when Odoo itself
allows the native reset/repost workflow and the document is simple enough to
reprice safely. It expresses the chosen accounting policy directly on the
source document and avoids technical bank or adjustment lines. The operation
does not edit a posted journal item in place: it uses `button_draft()`, changes
`invoice_currency_rate`, and calls `_post(soft=False)` as the current user.

The imported liquidity line and global currency rates remain unchanged. No
IMST journal and no payment-rate adjustment line are created. Tax-bearing or
legally protected documents are not eligible.

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
- no conflicting currency fact, combined fee, withholding, or lock;
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

- the complete document is still unpaid and none of its payment terms has ever
  been reconciled;
- transaction and document are within the effective date policy, default three
  calendar days, unless a trusted integration supplies the date;
- the bank/document executed rate is within the effective reference-rate
  policy, default 3%;
- the document has no tax, fee, withholding, stock valuation, fixed-asset,
  deferred-accounting, mixed-sign, or unsupported subledger complexity;
- the current user can reset and post the document;
- Odoo's native draftability checks, lock dates, secure hash, restrictive audit
  trail, cancellation requirement, sent state, and active EDI state all allow
  the reset.

The service then performs one atomic transaction:

1. lock and revalidate the document, bank statement, bank move, source line,
   and all selected payment terms;
2. snapshot the original rate, company value, identity, accounts, analytics,
   foreign amounts, and complete journal-line structure;
3. call native `button_draft()` as the current user;
4. set `invoice_currency_rate` to exact foreign total divided by actual bank
   amount;
5. repost with `_post(soft=False)`;
6. verify the identity and structure are unchanged and the document now carries
   the exact bank company-currency amount;
7. mark the statement foreign amount as document-derived and run the same exact
   OCA reconciliation path used by Settle.

For `$5.00 / €4.40`, the bill is reposted as:

```text
Dr Expense                         €4.40
Cr Payable              $5.00 /    €4.40
```

The bank move contains only the imported liquidity line and OCA's normal exact
payable/receivable counterpart. Its liquidity remains exactly `€4.40`.

Postconditions are checked in the same transaction:

- imported liquidity is unchanged;
- the selected company and foreign residuals are zero;
- bank suspense is zero;
- document identity, dates, accounts, analytics, foreign values, and structure
  are coherent with the snapshots;
- no exchange move or exchange line exists;
- no payment-rate economic or technical adjustment line exists;
- the repriced document company value equals the actual bank amount.

Any failed assertion rolls back the request.

## Audit and security

`account.immediate.settlement` records the exact/native-FX, `payment_rate`, or
preserved `legacy_adjustment` mechanism, including source amounts, discarded
estimate, carrying value, dates, rates, policy warning, provenance, generated
lines, reconciliation, native FX, original/applied invoice rates,
original/repriced document values, full before/after line snapshots, user, and
reversal state. New payment-rate records are marked `document_reprice`;
preview-era bank allocations are marked `legacy_bank_adjustment`.

`account.immediate.settlement.allocation` remains only for historical
payment-rate bank adjustments. New document repricings create no allocation
records.

Accounting users have read-only ACLs. Model create/write also require both
elevated server access and an in-process Python object token. A serialized RPC
context cannot reproduce object identity, so even a superuser RPC cannot
fabricate an audit record or generated line. While a document-reprice
settlement is active, its document and journal items cannot be directly edited,
drafted, cancelled, or deleted; users must reverse the settlement.

## Reversal and migration

Unreconciling a linked partial or choosing **Reverse Settlement** routes through
OCA's native `unreconcile_bank_line()` path. It removes reconciliation and
generated bank counterpart lines, restores suspense and the statement's
original foreign currency, amount, and source state, then resets the document
to draft, restores its original `invoice_currency_rate`, and reposts it.
Snapshot verification confirms the original company and foreign balances were
restored. A lock applied after settlement blocks reversal through normal Odoo
controls. Audit snapshots remain immutable.

The `saas~19.2.1.2.0` migration preserves preview-era adjustment records as
`legacy_adjustment`, keeps used entries inspectable, and archives only unused
auto-created IMST journals. The `saas~19.2.1.3.0` migration backfills exact
settlement source facts, preview differences, and immutable allocation
snapshots. The `saas~19.2.1.4.0` migration marks existing `payment_rate`
records as `legacy_bank_adjustment`; it does not alter their historical
entries. No new journal or historical settlement is created.

Deployment updates `usl_accounting` followed by
`rebuild_account_migration`.

The complete runtime feature is owned by `usl_accounting`: models, audit
records, policy settings, views, security, payment-widget assets and focused
tests. `rebuild_account_migration` depends on that foundation but does not own
or duplicate the settlement behavior.
