# Process an Expense

1. Open the **Expenses** app, then **My Expenses > Expenses to Process**. The removable **Needs action** filter initially shows drafts and approved expenses that still need work.
2. Select **Upload** to create an expense from a receipt, or **New** to enter one manually.
3. Check the employee, description, date, category, paid-by method, currency and analytic distribution.
4. Use the compact **Receipt** status (**Attached**, **Missing** or **Not
   required**) to find missing evidence. Open an expense to see its contextual
   next-step guidance; a required missing receipt blocks
   submission, approval and posting.
5. Select **Submit to Manager**, then **Approve**. An expense manager approving their own expense still performs both explicit steps.
6. Select **Post Expense**. Odoo creates and opens the native posted journal entry.
7. If the employee paid, select **Pay** on the journal entry or **Record Reimbursement** on the expense. If the company paid, keep the company-payment link.
8. Match the related bank transaction, then inspect the journal entry and analytical report impact.

Use the **Missing receipt** filter to prepare incomplete drafts. A category such as a configured fixed allowance can explicitly say **Receipt not required**; this is a category policy, not an exception hidden in the workflow.

## Group related expenses

For a trip, mission, project or coherent period, use a lightweight expense
batch instead of submitting each expense separately. The main expense list
does not show a permanent readiness column: use **Ready to submit** or
**Needs information**, then review readiness and missing details in the batch
creation preview.

Select explicit, related Draft, Approved or Posted expenses and choose
**Create expense batch**. This is the only list action; there is no automatic
**Submit ready expenses** shortcut. Creating the batch alone does not submit,
post or pay anything. **Submit batch** advances only its Draft expenses.

See [Expense Batches](../guides/expense-batches.md) for the employee, manager
and accounting workflow.
