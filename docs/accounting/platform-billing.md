# Platform billing accounting design

## Recognition model

For a payout with net `80` and commission rate `20%`:

- gross revenue is `80 / (1 - 20%) = 100`;
- the customer invoice recognizes `100` of revenue;
- the vendor bill recognizes `20` of platform commission;
- the optional compensation entry debits payable `20` and credits receivable
  `20`;
- the bill becomes fully reconciled and the invoice residual becomes `80`;
- the bank transaction settles that `80`.

Products, partner fiscal positions and standard Odoo computation determine
accounts and taxes. Analytic distribution is copied to invoice/bill lines.
Invoice and due dates drive the native payment-term behavior.

## Currency

Platform amounts use the configured platform currency. Bank receipts use the
session bank currency. The product never totals unrelated platform currencies.

When platform and bank currencies differ, the selected open bank statement
line keeps its actual bank-currency `amount`. Before reconciliation, the
application sets only the statement line's normal partner,
`foreign_currency_id` and `amount_currency` synchronization fields. It then
submits the receivable line through `account_reconcile_oca`. Odoo creates any
exchange difference. The application never:

- drafts a posted bank move;
- writes `debit`, `credit` or `balance` on a posted move line;
- disables move-validity checks;
- synthesizes reconciliation table rows directly.

Each payout is processed under an individual database savepoint. A rejected
transaction is marked blocked with an audit reason while successful payouts
remain committed with the session action.

## Compensation

Compensation is allowed only with a general journal and valid partner
receivable/payable accounts. The entry is balanced in company currency and
retains signed `amount_currency` when the platform currency differs.

## Invariants

- Generated posted moves remain balanced.
- The source Accounting reconstruction owns the accounting values; historical
  platform restoration adds only application relations.
- A payout has at most one invoice, one commission bill, one compensation
  entry and one bank transaction.
- A generated document links its session, platform and all contributing
  payouts.
- A session is paid only when all required invoices/bills and selected bank
  transactions are reconciled.
