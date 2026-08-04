# Documents and Paperless architecture

## System shape

Odoo and Paperless are independently deployable applications with independent
PostgreSQL databases and storage. Odoo never mounts Paperless media; Paperless
never mounts the Odoo filestore. Restarting, upgrading, or restoring Paperless
does not make Odoo unavailable.

The `usl_documents` add-on extends Odoo through supported models, controllers,
record rules, views, and one OWL client action. No upstream Odoo core file and
no Paperless frontend code is copied or patched.

```text
Pocket ID
  -> Odoo confidential OIDC client
  -> Paperless confidential OIDC client

Odoo browser -> Odoo record rules and Documents controllers
      -> Odoo business/link/cache models
      -> server-only Paperless REST API v10 client
          -> Paperless document, task, metadata, version, Saved View,
             permission, preview, download, and Trash endpoints
```

## Main contracts

`usl.document` is a synchronized cache of a stable Paperless root.
`usl.document.version` mirrors version identity and integrity metadata.
`usl.document.link` owns generic Odoo business relationships and pins the
specific Paperless file-version identity that supported the record when linked.
`usl.document.operation` records asynchronous upload/version work, including
persistent actionable failures and idempotent retry lineage.

`usl.paperless.tag`, `usl.paperless.correspondent`, and
`usl.paperless.document.type` are Paperless-ID-keyed catalogs.
`usl.document.smart.view` stores shared/archive identities and personal Odoo
filters. `usl.document.quick.filter` places manager-configured one-click
controls on shared Smart Views, while its required `ir_filter_id` is the
authoritative native Odoo domain, grouping, and ordering definition.
`usl.paperless.user.mapping` maps an Odoo user to one individual Paperless
user; no credential is sent to the client. In an SSO environment the mapping
also references the same Odoo-governed `(issuer, subject)` identity used for
Pocket login. New or changed mappings remain pending until **Verify identity**
confirms the remote Paperless user ID/username and the active Pocket link.
Only verified, currently safe mappings participate in document-object grants
or receive Paperless deep links.

Odoo and Paperless have separate Pocket client IDs, secrets, and callbacks.
Pocket proves identity but does not supply Odoo companies, Documents roles, or
Paperless permissions. Odoo profiles assign exact Documents groups, and the
server-side permission synchronizer writes the resulting per-document grants
to the mapped numeric Paperless user. Disabling the Odoo user or Pocket link
revokes those grants fail-closed. Paperless group synchronization is disabled.
Paperless's documented social-account default group grants only the minimal
model capabilities needed to load its UI, personal settings, and shared
catalogs. It never grants a document object. A pinned, idempotent initializer
creates that local Paperless group and reconciles existing Pocket accounts.
The non-human Paperless API account remains separate from every interactive
identity.

Local QA retains explicit `username/admin` accounts for repeatable role tests.
Those mappings carry a QA-only marker that is accepted only when the Odoo
process has `USL_DEPLOYMENT_ENV=qa`; pre-production cannot enable that path.

Synchronized tags, correspondents, document types, and archive-native shared
views use Paperless's supported unowned object form. This mirrors Odoo's shared
classification catalogs. Paperless model-level read permission makes the
catalog usable, while independently synchronized document-object permissions
still determine which titles, metadata associations, previews, and files an
identity can see. Personal Saved Views retain their individual owner.

The optional `partner_id` on a correspondent maps archive identity to
`res.partner`. It is not a document relationship. The normal UI uses a
user-aware computed relation so an inaccessible cross-company Contact is not
displayed. Matching behavior and archive name still belong to Paperless.

## Read path

1. Odoo determines the user's companies and confidentiality roles.
2. OCR/free-text, archive-ID, archive-metadata, and supported custom-field
   search execute in Paperless.
3. Stable Paperless IDs are intersected with Odoo record rules and structured
   business filters.
4. Only authorized cache records are serialized.
5. Thumbnail, preview, original, derivative, and version routes authorize the
   Odoo root and requested version again before server-side proxying.

Knowing a Paperless or Odoo ID is therefore insufficient to obtain metadata or
bytes. Shared metadata catalogs may be visible for classification, but they do
not disclose which restricted documents use them.

The client action mounts Odoo's supported `WithSearch`, `SearchModel`, and
`SearchBar` components against the `usl.document` search view. Native facets,
date filters, custom domains, grouping, and `ir.filters` favorites therefore
use the same mechanism as ordinary Odoo views. Smart-View shortcut chips create
normal SearchModel filters/groupings and restore the saved Odoo ordering.
Managers capture them from the active native search state; the add-on validates
their domain, context, order fields, and shared Smart View scope server-side.
The shortcut configuration form exposes synchronized proxy fields for the
native filter domain, grouping, and ordering. Writes update the same
`ir.filters` record atomically, including the saved-search name; no second
filter definition is stored on the shortcut model.
The Smart View configuration action supplies `default_scope=shared`; creation
resolves that context server-side because readonly form defaults are not
guaranteed to be included in an Odoo create payload. Every new shared view gets
a generated stable key before it is exposed by `accessible_views()`. Its
**Open Documents** action passes that key as `initial_workspace`, so the client
action opens the newly created definition directly.
Top tag chips update one SearchModel facet whose stable-ID `in` condition means
“any selected tag”; the search bar and the chips therefore always describe the
same query. Reusable shortcuts may use synchronized Paperless tag,
correspondent, or document-type IDs, but remain optional Odoo presentation
controls. The enclosing
archive-native Smart View—not a transient shortcut state—is the definition
synchronized to a Paperless Saved View. Remote OCR and custom-field conditions
are resolved once before Odoo runs count and page queries, avoiding duplicate
Paperless requests or inconsistent pagination.

`all_text` is a virtual search field. It calls Paperless's supported full-text
`query` contract for OCR and archive metadata, then adds only Odoo labels that
the current user can already read. The combined stable IDs are passed back
through normal `usl.document` record rules before names, counts, or snippets
are serialized. When no explicit order is selected, the Paperless relevance
order is preserved. Explicit compact-list ordering is accepted only through a
server allowlist of synchronized stored fields.

## Write path

Uploads calculate SHA-256 locally, search both current and historical version
checksums, and reuse an accessible root before submitting new bytes. A visible
operation is created, Paperless receives the file, and the durable Odoo link is
created only after its asynchronous task succeeds. A retry is safe across an
interrupted Odoo transaction because reconciliation imports the Paperless
commit before fixture or operation reuse. Failed operations stay visible until
the user retries or acknowledges them.

Historical Odoo binaries cross a separate migration boundary. The pilot tool
under `migration/documents_archive/` accepts only an explicit private
selection, verifies the approved dump and each filestore object against the
extracted manifest, then uses the same asynchronous Odoo-to-Paperless write
path. It records the legacy Odoo source identity in Paperless, applies an
explicit Odoo company/confidentiality/link decision, and reads the received
original back to verify its SHA-256. The source filestore is mounted read-only
and never becomes a product add-ons path or shared writable storage. The pilot
does not recreate legacy Enterprise `documents.document` records, install a
migration model, or copy the selected binary into the rebuilt Odoo filestore.

The ingestion operation captures company and confidentiality at submission
time. Successful asynchronous completion applies that exact policy before
permission synchronization; it must not silently fall back to `Internal` while
Paperless is processing.

Title, date, tags, correspondent, type, catalog values, matching rules, Saved
Views, versions, and Trash mutations call a supported Paperless endpoint first
and refresh the cache from the returned/next authoritative representation. A
failed call never leaves an optimistic Odoo value presented as saved.

The document detail uses Odoo's relational autocomplete and date-input
components but does not make those browser widgets authoritative. Every
individual field mutation is a write-through request followed by an
authoritative document refresh. Contact-backed correspondent creation first
checks Contact read access, resolves protected mappings without revealing
hidden-company identities, returns to the caller's environment, and then
rechecks ordinary correspondent access before reuse or creation.

Paperless metadata objects support one `match` expression and one algorithm
(`None`, `Any`, `All`, `Exact`, `Regex`, `Fuzzy`, or `Auto`). The Odoo
`rule_lines` field is a presentation adapter: Any/All lines compile to the one
supported expression and are read back from it. `Auto` delegates entirely to
Paperless's local neural classifier and periodic retraining. No workflow-based
OCR rules are layered on top because ingestion workflows may run before OCR,
and no Odoo classifier state is introduced.

Odoo-only company, confidentiality, accounting-evidence, review, and link
changes never pretend to be Paperless metadata. Permission changes are applied
to the actual Paperless document object, not inferred from tag or
correspondent permissions. User company/role/activation changes and direct
identity mapping changes compute the before/after visible document set and
resynchronize every affected root. A revocation failure aborts the Odoo access
change instead of leaving a wider Paperless permission behind.

## Synchronization and drift

Incremental synchronization stores a page/timestamp checkpoint before each
bounded run and resumes after interruption. Full reconciliation refreshes
catalogs, custom-field definitions, documents, versions, Saved Views, and
Trash; it detects missing roots without deleting relationships. A root that
was previously in Trash and then disappears from both active and Trash APIs is
retained as a permanent-deletion tombstone. Stable Paperless IDs preserve links
through metadata renames and file-version changes.

Odoo records actor and timestamp when it initiates Trash. Direct Paperless
Trash reconciliation records the API's `deleted_at`; Paperless 3.0.4 does not
return the actor through its supported Trash or history contracts, so Odoo
stores an explicit “actor not provided” source label instead of fabricating
attribution.

The cross-system integrity manifest separates those deliberate tombstones from
live roots. They are counted and reported for audit, but are not treated as
missing documents, permission failures, or checksum failures.

Paperless 3.0.4 with REST API v10 is the qualified contract. The client rejects
another API version or unsupported server major with a clear diagnostic. Tests
cover response drift, pagination, retries, historical-version duplicates,
permissions, and Trash restoration.

## Alternatives considered

Standard `ir.attachment` would offer native UI but duplicate every archive
binary and does not provide Paperless OCR, archive versions, or search
authority. A second OCA DMS layer would introduce another storage and
classification authority between the mandated systems. An iframe would keep
Paperless implementation effort low but would not provide Odoo authorization,
business context, or native navigation.

The selected native client action plus relational cache/link boundary keeps
Community Odoo upgradeable, leaves document reality in Paperless, and makes
ordinary work available without a second login.

For authentication, proxy-supplied `Remote-User` was rejected because a header
mistake could bypass login, and one shared OIDC client was rejected because its
redirects and secrets would couple two independently recoverable
applications. Paperless's supported django-allauth OpenID Connect provider
with a second Pocket client was selected.
