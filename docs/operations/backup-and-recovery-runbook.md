# Backup and recovery runbook

## Recovery objective

USL must be able to restore a coherent Odoo service containing the database, documents and required configuration to an understood point in time.

A backup is not trusted until restoration has been tested.

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
