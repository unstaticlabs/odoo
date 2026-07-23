# How To Generate and Review the FEC

Audience: accountant, CEO, finance operator.

Use this guide to generate the FEC from the reconstructed accounting ledger and inspect its evidence.

## Open the FEC Export

Go to:

```text
Accounting > Reporting > FEC
```

The wizard opens with the benchmark period defaults.

For the first closed Unstatic Labs benchmark, use:

```text
Start Date: 2024-01-10
End Date: 2025-09-30
Target Move: Posted Entries Only
Export Format: FEC TXT
FEC Test Mode: enabled
```

## Understand the Role Boundary

- Prosper's `USL Accountant Review` role and a normal finance operator receive
  a complete posted-entries FEC in test mode. Test mode is enabled and the
  checkbox is locked, so generating the file cannot change fiscal lock dates.
- An Accounting Manager can clear `FEC Test Mode` to generate the official
  path. This is manager-only because native Odoo may advance the fiscal-year
  lock date to the selected end date.
- Test mode changes the lock-date side effect, not the ledger contents. The
  benchmark export still contains the full posted scope and must reconcile.

## Generate the File

Click `Generate Export`.

Odoo creates a `.txt` FEC file and opens the `Download` tab. Click the filename
to retrieve it. The download surface does not require a report preview because
FEC preview is deliberately disabled.

## Review the Metadata

Check:

- company;
- source company id;
- period start;
- period end;
- target move scope;
- row count;
- debit total;
- credit total;
- file name.

The FEC must reconcile to the imported posted ledger. A structurally valid FEC is not enough by itself; accountant review remains required.

## Inspect FEC Review Decisions

Go to:

```text
Accounting > Review > Advanced Audit > Review Decisions
```

Filter for gate `FEC Validation`.

Open the FEC review decision. Confirm that it references the correct period and evidence.

## What Acceptance Means

The FEC can be technically generated and structurally validated while still requiring professional review.

An accountant should check:

- company and period;
- completeness of entries;
- journal codes and labels;
- entry numbers;
- account codes and labels;
- debit and credit totals;
- lettering where present;
- validation dates;
- reconciliation with Trial Balance, General Ledger, Balance Sheet and Profit and Loss.

Only record acceptance after this review.
