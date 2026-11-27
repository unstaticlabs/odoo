---
title: From your first sign-in to your first posted expense batch
type: tutorial
description: Learn on an approved training trip. Post the part paid by the company and the part paid by the employee, then find both journal entries.
lang: en
persona: accountant
app: Expenses
models:
  - usl.expense.batch
  - hr.expense
  - account.move
screen: /odoo
journey: post-your-first-expense-batch
tour: usl_expense_first_posted_batch
source:
  - custom-addons/usl_expense_batch/tests/test_expense_batch_journeys.py
screenshot_01-home_png: 1f11de5e9de4d9e92445a6ab3250f044339135c1d8b247a2815f74b37330ee25
screenshot_02-approved_png: 720e698241bc9bb9c45ef8fac373ffcbb6b8d1f022e5ae4115b34fbbbeb5cb36
screenshot_03-post_png: b7385726b2d498bc9a8eb72604c2e8db3dc084d3dd841b2b32e84c8ea06f529b
screenshot_04-entries_png: 8eb6cc97f4794f2d5d47b39ed247c98b01b90c49f7f8a6ac3a718e4e7e4f4e72
generated: true
---

<!-- Generated from the journey named in the front matter. Change the test, then run `make docs`. -->

# From your first sign-in to your first posted expense batch

Learn on an approved training trip. Post the part paid by the company and the part paid by the employee, then find both journal entries.

1. Use a training workspace with an approved expense batch and permission to post expenses. Sign in with your usual company account. You arrive on **Home**.

   ![Use a training workspace with an approved expense batch and permission to post expenses](post-your-first-expense-batch/01-home.png)

2. Choose the application menu in the upper-left corner.

3. Open **Expenses**. You will work with an example that combines a hotel paid by the employee and a meal paid by the company.

4. Open **Expense Batches**. Find the approved training trip prepared for you.

5. Open **Training seminar**, or the equivalent example in your training workspace.

6. Read the summary before posting. Identify who paid for each expense and follow the amounts through the next screens.

   ![Read the summary before posting](post-your-first-expense-batch/02-approved.png)

7. Choose **Post Batch**. Odoo posts the company-paid expense and opens a separate confirmation for the amount the employee advanced.

   ![Choose Post Batch](post-your-first-expense-batch/03-post.png)

8. Check the journal and accounting date, then choose **Post Expenses** to record the employee-paid expense.

9. You have posted your first batch. Compare the two amounts shown with the summary. The company charge and the employee reimbursement each have their own entry.

   ![You have posted your first batch](post-your-first-expense-batch/04-entries.png)
