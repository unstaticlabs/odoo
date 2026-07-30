# Paperless-backed Documents operations

## Supported stack targets

Do not run an unqualified `docker compose --profile paperless up`. It can use
development defaults, the wrong database filter, or another shared project.
The repository wrapper always supplies the environment, project, override
files, profile, ports, and safety checks together.

Local QA is fixed to:

- Compose project `codex-paperless-docs`;
- Odoo database `odoo_usl_documents_test` and filter
  `^odoo_usl_documents_test$`;
- Odoo `http://127.0.0.1:18080` and gevent port `18072`;
- Paperless `http://127.0.0.1:8010`;
- electronic invoice and e-reporting live flags `0`;
- preserved named volumes for both applications.

Use:

```bash
scripts/documents-stack qa up
scripts/documents-stack qa update
scripts/documents-stack qa bootstrap
scripts/documents-stack qa status
scripts/documents-stack qa logs
scripts/documents-stack qa stop
```

`update` updates both `usl_documents` and the optional
`usl_documents_accounting` bridge when installed, then recreates only the Odoo
application container. It does not install an unselected bridge or reset
databases, filestore, Paperless media, consume staging, exports, or
relationships. `bootstrap` is idempotent and seeds only synthetic QA material.

QA-only credentials are intentionally simple:

- Odoo: `admin/admin`, `documents-user/admin`,
  `documents-accountant/admin`, `documents-hr/admin`, and
  `documents-restricted/admin`;
- Paperless: `archive-admin/admin` and the same four role usernames with
  password `admin`.

Never reuse these credentials or `deploy/documents/qa.env` outside local QA.

## Pre-production

Copy `deploy/documents/preprod.env.example` outside the repository, replace
every placeholder from the secret manager, pin the Odoo image to an immutable
revision, and set:

```bash
export USL_DOCUMENTS_PREPROD_ENV=/secure/path/documents-preprod.env
scripts/documents-stack preprod config
scripts/documents-stack preprod preflight
scripts/documents-stack preprod up
```

Preflight refuses default/QA credentials, `CHANGE_ME` values, unpinned images,
an unsafe database filter, public HTTP Paperless URLs, local-only identity
settings, enabled live electronic-invoice/e-reporting flags, or a non-
preproduction environment. The target cannot silently fall back to QA or the
base Compose defaults.

Terminate TLS and SSO at the secured production ingress. Paperless should not
be publicly reachable except through that ingress. Direct Paperless users must
be individual identities with equivalent object permissions; Odoo-native work
does not require a Paperless login.

## Qualified versions and health

The exact qualified Paperless image is
`ghcr.io/paperless-ngx/paperless-ngx:3.0.4` (qualified digest
`sha256:3838b9a4260d23acc5bb63aed407138435e70b56e5806f4baa350ca184e57582`).
It was qualified against REST API v10. PostgreSQL is 16-bookworm, Valkey is
8.1.3-alpine, Gotenberg is 8.34, and Tika is 3.2.3.0-full. Never replace a pin
with `latest` or let an image pull become an implicit upgrade.

Compose health checks cover Odoo, Odoo PostgreSQL, Paperless web/worker,
Paperless PostgreSQL, Valkey, Tika, and Gotenberg. Odoo has no runtime
`depends_on` relationship that makes Paperless availability a prerequisite.

Monitor:

- HTTP health and Paperless API compatibility;
- task queue depth, failed consumption, and preview/OCR errors;
- last successful incremental/full synchronization and its checkpoint;
- permission failures, catalog/Saved View drift, Trash drift, and missing IDs;
- database, media, data/search, filestore, and backup capacity/age.

A Paperless outage pages the archive owner but must not restart or block Odoo.

## Identity and permission synchronization

Use a non-human service identity only for server-to-server API work. Keep its
token in Odoo secret/system configuration. Never expose it to a browser.

Map direct users under **Documents > Configuration > User access**. Each map is
one Odoo user to one individual Paperless user; shared administrators are not a
valid production mapping. After creating or changing a map, use **Verify
identity**: Odoo reads the Paperless user through the service API and requires
the ID and username to match before granting objects or exposing deep links. A
failed check remains visible on the mapping instead of being accepted
optimistically. Install the Odoo-created fail-closed Paperless workflow before
enabling web, consume, mail, or API intake. New archive items remain owned by
the service context until Odoo assigns company/confidentiality and synchronizes
actual document-object permissions.

In Paperless, put direct identities in a role that grants model-level read
access to Documents, Tags, Correspondents, Document types, Custom fields,
Storage paths, Notes, and Saved Views. Grant personal Saved View/UI-settings
management only to archive managers: Paperless uses the same global Saved View
change permission for personal and unowned shared definitions. Ordinary users
manage personal favorites in Odoo and may manage their own Paperless UI
settings. Do not grant global document change/delete rights merely to make the
UI load: Odoo's synchronized per-document view/change grants remain the
confidentiality boundary. Shared catalogs and archive-native views are
deliberately unowned Paperless objects, so every identity with the relevant
model read permission can use them without receiving access to a document that
carries them.

Healthy permission checks are quiet. A failure blocks file/deep-link access,
shows an actionable warning, and retains timestamp/error in diagnostics.
Metadata-object permissions alone are not acceptance evidence. Changing an
Odoo user's companies, Documents roles, active status, or individual Paperless
mapping resynchronizes every affected document object. Permission expansion
may remain pending and blocks access; permission revocation is fail-closed and
rolls back the Odoo access change if the old Paperless grant cannot be removed.

## Storage

Named volumes separate:

- Odoo PostgreSQL and complete Odoo filestore;
- Paperless PostgreSQL;
- Paperless media/originals;
- Paperless data/search/processing state;
- Paperless Trash staging, retained on its own volume;
- Valkey state;
- consume staging;
- portable exports.

Neither application mounts the other's writable storage. Paperless files are
clear text at application level, so use encrypted host storage, restricted host
access, encrypted off-host backups, and controlled portable exports.

Paperless has no switch that completely disables automatic Trash emptying. The
supported deployment therefore sets `PAPERLESS_EMPTY_TRASH_DELAY=36500` (100
years), and pre-production refuses any shorter value. Odoo owns the real
retention decision and calls the supported permanent-delete API only after its
approval gates pass. The separate Trash volume preserves the received file
while an item is in Paperless Trash; it is not a substitute for metadata,
database, or derivative backups. Do not use Paperless **Empty Trash** directly
except through an approved, audited retention procedure. If an archive
administrator nevertheless deletes a root directly, reconciliation records an
Odoo tombstone and reports the exceptional deletion rather than silently
recreating it.

## Upgrade and rollback

1. Read Paperless release and migration notes. For the qualified 3.0 line,
   follow its supported source-version path; do not skip migrations.
2. Capture coordinated backups, portable export, integrity manifest,
   configuration, secrets inventory, and current image digests.
3. Restore into an isolated pre-production target. Run migrations/reindexing,
   API compatibility, upload, OCR search, preview/download, version, Saved View,
   Trash, object-permission, outage, and restore tests.
4. Pin the new exact tag and digest. Upgrade Paperless independently and run
   full reconciliation before accepting traffic.
5. On failure, stop Paperless and restore its database, media, and data/search
   set together with the prior image/configuration. Never roll back only one
   storage component. Odoo remains on its independent lifecycle.

## Backup sets and integrity manifest

For one backup ID and maintenance window capture:

- Odoo PostgreSQL, complete filestore, configuration/secrets, installed module
  list, and git revision;
- Paperless PostgreSQL, media/originals, data/search state,
  configuration/secrets, and pinned image digest;
- `document_exporter` output containing originals, archive files, thumbnails,
  and JSON manifests;
- a cross-system manifest:

```bash
docker compose \
  --env-file "$USL_DOCUMENTS_PREPROD_ENV" \
  -f compose.yaml -f compose.documents.preprod.yaml \
  --profile paperless exec -T \
  -e USL_BACKUP_ID=2026-07-30T0900Z odoo \
  odoo shell -d odoo_usl_documents_preprod --no-http \
< scripts/odoo/documents_integrity_manifest.py > integrity.json
```

Run `scripts/documents-stack preprod preflight` first. Keep the exact
environment and override arguments; do not substitute the base Compose
defaults.

The exporter is a portable additional copy, not the operational backup.

## Acceptance and independent restore

Local QA:

```bash
make documents-qa-test
scripts/documents-stack qa test-accounting
make documents-qa-test-js
make documents-qa-acceptance
make documents-qa-recovery-test
```

The recovery test uses an isolated, timestamped Compose project. It removes
that project, its temporary volumes, and the sensitive temporary backup
artifacts after recording the result. Set
`USL_DOCUMENTS_PRESERVE_RECOVERY=1` only while diagnosing a synthetic restore,
then remove the preserved project and artifacts explicitly.

Pre-production uses the corresponding `documents-preprod-*` targets after
preflight. The real-service acceptance verifies Paperless 3.0.4/API v10,
asynchronous upload, OCR-only search, current and historical checksum duplicate
reuse, live tag/correspondent creation, multi-term matching expressions,
original and processed downloads, multi-link/unlink, generated-output
retention, external ingestion, versions, a real automatic matching rule,
Odoo-initiated Trash attribution and stable restore, direct mapped identities,
shared Saved View visibility, permissions, outage/resume, and reconciliation.

The recovery target:

1. exports Paperless;
2. captures both PostgreSQL databases and all authoritative volumes;
3. records SHA-256 manifests;
4. proves Odoo starts without Paperless;
5. proves Paperless starts without Odoo;
6. restores under a unique Compose project, database, and volume set, including
   retained Trash files;
7. verifies counts, relationships, previews, current/received-original
   checksums, permissions, and orphan detection;
8. stops the restored application containers after evidence capture while
   preserving the isolated restored volumes for review.

Successful QA evidence on 30 July 2026 restored 39 Odoo document roots, 22
active relationships, and 54 file-version rows with `integrity_ok=True`.
Nineteen roots are retained permanent-deletion tombstones from earlier
synthetic acceptance runs; the live/Trash Paperless set contains 20 stable
identities. Tombstones are reported explicitly and do not count as missing
roots, permission failures, or checksum failures. The timestamped artifacts
were written outside the repository under `/tmp`; that location is evidence
for the disposable rehearsal, not a production backup destination.

The final frontend acceptance passed both desktop and mobile QUnit variants:
19 tests and 109 assertions in each, including native search suggestions,
native tag facets, a bounded searchable large-tag picker, Smart View
shortcuts, one-click detail history normalization, linked-record return
navigation, Trash attribution and deletion gates, and an open-detail
no-overflow assertion. Live browser review exercised the native OCR facet,
additive top tag facet, real document detail, preview, classification, and
original download. Responsive review at 1280×720, 768×1024, and 390×844 opened
a real document detail without page overflow, clipped document actions,
browser exceptions, or failed HTTP responses. The previously failing
active-navigation and tag-chip states measured 7.23:1 and 12.26:1 contrast
respectively.

The target stops the isolated restored project after evidence capture and
preserves its volumes until review. Never pass `--volumes` to a manual cleanup
command.

## Trash, unlinking, and permanent deletion

Unlinking removes only one Odoo relationship. Paperless Trash is synchronized
as the same stable root and can be restored from Odoo with relationships
intact. Permanent deletion is separate, administrator-only, auditable, and
subject to a recorded reason, explicit approval, an expired retention date, no
retention hold, and no active Odoo relationship. Accounting and HR evidence is
held by default. Successful permanent deletion keeps an Odoo tombstone and
audit attribution. A missing root is reported; never repair it by silently
creating a new document with the same title. Paperless automatic expiry is
effectively disabled with the deployment's 100-year delay so that it cannot
bypass these gates.

Paperless 3.0.4 reports the Trash timestamp but not the deleting identity
through its supported Trash/history APIs. Odoo therefore records exact
attribution for Odoo-origin actions and an explicit unknown Paperless actor for
direct archive actions. Do not substitute container logs or an administrator
guess as legal audit attribution.
