---
title: Post an approved Batch
type: how-to
description: "Post a Batch whose expenses the manager approved: the company-paid side first, then the employee-paid side through Odoo's own posting wizard."
lang: en
persona: accountant
app: Expenses
models:
  - usl.expense.batch
  - hr.expense
  - account.move
screen: /odoo/action-usl_expense_batch.action_expense_batches
journey: post-an-approved-batch
tour: usl_expense_batch_accountant_handoff
source:
  - custom-addons/usl_expense_batch/tests/test_expense_batch_journeys.py
screenshot_01-approved_png: 80482bf56282e311293e6309c67053c5b67c6af1bb8fa20334d9f2d9db853261
screenshot_02-entries_png: b04ae3efce502505c341ba84ebf110eeef65a49100f620c0c94581fa757d6dcc
generated: true
---

<!-- Generated from the journey named in the front matter. Change the test, then run `make docs`. -->

# Post an approved Batch

Post a Batch whose expenses the manager approved: the company-paid side first, then the employee-paid side through Odoo's own posting wizard.

1. Open **Expenses > Expense Batches** and choose an approved Batch. **Post batch** is the primary action; the summary says how much is company-paid and how much is owed to the employee.

   ![Open Expenses Expense Batches and choose an approved Batch](post-an-approved-batch/01-approved.png)

2. Choose **Post batch**. The company-paid expenses are posted at once; for the employee-paid ones Odoo opens its posting wizard.

3. Confirm **Post Expenses** in the wizard. The journal and date are the ones Odoo proposes for expense reports.

4. Both journal entries are listed: one for the company-paid side, one for the reimbursement owed to the employee. Each links back to the Batch.

   ![Both journal entries are listed: one for the company-paid side, one for the reimbursement owed to the employee](post-an-approved-batch/02-entries.png)
