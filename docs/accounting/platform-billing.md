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
Genuine 0% commission is supported: gross equals net and no commission bill or
compensation is created. The same rule applies when a nonzero rate rounds the
commission to zero in the platform currency. Monthly commission bills include
only payouts with nonzero commissions. An explicit zero snapshot is retained
when prefilling a session; it is not replaced by the platform's current rate.

Before checking or generating a session, set the country on each effective
customer/supplier partner (or its commercial entity) and review its native
fiscal position. An explicit fiscal position alone does not replace missing
location evidence. The application never infers tax jurisdiction from USD/EUR
or a platform name and never assigns a blanket VAT exemption.
When the session has no explicit due date, partner payment terms determine
document maturities. A session due date is an intentional override.

Posting is independent from cash receipt. Until a payout is received, the
posted customer invoice residual remains an open receivable in standard
Accounting reconciliation views. A later platform payment may cover several
months: each payout stores its positive share of the same bank transaction and
the application submits all linked open receivable lines together. A payout
may also be paid in instalments; each received transaction stores the part it
settles, and the session remains Posted until the full debt is reconciled.

Native Accounting settlement is authoritative for the payout and session Paid
states. A posted or reversed invoice and bill with no currency-rounded residual
settle their payout, including when users registered or reconciled the payment
outside Platform Billing. Platform Billing bank allocations remain optional
matching evidence and operational guidance; they do not override the native
Accounting result. Any linked compensation entry must be posted and its
receivable and payable lines fully reconciled. Reopening native Accounting
settlement returns the affected payout and session to Posted automatically.

## Currency

Platform amounts use the configured platform currency. Bank receipts use the
session bank currency. The product never totals unrelated platform currencies.

When an incoming company-currency bank transaction creates a foreign-currency
payout, that transaction is the valuation source. The application divides the
actual bank amount by the platform net to derive company currency per platform
currency. It applies the inverse in Odoo's `invoice_currency_rate` convention
while the generated invoice and commission bill are still drafts. The
compensation entry uses the same rate.

For USD 1,000 received as EUR 700 at 20% commission:

- platform net: USD 1,000 / EUR 700;
- effective rate: `1 USD = 0.70 EUR`;
- gross invoice: USD 1,250 / EUR 875;
- commission bill and compensation: USD 250 / EUR 175;
- open receivable after compensation: USD 1,000 / EUR 700.

The actual EUR 700 liquidity remains unchanged and reconciliation creates no
exchange move. The effective rate is local to the generated documents; it does
not update global currency rates. Bank-rate payouts are separated from
reference-rate payouts, and different effective rates use separate documents.

A payout recorded without a bank transaction uses Odoo's reference rate. If
payment arrives later, Odoo may create the normal delayed-settlement exchange
gain or loss. A bank transaction in a non-company-currency journal also keeps
the reference-rate policy because it does not directly provide the company
currency valuation required by this treatment.

In particular, USD received into a USD bank account in an EUR company uses
**Odoo Reference Rate**. USD received into an EUR bank account can use
**Effective Bank Rate**. Bank import chooses based on receipt versus company
currency, not a bank's or platform's name.

Native recursive reconciliation may carry a previously chosen FX account into
an adjustment of the opposite sign. The compatibility layer corrects only the
configured gain/loss pair on the new exchange entry: positive company-currency
balance selects loss (debit), negative selects gain (credit). It preserves
amounts, custom non-FX mappings and reconciliation links. The account-direction
guard remains enabled; posted historical entries are not repaired by this change.

Existing zero-value draft bills can still be removed with **Reset Drafts** on
their generated session. This clears the governed links before deleting the
draft documents. Review the whole session first: this resets all its generated
drafts, not just zero bills. Posted documents require native correction/reversal;
do not clear workflow fields or delete payout references manually.

Before reconciliation, the application sets only the statement line's normal
partner, `foreign_currency_id` and `amount_currency` synchronization fields. It
then submits the receivable line through `account_reconcile_oca`. The
application never:

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
- A payout is paid when its required invoice and bill are settled and any
  linked compensation entry is posted and reconciled. A session is paid when
  every non-cancelled payout is paid. Bank allocations are supporting evidence,
  not a prerequisite for either state.
