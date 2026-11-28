---
title: Link and reconcile content-platform payouts
type: how-to
description: Link posted platform payouts to incoming bank transactions. Reconcile late, partial and pooled receipts.
lang: en
persona: finance_operator
app: Platform billing
models:
  - usl.platform.billing.payout
  - usl.platform.billing.session
source:
  - custom-addons/usl_platform_billing
generated: false
---

# Link and reconcile content-platform payouts

Use the illustrated guides to [create and post the monthly
session](create-a-monthly-platform-billing-session.md) and [post and reconcile a
session with its bank receipt](settle-a-platform-billing-session.md).

## Associate a bank receipt with an existing payout

From the session, open **Import bank transactions** and choose **Link bank
transactions to registered payouts**. Select the payout and incoming bank
transaction, review the allocation, then choose **Link selected
transactions**. Once the session is posted, use **Reconcile bank** to settle
the linked receipt.

If you are starting from a bank receipt before entering the payout, use
**Create payouts from bank transactions**. Complete the platform, original
reference, currency and amount on the resulting payout.

**All open** includes eligible incoming transactions in the session currency
from allowed bank journals. **Suggested only** narrows the list by bank label,
known partner or keywords. Check the label, amount and date if the match is
ambiguous.

## Late, partial or pooled receipts

- For a late payout, leave its bank transaction empty. The session stays
  **Posted** and the invoice remains open.
- For an instalment, allocate the amount received. Link each later receipt
  when it arrives. The session becomes **Paid** when the required documents
  and receipts are settled.
- If one receipt covers several months, select the affected payouts together
  and allocate the receipt once. Automatic pooled reconciliation requires
  the same customer and invoice currency. For a mixed receipt, keep the
  reviewed allocations and finish in Odoo Accounting.

## Foreign currency and access

A payout created from a bank receipt can show an **Effective Bank Rate**.
That rate values these documents and leaves Odoo’s general rates in place.
A payout entered before the receipt uses the Odoo reference rate. A later
receipt can therefore generate a normal exchange gain or loss.

A Platform Billing Administrator configures platforms and bank rules.
Operators prepare and reconcile sessions. Readers consult history for their
companies. These roles are assigned separately from Accounting, and automatic
posting is off by default. Attach the platform statement to the payout when
it is available.
