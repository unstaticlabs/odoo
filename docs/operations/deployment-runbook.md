# Deployment runbook

## Objective

Deploy an approved Odoo release without losing data, weakening controls or leaving the service in an ambiguous state.

For the exact USL distribution build, reconstruction, identity and local
qualification command, follow [Pre-production release](preproduction-release.md).
For the final freeze, portable sanitized assets, external-Pocket staging and
fingerprint-confirmed admission, follow
[Portable production migration candidate](portable-production-migration.md).
Track feature integration, owner assignments, infrastructure inputs, service
activation and go/no-go evidence in the
[Production cut-over readiness register](production-cutover-readiness.md).

## Before deployment

Confirm:

- the release and scope are approved;
- required checks and accounting gates pass;
- a recent recoverable backup exists;
- database and document storage are covered together;
- expected data changes are documented and rehearsed;
- external integrations and scheduled actions are understood;
- a rollback point and decision owner are identified;
- users know about material downtime or behaviour changes.

The future automated deployment flow must stop Odoo writers and take a
verified quiesced checkpoint with `scripts/odoo-backup create --mode quiesced`
before applying an upgrade. The command is defined in the
[backup and recovery runbook](backup-and-recovery-runbook.md); this runbook
does not authorize running it manually against production during deployment.

## Deployment

During deployment:

- prevent competing writes where required;
- record the exact release being deployed;
- apply only approved changes;
- keep external side effects controlled;
- stop on an unexplained critical error;
- retain logs and evidence needed to understand the outcome.

## Validation

Before normal use resumes, verify:

- users can authenticate with expected permissions;
- each company opens in the correct context;
- core records are readable;
- attachments are accessible;
- scheduled work is controlled;
- Odoo worker, database-pool, memory and request-recycling budgets match the
  approved [product performance policy](product-performance.md);
- critical integrations are healthy or visibly paused;
- accounting entries remain balanced;
- key reports and control totals remain consistent;
- no unexpected migration warning remains unresolved.

## Completion

A deployment is complete only when:

- validation passes;
- service status is communicated;
- evidence is recorded;
- unresolved non-critical issues have owners;
- rollback is no longer the recommended action.
