# Backup and recovery runbook

## Portable candidate versus admitted backup

The private portable migration candidate is restart material only before
admission. See
[Portable production migration candidate](portable-production-migration.md).
After admission, candidate reset is permanently disabled; use the coordinated
Odoo/Paperless backup procedure below. Pocket ID remains independently operated
and is never restored from an application candidate.

Before final freeze, assign and evidence the production RPO/RTO, schedule,
retention, separate failure domain, encryption/access policy, missed-backup
alerts and restore-test owner in the
[production cut-over readiness register](production-cutover-readiness.md).
Do not admit the candidate on the assumption that these will be decided later.

## Recovery objective

USL must be able to restore a coherent Odoo service containing the database, documents and required configuration to an understood point in time.

A backup is not trusted until restoration has been tested.

The qualified commit/image/database/source identity and disposable
reconstruction procedure are defined in
[Pre-production release](preproduction-release.md). Treat Odoo PostgreSQL and
filestore plus Paperless PostgreSQL, media and data as one recovery point.

## Backup scope

Backups must cover together:

- database records;
- attachment and document storage;
- required system configuration;
- installed USL modules and exact release identity;
- information needed to recover secrets through the approved secret-management process.

## Required properties

- Backups are automated, monitored and protected from ordinary application failure.
- Retention covers recent operational recovery and longer accounting/legal needs.
- Backup failures create visible alerts with an accountable owner.
- Access to backups is restricted and auditable.
- Recovery copies cannot accidentally contact production external services.

## Restoration rehearsal

Regularly restore into an isolated environment and verify:

- the service starts;
- expected companies and users exist;
- representative records and attachments open;
- accounting control totals match the recovery point;
- critical reports render consistently;
- integrations remain disabled until explicitly authorized;
- the recovery point and any data gap are clearly stated.

For the local immutable pre-production candidate, run:

```bash
scripts/preprod-release recovery-rehearsal /absolute/path/to/usl-online-dump
```

The release helper uses the existing coordinated Documents recovery mechanism
instead of inventing a second backup format. Compared with a Documents-only QA
fixture, this route also checks the reconstructed companies, enabled internal
users, accounting move/line counts, balanced posted debit/credit totals and
stored release commit/image. It is intentionally limited to the isolated
pre-production Compose project and cleans its restored project and sensitive
temporary backup after the proof.

Before individual Paperless identities are mapped, recovery acceptance may
preserve the source's exact known permission-failure set. It still rejects any
new missing document, orphaned relationship, checksum mismatch or unmirrored
Paperless record. This is recovery evidence only; the pre-production release
gate remains fail-closed until the permission-failure set is empty.

## Incident recovery

When recovery is required:

1. protect the damaged environment and evidence;
2. identify the intended recovery point and accepted data loss window;
3. restore into a controlled environment;
4. validate data, attachments, permissions and accounting controls;
5. obtain authorization before external side effects resume;
6. record the incident, decisions, lost interval and follow-up actions.

## Completion

Recovery is complete only when the restored state is validated, users understand its timestamp, external services are intentionally resumed and the incident record is complete.
