# Native project stock costs for reinvoiced products

CIO [#3473](https://odoo.unstaticlabs.com/odoo/project.task/3473) records the
2 November 2026 decision to include Odoo's `sale_project_stock_account` bridge.
It is already part of the clean distribution but absent from the retained
original production backup. No documented reason for that omission was found.
The earlier 19.3 catch-up deliberately preserved the installed module cohort;
this follow-up implements the explicit decision without changing that general
optional-module policy.

## What changes

For an Anglo-Saxon company, a project-linked stock delivery must not create a
second project cost for a product reinvoiced at cost or sales price: the customer
invoice already carries that cost. Odoo's native bridge supplies that behavior.
Other products and continental-accounting companies keep native delivery costs.
The stock move's company determines the result, including when the operator has
another active company. Native user permissions remain unchanged.

The signed release plan includes only this missing native integration, and only
when `usl_accounting`, `sale_project` and `project_stock_account` are already
installed. It does not install Sales, Projects or Stock on a smaller deployment.
The executor uses explicit update/init arguments, through the existing candidate
backup, upgrade, preservation, admission and recovery workflow. Once installed,
an otherwise unchanged release does not schedule the bridge again.

There is no backfill, deletion or recomputation of historical accounting or
analytic entries. Existing duplicate costs, if any, require a separately reviewed
accounting correction; installation does not repair them automatically.

## Qualification

- Release planner tests cover inclusion, all incomplete prerequisite combinations,
  an already installed bridge, legacy releases, signed-plan validation and
  rejection of an unrelated optional module using the integration reason.
- Candidate-command tests verify explicit native init and update without an
  update-all operation. Pre-existing release identity and branch policies remain.
- Native tests exercise actual deliveries, both accounting modes and all three
  reinvoicing policies. The existing regression covers a different active company.
  Removing the bridge's behavior reproduces duplicate analytic costs; restoring
  it makes these tests pass.
- A fresh clone of the retained original database is neutralized with pinned
  predecessor SQL before registry startup, on a private internal-only network,
  with no production connection or mounted live credentials. Existing full-row
  financial/history digests are captured before installation and compared after
  installation and a repeated update. No original database or filestore is
  written. Private evidence stays outside the repository.

These are focused installation/repeat checks, not a new full 19.3 migration
qualification. The old backup still emits pre-existing missing legacy-module,
view and schema warnings when opened with the current fork. Do not describe that
registry as a clean full-fork upgrade or an exact runtime-cohort pass. The existing
Feedback context-ID and PDP label differences remain separate work on #3473.

Use the candidate operations image for plan generation and validation; an older
operations image does not recognize the new reason. Keep its immutable image
reference coordinated through the normal release tooling. Production promotion
continues through existing automations.
