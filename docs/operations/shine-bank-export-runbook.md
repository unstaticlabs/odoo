# Shine scheduled bank-export runbook

## Safe rollout order

Keep the configuration paused until cut-over is proven:

1. deploy and upgrade `usl_accounting`; optionally upgrade
   `usl_documents_accounting` for Paperless mirroring;
2. create one **Scheduled Bank Export** for the company and Shine bank journal;
3. enter the exact bank account, `hello@shine.fr`,
   `accounting.files.shine.fr`, the responsible accountant, start month and
   expected delivery day (default 5);
4. choose an alias in the configured Odoo mail domain and configure the normal
   inbound alias/MX gateway to deliver that address to Odoo;
5. mount the first private OFX read-only, run cut-over preview, review its
   private JSON report, apply, and repeat preview until `candidate_count` is 0;
6. send a synthetic email with scrubbed OFX/PDF fixtures to the alias and prove
   source retention, company/journal mapping, import and duplicate delivery;
7. set the alias as Shine's scheduled accounting-export recipient, then enable
   **Process received exports**.

Do not place real exports, reports containing line IDs, or signed Shine URLs in
Git, tickets or normal logs. Follow
[`migration/bank_statement_ingestion/README.md`](../../migration/bank_statement_ingestion/README.md)
for adoption variables. The migration command fails closed on missing,
duplicate or conflicting identities and never uses label-based matching.

## Normal operation

At the start of each month, check the Shine journal or Accounting Overview. A
successful source produces one monthly statement. Open the official PDF,
choose **Confirm bank balances**, and compare the displayed period, opening and
closing values with the PDF. Resolve any exact difference or continuity issue,
then certify.

The two scheduled jobs process retained sources every ten minutes and update
expected-period activities daily. Pausing a configuration stops scheduled
processing; it does not delete sources or change accounting.

## Recovery

- **No export:** verify Shine scheduling, the alias address and inbound mail
  gateway. Recover the export from Shine and deliver it to the same alias.
- **Expired link:** download a fresh archive from Shine, attach it to the
  retained received-export record, and retry. Never paste a signed URL into
  chatter or logs.
- **Malformed OFX:** retain the failed original, attach a corrected bank export,
  and retry. When the retained recovery imports successfully, Odoo records the
  prior import failure as corrected while preserving both files. Retry cannot
  duplicate already accepted FITIDs.
- **Missing PDF:** transactions remain imported. Attach or redeliver the
  official statement later; certification remains blocked meanwhile.
- **Unsupported file:** inspect the retained original. CSV/QIF copies are
  expected alternatives and need no action when OFX is present; an actually
  unsupported attachment remains an explicit exception.
- **Balance difference:** verify PDF balances and period first, then inspect
  missing/duplicate movements and source exceptions. Never add an unexplained
  balancing line.
- **Replacement PDF:** retain both versions. Reopen first if the statement is
  certified, accept the reviewed replacement, then certify again.
- **Archive failure:** Odoo evidence remains immutable and certification stays
  valid. Correct Paperless access/connectivity and retry the archive job; do
  not remove the Odoo attachment.

## Monitoring and rollback

Monitor overdue journal activities, received exports in **Needs attention** or
**Import failed**, open statement exceptions, and Paperless archive failures.
Investigate with the record history; application logs intentionally omit file
contents and signed query strings.

To stop new consequences, pause the route and disable the two bank-export
scheduled jobs if required. Preserve all received sources, statements and
history. Roll back application code only with a database/filestore-compatible
release. Do not delete provider identities or certified snapshots. If a
certified accounting correction is necessary, reopen it through the product
workflow and record the reason.

## Deployment checks

Before enabling production, prove module clean install/upgrade, OCA OFX tests,
focused ingestion/security tests, migration preview/apply/repeat, manual OCA
import and reconciliation regression, company isolation, and manager,
accountant and read-only browser journeys. Live French e-invoice and
e-reporting flags remain disabled throughout this work.
