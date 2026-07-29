# Documents backed by Paperless-ngx

## Product outcome

Documents is the native Odoo workspace for ordinary enterprise archive work.
Users upload without selecting folders, search OCR and metadata, preview or
download originals, and relate one archive item to any number of authorized
business records. Advanced bulk work remains in Paperless.

Odoo is authoritative for company, business relationships, confidentiality,
review state, accounting meaning, and user authorization. Paperless is
authoritative for received files, versions, checksums, OCR, previews, archive
metadata, and search. Mirrored metadata in `usl.document` is a cache identified
by the stable Paperless document ID; synchronization detects missing items and
repairs drift.

## Daily workflow

The top-level **Documents** app opens on recent items and provides Needs
attention, accounting evidence, contracts/legal, restricted HR, and all
accessible workspaces. OCR search is executed by Paperless and intersected with
Odoo record rules before any title, snippet, thumbnail, or identity is returned.

Supported bills, invoices, journal entries, expenses, partners, companies,
projects, tasks, and employees expose:

- **Archive** for current relationships;
- **Find / upload** for searching and linking an existing archive item or
  uploading in record context.

Odoo uploads exist as visible operations. A durable relationship is created
only after Paperless reports successful processing. An identical SHA-256
checksum reuses the existing document. Removing a relationship never trashes or
deletes the Paperless original.

## States and review

Pending, processing, archived, duplicate, and failed operations remain
inspectable. Externally ingested items appear in Needs attention until company
and confidentiality decisions are made. Missing Paperless items, unavailable
previews, authentication failures, and permission-sync failures are explicit;
the underlying Odoo record remains usable.

Confidentiality is `internal`, `accounting`, `hr`, or `private`. Company access
is always explicit. Accountants see approved accounting evidence, not unrelated
internal, HR, or private material. HR content requires the HR archive role.
Paperless deep links are withheld until the user's individual Paperless identity
and document object permissions are synchronized.

## Deliberate boundaries

This capability does not replace Odoo attachments, Paperless administration,
collaborative editors, OCR, classifiers, or a legal retention decision. It does
not iframe or copy the Paperless frontend. Existing Odoo attachments are
retained when deliberately archived. Final Odoo-generated legal/accounting
outputs may have an operational Odoo copy plus an immutable Paperless archival
copy; their checksum and provenance make that deliberate duplication explicit.

