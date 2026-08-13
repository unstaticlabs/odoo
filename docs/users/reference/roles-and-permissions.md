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

## Read-only accountant

Can inspect documents, evidence, journal entries and reconciliations; filter and
drill into reports; download PDF/XLSX; inspect Accounting Controls and their
current or historical results; inspect declarations and closing material; and
generate a complete posted test FEC.

This role cannot create or edit accounting, post, pay, reconcile, configure,
lock periods, mark declarations and closing complete, run reception tests,
retry failed documents or activate electronic-invoice reception.

## Technical administrator

Can inspect the evaluator or engine key, source module and technical boundary
behind Controls, Reports and Declarations. Business configuration does not
allow arbitrary Python, SQL or JavaScript execution.

## Company access

The company switcher controls the active company context. A user sees only
companies granted on their user record. The highlighted company is where new
accounting records are created; selecting several companies is for combined
reading and does not turn a write into a cross-company operation.

The top navigation uses the primary company's configured interface color. A
colored dot identifies each company in the switcher, and `+N` beside the
primary company means that additional companies are selected for viewing.
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

Roles and responsible users are configured; personal names are not embedded in the product.
