---
title: Create and post a monthly platform billing session
type: how-to
description: Open the month, enter the payout each platform reported, run the checks, generate the invoices and bills and post them, even when a platform has not paid yet.
lang: en
persona: finance_operator
app: Platform billing
models:
  - usl.platform.billing.session
  - usl.platform.billing.payout
screen: /odoo/action-usl_platform_billing.action_platform_billing_sessions
journey: create-a-monthly-platform-billing-session
tour: usl_platform_billing_operator_creation_journey
source:
  - custom-addons/usl_platform_billing/tests/test_platform_billing_journeys.py
screenshot_01-sessions_png: 3b215c1a13142880706b704277e60012aee8511fbf0887c2b4f302d082a3514e
screenshot_02-period_png: 1de9e4486682bb9980debff62f2b9df5b78fb3eaf542eba70be1a91013e1beeb
screenshot_03-net_amount_png: 80f086a8e006ed9ad1afe16bdfe48e823908550876322e0db3dbcc5670a3fb3f
screenshot_04-coverage_png: ca0817b3e6dde6f24f5a9575540c63aa04a84232c3d749d3a3a11c5a9e3ab01c
screenshot_05-posted_png: 3c9d1cce3b29e91534ed78ce758bdae335362557bf72fa6703beebeb31dbc528
generated: true
---

<!-- Generated from the journey named in the front matter. Change the test, then run `make docs`. -->

# Create and post a monthly platform billing session

Open the month, enter the payout each platform reported, run the checks, generate the invoices and bills and post them, even when a platform has not paid yet.

1. Open **Platform Billing > Billing Sessions**. The list shows every month, closed ones included, so you can see what was done before.

   ![Open Platform Billing Billing Sessions](create-a-monthly-platform-billing-session/01-sessions.png)

2. Choose **New**.

3. Pick the **billing period**: the first day of the month you are closing. Odoo names the session after the month, in French as the history always has, and proposes the month-end as invoice date.

   ![Pick the billing period: the first day of the month you are closing](create-a-monthly-platform-billing-session/02-period.png)

4. Under **Payouts**, choose **Add a line** for each platform that reported a payout this month.

5. Pick the platform, the payout date and the platform's own reference, then enter the **net amount** the platform reported. The gross revenue and the commission are derived from the platform's rate.

   ![Pick the platform, the payout date and the platform's own reference, then enter the net amount the platform reported](create-a-monthly-platform-billing-session/03-net_amount.png)

6. Choose **Check**. The session is saved and the accounting checks run: platforms, journals, products and amounts must all be in place before anything is generated.

7. Choose **Generate drafts**: one customer invoice for the revenue and one bill for the commission, per payout, in draft.

8. Choose **Post documents**.

9. When an active platform has no payout this month, Odoo says which one before posting. This is the moment to add a forgotten line.

   ![When an active platform has no payout this month, Odoo says which one before posting](create-a-monthly-platform-billing-session/04-coverage.png)

10. Confirm when the gap is deliberate, for example a platform that pays quarterly.

11. The session is **Posted**. A payout whose money has not arrived stays open; the invoice is posted and waits for the bank.

   ![The session is Posted](create-a-monthly-platform-billing-session/05-posted.png)

12. **Reconcile bank** stays available: come back when the receipt shows on the bank statement.
