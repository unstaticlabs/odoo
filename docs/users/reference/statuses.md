# Statuses

## Documents

- **Draft** — editable and not yet part of posted ledgers.
- **Posted** — journal items have been created in the ledger.
- **Cancelled** — reversed or abandoned according to the document workflow.

## Payment

- **Not paid** — the full receivable or payable remains.
- **Partially paid** — a residual remains after matching.
- **In payment** — a payment exists but the bank or clearing step is incomplete.
- **Paid** — the receivable or payable has no residual.

## Expense batches

- **Draft** — at least one active expense is still Draft.
- **Submitted** — no active expense is Draft and at least one is waiting for
  review.
- **Approved** — no active expense is Draft or Submitted and at least one is
  Approved.
- **Posted** — every active expense is at least Posted.
- **Paid** — the employee-paid liability has been settled.
- **Returned** — all expense lines in the batch were returned for correction.

The batch status is the least advanced active expense status. Mixed-status
batches therefore advance Draft, Submitted and Approved lines without moving
later lines backwards.

Batch readiness is separate from workflow status and is not a permanent
column in the main expense list. At batch level, **Ready** means every line has
the required description, category, non-zero amount and receipt. **Needs
information** identifies one or more exceptions. Only an incomplete Draft
line blocks submission; an Approved or Posted exception stays visible for
review without being resubmitted.

## Reconciliation

- **Unreconciled** — no qualifying debit/credit match.
- **Partial** — linked items remain open for a residual amount.
- **Fully reconciled** — linked items balance and share a full matching reference.

## Hygiene

- **Open** — the underlying condition is present.
- **Resolved** — the condition is no longer present.
- **Dismissed** — reviewed and deliberately accepted without changing accounting.

Severity is **Blocking**, **Warning**, **Attention** or **Information**.

The result kind is **Accounting Result** when an evaluator completed, or
**Technical Failure** when no accounting conclusion could be produced.

## Declarations and closing

Readiness is separate from filing. A declaration can be prepared and reviewed
before it is marked filed, paid, refunded or credited. A Closing result can
pass, inform, warn, block or report a technical failure without locking the
period automatically.
