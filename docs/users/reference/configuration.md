# Configuration

Accounting managers use **Configuration** for:

- companies and fiscal-year dates;
- journals and payment methods;
- chart of accounts and account groups;
- taxes, tax grids and fiscal positions;
- currencies and historical rates;
- analytic plans and accounts;
- asset models;
- declaration configuration;
- Closing Controls.

## Bank statement files

CAMT and QIF statements work without journal-specific parsing rules. For a
bank's CSV or XLSX export, create a **Statement Sheet Mapping** in
**Configuration > Accounting**, then select it on the bank journal's advanced
settings. The mapping describes the date, amount, reference and partner columns;
it does not post or reconcile transactions automatically.

## Closing Controls

Open **Configuration > Closing Controls**. Each definition has a category, order, responsible role, explanation and accounting consequence.

Disable a control only when it is not applicable to the company. The next Closing Workspace refresh uses the enabled definitions. Changing a definition does not modify posted accounting.

## Safe configuration practice

Configuration changes can affect future documents and reports. Prefer an effective-date change or a new reusable rule over altering the meaning of a rule already used on posted documents.
