# Tutorial: First Accounting Review

Audience: Valentin, the accountant, or a finance operator opening the rebuilt accounting evidence for the first time.

Goal: make one complete pass through the main accounting review surfaces so you can see the imported company, generate a report, drill down to evidence, and identify what still needs review.

Time needed: about 20 minutes.

Prerequisites:

- You can sign in to Odoo.
- You have at least Accounting read-only access.
- The accounting reconstruction has already been run by the technical process.

## 1. Start on Accounting Home

1. Sign in to Odoo.
2. Open the Accounting app.

Accounting opens the active company's Home. Review these sections before
drilling into records:

- Cash and Bank
- Daily Accounting Work
- Open Balances
- Closing and Declarations
- Prepared Actions and Evidence

What to notice:

- bank and cash balances and unmatched bank transactions;
- draft or incomplete invoices, bills and expenses;
- open receivables and payables;
- the latest closing-readiness state and blocking controls;
- the next declaration deadline and overdue work;
- decisions prepared for Valentin or the accountant.

The `Dashboard` button opens the standard journal-card dashboard. Accounting
Home complements that native screen; it does not replace journal access.

## 2. Open Accounting Hygiene

1. From Accounting Home, go to:

```text
Accounting > Review > Control > Accounting Hygiene
```

2. Open Unstatic Labs and check:

- Bank to Match
- Incomplete Documents
- Vendor Documents Missing Evidence
- Expenses Missing Receipts
- Stale Draft Documents
- Stale Expense Work
- Current Closing Controls
- Open P0 and P1
- Prepared for Valentin and Prosper

What to notice:

- Open balances are review queues, not automatic errors.
- `Attention Required` identifies daily work or review evidence.
- `Blocked` identifies a P0 issue or blocking current closing control.
- The accountant can review the queues but cannot refresh controls or mutate
  accounting data.

## 3. Open the Latest Import Run

1. From Accounting Hygiene, click `Latest Import Evidence`.
2. Review the import run.
3. Review the import metadata:
   - source database;
   - source snapshot;
   - target database;
   - import mode;
   - imported counts;
   - warning count.

You are not expected to edit this screen. It is an audit record.

## 4. Open the Imported Journal Items

1. Open `Accounting > Review > Advanced Audit > Accounting Reconstruction Review`.
2. Open Unstatic Labs and click `Journal Items`.
3. Use the list filters to inspect entries by:
   - date;
   - journal;
   - account;
   - partner;
   - company.

Open one journal item. Notice the source-trace fields where available. These fields connect the target Odoo record back to the Odoo Online source record.

## 5. Preview the Trial Balance

1. Go back to the reconstruction summary.
2. Click `Generate Reports`.
3. In the wizard, set:
   - Report Type: `Trial Balance`
   - Company: `Unstatic Labs`
   - Data Scope: `All Native Accounting`
   - Start Date: `2024-01-10`
   - End Date: `2025-09-30`
   - Target Move: `Posted Entries Only`
   - Export Format: `XLSX`
4. Click `Apply Period` or `Refresh`.

You should see preview rows. The preview is useful before downloading an export.

What to notice:

- Preview Row Count tells you how many rows match the selected filters.
- Preview Metadata records the company, dates, report type and selected filters.
- Trial Balance lines include account code, account name, opening, debit, credit, movement and closing.
- The draft warning explains whether draft entries are excluded or included.

## 6. Drill Down from the Preview

1. In the report preview table, choose a row with a non-zero amount.
2. Click the external-link icon in the row.
3. Odoo opens the source records behind that report row.

For most reports, this opens journal items. For analytic reports, it opens analytic lines.

What to notice:

- Drill-down is how you verify composition, not just totals.
- A correct total is not enough; the contributing entries must also make sense.

## 7. Generate an Export

1. Return to the report wizard.
2. Click `Generate Export`.
3. Download the generated file.
4. Open the metadata field before closing the wizard.

The metadata tells an accountant what company, dates, report type, target move state and filters were used.

## 8. Review Open Discrepancies

1. Go to:

```text
Accounting > Review > Advanced Audit > Discrepancies
```

2. Filter for open items.
3. Open the P0 discrepancy.

Typical current P0:

- final report-variant and accountant acceptance are still pending.

This is not a ledger mismatch. It means technical evidence exists, but a human with the right authority still needs to accept or reject it.

## 9. Review Pending Decisions

1. Go to:

```text
Accounting > Review > Advanced Audit > Review Decisions
```

2. Filter by state `Draft`.
3. Open one report-parity decision.

Do not record a decision yet unless you have reviewed the evidence and have authority to approve. The purpose of this tutorial is to understand the workflow.

## 10. Open the Source Report Catalogue

1. Go to:

```text
Accounting > Review > Advanced Audit > Source Report Catalogue
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

## 11. Finish the First Review

At the end of this tutorial you have:

- reviewed the operational Accounting Home;
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
