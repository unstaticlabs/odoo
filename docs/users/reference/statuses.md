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

Context status is also independent:

- **Inherited from Batch** — the current shared revision supplied the value;
- **Explicit exception** — a deliberate expense value differs from the Batch
  and takes priority; an equivalent value is not counted as an exception;
- **Context to update** — the Batch changed since inheritance and awaits an
  explicit preview/apply action;
- **Fixed accounting** — the expense is beyond Draft and its accounting
  context will not be rewritten.

The Batch list shows these states through a narrow attention indicator instead
of a permanent status badge. Hover it or focus it with the keyboard to read the
specific reason.

Employee-paid and company-paid remaining counts are separate. A Batch can be
Posted while still showing reimbursement or bank-matching work; one payer
side never implies that the other is settled.

## Reconciliation

- **Unreconciled** — no qualifying debit/credit match.
- **Partial** — linked items remain open for a residual amount.
- **Fully reconciled** — linked items balance and share a full matching reference.

## Monthly bank statements

- **Expected** — the configured month has not arrived; it becomes overdue
  after the expected delivery day.
- **Processing** — the retained email or files are waiting for processing.
- **Needs attention** — an import, identity, evidence, balance or continuity
  issue must be resolved.
- **Ready for review** — OFX movements and the official archived PDF are
  complete enough for an accountant to confirm balances.
- **Certified** — the reviewed movement population, balances and exact
  Documents version are recorded in an immutable certification snapshot.
- **Reopened** — an Accounting Manager recorded why correction is required;
  the month must be certified again.

Certification proves completeness against the bank statement. It does not
mean that every movement has been reconciled to an invoice or payment.

## B2C review

SKU mappings and accounting/evidence links use **Pending**, **Verified** and
**Rejected**. Pending means no defensible decision has been made. Verified
means a reviewer confirmed the exact relationship; it does not modify the
linked product, stock or accounting record. Rejected preserves the original
evidence and records that the proposed relationship is not valid.

Monthly sessions are **Draft**, **Reviewed** or **Locked**. Reviewed means the
operator checked the evidenced totals and explained remaining gaps; it is not a
claim of complete coverage. Locked prevents refreshes and link/mapping changes
for that session. Only a B2C Manager can unlock it with an audit note.

## Hygiene

- **Open** — the underlying condition is present.
- **Resolved** — the condition is no longer present.
- **Dismissed** — this reviewed occurrence is hidden without changing
  accounting or disabling its Control. Unchanged evidence stays dismissed;
  new or materially changed records make the result actionable again.

Severity is **Blocking**, **Warning**, **Attention** or **Information**.

The result kind is **Accounting Result** when an evaluator completed, or
**Technical Failure** when no accounting conclusion could be produced.

## Declarations and closing

Readiness is separate from filing. A declaration can be prepared and reviewed
before it is marked filed, paid, refunded or credited. A Closing result can
pass, inform, warn, block or report a technical failure without locking the
period automatically.

## Electronic-invoice reception

- **Configuration incomplete** — required company identity or purchase-journal
  information is missing.
- **Not yet verified** — configuration exists, but the safe representative
  reception test has not passed.
- **Test passed** — the maintained fixture created a correct native draft bill.
- **Ready but inactive** — safe preparation passed and no live reception is
  running.
- **Production activation required** — deliberate registration or local
  reception startup is still required.
- **Registration in progress** — Odoo's Approved Platform is registering the
  company in the French directory.
- **Active** — the production receiver is connected and scheduled reception is
  running.

Connection states are separate: **Safe test ready** is Demo only;
**Connected; retrieval suspended** is registered but not polling; **Connected
and receiving** is live.

See [Activate electronic-invoice reception in production](../how-to/activate-electronic-invoice-reception.md)
before changing a production connection.
