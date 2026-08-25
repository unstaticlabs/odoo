# Expense Batch accounting and reporting

An Expense Batch is a review and reporting context, not a replacement ledger
document. Native expense posting still determines taxes, payable or company-
paid settlement, moves and analytic lines.

The Product supplies the normal expense account and tax defaults. A draft
Batch may override the expense account when an Expense or Accounting Manager
deliberately configures it. Batch analytics override Product-derived analytics
but never a deliberate line exception. Approved and posted accounting values
are immutable from the Batch context service.

Mixed payment modes stay in one visible Batch. Employee-paid expenses may be
grouped into a reimbursement receipt; company-paid expenses keep the native
separate entry and bank-matching path. Open counts are tracked independently,
so completion of one payer side does not conceal work on the other.

`account.move.line.expense_batch_id` and
`account.analytic.line.expense_batch_id` are stored related dimensions, as is
the source expense payment mode. They support list, search and pivot grouping
without copying business state. The Batch reference and backlink are carried
on generated expense moves, while the native expense link preserves access to
each receipt.

The Batch reconciliation indicator is **pending** until every active expense
has a posted move. It then compares the sum of linked expense-entry debits with
the expense total in company currency. A difference is a review signal; it
does not modify or reconcile accounting automatically.
