---
title: Group related expenses into a Batch
type: how-to
description: Select the expenses that belong together and add them to a proposed Batch so they share one business context.
lang: en
persona: employee
journey: group-expenses-into-a-batch
tour: usl_expense_batch_create_or_select
source:
  - custom-addons/usl_expense_batch/tests/test_expense_batch_journeys.py
screenshot_01-work_list_png: 268d0b2e5c18338ed4f12db6819df66107c9d86a2913d92ef14303b6b8bdee40
screenshot_02-preview_png: 0c16bec459c061412f94fe846b510d2b23cf459115e5c2a6b4defac1d6adfa3a
screenshot_03-grouped_png: 9e143e18e2cb0edff169db4db121321d1e6a3bca5c2673a6579f03e48921731b
generated: true
---

<!-- Generated from the journey named in the front matter. Change the test, then run `make docs`. -->

# Group related expenses into a Batch

Select the expenses that belong together and add them to a proposed Batch so they share one business context.

1. Open **Expenses > My Expenses**. Expenses that belong together, such as a hotel and a taxi from one trip, sit in the same work list.

   ![Open Expenses My Expenses](group-expenses-into-a-batch/01-work_list.png)

2. Tick the first expense of the trip.

3. Tick the other expenses of the same trip.

4. Choose **Add to a Batch**, the primary action once several expenses are selected.

5. Odoo proposes a compatible Batch from the employee, the company, the dates and the analytics. Keep it, or create a new one.

6. Check the preview: what the employee paid and what the company paid are totalled separately before anything changes.

   ![Check the preview: what the employee paid and what the company paid are totalled separately before anything changes](group-expenses-into-a-batch/02-preview.png)

7. Choose **Add to Batch**.

8. The work list no longer shows the grouped lines; they now live on the Batch, where you review and submit them together.

   ![The work list no longer shows the grouped lines; they now live on the Batch, where you review and submit them together](group-expenses-into-a-batch/03-grouped.png)
