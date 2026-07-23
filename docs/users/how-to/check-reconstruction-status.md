# How To Check the Reconstruction Status

Audience: CEO, accountant, finance operator.

Use this guide when you need to know whether the reconstructed accounting database is ready to review.

## Open the Summary

Go to:

```text
Accounting > Review > Control > Issues
```

Open the company you want to review.

## Read the Main Status Fields

Check:

- `Latest Import Status`: shows whether the latest import is complete, partial, failed or still review-bound.
- `Source Snapshot`: identifies the imported source package.
- `Source Dump SHA-256`: identifies the source backup.
- `Posted Moves`: number of imported posted journal entries.
- `Move Lines`: number of imported posted journal items.
- `Debit` and `Credit`: should match.
- `Balance`: should be zero.
- `Open P0` and `Open P1`: show important unresolved gates.
- `Pending Review Decisions`: shows how many decisions still need a human reviewer.

## Interpret Readiness

`Technical Evidence Available` means the system has enough technical evidence for review.

`Review Required` means a person must inspect evidence and record decisions.

`Blocked` means at least one important gate is still unresolved. A blocked status does not always mean an import failed. It can mean that accountant acceptance is still pending.

## Open Related Records

From the summary form, use:

- `Latest Import Run` to inspect import metadata.
- `Open Discrepancies` to inspect blockers.
- `Review Decisions` to inspect pending acceptances.
- `External Values` to inspect manual or externally supplied tax values.
- `Document Regeneration Cases` to inspect non-posted source records.
- `Imported Journal Items` to inspect posted ledger lines.
- `Source Reports` to inspect source report parity evidence.
- `Report Export` to generate reports.

## What Good Looks Like

For a technically clean reconstruction:

- debit equals credit;
- balance is zero;
- no technical failures are listed in evidence;
- all mandatory report families have target evidence;
- remaining blockers are clearly classified and assigned to review.

Do not close the accounting review while there are open P0 discrepancies or pending accountant review decisions.
