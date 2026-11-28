---
title: Review a Batch before submitting it
type: how-to
description: Check the batch totals, preview how the saved analytic context will apply, and review any exceptions before submitting.
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
screenshot_01-batch_png: 8cb0252447abce022327934eba2b9a1855e2622c0700956c944a59b540851172
screenshot_02-context_preview_png: 8f6c52a8814c7a224fd8149d17d2514cacc73e5a509dbb085edd7a0c009e3566
screenshot_03-exception_png: 5589c8bbf20ecb2913d4d415b8dbc824e8abd6839bfefe1210b52d97ff2778ab
generated: true
---

<!-- Generated from the journey named in the front matter. Change the test, then run `make docs`. -->

# Review a Batch before submitting it

Check the batch totals, preview how the saved analytic context will apply, and review any exceptions before submitting.

1. Open **Expenses > Expense Batches** and choose your Batch. The summary at the top totals what you paid and what the company paid; the **Expenses** tab lists the lines.

   ![Open Expenses Expense Batches and choose your Batch](review-a-batch-before-submitting/01-batch.png)

2. Review the saved analytic distribution under **Shared context**. Existing expenses keep their current values until you apply the context.

3. Choose **Apply context to … expenses** to open the preview.

4. Review which drafts will change. Expenses already submitted or approved keep their values, as do drafts with their own allocation unless an authorized reviewer selects them for replacement.

   ![Review which drafts will change](review-a-batch-before-submitting/02-context_preview.png)

5. Choose **Apply shared context** to update the eligible drafts.

6. A line that differs from the Batch is marked with a short helper saying what differs. Open the line to align it, or leave it when the difference is intended.

   ![A line that differs from the Batch is marked with a short helper saying what differs](review-a-batch-before-submitting/03-exception.png)

7. Nothing in the list deletes an expense.

8. Use **Remove from Batch** on a draft to return it to your expense list. You can add it to another batch.
