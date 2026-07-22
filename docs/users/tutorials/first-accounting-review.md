# Tutorial: First Accounting Review

Audience: Valentin, the accountant, or a finance operator opening the rebuilt accounting evidence for the first time.

Goal: make one complete pass through the main accounting review surfaces so you can see the imported company, generate a report, drill down to evidence, and identify what still needs review.

Time needed: about 20 minutes.

Prerequisites:

- You can sign in to Odoo.
- You have at least Accounting read-only access.
- The accounting reconstruction has already been run by the technical process.

## 1. Open the Reconstruction Summary

1. Sign in to Odoo.
2. Open the Accounting app.
3. Go to:

```text
Accounting > Review > Rebuild Evidence > Accounting Reconstruction Review
```

You should see one row per imported company. For Unstatic Labs, check these columns:

- Company
- Source Snapshot
- Source Dump SHA-256
- Latest Import Status
- Posted Moves
- Move Lines
- Debit
- Credit
- Balance
- Open P0
- Open P1
- Pending Review Decisions

What to notice:

- Debit and Credit should match.
- Balance should be zero.
- Readiness may still be `Blocked` if accountant decisions are pending.
- Open P0 and P1 counts show issues that must be reviewed before closure.

## 2. Open the Latest Import Run

1. Open the Unstatic Labs reconstruction summary row.
2. Click `Latest Import Run`.
3. Review the import metadata:
   - source database;
   - source snapshot;
   - target database;
   - import mode;
   - imported counts;
   - warning count.

You are not expected to edit this screen. It is an audit record.

## 3. Open the Imported Journal Items

1. Return to the reconstruction summary.
2. Click `Imported Journal Items`.
3. Use the list filters to inspect entries by:
   - date;
   - journal;
   - account;
   - partner;
   - company.

Open one journal item. Notice the source-trace fields where available. These fields connect the target Odoo record back to the Odoo Online source record.

## 4. Preview the Trial Balance

1. Go back to the reconstruction summary.
2. Click `Report Export`.
3. In the wizard, set:
   - Report Type: `Trial Balance`
   - Company: `Unstatic Labs`
   - Start Date: `2024-01-10`
   - End Date: `2025-09-30`
   - Target Move: `Posted Entries Only`
   - Export Format: `XLSX`
4. Click `Preview`.

You should see preview rows. The preview is useful before downloading an export.

What to notice:

- Preview Row Count tells you how many rows match the selected filters.
- Preview Metadata records the company, dates, report type and selected filters.
- Preview lines include account code, account name, debit, credit and balance where applicable.

## 5. Drill Down from the Preview

1. In the report preview table, choose a row with a non-zero amount.
2. Click the external-link icon in the row.
3. Odoo opens the source records behind that report row.

For most reports, this opens journal items. For analytic reports, it opens analytic lines.

What to notice:

- Drill-down is how you verify composition, not just totals.
- A correct total is not enough; the contributing entries must also make sense.

## 6. Generate an Export

1. Return to the report wizard.
2. Click `Generate Export`.
3. Download the generated file.
4. Open the metadata field before closing the wizard.

The metadata tells an accountant what company, dates, report type, target move state and filters were used.

## 7. Review Open Discrepancies

1. Go to:

```text
Accounting > Review > Rebuild Evidence > Discrepancies
```

2. Filter for open items.
3. Open the P0 discrepancy.

Typical current P0:

- final report-variant and accountant acceptance are still pending.

This is not a ledger mismatch. It means technical evidence exists, but a human with the right authority still needs to accept or reject it.

## 8. Review Pending Decisions

1. Go to:

```text
Accounting > Review > Rebuild Evidence > Review Decisions
```

2. Filter by state `Draft`.
3. Open one report-parity decision.

Do not record a decision yet unless you have reviewed the evidence and have authority to approve. The purpose of this tutorial is to understand the workflow.

## 9. Open the Source Report Catalogue

1. Go to:

```text
Accounting > Review > Rebuild Evidence > Source Report Catalogue
```

2. Open a source report, for example Trial Balance or Balance Sheet.
3. Review:
   - decision;
   - parity level;
   - target evidence key;
   - line count;
   - column count;
   - expression count.
4. Use the buttons for source lines and source expressions.

This is how Odoo shows the source report structure used for parity review.

## 10. Finish the First Review

At the end of this tutorial you have:

- checked that the imported ledger balances;
- opened the import run;
- previewed and exported a report;
- drilled down to source records;
- found discrepancies;
- found pending review decisions;
- found the source report catalogue.

Next, use the how-to guides for real work:

- [Generate, Preview and Export Accounting Reports](../how-to/generate-accounting-reports.md)
- [Review Discrepancies and Record Decisions](../how-to/review-discrepancies-and-decisions.md)
- [Generate and Review the FEC](../how-to/generate-and-review-fec.md)

