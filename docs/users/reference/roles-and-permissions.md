# Roles and Permissions

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

## Company access

The company switcher controls the active company context. A user sees only companies granted on their user record. Multi-company reports include only selected companies the user is allowed to access.

Roles and responsible users are configured; personal names are not embedded in the product.
