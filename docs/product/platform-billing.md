# Content-platform payout billing

## Outcome

Authorized Platform Billing users can turn creator/content-platform payout
statements and bank receipts into complete native Accounting documents without
manually rebuilding gross revenue, platform commission and settlement
relations.

The delivered application owns stable platform configurations, monthly
sessions and individual payouts. Native Odoo remains the authority for
partners, products, taxes, fiscal positions, accounts, payment terms,
currencies, journal entries, attachments and reconciliation.

## Scope

- A platform configuration is company-specific.
- A session defines its accounting month, invoice date, optional due-date
  override and one bank currency.
- A payout snapshots the platform commission rate and calculates gross and
  commission amounts using the platform currency's rounding.
- One customer invoice is generated per platform/session. Commission bills are
  grouped per session or per payout.
- An optional balanced entry per payout compensates platform payables against
  receivables. Keeping the entries separate preserves each payout's effective
  bank rate. The vendor bill is fully reconciled and the customer invoice
  retains the exact payout net as its residual.
- Posting does not require a bank receipt. Delayed payouts remain open customer
  receivables in native Accounting.
- Incoming bank transactions settle the remaining receivable through the
  pinned OCA reconciliation API. One pooled receipt may be allocated across
  several payouts and sessions.
- Multiple platform currencies may coexist in a session. They are never
  arithmetically combined; summaries remain grouped by platform and currency.

Canonical workflow states are `draft`, `ready`, `generated`, `posted`, `paid`
and `cancelled`. Generated, posted and paid truth is derived from linked native
documents and reconciliation, not a legacy text flag.

## Design choice

Three approaches were assessed:

1. A direct port of the Studio server action was rejected. It rewrote move-line
   balances and drafted posted bank moves, bypassing normal accounting
   invariants.
2. Native Odoo plus generic OCA modules alone was retained as the accounting
   engine but rejected as the complete operator solution. It does not model the
   payout reference, commission snapshot, grouped document relationships,
   evidence and session workflow required here.
3. The selected design is a thin product application over standard Odoo
   documents and pinned OCA reconciliation. It owns only the platform-specific
   orchestration and audit trail.

Foreign-currency bank-created payouts considered three valuation mechanisms:
keeping Odoo's reference rate and accepting an immediate FX entry, adding a
separate adjustment move, or applying the transaction-derived rate to draft
documents. The selected draft-document rate follows the distribution's **Use
payment rate** policy without resetting posted entries or creating adjustment
lines. It is used only when the creating bank transaction is already in company
currency. Payouts recorded before receipt keep Odoo's reference rate and normal
delayed-settlement FX.

## Controls

- Commission is strictly between 0% and 100%.
- Non-empty platform references are unique per company/platform.
- A payout may have several bank allocations, and a bank transaction may serve
  several payouts. A newly imported draft temporarily keeps a zero platform
  amount until the operator enters the original payout amount; completed
  allocations are positive and cannot exceed the payout or transaction.
- Platform, session, payout, journals and linked records must share a company.
- **All open** shows every positive, posted, unreconciled transaction in the
  session bank currency from an allowed company bank journal, including
  distant dates, unusual amounts, unknown labels and unallocated remainders.
  Recognition does not hide these rows. It ranks configured label patterns
  first, known partners second and keywords third. **Suggested only** is an
  optional narrower view.
- Bank import is deliberately selection-only. Imported receipts become draft
  payout rows, where the operator reviews platform, original reference,
  currency and original payout amount before running Check.
- A company-currency bank receipt that creates a foreign payout values its
  draft invoice, commission bill and compensation at the effective bank rate.
  The bank amount and global currency-rate table remain unchanged.
- Matching or unmatching a transaction on a certified statement may change
  reconciliation metadata and counterpart lines. It cannot change the
  certified source amount, date, account, statement identity or liquidity
  balance.
- The maintenance repair for older pooled compensation reverses the former
  entry and any resulting exchange differences through Odoo's accounting
  trail. It never deletes posted accounting history.
- Posting warns when the monthly session has no payout for an active platform.
  An operator may confirm the documented exception.
- Auto-posting is configurable and disabled by default.
- Posted documents cannot be reset or removed through the application.
- Reader, Operator and Administrator are opt-in, company-scoped roles. The
  generic Accountant role has no application access. Only Administrators
  configure platforms or delete eligible records.

## Boundary

This product accounts for payouts from content platforms. It is unrelated to
French electronic-invoice platform connectivity, PDP registration, invoice
reception or e-reporting. It performs no live platform/provider calls.
