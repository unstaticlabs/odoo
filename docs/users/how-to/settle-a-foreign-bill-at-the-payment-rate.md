---
title: Settle a foreign-currency bill at the payment rate
type: how-to
description: Match a bill in a foreign currency to the company-currency debit the bank reports, value the bill at the rate the payment implies, and undo it if needed.
lang: en
persona: accountant
app: Accounting
models:
  - account.move
  - account.bank.statement.line
screen: /odoo/action-account.action_move_in_invoice_type
journey: settle-a-foreign-bill-at-the-payment-rate
tour: usl_immediate_settlement
source:
  - custom-addons/usl_accounting/tests/test_accounting_journeys.py
screenshot_01-actions_png: aeb4f0b9316c2ccf69f5cbbc1e3ee1b2f8b62726cbf02216e94cf9f47abdac6c
screenshot_02-trace_png: 77f6a2910c8187fc1e0b234e83f841f9a897e2bb5b11ee9798af40284eeb2979
screenshot_03-detail_png: f6407771a6ba6af61ecbbcb154edd7c7a4492d9dbe32b2a72c09e825fc1e3929
generated: true
---

<!-- Generated from the journey named in the front matter. Change the test, then run `make docs`. -->

# Settle a foreign-currency bill at the payment rate

Match a bill in a foreign currency to the company-currency debit the bank reports, value the bill at the rate the payment implies, and undo it if needed.

1. Open the posted bill from **Accounting > Vendors > Bills**. When a bank line of the same partner matches it within the allowed days and rate deviation, the outstanding box offers three actions: the native **Add**, **Settle immediately** and **Use payment rate**, the recommended one in colour.

   ![Open the posted bill from Accounting Vendors Bills](settle-a-foreign-bill-at-the-payment-rate/01-actions.png)

2. **Best match** names the bank line Odoo compared the bill with; open it when you want to see the other candidates.

3. Choose **Use payment rate**. The bill is revalued at the rate the bank payment implies, so no exchange difference is written.

4. The bill is paid and a single line records the settlement: the mechanism, and **no FX** when nothing was booked for the exchange.

   ![The bill is paid and a single line records the settlement: the mechanism, and no FX when nothing was booked for the exch](settle-a-foreign-bill-at-the-payment-rate/02-trace.png)

5. Choose the info icon on the payment line.

6. The detail keeps the facts: what Odoo had estimated and discarded, the original value of the document, its value at the payment rate, and the entries involved.

   ![The detail keeps the facts: what Odoo had estimated and discarded, the original value of the document, its value at the ](settle-a-foreign-bill-at-the-payment-rate/03-detail.png)

7. **Unreconcile** reverses the whole settlement in one step when the match was wrong.

8. The bill is open again at its original value and the three actions are offered once more.
