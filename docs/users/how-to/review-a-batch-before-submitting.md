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
screenshot_01-batch_png: 8b65bfd471a7f21246385bb398df33bd9b1af5caf75009333175f5e4ea6117f7
screenshot_02-exception_png: 8b65bfd471a7f21246385bb398df33bd9b1af5caf75009333175f5e4ea6117f7
generated: true
---

<!-- Generated from the journey named in the front matter. Change the test, then run `make docs`. -->

# Review a Batch before submitting it

Read the Batch summary, fix the shared analytics once, and deal with a line that differs before you submit.

1. Open **Expenses > Batches** and choose your Batch. The summary at the top totals what you paid and what the company paid; the **Expenses** tab lists the lines.

   ![Open Expenses Batches and choose your Batch](review-a-batch-before-submitting/01-batch.png)

2. The analytic distribution of the Batch is shared by its lines. Change it here and every line that follows the Batch follows the change.

3. **Apply context** previews what would change before it changes anything; a line with its own explicit choice is left alone.

4. A line that differs from the Batch is marked with a short helper saying what differs. Open the line to align it, or leave it when the difference is intended.

   ![A line that differs from the Batch is marked with a short helper saying what differs](review-a-batch-before-submitting/02-exception.png)

5. Nothing in the list deletes an expense.

6. **Remove from Batch** is the only line action: the expense goes back to your work list unchanged, ready for another Batch.
