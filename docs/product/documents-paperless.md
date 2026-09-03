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
or ambiguous work stays actionable in **Needs attention** or diagnostics rather
than being described as archived. Failed uploads survive a reload and offer
**Choose file to retry** or **Dismiss**; retrying the same content and business
classification remains composite-hash idempotent.

A user normally attaches the file on the bill, task, expense, payroll record
or other business record where it belongs. That native attachment is usable at
once. Odoo archives it in the background, adds safe business tags and defaults,
and updates the record's one **Documents** button. Paperless downtime therefore
never blocks the business transaction.

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
Documents-only filter form. Its first two suggestions state their behavior:
**Everywhere** is progressive hybrid search, while **Meaning
(Semantic)** goes directly to the local BGE-M3 vector
index. The hybrid path sends one authorization-scoped POST to Paperless's
native Tantivy index across OCR, title, correspondent, type, tags, and all
accessible custom fields together, adds authorized Odoo link labels, and shows
those exact results immediately. A clear banner remains visible while BGE-M3
adds semantic-only matches; the exact ordering is never displaced. Repeated
identical lexical searches are cached for five seconds; identical semantic
requests with the same complete authorization scope are cached for 30 seconds
so paging and quick reloads do not recompute embeddings. Already-loaded
workspace catalogs are not rebuilt or resent on each keystroke. If local
embeddings are unavailable, the exact results remain with a short warning.
Neither search path calls a generative provider. Frequent field-specific
suggestions remain available for Title, Document content, Tags,
Correspondent, Type, Company, and Date. Specialist fields such as archive
identity, source, privacy, review state, availability, mapped Contact, and
employee remain available through the native custom-filter menu without
crowding the first suggestions. Chosen suggestions become normal removable
facets. The dropdown provides the familiar **Filters**, **Group By**, and
**Favorites** columns, including date ranges, custom domains, personal saved
searches, and practical defaults such as My uploads, Ready for review,
Needs attention, Linked/Not
linked, Accounting, HR, Company, Correspondent, Type, Employee, Privacy, and
month.

Immediately below it, each Smart View may expose a small manager-configured set
of one-click filters or groupings. The most-used accessible Paperless tags are
shown next as direct chips. These shortcuts compose with every native search
facet; they never replace or hide the active query. Selecting several tag
chips creates one native facet matching any selected tag, rather than requiring
every tag simultaneously. Managers maintain reusable shortcuts under
**Configuration > One-click shortcuts** and choose the Smart Views where each
one appears. Each shortcut is a shared native Odoo saved search (`ir.filters`)
containing the same domain, grouping, and ordering that the search bar uses.
Managers can capture the current query from **Favorites > Save as one-click
shortcut**. The complete definition is also visible and editable on one
**Configuration > One-click shortcuts** form: Odoo's visual filter builder,
up to three grouping levels, up to three sort levels, placement, icon, and
sequence. The underlying `ir.filters` record remains authoritative but is no
longer exposed as a separate item that users must open. Personal Favorites
remain private.

### Optional personal Gemini

An active Pocket-mapped internal user may opt into Gemini from Paperless
**My profile** without administrator approval. Metadata suggestions and
Paperless document chat are separate switches and both start off. The user
supplies and owns their Google API key; USL encrypts it per user, never shows it
again, and never sends it to Odoo or MCP. Disable stops both features while
retaining the encrypted key; delete removes the credential and disables both.

Before opt-in, Paperless explains that the relevant document text, filename,
metadata, and prompt will leave USL for Google Gemini when the user invokes an
enabled feature. The connection test sends no document. Suggestions remain
proposals for human review, and chat has no tools or write access. Portal,
anonymous, inactive, unmapped, and service identities cannot opt in.

Gemini is not used for upload, OCR, indexing, search, synchronization, or MCP.
Those local/core paths keep working when Gemini is disabled or unavailable.
There is deliberately no Odoo chat UI.

Managers create shared navigation views under **Configuration > Smart views**.
A new view receives a stable identity, appears immediately in every authorized
user's Documents sidebar, and provides **Open Documents** for direct review.
Optional **Available in Paperless** publication writes compatible tag,
correspondent, document-type, and text criteria to a shared Paperless Saved
View. Personal searches remain ordinary Odoo Favorites rather than being
silently created from the shared configuration screen.

Selected document, selected version, filters, sort, card/list layout, page, and
scroll position are represented in navigation state. Back closes the detail
and returns to the same list position; Forward reopens it. A reload or deep
link restores the selection. Session state is isolated by Odoo user so one
person's search is not inherited when another person signs in on the same
browser.
The detail panel is an anchored overlay at every desktop and mobile width, so
opening a result never recomputes or narrows the document grid beneath it. A
modal was rejected because document detail supports deep links, Back/Forward,
preview work, and long-form metadata rather than a short interrupting task;
resizing the list was rejected because it caused disruptive layout shifts.

The primary navigation starts with **Home** and **My library**, then the useful
business scopes **Accounting**, **Projects**, **Contracts & legal**,
**Banking**, and **Tax & reporting**. **HR** remains role-restricted.
Documents managers also receive **Inbox / To classify** and **All archived**;
ordinary users do not receive these broad operational views. **Archive
search** is available to Documents users but deliberately opens empty: the
archive is queried only after the user supplies text or a facet. **Trash**
remains separate. The former top-level **Needs attention** and **Recently added**
views are retained only as inactive compatibility identities so saved URLs and
API clients do not break.

**Home** is a working set, not an archive dump. It contains only prominent
library/evidence relationships that are starred, recently opened, need review,
or were added recently. **My library** contains every accessible relationship
whose presentation role is library or evidence. Search controls let a user
include all authorized background archive material, exclude it, or show only
background material; this choice never changes authorization. A star and
recent-open state are private to the current Odoo user and are not written to
shared Paperless tags or Saved Views.

Archive-native shared views use Paperless Saved View identities and stable
tag/type/correspondent IDs. Odoo-only company, confidentiality, linked-record,
accounting, project, and HR restrictions compose with those views and remain
visibly Odoo policy; they are never claimed to exist identically in Paperless.

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

When Paperless proposes metadata for an archived document, its Odoo detail
panel shows those proposals separately from applied metadata. An authorized
editor can review and apply one suggested type, correspondent, tag, or date at
a time. Suggestions never change Odoo or Paperless data merely by being shown,
and a classifier outage does not make the archived document unavailable.

The catalog synchronizer enables **Learn automatically** for active shared
tags, correspondents, and document types that still have no explicit rule and
already occur on at least two documents. Inbox tags are excluded. This setup is
idempotent, preserves every manually configured expression, and is rerun after
archive reconstruction as well as during normal synchronization.

Odoo separately reconciles the review state of an archive root. It promotes
**Needs attention** to **Reviewed** when a mandatory evidence relationship or a
direct-record/final-output relationship proves that the owning Odoo workflow
already reviewed the business context. A complete but manually created
Documents relationship moves only to **Ready for review**. Checksum-locked
migration classifications also finish as **Reviewed** so historical evidence
does not create a duplicate approval backlog. Unlinked external intake, policy
conflicts, missing records, and permission errors remain visible for review.
Classification is reconciled immediately
after archive context or a manual business relationship is applied, after a
completed Paperless synchronization, and at the end of the Documents
migration. A twice-daily unbounded recovery sweep catches interrupted or
historical work; it is a safety net rather than the normal processing path.

Two broader alternatives were rejected. Trusting all metadata assigned by
Paperless would clear the Inbox fastest, but it cannot establish Odoo company
or record authorization and would hide genuinely unassigned files. A one-time
migration update would clean today's backlog but would regress after future
intake or a final reconstruction. The combined Paperless-learning and
authoritative-link reconciliation keeps the archive classifier reusable while
leaving Odoo's access boundary deterministic.

Each catalog row and form shows the number of non-Trash documents the current
user may access and opens those documents with a removable native facet.

The tag picker is searchable and keyboard-friendly, keeps assigned values in
context, supports hierarchy and large catalogs, and permits inline tag
creation. A failed Paperless write rolls the visible state back and reports the
problem.

The document title is the detail panel's primary heading and is editable in
place. Correspondent, document type, date, and tags follow directly without a
redundant “Classification” heading or separate edit mode. Relational values
use Odoo's autocomplete, Search More, quick-create, outside-click, and Escape
behavior. The date uses Odoo's calendar picker and the product-wide
`DD/MM/YYYY` presentation. A Contact may be selected as the source for a
correspondent, but that explicit action creates or reuses only the Paperless
correspondent and mapping.

The detail header keeps two lightweight document links in a consistent place:
**Open Preview** uses Odoo's authorized preview route, while **Open in
Paperless** uses the current user's verified individual archive identity. Both
open in a new tab and are absent when the corresponding access is unsafe or
unavailable; the Paperless action is not duplicated in the footer menu.

Company is editable inline by Documents administrators because it is Odoo-owned
business policy, not immutable archive metadata. Choices are limited to
companies currently selected in Odoo. Ordinary users see the value read-only,
and an active Odoo-record link prevents moving the document to a conflicting
company. Every accepted change immediately recalculates Paperless object
permissions; a synchronization failure becomes a blocking document state
instead of silently claiming that access is safe.

Human review is completed in the ordinary document side panel. A concise
banner explains what should be checked and gives Documents managers a direct
**Mark reviewed** action. Completion requires an available archive document,
synchronized object permissions, and an assigned legal company; unresolved
conditions remain visible and cannot be dismissed as reviewed. Technical
records retain diagnostics but are not part of the normal review journey.

Compact mode follows Odoo list conventions. Every labelled column—Document,
Date, Correspondent, Type, Company, Tags, and Status—can be sorted from its
header. Ordering, paging, layout, filters, and grouping share the same URL and
favorite state used by the card view.

An archive correspondent may optionally map to an Odoo Contact:

- Paperless remains authoritative for archive matching;
- Odoo Contacts remain authoritative for operational business identity;
- exact-name suggestions require explicit acceptance and can be rejected;
- archive-only correspondents remain valid and do not pollute Contacts;
- inaccessible Contact mappings are hidden from users outside that company;
- mapping never links a document or grants access.

## Business records

Supported bills, invoices, journal entries, payments, assets, expenses and
batches, partners, companies, projects, tasks, employees, TESE payroll,
Platform Billing, declarations and closing workspaces expose one **Documents**
smart button. It shows the archived count plus a lightweight processing or
attention state when useful.

The button opens the full Documents workspace in record context. Existing links
start with a removable **Linked record** facet. Removing it lets the user search
and **Link to this record**. Native chatter and attachment controls remain the
normal upload surface; there is no competing **Archive in Paperless** action.
Removing a relationship never trashes or deletes the archived root.

Automatic archiving normally keeps supporting attachments in the background
so routine record evidence does not flood Home. An authorized attachment menu
offers **Keep in Documents** for a native attachment whose policy is archive on
request. That action starts asynchronous archiving; the original remains on
the Odoo record and continues to open normally. Documents reuses an existing
archive root when content and classification match, otherwise creates one root
and links it back to the record. Replacing the content of that same attachment
later creates a new version rather than a second root.

The attachment card shows whether the request is queued, being sent, indexed,
or needs review. After completion, **Open in Documents** opens that exact
archive identity in the Documents app. The record smart button shows one native
state at a time—Documents, Archiving, or Needs attention—rather than stacking
several counters in a broken stat-button layout. If archive schedulers are
paused, **Keep in Documents** fails before queuing and explicitly confirms that
the original is safe on the record.

The separate document-detail action can add a background relationship to **My
library** or remove a library relationship from it. That action changes only
the Odoo presentation role; it does not archive another file. Required
evidence cannot be demoted.

Context is additive. Projects receive **Projects** and one stable
**Project · Name** tag, Platform Billing receives one stable platform tag,
expenses use the canonical **Expenses** taxonomy, and accounting, HR, payroll,
tax and closing evidence receive their relevant business type. Existing manual
tags are never silently erased. A conflicting non-empty classification enters
**Needs attention**, except when a trusted, more-specific business policy
explicitly replaces a generic type. Access can be narrowed without creating a
false conflict; a request to relax existing confidentiality still requires
review.

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
and time. Paperless 3.0.5's supported Trash response provides `deleted_at` but
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

Automatically archived documents are record-scoped: an internal user must be
able to read at least one active linked Odoo record. Project privacy,
assignees, collaborators, followers, company selection and product roles are
re-evaluated when they change. Portal uploaders keep their authorized Odoo
attachment workflow but do not receive Documents or direct Paperless access.

Canonical target finalization creates the individual Paperless social account,
links it to the existing Odoo user through that immutable subject, and applies
the exact authorized document set. The first login therefore authenticates an
already governed identity; it does not create or infer business access.

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
and relationship make that deliberate duplication explicit. A trusted
`generated_final` origin queues that archival copy as `odoo_generated`
evidence; it follows the same retry, duplicate, and permission workflow as
other authoritative archive operations.
