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

The wizard opens on **All eligible**. This means every open incoming
transaction from an allowed bank journal is visible, even when its date,
amount or label is unusual. Matching is only a recommendation:

- configured label pattern first;
- known platform partner second;
- configured keywords third.

Use **Recommended only** when you want a shorter suggested list. Outgoing,
reconciled, unposted, fully allocated, wrong-company and disallowed-journal
transactions are excluded, and the summary explains the excluded counts.
Ambiguous or unknown rows remain manually selectable in **All eligible**.

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

1. Choose **Import bank transactions** on any Draft, Ready, Generated or
   Posted session.
2. Select the open payouts to settle. Payouts from other open sessions are
   shown too.
3. Select the received bank transactions and adjust amounts when the payment
   is partial.
4. Choose **Link selected transactions**. The selected payout total and the
   value represented by the selected bank transactions must match.
5. Choose **Reconcile bank**.
6. Leave delayed payouts without a bank transaction. The session stays
   **Posted** and its invoice remains an open customer debt.
7. When one later receipt covers several months, select all affected payouts
   in one wizard and allocate the pooled receipt once.
8. When one payout arrives in instalments, link and reconcile the first
   receipt, then repeat for the remaining open amount when it arrives.
9. Review blocked receipt groups individually. Other valid groups remain
   processed.
10. The session becomes **Paid** only when all required documents are settled
   and every payout has a reconciled bank transaction.

Automatic pooled reconciliation works when the selected invoices use the same
customer and invoice currency. In plain language, one bank receipt can
automatically close several invoices when the same platform owes all of them
in the same currency. If a receipt combines different platform customers or,
for example, EUR and USD invoices, the app records the reviewed allocations
but does not guess how to split customers or exchange rates. Finish that mixed
case in Odoo's standard Accounting reconciliation screen.

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
