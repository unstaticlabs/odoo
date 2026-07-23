# How To Review French VAT, CA12 and Tax-Package Values

Audience: accountant, CEO, finance operator preparing tax evidence.

Use this guide to inspect French VAT and tax-package review values for the imported Unstatic Labs accounting period.

## Open the French Tax Package Mapping

Go to:

```text
Accounting > Review > Advanced Audit > Imported French Tax Package Mapping
```

This view contains lines for French tax-package review, including 2065-SD, 2033 forms and CA12-related evidence where implemented.

## Review the Main Fields

For each line, check:

- form code;
- field code;
- field label;
- source kind;
- source formula;
- drill-down account prefixes;
- amount;
- rounded amount;
- benchmark amount;
- ledger amount;
- difference amount;
- difference classification;
- review status.

## Inspect VAT Evidence

For VAT and CA12 review, check:

- collected VAT;
- deductible VAT;
- VAT credit carryover;
- CA12 clearing entries;
- externally supplied declaration values;
- difference classifications.

External values are not hidden constants. They are represented as Odoo records.

Open:

```text
Accounting > Review > Advanced Audit > External Report Values
```

Use this screen to inspect:

- period;
- form;
- field;
- amount;
- source reference;
- review status;
- decision;
- reviewer.

## Generate a Tax Export

Open:

```text
Accounting > Reporting > French Tax Package and CA12 Mapping
```

In the export wizard:

1. Confirm company and dates.
2. Choose `PDF`, `XLSX` or `CSV`.
3. Click `Apply Period` or `Refresh`.
4. Review rows and metadata.
5. Click `Generate Export`.

## Decide Whether a Value Is Accepted

If a line is review-required:

1. Open the linked external value or discrepancy.
2. Click `Record Review Decision`.
3. Record the accountant's conclusion.

Do not accept a declaration value solely because it appears in a report. It must reconcile to ledger evidence, accepted external evidence, or accountant judgment.
