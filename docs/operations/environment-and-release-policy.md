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

## Operational evidence

Each production release records:

- version and included changes;
- approver;
- deployment time;
- validation results;
- migrations performed;
- observed warnings;
- rollback decision and outcome if used.
