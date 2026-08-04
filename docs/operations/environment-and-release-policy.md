# Environment and release policy

## Environments

The programme maintains distinct environments for:

- development;
- automated validation;
- staging and migration rehearsal;
- production.

Production data must not create external side effects from non-production environments. Sensitive production data is minimized, protected and access-controlled.

## Release gate

A release is eligible for production only when:

- its intended product outcome is documented;
- relevant automated checks pass;
- accounting-critical changes include parity evidence;
- data transformations are rehearsed on representative data;
- backup and rollback expectations are explicit;
- permissions are reviewed when access changes;
- known differences and risks are visible;
- an authorized human approves the release.

## Change discipline

- Production changes are attributable and reviewable.
- Hidden manual configuration is not an acceptable dependency.
- Environment configuration is reproducible.
- Secrets are not stored in source control or ordinary documentation.
- Emergency changes are documented and reconciled into the normal release process.

## Checkout and Compose ownership

One Compose project belongs to one checkout. Linked worktrees use a dedicated
project name, published ports and checkout-local Pocket ID environment. Host
helpers verify Docker's Compose project and working-directory labels before
they mutate a service.

Two operating models were considered. Sharing the canonical project and its
Pocket ID secrets across worktrees minimizes containers, but lets a helper
silently replace services with bind mounts from a different commit. Giving
each worktree a dedicated Compose project uses more local resources, but makes
the code, database, filestore, identity state and destructive lifecycle
unambiguous. The dedicated-project model is selected; the main checkout alone
may use the canonical default.

## Operational evidence

Each production release records:

- version and included changes;
- approver;
- deployment time;
- validation results;
- migrations performed;
- observed warnings;
- rollback decision and outcome if used.
