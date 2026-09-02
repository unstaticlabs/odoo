# Roles and Permissions

## Distribution roles and irreversible actions

Application access and **Irreversible Actions** are separate. Permanent
deletion, accounting lock changes, user/role changes, module maintenance,
arbitrary server actions and external e-invoice registration changes require
the separate capability. The product hides high-risk controls when practical,
and the server enforces the rule for every RPC call.

- **Full Product Administrator:** all applications, including Accounting and
  Sign management, plus technical configuration under an attributable human
  identity. Protected actions still require the separate **Irreversible
  Actions** capability.
- **Technical Administrator:** full B2C operations, Projects and Documents,
  Accounting read-only, and safe technical inspection. Cannot change security,
  locks, Accounting records or perform permanent deletion.
- **Accounting Reviewer:** reversible annual-review Accounting work in unlocked
  periods, including draft adjustments, posting, reset and reconciliation.
  Cannot reach unrelated applications, change locks or permanently delete.
- **AI Agent:** combines with explicitly assigned application groups, but can
  never perform Irreversible Actions. An authorized human must handle that
  boundary.

Every Agent create, update and delete is attributable in Distribution Audit.
Successful protected human actions are recorded there as well.

## Accounting manager

Can inspect all accounting, create and edit documents, post, pay, reconcile,
configure Accounting Controls, prepare declarations, manage closing and
generate final or test FEC files. Can inspect shared Report and Declaration
definitions and create company-specific overrides through the Accounting
Framework.

Can run the safe electronic-invoice reception test and govern production
approval, startup and suspension. Provider authentication must be completed by
the legal representative during the approved production change window.

Can review every permitted Expense Batch, configure its shared ledger account,
replace selected line exceptions after preview, and advance actionable native
expense states. The distribution makes this role an Expense Manager as well,
so the Batch queue and its native expense lines stay coherent without a second
role assignment. Company boundaries remain enforced.

## Read-only accountant

Can inspect documents, evidence, journal entries and reconciliations; filter and
drill into reports; download PDF/XLSX; inspect Accounting Controls and their
current or historical results; inspect declarations and closing material; and
generate a complete posted test FEC.

This role cannot create or edit accounting, post, pay, reconcile, configure,
lock periods, mark declarations and closing complete, run reception tests,
retry failed documents or activate electronic-invoice reception.

The role can inspect Expense Batch purpose, analytics, account treatment,
mixed-payer progress, journal entries, individual expenses and receipts, but
cannot alter context or trigger Batch workflow actions.

## Expense submitter

Can create and select Batches for their permitted draft expenses, edit shared
business or analytic context and keep deliberate line exceptions. Cannot set
a shared ledger-account override or force replacement of an exception unless
also granted an Expense or Accounting Manager role.

## Technical administrator

Can inspect the evaluator or engine key, source module and technical boundary
behind Controls, Reports and Declarations. Business configuration does not
allow arbitrary Python, SQL or JavaScript execution.

## B2C roles

- **B2C Reviewer:** can inspect structured orders, events, fulfilments,
  mappings, sessions, analytics and allowed native drill-downs. Cannot change
  records or inspect restricted raw provider payloads.
- **B2C Operator:** can verify or reject SKU mappings and evidence links,
  refresh or review monthly sessions, and lock an approved session.
- **B2C Manager:** also configures channels, unlocks sessions with an audit
  note and manages B2C access.
- **Restricted Provider Evidence:** separately grants access to raw provider
  payloads that may contain personal data. Grant it only for a documented
  investigation.

All B2C roles are company-scoped. They do not grant permission to post,
reconcile or modify the native Accounting records referenced by a B2C link.

## Company access

The company switcher controls the active company context. A user sees only
companies granted on their user record. The highlighted company is where new
accounting records are created; selecting several companies is for combined
reading and does not turn a write into a cross-company operation.

With one company selected, the top navigation uses its configured interface
color. A colored dot identifies each company inside the switcher. With several
companies selected, the navigation uses Odoo's neutral default theme and `+N`
shows how many additional companies are selected for viewing.
Administrators configure the color from **Settings > Users & Companies >
Companies**; leaving it empty assigns a stable automatic color. This interface
color does not change financial statements or external documents.

Interactive Accounting reports have a visible **Companies** selector. Summary
statements combine same-currency companies and show each company's
contribution. Detailed ledgers keep company-specific rows. FEC, French tax and
closing packages are always generated one company at a time. Different company
currencies must also be reported separately.

Changing the global company selector never grants access: Allowed Companies on
the user record remains authoritative.

For a multi-company expense user, an administrator enables **Expenses in all
allowed companies** on the user record. Odoo keeps one employee profile per
legal company and selects the right profile from the highlighted company;
contracts, payroll, approvals and accounting are never merged.

Roles and responsible users are configured; personal names are not embedded in the product.
