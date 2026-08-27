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

Open **Accounting > Configuration > Invoicing > Electronic Invoicing** or,
as an Accounting manager, **Settings > Users & Companies > Electronic
Invoicing**. Both shortcuts open the same readiness workspace. Use the native
**Settings > Users & Companies > Companies** menu for ordinary company
details such as the company email address. Complete the four
business steps: company identity, incoming purchase journal, accounting
contact and reception self-check.

The self-check uses the native decoder and then rolls its transaction back. It
does not contact a provider or leave synthetic accounting data.
**Ready for production** is the expected pre-activation state. Authentication,
production registration, manual production checks and deregistration are
external actions, not configuration previews.

Use [Prepare electronic-invoice reception](../how-to/prepare-electronic-invoice-reception.md)
for safe checks. Use
[Review an incoming electronic invoice](../how-to/review-incoming-electronic-invoice.md)
for the normal bill journey. Use
[Activate electronic-invoice reception in production](../how-to/activate-electronic-invoice-reception.md)
only during the approved production change window.

## Fiscal years

Configure the recurring closing day and month under
**Accounting > Configuration > Settings > Fiscal Year End**.

When the first exercise is exceptional, open
**Configuration > Companies**, select the company, then set
**First Reconstructed Fiscal-Year Start** and
**First Reconstructed Fiscal-Year End** under
**French Declaration Profile**.

For Unstatic Labs, the exceptional first exercise is
**10/01/2024–30/09/2025**; the recurring cadence then runs from 1 October to
30 September. The report presets **Fiscal Year** and **Fiscal Year to Date**
use these governed boundaries on screen and in PDF/XLSX exports.

## Exchange gain and loss direction

Open **Accounting > Configuration > Chart of Accounts**, then select an
account to configure **Entry Direction Check**.

The default **Automatic from French Account Code** warns when a draft credits
an exchange-loss account `666…` or debits an exchange-gain account `766…`.
Correct the line when it is a mistake. For a justified manual correction,
select **Confirm exceptional direction** on the draft before posting. Editing
the affected journal items requires confirmation again.

Native exchange adjustments, supplier or customer refunds, formal reversals
and reconstructed historical entries are handled without this manual
confirmation. Use **No Direction Check** only when the account intentionally
does not follow the configured normal direction.

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

For scheduled Shine exports, open **Configuration > Accounting > Bank
Statement Email Setup**. One setup belongs to exactly one company, bank
journal and bank account. Keep **Receive and process emails** disabled until
the approved sender, download host, alias route, historical start month and
responsible accountant have been checked and a synthetic email has passed.
CSV and QIF copies are retained as evidence; OFX is the transaction source and
the bank PDF is the balance evidence. This setup does not reconcile imported
transactions automatically.

## Currency Rate Automation

Open **Configuration > Currency Rate Automation** to govern ECB reference
rates. **Fill Missing Rates** imports every missing ECB publication day from
the displayed coverage boundary through the latest available date. The daily
scheduled action checks the recent publication history as well, so a temporary
outage does not leave a silent gap.

Keep **Share across same-currency companies** enabled to retrieve once and
maintain the same provider-controlled dates and values for every displayed EUR
company. The list names the companies that will be updated. Odoo retains a
separate native row for each company; restored, manual and transaction-specific
non-ECB rates remain untouched and may legitimately differ.

USL's company currency is EUR. Odoo therefore treats EUR as the implicit rate
`1.0` and does not require a generated EUR row. Automation creates native
`res.currency.rate` rows only for active foreign currencies such as USD and
GBP. Restored source rates and manager-entered manual rates are never
overwritten.

ECB rates are informational reference rates. Preserve the actual bank, card or
platform conversion when it defines a transaction.

## Expenses in several companies

An administrator opens **Settings > Users & Companies > Users**, selects the
user and enables **Expenses in all allowed companies**. **Refresh expense
access** verifies that every allowed company has its own active employee
profile.

The user then switches the highlighted company before creating an expense.
Odoo selects that company's employee profile automatically. This does not share
contracts, payroll, departments, approvers or accounting between companies.
Archived or ambiguous profiles show **Needs attention** and must be reviewed by
an administrator.

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

Disabling a Control is different from dismissing a Hygiene occurrence.
**Dismiss** acknowledges only the records and material evidence currently shown
on one result. It leaves the Control enabled; new records or changed evidence
can reopen that result. Disable a Control here only when the company no longer
wants it evaluated in the selected workflows.

Technical Administrators can inspect the installed evaluator key and technical
boundary on **Advanced Logic**. The product deliberately uses whitelisted
module evaluators instead of arbitrary Python entered in the UI.

## Reports

Open **Accounting Framework > Reports** to understand where a report appears,
its professional presentation style, default hierarchy, available filters and
PDF/XLSX support. **Arrondi par défaut** selects **Sans décimales** or **Deux
décimales** for new sessions (respectively **À l’euro** and **Au centime**
when the display unit is the euro). Create a company override before changing
it: shared definitions remain upgrade-managed. **Open Report** launches the
normal polished report; the configuration form is not a generic report
builder.

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
