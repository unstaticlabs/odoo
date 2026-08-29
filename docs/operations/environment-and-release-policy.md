# Environment and release policy

## Environments

The programme maintains distinct environments for:

- development;
- automated validation;
- staging and migration rehearsal;
- production.

Production data must not create external side effects from non-production environments. Sensitive production data is minimized, protected and access-controlled.

## Release gate

The repository-to-image boundary is defined in
[Production image CI boundary](production-image-ci.md). A successful merge to
`19-usl` produces the five repository-owned GHCR runtime images recorded by
`usl-distribution-release/v3`, each tagged for discovery by the full commit and
identified for deployment by its immutable digest. Deployment automation must
validate that artifact and must not rebuild source on a host.

A release is eligible for production only when:

- its intended product outcome is documented;
- relevant automated checks pass;
- the source and exact runtime action inventories have zero unclassified,
  ambiguous, changed or stale entries;
- accounting-critical changes include parity evidence;
- data transformations are rehearsed on representative data;
- backup and rollback expectations are explicit;
- permissions are reviewed when access changes;
- known differences and risks are visible;
- the reviewed merge and governed continuous-operations admission policy
  authorize the release; an incident or exception still requires its named
  human decision owner.

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

Every linked-worktree `odoo_dev` is an SSO-governed runtime. Its ignored
`.pocket-id.env`, Compose project name and four published ports form one
configuration unit. Ordinary deploy, test and QA helpers may stop Odoo, but
must restore it through that same Pocket ID overlay. `make doctor` verifies
both the running process environment and the database provider; `make
login-link` refuses to issue a link when they differ. This prevents a healthy
identity provider from masking an Odoo container restarted without its SSO
credentials.

## Operational evidence

Each production release records:

- version and included changes;
- Git commit, upstream baseline, OCA pins and patched bundle digest;
- all five qualified digest references, embedded revisions, attestations, OCA
  and action-risk identities;
- database UUID, installed module versions and coordinated cohort identity;
- approver;
- deployment time;
- validation results;
- upgrade plan and modules upgraded;
- observed warnings;
- rollback decision and outcome if used.
