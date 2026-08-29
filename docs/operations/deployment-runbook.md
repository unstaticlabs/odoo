# Deployment runbook

## Current boundary

During `migration-transition`, the governed one-off cutover remains the only
production-admission path. This repository does not enable a Komodo schedule or
change the running environment. After admission, the permanent authority is
[Post-migration continuous operations](continuous-releases.md).

## Permanent release path

An operator or schedule may select a candidate only by explicit validated
`usl-distribution-release/v3` contract before 03:45 Europe/Paris. At 04:00 the
controller validates identities and window, drains queues, pauses writers,
creates the coordinated `usl-production-cohort/v1`, restores every unit into
fresh isolated volumes, rehearses the planned upgrade and runs affected
qualification.

Only then does the controller record that mutation started and enter the
supervised `upgrade-production` hook. That hook appends the candidate pins,
Resource Syncs Komodo, DeployStacks/readbacks the exact commit, and applies only
the planned Odoo `-u` set while writers remain paused. The supervised `admit`
hook validates service, data and accounting controls, appends and reads back
the admitted GitOps identity, then the controller may reopen writers and record
`usl-deployment-run/v1`.

When no valid candidate exists at cutoff, the same run becomes `backup_only`:
it drains, snapshots, independently restores, verifies, reopens and records,
with release-only stages explicitly skipped.

## Admission controls

Before reopening, prove at minimum:

- exact release, image, source, OCA, module-version and action-risk identities;
- database/filestore and Paperless cohort checksums and fresh restore evidence;
- authentication, company boundaries, governed permissions and attachments;
- balanced and immutable posted entries, lock dates, journal sequences,
  reconciliations, analytic/currency/tax semantics and evidence;
- representative reports, FEC, queues, scheduled work and integrations;
- the affected clean install, representative upgrade and identical repeated
  upgrade;
- `USL_EINVOICE_LIVE_ENABLED=0` and `USL_EREPORTING_LIVE_ENABLED=0` in every
  rehearsal, restore, recovery and non-production runtime.

## Failure decisions

Before mutation, failure records a safe deferral/failure and reopens without
changing production data. After mutation and before candidate writers reopen,
the controller automatically restores the previous coordinated cohort and
recovery pins, verifies them and only then reopens. If this recovery fails,
writers stay paused for an incident decision.

Once candidate writers reopen, automatic rollback is forbidden because new
business writes may exist. A later failure is an incident requiring a human
decision. Reaching 07:00 before mutation defers; reaching it after mutation
does not authorize reopening an uncertain state.
