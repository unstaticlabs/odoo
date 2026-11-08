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
screenshot_01-actions_png: 430756c591104b2683ac59285aa45a6689dfbc3be8fe9e01fbee03cb5814a775
screenshot_02-trace_png: 33527bd84d55f761c26180fd07149f0ccbc47d26f3278dd309f2edd0a95eb59f
screenshot_03-detail_png: 151ea1c085afb785bbb2a507e5093f35a562a50058f9b3b7eb70e5c6cce40894
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
