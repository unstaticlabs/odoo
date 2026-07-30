# Process content-platform payouts

## Before you start

A Platform Billing Administrator must configure the platform's partners,
products, journals, commission rate, currency and bank label rules.
Auto-posting is off by default. Supporting statements can be attached to each
payout. The normal Accountant role does not open this app unless the user also
receives a Platform Billing role.

## Create and check a session

1. Open **Platform Billing → Billing Sessions**.
2. Create a session for the accounting month.
3. Confirm the invoice date, optional due-date override and bank currency.
4. Add payouts manually, or choose **Import bank transactions** to regenerate
   candidates from native bank data.
5. Check every platform reference and net platform amount.
6. Choose **Check**. Correct any blocking message before continuing.

Pattern matches are considered before known partners and keywords. Outgoing,
reconciled and other-company transactions are not candidates. An unallocated
remainder of a pooled receipt may appear again. Ambiguous matches are not
selected automatically.

## Generate and post

1. Choose **Generate drafts**.
2. Open the Journal Entries smart button and review invoices, commission bills,
   taxes, accounts, analytic distribution, dates and attachments.
3. If everything is correct, choose **Post documents**.

If one or more active platforms have no payout in the session, Odoo shows a
warning. Go back to add them, or choose **Post Anyway** when the omission is
intentional.

If compensation is enabled, Odoo posts a balanced payable/receivable entry and
reconciles it with the commission bill and customer invoice. Posted documents
cannot be reset from this application.

## Reconcile the payout

1. For received payouts, select the correct incoming bank transaction and enter
   the amount allocated to that payout.
2. Choose **Reconcile bank**.
3. Leave delayed payouts without a bank transaction. The session stays
   **Posted** and its invoice remains an open customer debt.
4. When one later receipt covers several months, open each posted session and
   allocate its share of the same receipt. Reconcile after all shares equal the
   receipt total.
5. Review blocked receipt groups individually. Other valid groups remain
   processed.
6. The session becomes **Paid** only when all required documents are settled
   and every payout has a reconciled bank transaction.

For a foreign-currency platform, the bank amount remains unchanged in the bank
currency. Odoo/OCA uses the platform amount as foreign currency and creates any
exchange difference.

## Roles

- **Reader:** inspect records and evidence only.
- **Operator:** prepare, generate, post and reconcile sessions.
- **Administrator:** also configure platforms and delete eligible drafts.

All three roles are company-scoped and must be assigned explicitly.

This workflow accounts for creator/content-platform payouts. It is unrelated
to French electronic-invoice platform connectivity.
