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

## Correct draft ownership

`hr.expense.configure_draft_expense(values)` prepares one existing draft in one
transaction. The MCP companion exposes it through `expenses_update_draft`.
Select both authorized companies and supply an employee belonging to the target
company. Replace incompatible category/account references or clear them with
`false`; clear taxes with `[]` and analytics with `{}`. An incompatible batch or
supplier bill/vendor link also needs an explicit replacement or clear.

The correction keeps the expense, receipt bytes and history. It moves owned
attachment company metadata, supersedes pending receipt work from the previous
ownership, preserves the document currency and amount, and recalculates the
company amount using the target company's native dated rate. The final expense
is returned by the same transaction. Native batch removal and its context
restoration participate in that transaction, so a later validation failure
leaves the original batch and evidence intact.

The reply keeps `result` and `expense` and adds `financial_comparison.before`
and `financial_comparison.after`. Both snapshots come from the same locked
transaction: before includes the original batch context, and after includes
native removal/restoration, explicit clears and recomputation. Relation values
use native read conventions (an ID/name pair or `false`; taxes are ID lists).
`total_amount_currency`, `untaxed_amount_currency` and `tax_amount_currency`
use `currency_id`. `total_amount`, `untaxed_amount` and `tax_amount` use
`company_currency_id`, including for foreign-currency expenses. The company
association is explicit because native `untaxed_amount` field metadata names
the document currency even though its computation uses company currency. No
companion read or tax calculation constructs this receipt.

Submitted, approved, posted, paid or already-accounted expenses are refused.
Ordinary label edits keep their normal access requirements, including when an
unchanged supplier bill exists. Split-family ownership needs a coordinated
family correction; this action does not silently move sibling expenses. Other
draft fields on split parts remain available. No expense is submitted, posted
or paid by this action.

## Alternatives considered

### Draft splits and linked ownership corrections

For genuinely different expense natures, accounts or analytics, use the native
Split Expenses wizard or the `expenses_split_draft` MCP shortcut. Supply two to
fifty gross parts whose total matches the source in its document currency. A
service part uses its own native purchase taxes; a direct import-VAT part can use
the appropriate account with no percentage tax. The workflow never guesses one
VAT rate for mixed accounting components and never submits or posts a part.

Fixed-cost categories retain their native unit-price and quantity behavior. A
fixed-cost category that would change a foreign receipt to the company currency
is refused with a variable-price category suggested as the recovery path. Each
saved part is checked against its planned gross amount and original defaults;
incompatible native recomputation rolls back the whole split.

The source record becomes the first financial part and remains the native split
origin. Further parts are native copies, with copied evidence carrying the same
checksum. Employee, company, document currency, payment method and date are
retained; descriptions and analytic choices follow the source unless explicitly
changed. An existing Batch is retained across the parts, and explicit accounting
exceptions keep their provenance. Source receipt work is superseded, without
deleting its history. Results contain every part and the before/after gross total.

Read the split origin/count through `expenses_get_context`, then review all linked
records in Split Expenses before an ownership correction. The separate
`expenses_correct_draft_family` shortcut requires their complete explicit ID list,
the intended company/employee pair and one mapping choice per part. Compatible
existing values remain; incompatible categories, accounts, taxes, analytics or
Batch memberships need explicit replacements or clears. Document gross amounts
and currencies stay unchanged, while target-company amounts use native dated
rates. A hidden, submitted or accounted part, a stale member list or an invalid
mapping leaves the whole family unchanged. The single-expense preparation action
continues to refuse ownership edits of one sibling.

Unknown transport completion is not replayed automatically. Read the source's
split origin and resulting parts, or every named family member after correction,
and compare persisted totals and mappings first. A completed wizard cannot be
applied twice; another deliberate UI split uses a fresh native wizard.

The addon keeps the native wizard's write/copy/attachment/origin algorithm from
`addons/hr_expense/wizard/hr_expense_split_wizard.py` at `fb119fee0b5a`.
Its copy delegate merges `from_split_wizard=True` into the existing context,
instead of replacing that context: selected companies and the source-authorized
in-process Agent marker must survive native child creation. Generic helper,
follower and copy operations receive no additional grant. Upstream sync must
compare that delegate with the upstream method; accounting, context-retention,
generic-denial and evidence tests cover its compatibility responsibility.

These changes add native transient wizard fields only. Module version 15 uses
normal idempotent ORM upgrades and preserves existing expense, attachment,
message and financial rows; no historical data reconstruction is involved.

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
