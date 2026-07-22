# Imported Ledger, Draft Regeneration and Review-Only Records

Audience: accountant, CEO, finance operator.

The rebuilt accounting product uses three different kinds of accounting evidence. Understanding the difference prevents accidental changes to accounting truth.

## Imported Posted Ledger

The imported posted ledger is the statutory baseline.

It contains posted journal entries and journal items recreated in the target from source accounting facts. These entries are protected by lock dates and should not be manually changed.

Use this for:

- Trial Balance;
- General Ledger;
- Balance Sheet;
- Profit and Loss;
- FEC;
- tax and VAT reconciliation;
- statutory review.

## Generated Draft Documents

Some source records were not posted. They are represented as document-regeneration cases.

When generated, they become draft target `account.move` records. They help test and inspect the operational workflow, but they are not posted statutory history.

Use this for:

- reviewing draft invoice or bill reconstruction;
- checking source draft line totals;
- preparing future workflow decisions.

Do not include generated drafts in the posted ledger baseline unless a later approved process says so.

## Review-Only Records

Some source facts should not become target accounting entries directly. They are represented as review-only records.

Examples:

- cancelled source moves;
- zero-line draft records;
- payments without source journal entries;
- cross-boundary reconciliations;
- future depreciation schedule rows;
- source report definitions.

Review-only does not mean ignored. It means preserved without silently changing the posted ledger.

