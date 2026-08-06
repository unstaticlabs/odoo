# Process an Expense

1. Open the **Expenses** app, then **Expenses to Process**. The removable **Needs action** filter initially shows drafts and approved expenses that still need work. Select the **Expenses** app title whenever you want to return to **My Expenses**.
2. Select **Upload** to create an expense from a receipt, or **New** to enter one manually.
3. Check the employee, description, date, category, paid-by method, currency and analytic distribution.
4. Use the compact **Receipt** status (**Attached**, **Missing** or **Not
   required**) to find missing evidence. Open an expense to see its contextual
   next-step guidance; a required missing receipt blocks
   submission, approval and posting.
5. If the company may already have paid the expense, select **Find bank
   transactions** before posting. Review the amount, date, journal, label,
   partner and the plain-language facts shown for each suggestion.
6. For an exact amount, select **Use**, read the confirmation and choose **Use
   and reconcile**. Odoo submits, approves and posts the native company-paid
   expense when your permissions and its validations allow it, then matches
   the resulting payment to that bank transaction.
7. If an amount is merely close, correct the expense or investigate the bank
   item in **Bank Matching**; a close amount cannot be applied automatically.
8. If no company transaction applies, select **Submit to Manager**, then
   **Approve** and **Post Expense** normally. For an employee-paid expense,
   select **Pay** on the journal entry or **Record Reimbursement** on the
   expense.
9. Inspect the posted entry, accepted company-payment evidence and analytical
   report impact.

The bank partner is applied as the vendor only when the selected transaction
has one, and the confirmation names the change first. Native duplicate review,
analytic requirements, lock dates and permissions can stop the one-click
flow. When that happens, no payment mode, vendor, payment or reconciliation is
left half-applied.

A scoped read-only accountant can inspect suggestions and accepted history but
cannot refresh or use them. Ordinary employees cannot see bank-match evidence.

Use the **Missing receipt** filter to prepare incomplete drafts. A category such as a configured fixed allowance can explicitly say **Receipt not required**; this is a category policy, not an exception hidden in the workflow.

## Group related expenses

For a trip, mission, project or coherent period, use a lightweight expense
batch instead of submitting each expense separately. The main expense list
does not show a permanent readiness column: use **Ready to submit** or
**Needs information**, then review readiness and missing details in the batch
creation preview.

Select explicit, related Draft, Approved or Posted expenses and choose
**Add to a Batch** (**Ajouter à un lot de dépenses** in French). The preview proposes compatible existing Batches before a
new one, shows the mixed-payer split and explains which draft values inherit
shared analytics or accounting context. Explicit line exceptions are
preserved. Adding alone does not submit, post or pay anything; the explicit
create-and-submit choice advances only ready Draft expenses.

See [Expense Batches](../guides/expense-batches.md) for the employee, manager
and accounting workflow.
