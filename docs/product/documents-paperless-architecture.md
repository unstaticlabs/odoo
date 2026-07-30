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
Odoo browser
  -> Odoo record rules and Documents controllers
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
filters. `usl.paperless.user.mapping` maps an Odoo user to one individual
Paperless user; no credential is sent to the client.

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

## Write path

Uploads calculate SHA-256 locally, search both current and historical version
checksums, and reuse an accessible root before submitting new bytes. A visible
operation is created, Paperless receives the file, and the durable Odoo link is
created only after its asynchronous task succeeds. A retry is safe across an
interrupted Odoo transaction because reconciliation imports the Paperless
commit before fixture or operation reuse. Failed operations stay visible until
the user retries or acknowledges them.

Title, date, tags, correspondent, type, catalog values, matching rules, Saved
Views, versions, and Trash mutations call a supported Paperless endpoint first
and refresh the cache from the returned/next authoritative representation. A
failed call never leaves an optimistic Odoo value presented as saved.

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
