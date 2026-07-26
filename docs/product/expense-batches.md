# Lightweight expense batches

Status: implementation candidate on `codex/feat-expense-batches`

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

## Completeness and accounting invariants

The batch preview identifies missing description, category, non-zero amount
and required receipt before submission. The installed USL receipt policy is
honored when present. Native analytic validation still runs during approval.

The implementation must preserve these invariants:

1. No batch can cross a company or employee boundary.
2. Only unbatched draft expenses can enter a new batch.
3. Submission is atomic: incomplete lines block the whole action.
4. Refused lines are retained as review history but do not block the remaining
   active lines from progressing.
5. Posted moves retain `expense_batch_id`, use the batch name as `ref`, and
   retain native `expense_id` journal-line links and copied attachments.
6. Read-only accountants can inspect batches but cannot mutate or trigger
   workflow actions.

## Isolated QA environment

Feature QA must not use `odoo_dev` or the normal Compose project. Use a
separate project, ports and database:

```bash
ODOO_SAAS_COMPOSE_PROJECT=usl-odoo-expense-batch \
ODOO_DEV_DB=odoo_expense_batch_qa \
ODOO_INIT_DB=odoo_expense_batch_qa \
ODOO_HTTP_PORT=8169 \
ODOO_GEVENT_PORT=8172 \
scripts/odoo-dev test usl_expense_batch odoo_expense_batch_test
```

For browser QA, start the same isolated project after initializing
`rebuild_account_migration`, which installs this add-on as a product
dependency. Scheduled jobs remain disabled.
