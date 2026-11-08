---
title: Review a Batch before submitting it
type: how-to
description: Read the Batch summary, fix the shared analytics once, and deal with a line that differs before you submit.
lang: en
persona: employee
app: Expenses
models:
  - usl.expense.batch
  - hr.expense
screen: /odoo/action-usl_expense_batch.action_expense_batches
journey: review-a-batch-before-submitting
tour: usl_expense_batch_focused_review
source:
  - custom-addons/usl_expense_batch/tests/test_expense_batch_journeys.py
screenshot_01-batch_png: fbfd6aad0177afff8bd9443cd9ff01077a73f36b70b19649d5429f252f961cec
screenshot_02-exception_png: e4f7c3c7ab627f0d3e508572ac4b7605f05c94002b312136af65d19a393c0363
generated: true
---

<!-- Generated from the journey named in the front matter. Change the test, then run `make docs`. -->

# Review a Batch before submitting it

Read the Batch summary, fix the shared analytics once, and deal with a line that differs before you submit.

1. Open **Expenses > Expense Batches** and choose your Batch. The summary at the top totals what you paid and what the company paid; the **Expenses** tab lists the lines.

   ![Open Expenses Expense Batches and choose your Batch](review-a-batch-before-submitting/01-batch.png)

2. The analytic distribution of the Batch is shared by its lines. Change it here and every line that follows the Batch follows the change.

3. **Apply context** previews what would change before it changes anything; a line with its own explicit choice is left alone.

4. A line that differs from the Batch is marked with a short helper saying what differs. Open the line to align it, or leave it when the difference is intended.

   ![A line that differs from the Batch is marked with a short helper saying what differs](review-a-batch-before-submitting/02-exception.png)

5. Nothing in the list deletes an expense.

6. **Remove from Batch** is the only line action: the expense goes back to your work list unchanged, ready for another Batch.
