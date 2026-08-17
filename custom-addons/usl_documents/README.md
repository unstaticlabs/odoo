# USL Documents integration

`usl_documents` is an isolated Odoo extension over supported Paperless REST API
v10. It intentionally stores metadata and business relationships, never the
Paperless binary.

`PaperlessClient` owns HTTP contracts and compatibility checks. Controllers
authorize every thumbnail, preview, and download through Odoo record rules
before proxying server-side. `usl.document` is the synchronized metadata cache;
`usl.document.link` is the generic business relationship; operations track
asynchronous ingestion. Synchronization is stable on `paperless_id`.

Native `ir.attachment` remains the operational upload surface. Durable files
on supported records are queued only after their final owner is known, so an
upload never waits for Paperless. The worker reuses archive roots only when the
content checksum and stable classification-metadata hash both match, creates
versions when an Odoo attachment changes, applies contextual metadata, and
mirrors actual linked-record read access to Paperless object permissions.
Projects and platforms use stable entity-tag mappings; renames update the
existing tag instead of creating a tag per task, payout or name change.
Formats Paperless cannot consume (XML, calendar and ZIP files) and inline mail
images remain on their native records. Reconstruction classifies those files
explicitly instead of feeding a permanent archive retry loop.

API v10 document payloads carry correspondent, document-type, and tag IDs.
`usl.paperless.tag`, `usl.paperless.correspondent`, and
`usl.paperless.document.type` cache those catalogs by stable Paperless ID.
Synchronization hydrates the catalogs before document rows. User writes call
the supported Paperless endpoint first and cache its returned representation.
Fallback correspondent and document-type names keep a record intelligible
during a partial reconciliation; normal filters, smart views, and editing use
stable relational Paperless identities.

`usl.document.smart.view` provides manager-owned shared views and private saved
filters. Archive-native shared views are synchronized with Paperless Saved
Views by stable REST identity. Shared metadata views reference relational IDs,
so Paperless renames do not break navigation. Odoo business rules still own
company, confidentiality, accounting evidence, HR, review state, and
linked-record context; these constraints are never represented as if
Paperless enforced an identical Saved View.

`usl.document.quick.filter` places optional one-click controls on Smart Views,
but each control's query is a shared native `ir.filters` record. Domain,
grouping, and ordering are captured from Odoo's SearchModel, validated on the
server, and replayed through the same search bar. Personal searches remain
ordinary private Odoo Favorites.

Shared Paperless catalogs and archive-native Saved Views are written with no
Paperless owner, its supported shared-object form. Direct identities need the
corresponding global model read permissions, but document roots remain owned by
the integration service and visible only through Odoo-synchronized object
grants. Personal Paperless and Odoo saved views retain their individual owner.

Incremental synchronization saves a page and timestamp checkpoint before each
bounded run, resumes after interruption, and uses full reconciliation to
refresh catalogs/Saved Views/Trash and mark missing roots without deleting
relationships. A trashed Paperless root remains the same `usl.document` and
retains its Odoo links; Restore calls the supported Trash endpoint.
An automatic native-attachment match to that root preserves Trash, links the
business record, and remains an explicit review issue until a manager restores
the archive document.

File versions are persisted as `usl.document.version`: the API current marker
identifies the current file and `is_root` identifies the received original.
Version preview and download routes verify that the requested version belongs
to the Odoo-authorized root before proxying bytes. Restore downloads an
authorized old version server-side and submits it to Paperless's supported
update-version API, creating a new current version instead of mutating history.
Root duplicate detection searches both the current checksum and historical
version checksums and requires the matching version's classification-metadata
hash. Link targets are deliberately excluded from that metadata hash, so the
same evidence can be linked across records with the same business context.

Paperless correspondents optionally map to `res.partner`. Archive matching and
the Paperless name remain remote authority; Odoo owns the mapped business
identity. The workspace and native catalog expose the mapped Contact only when
the current user can read it. Mapping does not create a Contact, document link,
or access grant. Inline Contact selection reuses a visible mapping, safely
adopts one exact unmapped correspondent, or creates a new Paperless
correspondent through the supported API.

The workspace uses Odoo's SearchModel/SearchBar, relational autocomplete,
locale-aware date input, Pager, table classes, and native Favorites lifecycle.
`all_text` is the default broad Paperless full-text query plus authorized Odoo
link labels. Explicit list ordering is server-allowlisted and persisted through
the standard action URL/favorite state.

The workspace does not expose healthy synchronization state. It returns a
document-level access error only when permission synchronization failed.
Checksums, archive IDs, and last access checks are manager diagnostics.

Configuration keys use the `usl_documents.*` namespace. The service URL and
token are server-only; the public URL is used solely for permission-synchronized
individual deep links. Extend supported business models through
`usl.document.link._allowed_models()` and `usl.document.link.mixin` archive
policy, context, relationship and access-trigger hooks, with explicit company
and confidentiality tests. Do not add a second attachment ingestion path.

`usl_documents` depends on `usl_pocketid` for the canonical Odoo identity
link. Interactive Odoo and Paperless sessions use distinct confidential Pocket
OIDC clients. A Paperless mapping is permission-eligible only while its Odoo
user, Pocket `(issuer, subject)` link, and remote numeric Paperless identity
remain verified. Pocket groups are never copied into Paperless authorization.
The service API token remains a separate non-human integration identity.
Canonical finalization pre-provisions each governed Paperless social account,
upserts its Odoo mapping, and applies the exact visible document set. The
reconciler is idempotent and fail-closed; first login is authentication proof,
not an account-creation or authorization step.

The implementation compared three credible approaches. Standard `ir.attachment`
would preserve native UI but duplicate archive binaries and lacks Paperless OCR,
versions, and search authority. A general OCA DMS layer would add another
storage/classification model between Odoo and the mandated Paperless archive.
An isolated native client action plus metadata/link cache was selected because
it keeps Community Odoo upgradeable while leaving document reality in
Paperless. No upstream Odoo code is patched.

Run the deterministic local QA checks with:

```bash
make documents-qa-test
make documents-qa-test-js
make documents-qa-acceptance
make documents-qa-recovery-test
```

Tests and fixtures must mock provider calls unless operating an explicitly
isolated synthetic Paperless QA profile.

Real-service validation uses `scripts/documents-acceptance` with an isolated
Compose project/database. It verifies API compatibility, fail-closed workflow
ownership, asynchronous upload, OCR search, full-history composite-hash reuse,
multi-link/unlink, version replacement, external ingestion, legal metadata
hydration, Paperless automatic matching, shared Saved View identity,
Odoo-generated output retention, permissions, outage/resume, and integrity
manifest generation. It reconciles first so a rerun after an interrupted
cross-system transaction reuses Paperless commits.
`scripts/documents-recovery-test` adds independent backup/restore proof under a
new Compose project name and removes its containers, volumes, and temporary
backup artifacts after the proof. Set `USL_DOCUMENTS_PRESERVE_RECOVERY=1` only
for deliberate restore diagnostics.

Use `scripts/documents-stack qa ...` or `scripts/documents-stack preprod ...`
for deployment. Never use the base Paperless Compose profile without its
environment/override/project qualification.
