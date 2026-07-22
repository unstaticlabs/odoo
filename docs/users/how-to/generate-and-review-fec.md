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

## Generate the File

Click `Generate Export`.

Odoo creates a `.txt` FEC file and shows export metadata.

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
Accounting > Review > Rebuild Evidence > Review Decisions
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

