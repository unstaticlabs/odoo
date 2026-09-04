# Documents and Paperless architecture

## System shape

Odoo and Paperless are independently deployable applications with independent
PostgreSQL databases and storage. Odoo never mounts Paperless media; Paperless
never mounts the Odoo filestore. Restarting, upgrading, or restoring Paperless
does not make Odoo unavailable.

The `usl_documents` add-on extends Odoo through supported models, controllers,
record rules, views, and one OWL client action. No upstream Odoo core file is
patched. The Paperless distribution image carries one exact-source,
hash-guarded backend/frontend overlay for the governed semantic API and
per-user Gemini settings; it is compiled from the exact Paperless 3.0.5 source
rather than injected at runtime.

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
Pocket login. Target finalization deterministically provisions that Paperless
identity, verifies the remote numeric ID/username, upserts the mapping and
synchronizes object grants. New or manually changed mappings remain pending
until **Verify identity** confirms the same contract. Only verified, currently
safe mappings participate in document-object grants or receive Paperless deep
links. The five-minute archive reconciler also verifies that every mapped
Paperless account still exists, keeps the expected username, and remains
active. Remote identity drift fails the mapping and revokes its object grants;
the governed user reconciliation is the only path that repairs the remote
account lifecycle.

Odoo and Paperless have separate Pocket client IDs, secrets, and callbacks.
Pocket proves identity but does not supply Odoo companies, Documents roles, or
Paperless permissions. Odoo profiles assign exact Documents groups, and the
server-side permission synchronizer writes the resulting per-document grants
to the mapped numeric Paperless user. Disabling the Odoo user or Pocket link
revokes those grants fail-closed. Paperless group synchronization is disabled.
Paperless's documented social-account default group grants only the minimal
model capabilities needed to load its UI, personal settings, and shared
catalogs. It never grants a document object. A pinned, idempotent initializer
creates that local Paperless group. A separate fail-closed reconciler creates
or updates the individual account and `pocket-id` social-account link directly
from the governed immutable-subject manifest, deactivates stale governed
accounts, then lets Odoo write exact per-document grants. This avoids a
first-login race without making Pocket groups an authorization source.
The non-human Paperless API account remains separate from every interactive
identity.

Permission and ownership changes do not alter the text embedded by BGE-M3.
The distribution therefore preserves Paperless's native permission write,
Tantivy refresh, cache invalidation and document signals while reusing the
existing vector for permission-only bulk edits. Other metadata or content
changes retain Paperless's normal embedding refresh.

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
Every Smart View prepends one permanent, icon-only starred-document shortcut.
It is a normal additive `is_starred` SearchModel facet, so it filters the
currently active search and never replaces its company, privacy, text, or
relationship constraints.

`all_text` is a virtual search field. Odoo first derives the stable Paperless
root IDs permitted by current Documents record rules and allowed companies.
It sends that complete scope in one POST request to the distribution's bounded
lexical endpoint. Paperless intersects its own object permissions, applies the
result as one Tantivy term-set filter, and searches title/OCR, archive metadata,
and every indexed custom-field name/value in one native query. Plain text keeps
ordinary French and English punctuation out of the advanced query language.
An empty scope makes no Paperless request. Odoo adds only local relationship
labels the current user can already read and rechecks every returned root ID.

The browser publishes that exact lexical result immediately, with a visible
semantic-refinement banner, then sends the same mandatory scope to the
authenticated semantic endpoint. Paperless intersects permissions again before
querying its native vector index. The final ranking always retains the complete
lexical order and appends only semantic-only IDs, so references, VAT numbers,
amounts, dates, codes, and ordinary word matches never move behind approximate
matches. A five-second, scope-sensitive process cache removes the duplicate
lexical network call made by the refinement pass. The client also keeps the
already-loaded workspace catalogs and asks the server to omit their invariant
payload during later keystroke searches. An independent 30-second semantic
cache includes the Paperless URL, integration-token hash, full authorized
root scope, query or source document, limit, and facets. It therefore reuses
only an identical authorized request and never substitutes a stale broader
scope after permissions change.

Workspace role and linked-record filters batch target visibility through
Odoo's native record-rule access domain once per linked model. They do not
cache authorization decisions or check hundreds of target records one by one;
inactive but readable business records remain valid relationship targets.

**Everywhere** is the progressive hybrid path. **Meaning (Semantic)**
is an explicit vector-only path. Exact-only
and meaning-only modes remain bounded advanced options. If the index or Ollama
is unavailable, hybrid search keeps the already-visible lexical result plus one
structured warning; a meaning-only request fails honestly. Search never invokes
a generative model. When no explicit list order is selected, this exact-first
relevance is preserved.
Once vector retrieval completes, each scored result carries its bounded cosine
similarity in that workspace response. The client renders the clamped, rounded
percentage as a color-coded semantic-similarity chip and offers explicit
semantic-closeness ordering. The score is query-dependent transient metadata;
persisting it on `usl.document` was rejected because a later query would make
that stored value stale and misleading. Semantic ordering was also kept
opt-in so the default hybrid path continues to put exact lexical matches first.
Explicit compact-list ordering is accepted only through a server allowlist of
synchronized stored fields.

The read-only Documents MCP facade exposes this same retrieval boundary rather
than generic model calls. It lists only accessible shared and caller-owned
Smart Views, and accepts a returned view ID as an additive candidate scope for
exact, hybrid, semantic, or similar-document retrieval. Stored structured
filters are replayed server-side; explicit MCP filters may narrow or replace
the matching stored value but cannot remove the Smart View domain or Odoo
record rules. A view with no text query can be browsed without Paperless
search. Another user's private view and a nonexistent view are
indistinguishable. Saved-view writes remain in the native Documents UI.

## Personal generative boundary

Gemini is neither an archive dependency nor a search component. Paperless owns
the two optional personal generation surfaces—metadata suggestions and its
existing document chat—while Odoo exposes no chat UI and MCP remains strictly
read-only. Both features are off by default and require the current active
internal Paperless user to have a governed Pocket mapping, an individual
encrypted credential, and the relevant personal toggle.

The credential is a one-to-one Paperless profile value encrypted with a random
AES-256-GCM data key. A versioned deployment master key wraps that data key;
associated data binds both encryption layers to the user, credential revision,
and master-key identity. Only a secret-file path is present in process
environment. The key itself never crosses into Odoo, MCP, browser storage,
Paperless exports, logs, or exception chains, and an administrator has no API
for selecting another user's profile.

The provider, OpenAI-compatible Google endpoint, and stable model allowlist
are distribution constants. Native global generative fields are read-only,
hidden from the settings UI, cleared by migration, and rejected by the release
check. The connection test calls only the provider's model-list endpoint and
sends no document. Metadata generation re-resolves the initiating user and
document before the external call. Streaming chat rechecks the active mapping,
toggle, and every context document before synthesis and before emitting each
chunk. Provider failures are deliberately sanitized because third-party
exception objects may retain request headers.

Document content is always untrusted prompt context. The fixed chat prompt
forbids following document instructions and supplies no tools; metadata
suggestions are returned for user review and never applied automatically.
Disabling or deleting a profile takes effect on the next authorization check.
Upload, OCR, local embeddings, Tantivy/vector retrieval, Odoo synchronization,
and MCP never resolve this configuration and continue during a Gemini outage.

## Write path

Native Odoo attachments are the operational write path. Creation, final-record
reparenting and content replacement enqueue a local operation after the Odoo
file is durable; no Paperless request runs in the user's transaction. Temporary
composer files, inline images, binary fields, URLs, web assets and unsupported
models are excluded. Supported evidence stays immediately previewable from its
ordinary Odoo record while a bounded worker archives it.
Authoritative formats Paperless cannot consume remain attached to their native
business records. Reconstruction classifies those exclusions explicitly; it
does not discard them or misrepresent a failed conversion as missing evidence.

The worker calculates a content SHA-256 and a second SHA-256 over canonical
business classification: company, confidentiality, accounting-evidence policy,
access scope, stable tags/entity identities, type, correspondent and document
date. It searches current and historical versions for that composite identity
and reuses an accessible root before submitting new bytes. Odoo relationship
targets are excluded from the metadata hash, so the same binary with the same
classification on another record adds a relationship; changed content on the
same attachment becomes a Paperless version. Retry identity is the native
attachment, content hash and metadata hash. Failed operations stay visible until
retry or acknowledgement.

The native attachment remains the record's immediate copy throughout this
flow. Its card reads the latest operation in one bounded query, polls only while
that operation is active, and exposes **Open in Documents** after completion.
The action passes the exact authorized `usl.document` identity to the client
workspace; it does not send a raw Paperless URL to the browser. New on-request
operations fail before queue creation when the required attachment or polling
scheduler is inactive.

TESE payslip archiving keeps the root's mandatory HR/accounting evidence role
and adds three explicit business relationships where available: the payslip,
its accounting move, and a `library`-role relationship to the attached
employee. Changing the root intake role to library was rejected because it
would weaken the payroll evidence classification; the relationship makes the
same root appear in the employee's accessible Library without another upload.

Content-only reuse was rejected because the same bytes can carry different
company, HR or accounting meaning. Including target record IDs in the metadata
hash was also rejected because it would force a redundant archive upload for
every task or bill link. Paperless automatic duplicate handling remains a final
safety signal for an unmirrored remote file, but it is not treated as proof that
Odoo classification matches.
When an operational attachment matches a root already in Paperless Trash, the
bridge preserves Trash intent, adds the Odoo relationship, and records a review
issue on both the source record and archived root. Restoring that root lets the
same idempotent operation finish; it never creates duplicate bytes or silently
restores a discarded document.

`usl.document.link.mixin` is the stable extension contract. Product models
provide archive policy, additive business context, related records and fields
that may change access. Contextual tags and types are sent with the initial
Paperless upload. Existing manual metadata wins; conflicting non-empty defaults
produce a review item. Stable project/platform mappings update one nested tag
through entity renames and prevent per-record tag growth.

Managed bank statements use this same write path with a stricter completion
contract. The received PDF remains attached to the source email in Odoo, but a
checkpoint is not certifiable until Paperless exposes a version with the same
SHA-256, the version is pinned on the statement relationship, accounting access
is synchronized and the archived original is available. A corrected PDF is
submitted as a new version of the existing statement root. Advisory locking
makes a cron/manual retry converge without submitting the same accepted source
twice.

Historical Odoo binaries cross a separate migration boundary. The canonical
tool under `migration/documents_archive/` verifies the approved dump and every
filestore object, then uses the supported asynchronous Odoo-to-Paperless write
path. Source identities, folder paths, access history and exact tag truth stay
in sealed external evidence. The live archive receives only user-facing
classification, original business timestamps, Odoo relationships and access
policy. The source filestore is mounted read-only and never becomes a product
add-ons path or shared writable storage; no migration model or provenance field
is installed in the distribution.

The ingestion operation captures company and confidentiality at submission
time. Successful asynchronous completion applies that exact policy before
permission synchronization; it must not silently fall back to `Internal` while
Paperless is processing.

The minute-level ingestion poll is also the queue watchdog. It isolates each
operation, settles a successful archive even if its source record was deleted,
and leaves that orphan document in `Needs attention`. A Paperless task that
still has no final result after six hours becomes an explicit failed operation
instead of remaining `processing` indefinitely. The timeout is configurable
through `usl_documents.processing_timeout_minutes`.

Automatic archives use `linked_record` access. Odoo evaluates real read access
to any active linked record, stores the resulting permitted internal users for
search rules, and applies the same identities as Paperless object permissions.
Controllers recheck linked-record access before every file response. Access
reductions are fail-closed; portal users are deliberately outside the direct
Documents/Paperless permission set.

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
Trash reconciliation records the API's `deleted_at`; Paperless 3.0.5 does not
return the actor through its supported Trash or history contracts, so Odoo
stores an explicit “actor not provided” source label instead of fabricating
attribution.

The cross-system integrity manifest separates those deliberate tombstones from
live roots. They are counted and reported for audit, but are not treated as
missing documents, permission failures, or checksum failures.

Paperless 3.0.5 with REST API v10 is the qualified contract. The client rejects
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
