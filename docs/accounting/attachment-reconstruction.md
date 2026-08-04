# Accounting attachment reconstruction

Last verified: 4 August 2026

## Product contract

Accounting evidence is reconstructed with the business record it supports.
Original invoices, receipts, uploaded PDFs, structured invoice files and
chatter attachments are recreated through the target Odoo ORM. They inherit
the company and access rules of the native invoice, bill, expense, asset or
other Accounting record. Reconstruction tools and source identifiers remain
outside the normal Accounting UI.

The importer verifies the source file size and SHA-1 before writing it. It then
verifies the target attachment size and checksum, preserves the source-selected
main attachment, and creates an internal chatter note when the source file was
attached to a message. It does not recreate source email delivery or notify
followers.

Draft invoices and bills with accounting lines are regenerated as native
drafts. A source invoice or bill that only contains an uploaded file is also
regenerated as an empty native draft so the user can review the file and
continue the ordinary bill workflow. Cancelled empty records remain
review-only.

## Selected design

Two implementation approaches were compared:

1. copy the complete SaaS filestore and its `ir.attachment` rows into the
   Community database;
2. inventory the complete source filestore, then replay only files attached
   to mapped Accounting records through the target ORM.

The second approach is selected. A raw copy would retain source record IDs and
Enterprise-only owners that do not exist in the rebuilt database, bypass
native access checks, expose unrelated private files, and copy regenerable web
assets. ORM replay preserves business relationships and applies target model,
company and attachment security.

## Current verified inventory

The source dump `e1d95464d1ff…` contains:

- 2,312 binary attachment metadata rows;
- 1,754 unique referenced blobs;
- 1,774 physical files;
- 710 material Accounting attachments:
  - 669 directly attached to Accounting records;
  - 41 attached only through Accounting chatter;
- 338 groups where several metadata rows intentionally reference the same
  content-addressed blob;
- 20 physical files with no `ir.attachment` metadata.

All 1,754 referenced source blobs are readable and match their recorded SHA-1
and size. The 20 unreferenced physical files cannot be linked to a record,
company or access rule and are classified as non-blocking source-package
orphans. They are not copied into the target.

On `odoo_dev`, all 710 material source attachment identities are present once,
with no source/target checksum or size difference and no missing chatter link.
The target binary read verifies every imported file through Odoo storage, not
only database metadata.

## Commands

Keep the restored source database read-only and mounted with its filestore:

```bash
make accounting-source-restore
make accounting-dev-attachments
make accounting-attachment-audit
```

`accounting-dev-attachments` is a focused development refresh. It is
idempotent and does not rebuild the ledger. A clean full reconstruction also
uses the same importer as part of the exact and native replay stages.

The audit writes private evidence to:

```text
artifacts/accounting-compat/private/dev-attachment-replay-status.json
artifacts/accounting-compat/private/attachment-reconstruction-status.json
```

These artifacts contain source identities and must not be committed.

## Acceptance and exclusions

The audit blocks on a missing or unreadable referenced source file, a checksum
or size difference, an unmapped material Accounting record, a duplicate target
source identity, a missing target binary, or a missing expected chatter link.

The following are justified exclusions:

- regenerable web assets, view images and other technical binaries;
- files belonging only to unrelated applications;
- physical blobs with no source attachment metadata;
- URL-only expense links, because they have no filestore binary to restore.

These exclusions do not remove a file from the source backup. They only prevent
unrelated or ungoverned content from being copied into the Accounting product.
