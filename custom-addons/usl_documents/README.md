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
Synchronization hydrates those IDs from the supported metadata endpoints before
writing the cache. Paperless remains authoritative for those values.
Incremental synchronization saves a page and timestamp checkpoint before each
bounded run, resumes after interruption, and uses full reconciliation to mark
missing roots without deleting relationships.

File versions are persisted as `usl.document.version`: the first API version is
current and `is_root` identifies the received original. Version download routes
verify that the requested version belongs to the Odoo-authorized root before
proxying bytes.

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

Run focused tests with:

```bash
docker compose --profile test run --rm test \
  --test-enable --test-tags=/usl_documents
```

Tests and fixtures must mock provider calls unless operating an explicitly
isolated synthetic Paperless QA profile.

Real-service validation uses `scripts/documents-acceptance` with an isolated
Compose project/database. It verifies API compatibility, fail-closed workflow
ownership, asynchronous upload, OCR search, checksum reuse, multi-link/unlink,
version replacement, external ingestion, legal metadata hydration,
Odoo-generated output retention, permissions, outage/resume, and integrity
manifest generation. `scripts/documents-recovery-test` adds independent
backup/restore proof under a new Compose project name.
