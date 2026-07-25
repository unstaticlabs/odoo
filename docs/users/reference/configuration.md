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
- Accounting Controls.

## Bank statement files

CAMT and QIF statements work without journal-specific parsing rules. For a
bank's CSV or XLSX export, create a **Statement Sheet Mapping** in
**Configuration > Accounting**, then select it on the bank journal's advanced
settings. The mapping describes the date, amount, reference and partner columns;
it does not post or reconcile transactions automatically.

## Accounting Controls

Open **Configuration > Controls**. The catalogue shows every configured control
used by Accounting Hygiene or Closing. Each definition explains what it checks,
why it matters, the expected resolution, its responsible role, its origin and
its readiness effect.

Accounting Managers can enable a control, choose whether it applies to Hygiene
or Closing, limit its Closing period scope, and set its effect to Dynamic,
Informational, Advisory or Blocking. Dynamic keeps the evaluator's contextual
recommendation. Editing business behavior labels the control
**Company-specific**.

Use **Refresh Results** after a configuration change. The next Hygiene and open
Closing refresh uses the new policy. A disabled Hygiene result resolves
naturally and a disabled Closing result disappears from the refreshed
workspace; historical results remain available. Configuration never posts,
reconciles, changes declarations or applies lock dates.

Technical Administrators can inspect the installed evaluator key and technical
boundary on **Advanced Logic**. The product deliberately uses whitelisted
module evaluators instead of arbitrary Python entered in the UI.

## Safe configuration practice

Configuration changes can affect future documents and reports. Prefer an effective-date change or a new reusable rule over altering the meaning of a rule already used on posted documents.
