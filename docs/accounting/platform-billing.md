# Platform billing accounting design

## Recognition model

For a payout with net `80` and commission rate `20%`:

- gross revenue is `80 / (1 - 20%) = 100`;
- the customer invoice recognizes `100` of revenue;
- the vendor bill recognizes `20` of platform commission;
- the optional compensation entry debits payable `20` and credits receivable
  `20`;
- the bill becomes fully reconciled and the invoice residual becomes `80`;
- the bank transaction settles that `80`, immediately or later.

Products, partner fiscal positions and standard Odoo computation determine
accounts and taxes. The platform form shows the current effective defaults so
they can be reviewed before generation. For the USL French chart, content
services use `706000` and platform sales commissions use `622200`; partner
receivable/payable accounts remain native Odoo configuration. The local QA
fixture reuses the journal proven by restored payout allocations (`Banque
Shine`, account `512001`) rather than choosing an arbitrary liquidity account.
Analytic distribution is copied to invoice/bill lines.
When the session has no explicit due date, partner payment terms determine
document maturities. A session due date is an intentional override.

Posting is independent from cash receipt. Until a payout is received, the
posted customer invoice residual remains an open receivable in standard
Accounting reconciliation views. A later platform payment may cover several
months: each payout stores its positive share of the same bank transaction and
the application submits all linked open receivable lines together. A payout
may also be paid in instalments; each received transaction stores the part it
settles, and the session remains Posted until the full debt is reconciled.

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

Each independent bank transaction is processed under a savepoint. All payouts
sharing one pooled transaction are atomic; another rejected transaction does
not roll back successful groups.

## Compensation

Compensation is allowed only with a general journal and valid partner
receivable/payable accounts. The entry is balanced in company currency and
retains signed `amount_currency` when the platform currency differs.

## Invariants

- Generated posted moves remain balanced.
- The source Accounting reconstruction owns the accounting values; historical
  platform restoration adds only application relations.
- A payout has at most one invoice, one commission bill and one compensation
  entry. It may have several bank allocations, while one transaction may be
  shared by several payouts.
- A generated document links its session, platform and all contributing
  payouts.
- A session is paid only when all required invoices/bills are settled and
  every payout has a reconciled bank transaction. Otherwise it remains posted.
