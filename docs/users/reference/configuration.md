# Configuration

Accounting managers use **Configuration** for:

- companies and fiscal-year dates;
- journals and payment methods;
- chart of accounts and account groups;
- taxes, tax grids and fiscal positions;
- currencies and daily reference rates;
- analytic plans and accounts;
- asset models;
- governed Bank Matching Rules, including usage evidence and inert rule
  suggestions;
- French electronic-invoice reception readiness, safe testing and deliberate
  production activation;
- the configurable Accounting Framework for Controls, Reports and
  Declarations.

## E-Invoicing

Open **Configuration > Invoicing > E-Invoicing**. Complete the French company
identifier and incoming purchase journal, then use **Test Reception** to create
a synthetic draft supplier bill without contacting a provider.

**Ready but inactive** is the expected state before production. Authentication,
production registration, Pilot Phase, live polling and deregistration are
external actions, not configuration previews.

Use [Prepare electronic-invoice reception](../how-to/prepare-electronic-invoice-reception.md)
for safe checks. Use
[Activate electronic-invoice reception in production](../how-to/activate-electronic-invoice-reception.md)
only during the approved production change window.

## Bank Matching Rules

Open **Configuration > Bank Matching Rules** to govern recurring direct
accounting treatments. The list distinguishes rules with recorded use, rules
that are executable but unused, incomplete rules, suggestions awaiting review
and legacy partner-only rules made redundant by smart partner inference.

Only an Accounting Manager can discover, approve, automate, dismiss or archive
rules. Finance Operators use approved rules through Bank Matching. Suggestions
created by deterministic analysis or a future Accounting Agent cannot affect
matching until a manager approves them. See
[Manage Bank Matching Rules](../how-to/manage-bank-matching-rules.md).

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

## Currency Rate Automation

Open **Configuration > Currency Rate Automation** to govern ECB reference
rates. **Fill Missing Rates** imports every missing ECB publication day from
the displayed coverage boundary through the latest available date. The daily
scheduled action checks the recent publication history as well, so a temporary
outage does not leave a silent gap.

USL's company currency is EUR. Odoo therefore treats EUR as the implicit rate
`1.0` and does not require a generated EUR row. Automation creates native
`res.currency.rate` rows only for active foreign currencies such as USD and
GBP. Restored source rates and manager-entered manual rates are never
overwritten.

ECB rates are informational reference rates. Preserve the actual bank, card or
platform conversion when it defines a transaction.

## Employee expense payable account

For an expense paid personally by an employee, Odoo uses the **Account
Payable** configured on that employee's **Work Contact**.

For Valentin:

1. open **Contacts** and select the contact linked to Valentin's user and
   employee;
2. open the **Accounting** or **Invoicing** tab;
3. under **General**, confirm **Account Payable** is `455100 — Associés -
   Comptes courants - Valentin`;
4. confirm `455100` allows reconciliation.

The user contact, employee Work Contact and partner on the open `455100`
journal items must be the same contact. Do not configure this on the Notes de
frais journal, and do not change the company-wide supplier payable account:
those settings serve other accounting purposes.

After changing this setting, only newly posted expenses use the corrected
account automatically. Ask the accountant how to correct an existing posted
entry; do not edit a posted journal item directly.

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
