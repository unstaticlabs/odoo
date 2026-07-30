# USL Documents integration

`usl_documents` is an isolated Odoo extension over supported Paperless REST API
v10. It intentionally stores metadata and business relationships, never the
Paperless binary.

`PaperlessClient` owns HTTP contracts and compatibility checks. Controllers
authorize every thumbnail, preview, and download through Odoo record rules
before proxying server-side. `usl.document` is the synchronized metadata cache;
`usl.document.link` is the generic business relationship; operations track
asynchronous ingestion. Synchronization is stable on `paperless_id`.

API v10 document payloads carry correspondent, document-type, and tag IDs.
`usl.paperless.tag`, `usl.paperless.correspondent`, and
`usl.paperless.document.type` cache those catalogs by stable Paperless ID.
Synchronization hydrates the catalogs before document rows. User writes call
the supported Paperless endpoint first and cache its returned representation.
The compatibility name fields on `usl.document` remain read-only during
migration; new filters and smart views use relations.

`usl.document.smart.view` provides manager-owned shared views and private saved
filters. Archive-native shared views are synchronized with Paperless Saved
Views by stable REST identity. Shared metadata views reference relational IDs,
so Paperless renames do not break navigation. Odoo business rules still own
company, confidentiality, accounting evidence, HR, review state, and
linked-record context; these constraints are never represented as if
Paperless enforced an identical Saved View.

Incremental synchronization saves a page and timestamp checkpoint before each
bounded run, resumes after interruption, and uses full reconciliation to
refresh catalogs/Saved Views/Trash and mark missing roots without deleting
relationships. A trashed Paperless root remains the same `usl.document` and
retains its Odoo links; Restore calls the supported Trash endpoint.

File versions are persisted as `usl.document.version`: the API current marker
identifies the current file and `is_root` identifies the received original.
Version preview and download routes verify that the requested version belongs
to the Odoo-authorized root before proxying bytes. Restore downloads an
authorized old version server-side and submits it to Paperless's supported
update-version API, creating a new current version instead of mutating history.
Root duplicate detection searches both the current checksum and historical
version checksums.

Paperless correspondents optionally map to `res.partner`. Archive matching and
the Paperless name remain remote authority; Odoo owns the mapped business
identity. The workspace and native catalog expose the mapped Contact only when
the current user can read it. Mapping does not create a Contact, document link,
or access grant.

The workspace does not expose healthy synchronization state. It returns a
document-level access error only when permission synchronization failed.
Checksums, archive IDs, and last access checks are manager diagnostics.

Configuration keys use the `usl_documents.*` namespace. The service URL and
token are server-only; the public URL is used solely for permission-synchronized
individual deep links. Extend supported business models through
`usl.document.link._allowed_models()` and the link mixin, with explicit company
and confidentiality tests.

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
ownership, asynchronous upload, OCR search, full-history checksum reuse,
multi-link/unlink, version replacement, external ingestion, legal metadata
hydration, Odoo-generated output retention, permissions, outage/resume, and
integrity manifest generation. It reconciles first so a rerun after an
interrupted cross-system transaction reuses Paperless commits.
`scripts/documents-recovery-test` adds independent backup/restore proof under a
new Compose project name.

Use `scripts/documents-stack qa ...` or `scripts/documents-stack preprod ...`
for deployment. Never use the base Paperless Compose profile without its
environment/override/project qualification.
