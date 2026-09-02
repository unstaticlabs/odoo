# Expense batches and contextual accounting

Status: shipped

End-user workflow: [Lots de dépenses](../users/guides/expense-batches.md)

## Product decision

USL uses `usl.expense.batch`, shown in French as **Lot de dépenses**, as the
optional context and review unit for related expenses. Native `hr.expense`
records remain the evidence, category, amount, tax, payer, analytic and
accounting authority.

The concepts have distinct jobs:

- the Expense Product says what was bought and supplies stable category,
  policy, tax and account defaults;
- the Batch says why expenses belong together and supplies shared business,
  analytic and, when deliberately configured, account context;
- the individual expense keeps the receipt and every deliberate exception.

A Batch is encouraged for trips, productions, events, projects and periodic
claims, but an isolated expense can use the native unbatched workflow. A Batch
is always limited to one employee and one company. It may contain both
employee-paid and company-paid expenses.

A Batch is a documentary grouping, not a second accounting workflow. It stays
open when its expenses are posted or paid and accepts later draft, approved or
posted expenses. Users close it only by archiving it. The Batch shows the
expenses' current processing progress separately from its open/archive state.

An SBFH travel is an Epic, not a new Trip record. For example,
`SBFH — Canada 2026` combines the native `Projet: SBFH prod` and
`Epic: Canada 2026` analytic accounts. Travel Batches may deliberately apply
`625600 Missions` while Products continue to distinguish transport, meals and
gifts.

## Alternatives considered

Native Odoo multi-record submission remains the execution engine, but it does
not persist a business grouping or shared context. Reintroducing the removed
expense sheet would add a second state machine and make optional grouping
heavier. The maintained OCA expense add-ons do not provide an Odoo 19
persistent contextual Batch. Extending the shipped lightweight Batch therefore
adds the missing context without replacing Products or native accounting.

Storing separate Activity and Epic foreign keys was also rejected. Native
analytic distributions already express multiple plans, drive analytic lines
and reporting, and let a line vary from the Batch. The Batch stores one native
`analytic_distribution` and presents it grouped by plan.

## Context and precedence

An open Batch can define:

- context type, purpose, intended date window and shared notes;
- a native analytic distribution;
- an optional shared expense-account override;
- the employee, company and visible reference.

Actual first and last expense dates remain separately computed. This makes an
out-of-window receipt visible without silently changing the intended trip
window.

Every expense records account and analytic provenance as Product/default,
Batch-inherited, explicit exception, inferred suggestion or legacy. The
effective precedence is:

1. an explicit expense-specific decision;
2. a configured Batch value;
3. Product/native defaults;
4. an unconfirmed inferred suggestion.

Initial assignment changes only missing or Product-derived draft values.
Explicit line values survive. Only effective values that differ from the Batch
appear as exceptions; equivalent values do not inflate the review count.
Approved and posted lines can retain or receive a Batch link for review, but
their accounting context is never rewritten.

Batch context remains editable while the Batch is open. Changing it increments
a tracked revision. Previously inherited draft lines become stale until a user
previews and applies the new revision. Approved and posted lines are skipped.
The preview counts changed, unchanged, exceptional and skipped lines. Expense
or Accounting Managers may deliberately select draft exceptions to replace;
ordinary submitters cannot. Reapplying the same revision is idempotent.

Before first inheritance, the line stores its Product/default baseline.
Removing a line restores that baseline only when its current value still
matches the last value applied by the Batch. A later explicit edit always
survives removal.

## Capture, grouping and review

The expense list keeps native Upload, New and single-expense submission.
Selecting related records exposes **Add to a Batch**; for a multi-selection it
is the primary grouping action while native actions remain available.

The create-or-select preview:

- ranks compatible open Batches using employee, company, overlapping dates
  and analytic affinity;
- warns before creating an overlapping or likely duplicate Batch;
- shows total, payer split, readiness and context impact;
- preserves explicit exceptions and skips later-stage accounting values;
- adds records without changing their native state, unless the user chooses
  the explicit create-and-submit action.

Duplicate evidence and an expense outside the intended Batch dates are
warnings, never automatic rejections. They do not block adding, submitting,
approving or posting. Duplicate detection combines the native
duplicate-candidate signal with matching receipt checksums. Missing receipts,
required fields, out-of-window dates, stale context and explicit exceptions
remain visible on the Batch.

The Batch form leads with purpose, one compact summary, payer split, readiness,
interactive shared analytics and the expense list. A narrow attention indicator
explains the exact line exception without a permanent status column. Product/nature summaries make
both “how much did the context cost?” and “what kinds of expenses made it up?”
answerable. Ledger reconciliation and the account override are progressively
disclosed to accounting roles.

## Native workflow and mixed payers

Submit, approve and post operate on only the actionable native subset and
never regress later lines. Their availability follows the expenses requiring
each action, not a Batch lifecycle state. Incomplete draft lines block
submission atomically.
A problematic draft, submitted or approved line can be removed and returned
for correction without rejecting the rest of the Batch.

Native Odoo may group compatible employee-paid expenses in one reimbursement
receipt and creates the required company-paid entries separately. The Batch
therefore has independent employee-paid and company-paid remaining counts. If
company-paid posting succeeds before the employee reimbursement posting wizard
is completed, the Batch stays visibly unfinished.

Generated entries carry the Batch reference and direct Batch link. Expense
lines retain native `expense_id` links and attachments. The navigation path is:

`Notes de frais entry → Lot de dépenses → expense → receipt`.

The Batch accounting control compares its active expense total with the debit
side of linked posted expense entries. A pending side is not presented as
reconciled.

## Reporting and service contract

Stored Batch and payer dimensions are available on journal items and analytic
lines. Expense, journal-item and analytic reporting retain Product, account,
employee, payer, period and native analytic-plan dimensions, so Batch and Epic
totals reconcile to the underlying ledger.

Normal Odoo services expose the same rules used by the UI:

- `hr.expense.get_expense_batch_candidates(expense_ids)`;
- `usl.expense.batch.add_expenses(expense_ids)`;
- `usl.expense.batch.preview_context_application(...)`;
- `usl.expense.batch.apply_context(..., expected_revision=...)`;
- `usl.expense.batch.get_review_summary()`.

They return structured changed, unchanged, exception and skipped results,
check access and company/employee boundaries, and reject stale revisions
before mutation. Future MCP or AI preparation must use these services rather
than reproducing precedence in browser code.

## Security and compatibility invariants

1. A Batch cannot cross employee or company boundaries.
2. Submitters manage their permitted draft expenses and Batch business or
   analytic context.
3. Only Expense or Accounting Managers set a ledger-account override or force
   replacement of an explicit exception.
4. Read-only accountants can inspect the Batch, entries, analytics and
   evidence but cannot mutate or advance it.
5. Approved, posted, paid, refused and historical accounting is never
   reclassified by context application.
6. Product tax, mileage, allowance and receipt-policy behavior remains native.
7. Refreshes and retries cannot duplicate distributions, links or entries.

## Historical transition

The reconstruction step creates `SBFH — Canada 2026` for eligible Canada
drafts, applies `625600 Missions`, `Projet: SBFH prod` and
`Epic: Canada 2026`, and maps only unambiguous descriptions to reusable
Transport/Accommodation, Foreign Meals or non-recoverable-VAT Gifts Products.
Ambiguous drafts stay on their original Product for review. Missing-receipt
lines remain incomplete, imported taxes from outside the company fiscal country
are removed before posting, and nothing is submitted. In the authoritative source
dump, this yields 19 inherited Canada drafts: 18 deterministic category mappings
and one unchanged Zen Kyoto Product classification for review.

The four proven trip Products (`AUS26`, `CA26`, `LPASUM26`, `BCN2602`) are
archived after the transition. Existing history keeps displaying them. The
step emits external evidence, verifies non-draft signatures and verifies that
a second run changes nothing; migration provenance does not enter the product
registry.
