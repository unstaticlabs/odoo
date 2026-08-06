# Expense classification and Batch context

This is a preparation contract for a future Accounting Agent. It does not
authorize submission, approval, posting, payment or reconciliation.

Treat Product and Batch as independent proposals. Product expresses expense
nature; Batch expresses shared purpose and context. Evidence may include the
receipt, email metadata, dates, location, calendar and existing Batches. Do
not require a trip code in a subject.

For every proposal, retain a concise evidence explanation and uncertainty.
Ambiguous Product or Batch choices remain drafts for human review. Duplicate
signals are warnings. Missing evidence never causes automatic submission.

Use the access-checked ORM services, in this order:

1. `hr.expense.get_expense_batch_candidates(expense_ids)` to read ranked
   compatible contexts;
2. `usl.expense.batch.preview_context_application(...)` to explain changes,
   preserved exceptions and skipped records;
3. `usl.expense.batch.apply_context(..., expected_revision=...)` for permitted
   draft updates;
4. `usl.expense.batch.get_review_summary()` for a stable review payload.

Never duplicate precedence in client code. Never force an explicit exception,
reuse a stale revision or cross employee/company boundaries. A retry with the
same revision and values must be idempotent. If the service reports a stale
revision, refetch and present the new preview rather than retrying blindly.
