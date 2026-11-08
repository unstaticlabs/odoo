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
screenshot_01-evidence_png: d7fbc5561dc77d4e90ce53029f19e1e8a65d6feb7cb675d813af2df391c3bbe1
screenshot_02-reconciliation_png: 467f2f2f95e964387c256fa9085e4ca14a547b54b42251a0f4e8d2b3e0911e50
generated: true
---

<!-- Generated from the journey named in the front matter. Change the test, then run `make docs`. -->

# Audit a Batch as a read-only accountant

Read everything about a Batch, its lines, its receipts and its accounting, without being able to change any of it.

1. Open **Expenses > Batches** and choose the Batch. With read-only accounting access you see the summary, the lines and their receipts exactly as the employee and the manager do.

   ![Open Expenses Batches and choose the Batch](audit-a-batch-read-only/01-evidence.png)

2. No lifecycle button and no line action is shown: nothing you do here can submit, approve, post or move an expense.

3. Open the **Accounting and history** tab.

4. The reconciliation state of the Batch and its history are visible and locked. Use them to answer an audit question or to prepare a remark for the accountant who can act.

   ![The reconciliation state of the Batch and its history are visible and locked](audit-a-batch-read-only/02-reconciliation.png)
