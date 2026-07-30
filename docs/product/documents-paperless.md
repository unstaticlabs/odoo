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
than being described as archived. Failed uploads survive a reload and offer
**Choose file to retry** or **Dismiss**; retrying the same content remains
checksum-idempotent.

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

The workspace uses Odoo's native search model and SearchBar rather than a
Documents-only filter form. Ordinary text can target OCR/document content,
title, tags, correspondent, type, company, source, privacy, review state,
availability, mapped Contact, employee, archive ID, or Paperless custom-field
values. Chosen suggestions become normal removable facets. The dropdown
provides the familiar **Filters**, **Group By**, and **Favorites** columns,
including date ranges, custom domains, personal saved searches, and practical
defaults such as My uploads, Needs review, Linked/Not linked, Accounting, HR,
Company, Correspondent, Type, Employee, Privacy, and month.

Immediately below it, each Smart View may expose a small manager-configured set
of one-click filters or groupings. The most-used accessible Paperless tags are
shown next as direct chips. These shortcuts compose with every native search
facet; they never replace or hide the active query. Selecting several tag
chips creates one native facet matching any selected tag, rather than requiring
every tag simultaneously. Managers maintain reusable shortcuts under
**Configuration > One-click shortcuts** and choose the Smart Views where each
one appears.

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

Shared archive-native views are globally visible Saved View objects in
Paperless. Paperless's sidebar is a per-user preference, so a direct user may
favorite one without changing the shared definition. Personal Paperless Saved
Views and personal Odoo favorites remain separate and private to their owner.

## Classify and automate

**Tags**, **Correspondents**, and **Document types** are top menus in the
Documents app. Users can inspect and edit Paperless matching behavior in plain
language: how a value matches, words or patterns to look for, and whether case
matters. Odoo uses the supported Paperless behavior; it does not implement a
parallel classifier or confidence score.

Paperless exposes one matching expression and one algorithm per tag,
correspondent, or document type. Odoo does not invent a second rule engine.
For **Any word** and **All words**, the form presents that expression as one
word or phrase per line so several alternatives or requirements are practical
to edit. Exact, regular-expression, and fuzzy modes retain their full
expression. **Learn automatically** selects Paperless's local neural
classifier: users teach it by correcting reviewed, non-inbox examples, and
Paperless retrains periodically. It is probabilistic and local, but it does not
create an inspectable list of heuristic rules that either application could
truthfully display.

Each catalog row and form shows the number of non-Trash documents the current
user may access and opens those documents with a removable native facet.

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
every prior version; it never overwrites the received original. A relationship
to a business record stores the exact supporting file-version identity, so a
later replacement does not silently change the evidence for an older business
decision.

Trashed Paperless documents disappear from ordinary search but retain their
stable Odoo relationships. **Trash** shows them as **In Trash**, suppresses
normal edit/download actions, and offers authorized Restore. Restore returns
the same root and relationships. Permanent deletion is a separate
administrator and retention decision: it requires a reason and approval,
cannot bypass a hold, active Odoo relationship, or unexpired retention window,
and leaves an auditable Odoo tombstone rather than converting the item into an
unexplained missing reference.

When Trash is initiated from Odoo, the detail shows the initiating Odoo user
and time. Paperless 3.0.4's supported Trash response provides `deleted_at` but
does not identify the deleting user; a direct Paperless action is therefore
labelled honestly as moved in Paperless with the actor unavailable, rather
than guessing an audit identity.

The deployment effectively disables Paperless's automatic Trash expiry with a
100-year delay, leaving Odoo's audited retention decision in control. A direct
archive-administrator deletion is treated as an exceptional tombstone and
diagnostic finding; it never causes Odoo to invent a replacement document.

External Paperless ingestion is found by resumable synchronization. Items
without an Odoo company, confidentiality, or business decision enter **Needs
review**. Missing roots, processing failure, permission failure, duplicate
ambiguity, and unavailable previews use concise actionable states. The Odoo
business record remains usable during every Paperless outage. An already-open
detail retains cached classification, Odoo links, and version labels while
preview/download actions are unavailable, and offers one concise retry action.

## Permissions

Company access is explicit. Accountants see approved accounting evidence, not
unrelated internal, HR, or private material. HR files require the HR archive
role. Search results and direct routes apply Odoo authorization before
returning a title, tag, thumbnail, preview, version, or byte.

Direct Paperless work uses an individually mapped Paperless identity backed by
the same immutable Pocket `(issuer, subject)` as the Odoo user. Odoo and
Paperless use separate confidential OIDC clients; Pocket proves the person but
never supplies companies or document roles. Deep links are withheld until the
Pocket link, Paperless user, and document's Paperless object permissions are
synchronized. A tag, correspondent, Pocket group, saved view, or matching rule
never grants business access. Changes to an Odoo user's active companies,
Documents roles, active/Pocket state, Pocket link, or Paperless mapping
immediately resynchronize affected permissions. An access reduction fails
closed and rolls the Odoo change back when Paperless cannot revoke the old
permission safely.

Tags, correspondents, document types, and shared Saved Views use Paperless's
supported unowned/shared form. Direct identities receive only the global model
permission needed to list those shared concepts; actual document visibility
still requires the per-document grant calculated by Odoo. Removing a document
grant therefore removes the title, metadata, preview, and bytes even though the
shared tag catalog remains usable.

## Deliberate boundaries

This capability does not replace Odoo attachments, Paperless administration,
collaborative editors, OCR, classifiers, or retention judgment. It does not
iframe or copy the Paperless frontend. Existing Odoo attachments remain when
deliberately archived. Final Odoo-generated legal or accounting outputs may
have an operational Odoo copy plus a Paperless archival copy; checksum, source,
and relationship make that deliberate duplication explicit.
