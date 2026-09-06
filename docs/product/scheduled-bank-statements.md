# Scheduled bank-statement review

## Outcome

The monthly bank checkpoint proves that Odoo contains the complete movement
population reported by the bank. Shine sends its scheduled accounting export
to a dedicated Odoo address. Odoo retains the email and every original file as
source provenance, imports the OFX into native bank statement lines, archives
the official PDF in the Paperless-backed Documents source of truth, links its
exact version to one native monthly bank statement, and makes discrepancies
actionable.

The OFX is the transaction source. The PDF is evidence and the source of the
balances the accountant confirms. The PDF is never parsed into transactions.
Certification does not imply that every movement is matched to an invoice or
payment, and it never changes an accounting lock date.

## Monthly journey

The bank journal, Accounting Overview, Accounting Hygiene and Bank Statements
are the normal entry points. Accountants do not work from mail-routing screens.
The oldest completed, uncertified month from the configured cut-over is shown
with one of these statuses:

- **Expected** — the export has not arrived; after the configured delivery day,
  the responsible accountant receives one activity.
- **Processing** — a retained source is waiting for or undergoing processing.
- **Needs attention** — source processing, identity, evidence, balance or
  continuity needs a decision.
- **Ready for review** — the source population and evidence are complete; the
  accountant confirms the bank balances and certifies.
- **Certified** — an immutable snapshot identifies the evidence and movement
  population used for the review.
- **Reopened** — an Accounting Manager recorded a reason and made correction
  possible; the period must be certified again.

Transactions can be imported while the PDF is missing. Certification remains
blocked until the PDF is accepted and its exact checksum is archived,
version-pinned, classified as reviewed accounting evidence and accessible in
Documents; balances are confirmed; the opening plus movements equals the bank
closing balance; continuity is valid; all
movements have a reviewed provider identity; and no source exception remains
open.
Unmatched or suspense movements remain visible as separate non-blocking
accounting hygiene.

## Evidence and corrections

Statement dates come from validated retained files before the email subject.
The OFX coverage dates must agree with its single calendar month and transaction
dates. An exact copy of an already accepted PDF inherits that statement's period
within the same company and bank route. Conflicting file evidence needs review.
When no file establishes the period, explicit subject dates or a French month
and year (for example `août 2026`) remain a fallback, not a file identity check.

If dates are still missing, use **Correct period** on the received export in
**Needs attention**. Enter the dates verified on the original statement and a
reason. Odoo records the correction and reassociates retained PDFs without
downloading links or reimporting transactions. The action cannot override dates
from verified files or replace accepted evidence. Original source files remain
read-only. Missing OFX data or a replacement-PDF decision can still block statement review.

Every original email, ZIP and extracted file remains downloadable with its
filename, received source and SHA-256 checksum. Forwarding the same email or
file does not add transactions. Two similar-looking transactions remain
distinct when their FITIDs differ.

A later PDF is retained as a candidate; it does not silently replace the
accepted PDF. A certified period must be reopened before another PDF can be
accepted. The reviewed replacement becomes a new version of the same Paperless
root. Certification history pins the accepted PDF checksum, Paperless root and
exact Paperless version, so a later current version cannot change earlier proof.

The **Official statement** action opens the pinned original through the Documents
authorization boundary. The Odoo attachment remains available in source
details for provenance and recovery, but it is not a substitute for a completed
Documents record. A temporary Documents outage presents **Retry Documents**;
a damaged or incomplete PDF instead presents **Replace statement PDF** and
explains that the original bank PDF must be selected. Both actions are on the
monthly statement, and the replaced source remains in the audit history.

Certified bank facts and the liquidity side of each bank entry cannot be
silently changed. Reconciliation, partner categorization and legitimate
counterpart work remain available because they do not change bank completeness.

## Roles

- Accounting users receive/process exports, inspect evidence, confirm balances
  and certify a valid checkpoint. The bridge grants their native role the
  company-scoped, read-only Documents evidence capability required to open the
  archived statement.
- Accounting Managers also configure routes, confirm the first cut-over
  baseline, decide ambiguous transaction identities and reopen certified
  checkpoints with a reason; they receive the same scoped archive-read
  capability without relying on identity-profile side effects.
- Read-only accountants can inspect allowed-company statements, evidence and
  history but cannot process, decide, certify or reopen.
- Other users have no access to the new sources, evidence or configuration.

All access remains company-scoped. The route binds exactly one company, bank
journal and source account.

## Product choices

A live Shine/PSD2 connection was considered and rejected: it adds provider
availability, credentials, cost and operational dependence without improving
the required monthly evidence checkpoint. A standalone ingestion ledger was
also rejected because it would create a second transaction truth. The selected
design extends native statements, lines, attachments, mail and activities and
reuses maintained OCA OFX import behavior.

CSV and QIF copies are retained but are not fallback transaction sources. An
expired Shine link requires using **Add missing file** on the received email to
attach a newly downloaded export archive, then retrying. This technical source
screen is a recovery path; ordinary monthly work remains on the statement.
OCR, PDF transaction extraction, PayPal and
automatic accounting reconciliation remain outside this product.
