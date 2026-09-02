# Shine scheduled bank-export runbook

## Safe rollout order

Keep the configuration paused until cut-over is proven:

1. deploy and upgrade `usl_accounting`, `usl_documents` and
   `usl_documents_accounting`; Paperless archival is required for the official
   bank statement;
2. create one **Bank Statement Email Setup** for the company and Shine bank
   journal under **Configuration → Accounting**, immediately after **Bank
   Matching Rules**;
3. enter the exact bank account, `hello@shine.fr`,
   `accounting.files.shine.fr`, the responsible accountant, start month and
   expected delivery day (default 5);
4. choose an alias in the configured Odoo mail domain and configure the normal
   inbound alias/MX gateway to deliver that address to Odoo;
5. mount the first private OFX read-only, run cut-over preview, review its
   private JSON report, apply, and repeat preview until `candidate_count` is 0;
6. send a synthetic email with scrubbed OFX/PDF fixtures to the alias and prove
   source retention, company/journal mapping, import and duplicate delivery;
7. set the displayed **Send bank exports to** address as Shine's scheduled
   accounting-export recipient, then enable **Receive and process emails**.

Do not place real exports, reports containing line IDs, or signed Shine URLs in
Git, tickets or normal logs. Follow
[`migration/bank_statement_ingestion/README.md`](../../migration/bank_statement_ingestion/README.md)
for adoption variables. The migration command fails closed on missing,
duplicate or conflicting identities and never uses label-based matching.

## Normal operation

At the start of each month, check the Shine journal or Accounting Overview. A
successful source produces one monthly statement. Confirm that its official
statement is **Available** in Documents, then open the version-specific
original through the **Official statement** action. Choose **Confirm bank balances** and compare the
displayed period, opening and closing values with that PDF. Resolve any exact
difference, continuity or Documents issue, then certify.

Odoo processes a configured bank email as soon as it has retained the message
and attachments. A ten-minute recovery job picks up any source left in the
received state after an interrupted mail transaction. The daily job updates
expected-period activities. Pausing a configuration stops processing; it does
not delete sources or change accounting.

Every retained attachment receives a terminal disposition. Odoo imports OFX
transactions, archives official PDFs, recognizes exact duplicates, and retains
supplemental CSV/QIF or unrelated files as intentionally ignored with a reason.
A file that cannot be handled is retained as failed with recovery guidance; it
must never remain indefinitely pending.

Shine may identify a French account either with the complete IBAN or with its
OFX bank code, branch code and account-number components. Odoo accepts the
component form only when all three exactly match the configured IBAN. Imported
transaction provenance always records the complete configured IBAN.

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
- **Missing PDF:** transactions remain imported. From the monthly statement,
  choose **Add official PDF** and select the original PDF downloaded from the
  bank. Odoo retains it with the received email and saves it in Documents;
  certification remains blocked until Documents verifies the exact version.
- **Unsupported file:** Odoo retains it unchanged and marks it intentionally
  ignored because it is not an automated import input. The recorded reason
  remains visible with the source file.
- **Balance difference:** verify PDF balances and period first, then inspect
  missing/duplicate movements and source exceptions. Never add an unexplained
  balancing line.
- **Replacement PDF:** retain both versions. Reopen first if the statement is
  certified, accept the reviewed replacement, then certify again.
- **Documents temporarily unavailable:** choose **Retry Documents**. Odoo
  resubmits the exact retained PDF; retrying does not create another accounting
  transaction or discard the original email.
- **Damaged or incomplete PDF:** retry cannot repair the file. Choose **Replace
  statement PDF** on the monthly statement and select the original PDF
  downloaded from the bank. Odoo keeps the damaged copy in the audit history,
  makes the replacement the official statement, and saves it in Documents.
- **Documents classification or access issue:** follow the exact statement
  message (for example review the document as Banking evidence or synchronize
  access), correct that property in Documents, then choose **Retry Documents**.
- **Archived original unavailable:** restore the same Paperless root/version or
  repair its object permissions. An earlier certification snapshot remains in
  history, but the period shows **Needs attention** until the exact original is
  available again.

## Monitoring and rollback

Monitor overdue journal activities, received exports in **Needs attention** or
**Import failed**, open statement exceptions, and Documents archive failures or
unavailable originals. Investigate with the statement, its Documents link and
record history; application logs intentionally omit file contents and signed
query strings.

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
