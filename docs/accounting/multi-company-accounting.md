# Multi-company Accounting

## Product contract

The Distribution uses Odoo's native company selector and company-aware
records. An authorized user can select several allowed companies for combined
reading, but creates and changes Accounting records in one active company at a
time.

The working database contains two EUR legal companies, **Unstatic Labs** and
**USL MEDIA**, with company-scoped charts, journals, taxes, fiscal settings and
operational records. Historical configuration needed to understand posted
records remains available even when inactive. Payment methods used by real
payments and expenses map to native Community behavior; unsupported, unused
Enterprise transports are not imitated with inert custom configuration.

## Reports

Interactive reports have their own **Companies** selector because a saved
report must preserve its scope independently from later global-selector
changes. A new report starts with the companies currently selected in Odoo's
global selector.

- Balance générale, Journal comptable, Bilan, Compte de résultat, Cash Flow,
  Executive Summary, grouped assets and French management statements combine
  equivalent rows when all selected companies use the same company currency.
  Each combined row retains company contributions and drills into all selected
  source lines.
- Detail reports keep company-specific rows so journal-item identity, running
  balances, currencies and reconciliation evidence are not blurred.
- PDF and XLSX use the same selected-company scope and calculations as the
  screen.
- FEC, French tax packages and closing packages remain one-company outputs.
- Companies with different company currencies must be reported separately.

The Accounting Overview follows the global company selector. With one company
selected it opens that company's complete cockpit directly. With several
companies selected it shows one clearly labelled cockpit card per company,
because cash projections, closing readiness, declarations and remediation
actions are legal-company states that must not be presented as one synthetic
state. Additive alert counts on Home are combined across the selected
companies and retain a visible per-company contribution; their drill-downs use
the exact same selected-company domain. Hygiene, Declarations and Closing
records remain company-scoped inside those combined lists.

These combined views are management totals, not legal consolidation. The
Distribution does not currently implement consolidation account mapping,
eliminations, multi-ledgers or currency-translation adjustments. Those require
a separately approved group-consolidation design. This boundary follows Odoo's
distinction between ordinary multi-company reporting and its full
consolidation toolset.

## Shared reference data without shared legal records

### Currency rates

Companies with the same base currency use one ECB retrieval and one automated
coverage boundary. Odoo still stores a native rate row per company, because
its conversion API is company-aware, but provider-controlled values and dates
are synchronized. A new EUR company therefore benefits from the EUR group's
existing automated coverage instead of starting with only the latest day.

Restored rates identified as ECB reference rates join the shared history.
Manager-entered rates and bank/platform transaction rates are not copied or
overwritten; they remain company-specific accounting evidence. Companies with
another base currency form a separate synchronization group.

### People and expenses

One Odoo user can submit expenses for every allowed company after an
administrator enables **Expenses in all allowed companies** on the user. The
Distribution maintains one minimal native employee profile per company and
Odoo automatically selects the profile for the active company.

An administrator may exclude an allowed company from employee provisioning.
The user keeps access to that company, but Odoo does not create or reactivate an
employee profile there. Existing employee records are archived only through an
explicit reviewed action.

The profiles share only the login, person and work contact. Contracts,
departments, approvers, payroll, private HR data and expense accounting remain
company-specific. Removing company access never deletes the employee or its
history. An archived or ambiguous employee profile is reported for review
rather than guessed or reactivated.

This builds on Odoo's native one-user/one-employee-per-company constraint. A
single cross-company employee record was rejected because it would weaken
company checks around expenses, HR and payroll. OCA's
`hr_employee_multi_company` was also reviewed; its 18.0 release is Beta and
addresses employee visibility rather than this expense identity lifecycle, and
no qualified saas~19.3 integration is available.

### Operational accounting baseline

An imported company that contains accounts or bank activity but lacks ordinary
operational journals receives idempotent native journals for customer invoices,
vendor bills, general entries and expenses, plus ordinary customer/supplier and
funds-in-transit defaults when absent. Source configuration is never replaced.
This closes a practical gap in sparse Online companies without inventing any
historical move or sharing accounting records between legal entities.

## Access and operating rules

- Allowed Companies on the user record is the hard access boundary.
- The highlighted company in the global selector is the active company used
  for new accounting records and company-dependent configuration.
- Selecting several companies broadens permitted reading; it does not make a
  write operation cross-company.
- Home labels combined widgets explicitly. Activities, assigned tasks and AI
  attention use the selected-company record-rule scope; Accounting alert
  counts aggregate that same scope and show the contributing companies.
- Company-state Accounting cards never manufacture a consolidated readiness,
  deadline, cash projection or closing status. They are duplicated and
  labelled per company in multi-company mode.
- Each company has a dedicated **Interface color** under **Settings > Users &
  Companies > Companies**. With one company selected, its color is applied to
  the top navigation bar and remains visible inside the company selector.
  When several companies are selected, a `+N` indicator shows the broader
  reading scope and the navigation returns to Odoo's neutral default theme.
  Leaving the field empty uses a deterministic automatic color. Interface
  colors do not change invoices, reports, emails or other company branding.
- List views hide the Company column by default when only one company is
  selected and show it by default when several are selected. This rule applies
  to native and custom company fields; a user's explicit optional-column choice
  remains authoritative for that view.
- Accounting Manager and read-only accountant permissions remain identical in
  each allowed company. The reviewer can inspect combined reports but cannot
  post, reconcile, configure or close.
- A report rejects an unauthorized company even if its identifier is supplied
  directly to the report API.
- An administrator governs cross-company expense access under **Settings >
  Users & Companies > Users**. Employees still switch the active company before
  creating an expense; the resulting expense and employee profile belong only
  to that company.

## Regression evidence

Automated coverage protects company-scoped report models, reviewer record
rules, same-currency aggregation, currency synchronization, company-specific
expense profiles, contribution evidence, multi-company drill-down and
different-currency rejection. Acceptance exercises invoices, credit notes,
bills, refunds, entries, payments, bank transactions and employee expenses in
each company on an isolated clone. It also proves that a scoped reviewer cannot
read an unauthorized company's Accounting or operational records.

Run it against an isolated current-release database with:

```bash
make accounting-multicompany-acceptance COMPOSE_PROJECT=<project>
```

This proves ordinary multi-company operation and same-currency management
aggregation. It is not a claim of Enterprise legal-consolidation parity: the
source contains no consolidation setup, and the Distribution does not ship
consolidation mappings, eliminations, translation adjustments or Enterprise
payment-batch transports.

Official reference:
[Odoo 19 multi-company Accounting](https://www.odoo.com/documentation/19.0/applications/finance/accounting.html#multi-company)
and [Odoo 19 consolidation](https://www.odoo.com/documentation/19.0/applications/finance/accounting/get_started/consolidation.html),
[multi-company ORM guidance](https://www.odoo.com/documentation/19.0/developer/howtos/company.html),
and [OCA multi-company](https://github.com/OCA/multi-company/tree/18.0/hr_employee_multi_company).
