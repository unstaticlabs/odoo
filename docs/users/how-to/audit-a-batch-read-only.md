---
title: Audit a Batch as a read-only accountant
type: how-to
description: Read everything about a Batch, its lines, its receipts and its accounting, without being able to change any of it.
lang: en
persona: accountant
app: Expenses
models:
  - usl.expense.batch
screen: /odoo/action-usl_expense_batch.action_expense_batches
journey: audit-a-batch-read-only
tour: usl_expense_batch_readonly_audit
source:
  - custom-addons/usl_expense_batch/tests/test_expense_batch_journeys.py
screenshot_01-evidence_png: fc72ba31ef738bf643807e927a4957ac83aa2cd1f873f29a0482c2424d3020d2
screenshot_02-reconciliation_png: 8abd535e7139a977fc9de49b12c6360e30ae6e044039a688eaf508de778b9935
generated: true
---

<!-- Generated from the journey named in the front matter. Change the test, then run `make docs`. -->

# Audit a Batch as a read-only accountant

Read everything about a Batch, its lines, its receipts and its accounting, without being able to change any of it.

1. Open **Expenses > Expense Batches** and choose the Batch. With read-only accounting access you see the summary, the lines and their receipts.

   ![Open Expenses Expense Batches and choose the Batch](audit-a-batch-read-only/01-evidence.png)

2. No lifecycle button and no line action is shown: nothing you do here can submit, approve, post or move an expense.

3. Open the **Accounting and history** tab.

4. The reconciliation state of the Batch and its history are visible and locked. Use them to answer an audit question or to prepare a remark for the accountant who can act.

   ![The reconciliation state of the Batch and its history are visible and locked](audit-a-batch-read-only/02-reconciliation.png)
