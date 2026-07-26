# Configuration

Accounting managers use **Configuration** for:

- companies and fiscal-year dates;
- journals and payment methods;
- chart of accounts and account groups;
- taxes, tax grids and fiscal positions;
- currencies and historical rates;
- analytic plans and accounts;
- asset models;
- the configurable Accounting Framework for Controls, Reports and
  Declarations.

## Accounting Framework

Open **Configuration > Accounting Framework**. Its three
catalogues show the business definitions used by the operational Accounting
product:

- **Controls** govern Hygiene and Closing readiness;
- **Reports** govern purpose, presentation, filters, exports and Reporting
  navigation;
- **Declarations** govern applicability, fiscal versions, forms, deadlines and
  official sources.

Each definition identifies its origin, version, lifecycle, company scope,
business purpose and expected outcome. Shared Odoo/OCA/localization/USL
definitions are upgrade-managed. Select **Customize for Company** on a Report
or Declaration to create an editable company override without changing the
shared definition.

## Bank statement files

CAMT and QIF statements work without journal-specific parsing rules. For a
bank's CSV or XLSX export, create a **Statement Sheet Mapping** in
**Configuration > Accounting**, then select it on the bank journal's advanced
settings. The mapping describes the date, amount, reference and partner columns;
it does not post or reconcile transactions automatically.

## Accounting Controls

Open **Configuration > Accounting Framework > Controls**. The catalogue shows every configured control
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

## Reports

Open **Accounting Framework > Reports** to understand where a report appears,
its professional presentation style, default hierarchy, available filters and
PDF/XLSX support. **Open Report** launches the normal polished report; the
configuration form is not a generic report builder.

Company overrides take precedence for their company and effective dates. The
interactive session and export metadata retain the resolved definition version.

## Declarations

Open **Accounting Framework > Declarations** to inspect fiscal versions,
applicability profiles, official sources and filing guidance. **Instances**
opens the separate company/period obligations generated from a definition.
Company overrides can refine an obligation without rewriting localization
records delivered by the module.

## Safe configuration practice

Configuration changes can affect future controls, reports and declarations.
Use a new definition version and effective dates when meaning changes.
Historical results retain their definition snapshot; do not repurpose an old
version to mean something different.
