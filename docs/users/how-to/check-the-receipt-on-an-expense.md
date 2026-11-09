---
title: Check the receipt attached to an expense
type: how-to
description: Find the receipt behind an expense, open it, and confirm who paid before the expense goes into a Batch.
lang: en
persona: employee
app: Expenses
models:
  - hr.expense
screen: /odoo/action-hr_expense.hr_expense_actions_my_all
journey: check-the-receipt-on-an-expense
tour: usl_expense_batch_receipt_capture
source:
  - custom-addons/usl_expense_batch/tests/test_expense_batch_journeys.py
screenshot_01-upload_png: 268d0b2e5c18338ed4f12db6819df66107c9d86a2913d92ef14303b6b8bdee40
screenshot_02-receipt_png: bd596c941b5e842f19408c99d1c18df5004d7e84f6b47c6659ee9b71788ef5e8
generated: true
---

<!-- Generated from the journey named in the front matter. Change the test, then run `make docs`. -->

# Check the receipt attached to an expense

Find the receipt behind an expense, open it, and confirm who paid before the expense goes into a Batch.

1. Open **Expenses > My Expenses**. **Upload** adds a receipt and creates its expense in one go; expenses that already have a receipt are listed with it.

   ![Open Expenses My Expenses](check-the-receipt-on-an-expense/01-upload.png)

2. Open the expense you want to check.

3. The paper-clip counter in the chatter says how many files the expense carries. Choose it.

4. The receipt is listed under **Files**, below the expense. Open it and check that it is the right document and that it is readable.

   ![The receipt is listed under Files, below the expense](check-the-receipt-on-an-expense/02-receipt.png)

5. Check **Paid by**: your own money is reimbursed to you, a company card is settled by the company. Change it here if the receipt says otherwise.
