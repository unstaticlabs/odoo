# Content-platform payout billing

## Outcome

Accounting users can turn creator/content-platform payout statements and bank
receipts into complete native Accounting documents without manually rebuilding
gross revenue, platform commission and settlement relations.

The delivered application owns stable platform configurations, monthly
sessions and individual payouts. Native Odoo remains the authority for
partners, products, taxes, fiscal positions, accounts, payment terms,
currencies, journal entries, attachments and reconciliation.

## Scope

- A platform configuration is company-specific.
- A session defines its accounting month, invoice and due dates, and one bank
  currency.
- A payout snapshots the platform commission rate and calculates gross and
  commission amounts using the platform currency's rounding.
- One customer invoice is generated per platform/session. Commission bills are
  grouped per session or per payout.
- An optional balanced entry compensates platform payables against
  receivables. The vendor bill is fully reconciled and the customer invoice
  retains the payout net as its residual.
- Incoming bank transactions settle the remaining receivable through the
  pinned OCA reconciliation API.
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

## Controls

- Commission is strictly between 0% and 100%.
- Non-empty platform references are unique per company/platform.
- One bank transaction can belong to only one payout.
- Platform, session, payout, journals and linked records must share a company.
- Bank candidates exclude outgoing, reconciled, already-linked and
  cross-company transactions. Pattern recognition takes priority over partner
  recognition, then configured keywords.
- Auto-posting is configurable and disabled by default.
- Posted documents cannot be reset or removed through the application.
- Accounting reviewers have company-scoped read-only access; accounting users
  operate workflows; only managers configure platforms or delete eligible
  records.

## Boundary

This product accounts for payouts from content platforms. It is unrelated to
French electronic-invoice platform connectivity, PDP registration, invoice
reception or e-reporting. It performs no live platform/provider calls.
