# Lightweight expense batches

Status: implemented on `codex/feat-expense-batches`

End-user workflow: [Notes de frais](../users/guides/expense-batches.md)

## Product decision

USL uses a lightweight `usl.expense.batch` context, shown in French as
**Note de frais**. Individual `hr.expense` records remain the evidence,
correction, tax, analytic and journal-line units. The batch supplies the
shared name, purpose, submission, review and accounting reference.

A batch is optional and deliberately narrower than the former heavy expense
report:

- one employee and one company per batch;
- any meaningful period, trip, mission, project or purpose;
- employee-paid and company-paid expenses can share the review context;
- native Odoo expense states and permissions remain authoritative;
- one problematic expense can be removed and returned to draft without
  returning the other expenses;
- native posting still creates one employee receipt for compatible
  employee-paid expenses and one dedicated entry per company-paid expense;
- every resulting move retains the batch name and a direct batch link.

The one-employee boundary is intentional. A note de frais is a personal claim,
and Odoo's access rules and reimbursement liability are employee-specific.
Cross-employee selections are rejected before a batch is created.

## Alternatives considered

### Standard Odoo 19 bulk selection

Odoo 19 already submits, approves and posts selected expense records in bulk.
It groups employee-paid expenses by employee during posting and retains each
expense as a distinct journal line. This is reused as the execution engine,
but it does not persist a meaningful business grouping or shared purpose.

### Maintained OCA expense add-ons

The maintained OCA `hr-expense` repository was reviewed on its 18.0 branch,
the latest published branch relevant to the removed report model. Its modules
cover advances, cancellation, exceptions, invoices, payments, petty cash,
sequences, tier validation and vendor receipts. It does not provide a
lightweight persistent batch context compatible with Odoo 19's record-based
workflow.

### Restore the former expense report

Restoring `hr.expense.sheet` would provide grouping, but would also restore a
parallel state machine, heavier navigation and migration surface. That
conflicts with the requirement that individual expenses remain the correction
unit and that batching stay optional.

The isolated add-on was selected because it adds only the missing context and
orchestration while keeping upstream accounting behavior intact.

### Link only Draft records or permit later eligible states

Limiting creation to writable Draft records would preserve native employee
write rules without any special handling, but would fail the requirement to
group already-Approved expenses and would make the toolbar promise differ by
role. Allowing arbitrary workflow states would risk changing an expense that
is already under review, payment or settlement.

The selected boundary is unbatched Draft, Approved and Posted expenses.
Submitted, In payment, Paid and Returned expenses are rejected. Approved and
Posted expenses are normally read-only to their employee, so the server first
checks that the caller can read every selected record, that the new batch
passes its own access rule, and that employee, company, state and existing
batch constraints all match. It then elevates only the technical
`expense_batch_id` link; expense evidence, accounting data and workflow state
remain unchanged.

## User experience contract

The normal **Expenses > My Expenses** list stays focused on the expense
records. It shows **Attachment status**, the optional **Expense Batch** link
and the native expense status, but it does not add a permanent **Batch
readiness** column.

Readiness is progressive information:

- **Ready to submit**, **Needs information** and **Already in a batch** are
  available as list filters for preparing draft expenses;
- **Create expense batch** accepts one or more selected, unbatched expenses
  in Draft, Approved or Posted status;
- Submitted, In payment, Paid, Returned and already-batched expenses are not
  eligible;
- the creation preview shows aggregate readiness, line-level attachment and
  expense statuses, optional missing information, common analytic context,
  dates and employee/company-paid totals before anything is saved or
  submitted;
- closing the creation flow reloads and re-renders the underlying expense
  list so newly assigned batch links are visible immediately.

**Create expense batch** is the only batch action in the expense-list toolbar
and appears only when every selected expense is eligible. The former
automatic **Submit ready expenses** shortcut is intentionally absent: the
system must never infer a claim from every ready draft without an explicit
selection. On desktop, the one action must remain on the same toolbar row as
the native expense actions. Adding the batch feature must not increase the
toolbar's vertical height.

## Action semantics

**Create expense batch** opens a preview for the explicit selection. The
secondary **Create batch** action saves the grouping without changing any
expense workflow status.

**Submit batch** creates the grouping when necessary and submits only its
Draft expenses for manager review. Approved and Posted expenses keep their
current status; the action does not post journal entries and does not create
payments. This distinction is stated in the button helper.

Mixed-status batches advance by native stage without regressing later lines:

- Submit acts on Draft expenses;
- Approve acts on Submitted expenses;
- Post acts on Approved expenses;
- expenses already beyond the current stage remain unchanged.

The batch status is the least advanced status among its active expenses. It
therefore describes the next batch-level action without replacing the
individual expense statuses.

## Completeness and accounting invariants

The batch preview identifies missing description, category, non-zero amount
and required receipt. The aggregate **Batch readiness** is **Ready** only when
every line is complete; otherwise it is **Needs information**. Each line keeps
an **Attachment status** of **Attached**, **Missing** or **Not required**, plus
its native expense status. **Missing information** is an optional line detail,
not a second line-level readiness column.

Only incomplete Draft expenses block **Submit batch**, because Approved and
Posted lines are not submitted again. The preview still warns about incomplete
later-stage lines so reviewers can see the exception. The installed USL
receipt policy is honored when present. Native analytic validation still runs
during approval.

The implementation must preserve these invariants:

1. No batch can cross a company or employee boundary.
2. Only unbatched Draft, Approved or Posted expenses can enter a new batch.
3. Ineligible and already-batched expenses are rejected in both the UI and
   server-side model rules.
4. Submission is atomic for the Draft subset: an incomplete Draft line blocks
   every Draft transition and no partial submission occurs.
5. Refused lines are retained as review history but do not block the remaining
   active lines from progressing.
6. Posted moves retain `expense_batch_id`, use the batch name as `ref`, and
   retain native `expense_id` journal-line links and copied attachments.
   A pre-existing posted move is linked only when all of its expense records
   belong to the same batch.
7. Read-only accountants can inspect batches but cannot mutate or trigger
   workflow actions.

## Isolated QA environment

Feature QA must not update or run the feature branch against canonical
`odoo_dev`. Use a separate Compose project, ports and database.

For focused automated tests, a minimal disposable database is sufficient:

```bash
ODOO_SAAS_COMPOSE_PROJECT=usl-odoo-expense-batch \
ODOO_DEV_DB=odoo_expense_batch_qa \
ODOO_INIT_DB=odoo_expense_batch_qa \
ODOO_HTTP_PORT=8169 \
ODOO_GEVENT_PORT=8172 \
scripts/odoo-dev test usl_expense_batch odoo_expense_batch_test
```

For integrated browser and product demonstrations, use an isolated database
and filestore copy of the latest local `odoo_dev`, including its pinned OCA
dependencies. Update `rebuild_account_migration` and `usl_expense_batch` only
inside that copy. The current aligned QA database is
`odoo_expense_batch_aligned_qa` on port `8169`.

This distinction is deliberate: the minimal database proves the feature in
isolation, while the aligned clone proves that Accounting and Expenses work
together with representative data. Scheduled jobs remain disabled, and the
canonical `odoo_dev` database and filestore remain unchanged.
