# How To Review Source Report Evidence

Audience: accountant, CEO, finance operator reviewing parity evidence.

Use this guide when you need to understand how the rebuilt reports were compared with the accounting reports that existed in the Odoo Online source.

## Open the Source Report Catalogue

Go to:

```text
Accounting > Review > Rebuild Evidence > Source Report Catalogue
```

This screen lists active accounting reports discovered in the source system.

For each report, review:

- report name;
- source report identifier;
- source model;
- country or localization binding;
- availability decision;
- parity level;
- target evidence key;
- line count;
- column count;
- expression count;
- extraction status.

## Inspect Report Lines

Open a report and click the source-lines action, or go directly to:

```text
Accounting > Review > Rebuild Evidence > Source Report Lines
```

Use this screen to review the report hierarchy:

- parent and child lines;
- sequence;
- line code;
- line label;
- source group;
- foldable or visible state;
- target mapping status.

This is useful when checking whether a French annual statement line, balance-sheet section or tax-report section has been represented in the target.

## Inspect Report Expressions

Go to:

```text
Accounting > Review > Rebuild Evidence > Source Report Expressions
```

Expressions show how source report lines were configured. They are evidence for parity review, not copied Enterprise implementation code.

Review:

- expression label;
- computation engine;
- formula;
- subformula;
- date scope;
- sign or rounding hints;
- mapping status.

## Inspect Report Columns

Go to:

```text
Accounting > Review > Rebuild Evidence > Source Report Columns
```

Columns help explain whether a report expected current-period values, comparison values, gross amounts, depreciation amounts or net amounts.

## Compare with Target Reports

After reviewing the source structure, open the target report export wizard:

```text
Accounting > Reporting > Trial Balance
```

or another report launcher.

Generate the matching target report for the same company and period. Then compare:

- report purpose;
- period;
- line structure;
- account membership;
- signs;
- rounded values;
- drill-down membership;
- known discrepancy classification.

## What This Evidence Can and Cannot Prove

Source report evidence can prove that the source structure was inventoried and that target reports were mapped against it.

It does not prove accountant acceptance by itself. A report can be technically mapped and still require professional review for classification, tax meaning, PCG version treatment or external declaration values.
