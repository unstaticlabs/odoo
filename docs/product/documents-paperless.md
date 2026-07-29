# Documents backed by Paperless-ngx

## Product outcome

Documents is the native Odoo workspace for ordinary enterprise archive work.
Users upload without selecting folders, search OCR and metadata, preview or
download originals, and relate one archive item to any number of authorized
business records. Advanced bulk work remains in Paperless.

Odoo is authoritative for company, business relationships, confidentiality,
review state, accounting meaning, and user authorization. Paperless is
authoritative for received files, versions, checksums, OCR, previews, tags,
correspondents, document types, archive matching, and search. Relational Odoo
caches are keyed by stable Paperless IDs. Odoo writes archive metadata through
the supported API and immediately reads it back; synchronization detects
missing items and repairs drift.

## Daily workflow

The top-level **Documents** app opens on recent items and provides **Needs
review**, **Recently added**, **Accounting**, **Contracts & legal**,
**Banking**, **Tax & reporting**, restricted **HR**, and **All documents**
smart views. Views use stable metadata IDs and Odoo business rules instead of
translated-name matching or folders. Managers maintain shared views; each user
can save private filters.

One search box covers Paperless OCR, titles, IDs, and metadata. Quick filters
cover company, type, correspondent, linked status, and common tags. Date,
source, confidentiality, review, and business-link filters remain under
**More filters**. Paperless results are intersected with Odoo record rules
before any title, tag, thumbnail, or identity is returned.

Supported bills, invoices, journal entries, expenses, partners, companies,
projects, tasks, and employees expose:

- **Archive** for current relationships;
- **Find / upload** for searching and linking an existing archive item or
  uploading in record context.

Odoo uploads exist as visible operations. A durable relationship is created
only after Paperless reports successful processing. An identical SHA-256
checksum reuses the existing document. Removing a relationship never trashes or
deletes the Paperless original.

Cards show the title, date, correspondent, type, colored Paperless tags, and a
relevant Odoo link. Healthy technical state stays out of the daily interface.
The detail drawer places preview, classification, and linked records first.
**Download original** is primary; searchable PDF, upload new version, and
Paperless are under **More**. Checksums, archive identity, and last access check
are in manager-only technical details.

**File versions** is a compact expandable history. Current and received
original files are labelled. Each version can be previewed or downloaded.
Restoring an earlier file creates a new current Paperless version and preserves
the entire history. It never rewrites or removes the received original. Search
state is retained when a user follows a linked record and returns.

Paperless tags, correspondents, and document types are editable inside the
document detail. Their catalogs and matching rules live under
**Configuration**, together with **Smart views**, **Document register**,
**Linked records**, and **User access**. Ordinary users may create, assign, and
edit archive metadata; managers alone may delete shared metadata or maintain
shared views.

## States and review

Pending, processing, archived, duplicate, and failed operations remain
inspectable. Externally ingested items appear in Needs review until company and
confidentiality decisions are made. Missing Paperless items, unavailable
previews, authentication failures, and permission-sync failures are explicit.
Healthy permission synchronization is not displayed. A failure becomes a
blocking document banner with the detailed error and last-check time retained
in diagnostics; the underlying Odoo record remains usable.

Confidentiality is `internal`, `accounting`, `hr`, or `private`. Company access
is always explicit. Accountants see approved accounting evidence, not unrelated
internal, HR, or private material. HR content requires the HR archive role.
Paperless deep links are withheld until the user's individual Paperless identity
and document object permissions are synchronized.

External Paperless ingestion is discovered by resumable incremental
synchronization. New items without an Odoo company or business decision enter
**Needs review**. Review assigns Odoo-authoritative company,
confidentiality, evidence, and workflow state; Paperless-authoritative title,
correspondent, type, tags, OCR, and versions continue to synchronize by stable
archive identity.

Direct Paperless work uses the mapped individual account. Supported metadata
changes made there appear in Odoo after synchronization; bulk operations,
classifier supervision, workflow administration, and archive recovery remain
Paperless journeys.

## Deliberate boundaries

This capability does not replace Odoo attachments, Paperless administration,
collaborative editors, OCR, classifiers, or a legal retention decision. It does
not iframe or copy the Paperless frontend. Existing Odoo attachments are
retained when deliberately archived. Final Odoo-generated legal/accounting
outputs may have an operational Odoo copy plus an immutable Paperless archival
copy; their checksum and provenance make that deliberate duplication explicit.
