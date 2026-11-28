---
title: Generate, post and reconcile a platform billing session
type: how-to
description: "Take a prepared session from draft to paid: run the preflight, generate and post the documents, and reconcile the bank receipt already matched to the payout."
lang: en
persona: accountant
app: Platform billing
models:
  - usl.platform.billing.session
  - account.move
screen: /odoo/action-usl_platform_billing.action_platform_billing_sessions
journey: settle-a-platform-billing-session
tour: usl_platform_billing_manager_journey
source:
  - custom-addons/usl_platform_billing/tests/test_platform_billing_journeys.py
screenshot_01-draft_png: f6da54ec4ab44d7a91c536b90c7ee25802082ade646351145298fe342f503818
screenshot_02-paid_png: a099d2f3d9704a8e14a3eda6f32364b4ba2fa5a238e01eafc5a8c2c87b1bd3ab
screenshot_03-moves_png: 9becbf2e0fc6e1cf653d29b64f12a8c5fe95d72499f9777417f20be51409061c
generated: true
---

<!-- Generated from the journey named in the front matter. Change the test, then run `make docs`. -->

# Generate, post and reconcile a platform billing session

Take a prepared session from draft to paid: run the preflight, generate and post the documents, and reconcile the bank receipt already matched to the payout.

1. Open **Platform Billing > Billing Sessions** and choose a session in **Draft** whose payouts are entered and whose bank receipt was matched.

   ![Open Platform Billing Billing Sessions and choose a session in Draft whose payouts are entered and whose bank receipt wa](settle-a-platform-billing-session/01-draft.png)

2. Choose **Check** to run the accounting preflight.

3. Choose **Generate drafts**: the customer invoices and the commission bills are created in draft.

4. Choose **Post documents**. The documents are posted and the commission is compensated against the revenue, so only the net amount is expected from the bank.

5. Choose **Reconcile bank**. The receipt matched to the payout settles the net amount through the bank reconciliation.

6. The session is **Paid**: the invoice, the bill and the bank line agree.

   ![The session is Paid: the invoice, the bill and the bank line agree](settle-a-platform-billing-session/02-paid.png)

7. Choose **Journal Entries** to see what was posted.

8. Every document is a native Odoo journal entry, open to the usual accounting views and reports.

   ![Every document is a native Odoo journal entry, open to the usual accounting views and reports](settle-a-platform-billing-session/03-moves.png)

9. The compensation entries read **No Payment Required**: they move the commission, they do not expect money.
