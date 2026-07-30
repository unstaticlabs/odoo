# Process content-platform payouts

## Before you start

An Accounting manager must configure the platform's partners, products,
journals, commission rate, currency and bank label rules. Auto-posting is off
by default. Supporting statements can be attached to each payout.

## Create and check a session

1. Open **Platform Billing → Billing Sessions**.
2. Create a session for the accounting month.
3. Confirm the invoice date, due date and bank currency.
4. Add payouts manually, or choose **Import bank transactions** to regenerate
   candidates from native bank data.
5. Check every platform reference and net platform amount.
6. Choose **Check**. Correct any blocking message before continuing.

Pattern matches are considered before known partners and keywords. Outgoing,
reconciled, already-linked and other-company transactions are not candidates.
Ambiguous matches are not selected automatically.

## Generate and post

1. Choose **Generate drafts**.
2. Open the Journal Entries smart button and review invoices, commission bills,
   taxes, accounts, analytic distribution, dates and attachments.
3. If everything is correct, choose **Post documents**.

If compensation is enabled, Odoo posts a balanced payable/receivable entry and
reconciles it with the commission bill and customer invoice. Posted documents
cannot be reset from this application.

## Reconcile the payout

1. Ensure every payout has the correct incoming bank transaction and actual
   bank amount.
2. Choose **Reconcile bank**.
3. Review any blocked payout individually. Other valid payouts remain
   processed.
4. The session becomes **Paid** only when all required documents and selected
   bank transactions are settled.

For a foreign-currency platform, the bank amount remains unchanged in the bank
currency. Odoo/OCA uses the platform amount as foreign currency and creates any
exchange difference.

## Reviewer access

Accounting reviewers can inspect platforms, sessions, payouts, evidence,
generated documents and bank links for their allowed companies. They cannot
configure, generate, post, reconcile or delete records.

This workflow accounts for creator/content-platform payouts. It is unrelated
to French electronic-invoice platform connectivity.
