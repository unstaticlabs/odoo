# Documents backed by Paperless-ngx

## Product outcome

**Documents** is the native Odoo workspace for ordinary archive work. Users can
drop in a file without choosing a folder, find text inside it, understand its
business context, preview or download the received file, and link one archived
document to several Odoo records. Advanced bulk work remains in Paperless.

The workspace is deliberately about documents, not integration health:

- cards show title, date, correspondent, type, useful Odoo context, and
  readable Paperless tag chips;
- **Download original** is the primary file action;
- tags can be searched, added, removed, and created beside the document;
- current file information is always visible; only earlier versions collapse;
- healthy API, checksum, permission, and synchronization details stay hidden;
- an exception is shown only when the user can act on it;
- the former **Recent uploads** operation log is not part of the workspace.

An active upload remains visible while it is pending or processing. Success
opens the archived document and gives a short confirmation. Duplicate, failed,
or ambiguous work stays actionable in **Needs review** or diagnostics rather
than being described as archived.

## Authority boundary

Odoo owns legal company, confidentiality, review state, accounting meaning,
business relationships, and user authorization. Paperless owns received files,
derivatives, OCR, previews, file versions, checksums, archive tags,
correspondents, document types, matching behavior, Saved Views, and archive
search.

Odoo keeps relational caches keyed by stable Paperless IDs. A user change to
archive metadata is written through Paperless REST API v10 and then read back;
there is no second editable source of truth. Odoo never stores the Paperless
binary by default, exposes a service credential, or gives the browser a
protected Paperless file URL.

The detailed integration contract is in
[`documents-paperless-architecture.md`](documents-paperless-architecture.md).

## Find and navigate

The main search accepts ordinary OCR/title text and structured suggestions such
as tag, correspondent, document type, company, date, linked record,
confidentiality, and review state. Suggestions become removable facets. Common
company/type/correspondent controls remain one click away; less common fields
are under **More filters**. A user can save a personal view without changing
company navigation.

Selected document, selected version, filters, sort, card/list layout, page, and
scroll position are represented in navigation state. Back closes the detail
and returns to the same list position; Forward reopens it. A reload or deep
link restores the selection. Session state is isolated by Odoo user so one
person's search is not inherited when another person signs in on the same
browser.

The shared navigation is **Needs review**, **Recently added**, **Accounting**,
**Contracts & legal**, **Banking**, **Tax & reporting**, restricted **HR**,
**All documents**, and **Trash**. Archive-native shared views use Paperless
Saved View identities and stable tag/type/correspondent IDs. Odoo-only company,
confidentiality, linked-record, accounting, and HR restrictions compose with
those views and remain visibly Odoo policy; they are never claimed to exist
identically in Paperless.

## Classify and automate

**Tags**, **Correspondents**, and **Document types** are top menus in the
Documents app. Users can inspect and edit Paperless matching behavior in plain
language: how a value matches, words or patterns to look for, and whether case
matters. Odoo uses the supported Paperless behavior; it does not implement a
parallel classifier or confidence score.

The tag picker is searchable and keyboard-friendly, keeps assigned values in
context, supports hierarchy and large catalogs, and permits inline tag
creation. A failed Paperless write rolls the visible state back and reports the
problem.

An archive correspondent may optionally map to an Odoo Contact:

- Paperless remains authoritative for archive matching;
- Odoo Contacts remain authoritative for operational business identity;
- exact-name suggestions require explicit acceptance and can be rejected;
- archive-only correspondents remain valid and do not pollute Contacts;
- inaccessible Contact mappings are hidden from users outside that company;
- mapping never links a document or grants access.

## Business records

Supported bills, invoices, journal entries, expenses, partners, companies,
projects, tasks, and employees expose one Documents smart button:

- with no linked archive items it is **Upload**;
- with links it is **N Documents**.

The button opens the full Documents workspace in record context. Existing links
start with a removable **Linked record** facet. Removing it lets the user search
and **Link to this record**; upload remains available in the same context.
There is no competing Archive button. Removing a relationship never trashes or
deletes the archived root.

For a mapped Contact, the same workspace composes archive correspondent mapping
and explicit Odoo relationships without duplicating results.

## Versions, Trash, and review

The current file is permanently visible under **File versions**, with label,
date, submitting user, preview, and download. **Earlier versions (N)** contains
the history. **Current** and **Received original** are distinct badges.
Restoring an earlier file creates a new current Paperless version and preserves
every prior version; it never overwrites the received original.

Trashed Paperless documents disappear from ordinary search but retain their
stable Odoo relationships. **Trash** shows them as **In Trash**, suppresses
normal edit/download actions, and offers authorized Restore. Restore returns
the same root and relationships. Permanent deletion is a separate
administrator and retention decision.

External Paperless ingestion is found by resumable synchronization. Items
without an Odoo company, confidentiality, or business decision enter **Needs
review**. Missing roots, processing failure, permission failure, duplicate
ambiguity, and unavailable previews use concise actionable states. The Odoo
business record remains usable during every Paperless outage.

## Permissions

Company access is explicit. Accountants see approved accounting evidence, not
unrelated internal, HR, or private material. HR files require the HR archive
role. Search results and direct routes apply Odoo authorization before
returning a title, tag, thumbnail, preview, version, or byte.

Direct Paperless work uses an individually mapped Paperless identity. Deep
links are withheld until that identity and the document's Paperless
object-level permissions are synchronized. A tag, correspondent, saved view, or
matching rule never grants business access.

## Deliberate boundaries

This capability does not replace Odoo attachments, Paperless administration,
collaborative editors, OCR, classifiers, or retention judgment. It does not
iframe or copy the Paperless frontend. Existing Odoo attachments remain when
deliberately archived. Final Odoo-generated legal or accounting outputs may
have an operational Odoo copy plus a Paperless archival copy; checksum, source,
and relationship make that deliberate duplication explicit.
