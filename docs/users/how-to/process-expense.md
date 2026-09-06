# Process an Expense

1. Open the **Expenses** app, then **Expenses to Process**. The removable **Needs action** filter initially shows drafts and approved expenses that still need work. Select the **Expenses** app title whenever you want to return to **My Expenses**.
2. Select **Upload** to create an expense from a receipt, or **New** to enter one manually.
3. If you work for several companies, first highlight the company that incurred
   the expense in the company selector. Odoo uses your employee profile for
   that company automatically. Then check the employee, description, date,
   category, paid-by method, currency and analytic distribution.
4. Use the compact **Receipt** status (**Attached**, **Missing** or **Not
   required**) to find missing evidence. Open an expense to see its contextual
   next-step guidance; a required missing receipt blocks
   submission, approval and posting.
5. When an emailed receipt contains a PDF link instead of an attachment, open
   the expense. For an unfamiliar email format, choose **Choose receipt link**
   and select the sanitized option that describes the receipt. Your choice
   teaches the instance-wide matcher; later confident matches download in the
   background. The signed link is not exposed or saved elsewhere.
6. If the company may already have paid the expense, select **Find bank
   transactions** before posting. Review the amount, date, journal, label,
   partner and the plain-language facts shown for each suggestion.
7. For an exact amount, select **Use**, read the confirmation and choose **Use
   and reconcile**. Odoo submits, approves and posts the native company-paid
   expense when your permissions and its validations allow it, then matches
   the resulting payment to that bank transaction.
8. If an amount is merely close, correct the expense or investigate the bank
   item in **Bank Matching**; a close amount cannot be applied automatically.
9. If no company transaction applies, select **Submit to Manager**, then
   **Approve** and **Post Expense** normally. For an employee-paid expense,
   select **Pay** on the journal entry or **Record Reimbursement** on the
   expense.
10. Inspect the posted entry, accepted company-payment evidence and analytical
   report impact.

The bank partner is applied as the vendor only when the selected transaction
has one, and the confirmation names the change first. Native duplicate review,
analytic requirements, lock dates and permissions can stop the one-click
flow. When that happens, no payment mode, vendor, payment or reconciliation is
left half-applied.

A scoped read-only accountant can inspect suggestions and accepted history but
cannot refresh or use them. Ordinary employees cannot see bank-match evidence.

Linked receipt download may complete in the background. If it needs attention,
the expense keeps the original email and offers **Retry**, **Teach another
link**, **Ignore**, and the normal receipt attachment action. Attach the PDF
manually whenever the choices are ambiguous. If the provider requires login,
select **Open receipt website**, review the displayed host, and continue in the
new browser tab. Sign in with the provider, download the PDF, return to the
expense, and select **Attach downloaded receipt**. Odoo never receives or
stores your provider password, verification code, cookies, or browser session.
The manual receipt immediately supersedes pending downloads.

Use the **Missing receipt** filter to prepare incomplete drafts. A category such as a configured fixed allowance can explicitly say **Receipt not required**; this is a category policy, not an exception hidden in the workflow.

If the active company has no employee profile, ask an administrator to open
your user under **Settings > Users & Companies > Users**, enable **Expenses in
all allowed companies**, and select **Refresh expense access**. Do not change an
existing expense to another company: create it in the correct active company.

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
