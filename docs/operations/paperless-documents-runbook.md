# Paperless-backed Documents operations

## Supported stack targets

Do not run an unqualified `docker compose --profile paperless up`. It can use
development defaults, the wrong database filter, or another shared project.
The repository wrapper always supplies the environment, project, override
files, profile, ports, and safety checks together.

Local QA is fixed to:

- Compose project `codex-paperless-docs`;
- Odoo database `odoo_usl_documents_test`, hard runtime binding
  `db_name = odoo_usl_documents_test`, and web filter
  `^odoo_usl_documents_test$`;
- Odoo `http://127.0.0.1:18080` and gevent port `18072`;
- Paperless `http://127.0.0.1:18010`;
- Pocket ID `http://pocket-id-documents.localhost:18110`;
- electronic invoice and e-reporting live flags `0`;
- preserved named volumes for both applications.

Ports `8069`, `8072`, `8010`, and `1411` belong to the canonical `19-usl`
development stack. The Documents wrapper refuses a QA configuration that
reuses them, so a feature worktree cannot silently replace the main runtime.

Use:

```bash
scripts/documents-stack qa build
scripts/documents-stack qa up
scripts/documents-stack qa update
scripts/documents-stack qa bootstrap
scripts/documents-stack qa status
scripts/documents-stack qa logs
scripts/documents-stack qa stop
```

The QA target uses `codex-paperless-docs-odoo:documents-qa`, never the
canonical development image. Run `build` after Dockerfile, Python dependency,
or upstream/OCA dependency changes; ordinary mounted add-on edits need only
`update`.

The explicit `db_name` binding prevents this stack's cron worker from opening
other development, restore, or migration databases that happen to share the
PostgreSQL container. The `dbfilter` alone protects HTTP database selection but
is not the runtime isolation boundary for scheduled jobs.

The Odoo Online archive is never loaded through this product QA helper. Use the
dump-bound `make documents-restore` workflow from the source-truth migration
runbook. It validates the complete selected perimeter, writes sealed evidence
outside the product database, and must pass an unchanged second run.

`update` updates `usl_documents`, its required `usl_pocketid` identity
foundation, and the optional `usl_documents_accounting` bridge when installed,
then recreates only the Odoo application container. It does not reset
databases, filestore, Paperless media, consume staging, exports, or
relationships. `bootstrap` is idempotent and seeds only synthetic QA material.
The wrapper creates `.documents-qa-sso.env` with mode `0600`, provisions
separate Odoo and Paperless Pocket clients idempotently, and never commits
their secrets. It uses port `18110` so it does not collide with the canonical
development Pocket tenant on `1411`.
After Paperless is healthy, the qualified target runs a pinned, idempotent
initializer for the Paperless-local SSO capability group. It grants only
catalog/UI permissions; Odoo continues to synchronize every document object
grant. A failed initializer fails the target instead of leaving a newly
authenticated user with a broken Paperless dashboard.

The same fail-closed startup initializes the local semantic model. When the
qualified `usl-bge-m3:documents-20260824-rc1` alias is absent, Compose pulls
the source `bge-m3:latest` manifest, rejects any digest other than the
repository-qualified SHA-256, copies that exact manifest to the qualified
alias, and verifies it again before Paperless starts. Existing qualified model
volumes perform no pull. This bootstrap is for the pinned model artifact, not
permission to accept an arbitrary `latest` model in a release.

QA-only credentials are intentionally simple:

- Odoo: `admin/admin`, `documents-user/admin`,
  `documents-accountant/admin`, `documents-hr/admin`, and
  `documents-restricted/admin`;
- Paperless: `archive-admin/admin` and the same four role usernames with
  password `admin`.

Never reuse these credentials or `deploy/documents/qa.env` outside local QA.
They are an explicit test-only exception; ordinary pre-production users use
Pocket. The QA mapping exception is ignored unless
`USL_DEPLOYMENT_ENV=qa`.

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
an unsafe database filter, public HTTP Paperless or Pocket URLs, local-only
identity settings, a reused Odoo/Paperless OIDC client, enabled password login
in Paperless, a callback protocol that differs from Paperless's public URL,
Pocket-to-Paperless group synchronization, enabled live
electronic-invoice/e-reporting flags, or a non-preproduction environment. The
target cannot silently fall back to QA or the base Compose defaults.

Terminate TLS at the secured production ingress. Paperless should not
be publicly reachable except through that ingress. Direct Paperless users must
be individual identities with equivalent object permissions; Odoo-native work
does not require a Paperless login.

## Qualified versions and health

The exact qualified Paperless image is
`ghcr.io/paperless-ngx/paperless-ngx:3.0.5` (qualified digest
`sha256:65a4cabf0169ea7fbd90ab7bb28ba3f8b5909613635acda1a03ad606f34b456b`).
It was qualified against REST API v10. PostgreSQL is 16-bookworm, Valkey is
8.1.3-alpine, Gotenberg is 8.35, and Tika is 3.3.1.0-full. Never replace a pin
with `latest` or let an image pull become an implicit upgrade.

Compose health checks cover Odoo, Odoo PostgreSQL, Paperless web/worker,
Paperless PostgreSQL, Valkey, Tika, Gotenberg, and the digest-pinned Pocket ID
v2.14.0 service. Odoo has no runtime
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

Create two confidential clients in Pocket ID:

- Odoo callback: `https://<odoo-host>/auth_oauth/signin`;
- Paperless callback:
  `https://<paperless-host>/accounts/oidc/pocket-id/login/callback/`.

Both use authorization code, PKCE, `openid profile email groups`, and the same
allowed-user group, but never the same client ID or secret. Paperless uses its
documented `allauth.socialaccount.providers.openid_connect` provider,
auto-signup, disabled regular frontend login, and optional automatic SSO
redirect. Keep `PAPERLESS_SOCIAL_ACCOUNT_SYNC_GROUPS=false`: Pocket groups
gate login and never become document authorization.

Each direct user is one Odoo user, one immutable Pocket identity, and one
individual numeric Paperless user; shared administrators are not a valid
production mapping. Archive restore uses the disposable `odoo-migration`
Paperless owner; target finalization and `make paperless-users` provision the
runtime `odoo-integration` owner, claim any remaining migration-owned roots,
map governed Pocket identities, and synchronize exact document-object
permissions. Its temporary administrator token has no password login and is
revoked and deactivated automatically when restoration succeeds or fails. Run
`make paperless-users` after a governed identity, Documents
role, or company assignment changes. Both commands are idempotent and fail
closed on an ambiguous subject, username, email, role, or remote identity.

**Documents > Configuration > User access** remains the inspection and
exception-maintenance surface. **Verify identity** is available for an
explicitly managed identity outside the standard manifest, but the canonical
target does not depend on a person's first Paperless login. Odoo requires the
Paperless ID/username and active Pocket link to match before granting objects
or exposing deep links. A failed check remains visible instead of being
accepted optimistically. Install the Odoo-created
fail-closed Paperless workflow before
enabling web, consume, mail, or API intake. New archive items remain owned by
the service context until Odoo assigns company/confidentiality and synchronizes
actual document-object permissions.

Policy reconciliation is idempotent. When the owned workflow already has the
required trigger sources, fail-closed owner, order, enabled state, and empty
interactive grants, Odoo must not issue a `PUT`. Likewise, an unchanged full
metadata synchronization must not invalidate already synchronized object
permissions. Repeated writes make Paperless schedule avoidable bulk index work
and can delay normal ingestion behind large OCR documents; increasing worker
or embedding concurrency is not a substitute for eliminating those writes.
When a permission or owner change is required, the USL Paperless patch still
runs the native Tantivy/cache/signal refresh but marks the operation as
embedding-invariant: permissions are applied to the current document while its
unchanged BGE-M3 vector is reused. Generic bulk metadata and content edits keep
the normal vector refresh. A full identity reconciliation should therefore end
with bounded successful `bulk_update` tasks, no nonterminal task rows and no
Ollama embedding requests during those permission tasks.

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
Odoo user's companies, Documents roles, active/Pocket status, Pocket identity,
or individual Paperless mapping resynchronizes every affected document object.
Permission expansion
may remain pending and blocks access; permission revocation is fail-closed and
rolls back the Odoo access change if the old Paperless grant cannot be removed.

The migration imports no source Paperless user, token, password, proxy account,
or connection state. The archive restore uses only its non-human service
identity. Governed interactive accounts and mappings are target configuration
created after business-data parity, so a clean reconstruction is immediately
usable without weakening source-truth controls.

Source-complete reconstruction uses a controlled two-phase semantic-index
path. While the bounded uploader materializes originals, OCR, metadata and
permissions, `PAPERLESS_USL_DEFER_SEMANTIC_INDEX=true` suppresses only the
incremental post-consume embedding signal. The migration runner then waits for
all ordinary Paperless tasks, force recreates the service with the switch set
back to `false`, runs the supported vector migrate/update/compact commands, and
requires release-inventory parity. Its exit trap restores normal runtime even
after failure. Do not set this variable for ordinary operation; production and
pre-production admission reject it.

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

## Personal Gemini

Personal Gemini is optional, per-user, and Paperless-only. It must not be
configured through Paperless's native global LLM settings. The distribution
migration clears those fields, the frontend hides them, and
`check_personal_ai_release` rejects any later global value.

The master-key ring is an independently protected Docker secret. The only
runtime variable is
`USL_PERSONAL_AI_MASTER_KEYS_PATH=/run/secrets/usl_personal_ai_master_keys`;
never use an inline value or a variable ending in `_FILE`. Back up the key ring
separately from the Paperless database and never place it in Paperless exports.

After deployment, migration, restore, or rotation run inside the Paperless
webserver:

```bash
python manage.py showmigrations paperless_personal_ai
python manage.py makemigrations --check --dry-run paperless_personal_ai
python manage.py check_personal_ai_release
```

The first command must show `0001_initial` applied, the second must report no
changes, and the third must print only the active non-secret key identity.
Then run the backend and exact-source frontend gates described in
`docs/operations/personal-gemini-runbook.md`.

Users manage their own keys under **My profile → Personal Gemini**. Support may
explain privacy, disablement, deletion, and provider-side revocation, but must
never ask for, copy, test, export, or impersonate a user's key. The complete
privacy, eligibility, rotation, incident-response, and independent-restore
procedure is maintained in `docs/operations/personal-gemini-runbook.md`.

## Acceptance and independent restore

For the source-derived portable Odoo/Paperless/Ollama/MCP artifact, use
[Documents release cohort](documents-release-cohort.md). The workflow below is
the synthetic/local stack recovery suite; it does not replace the
digest-bound cohort or its target-architecture gate.

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
preflight. The real-service acceptance verifies Paperless 3.0.5/API v10,
asynchronous upload, OCR-only search, current and historical checksum duplicate
reuse, live tag/correspondent creation, multi-term matching expressions,
original and processed downloads, multi-link/unlink, generated-output
retention, external ingestion, versions, a real automatic matching rule,
Odoo-initiated Trash attribution and stable restore, direct mapped identities,
shared Saved View visibility, permissions, outage/resume, and reconciliation.

Trusted `generated_final` attachments enter the ordinary durable operation
queue with source `odoo_generated` and the evidence role. Acceptance commits
the queued operation, runs the same archive worker used in production, and
accepts only an archived or checksum-duplicate result. It does not bypass the
worker or relabel a generic attachment after ingestion.

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

The frontend gate runs the Documents QUnit suite in both desktop and mobile
presets. It covers Home/My library navigation, role-restricted manager views,
empty-by-default Archive search, hybrid/exact/semantic modes, background
include/exclude/only controls, private stars, native-attachment **Keep in
Documents**, promotion/demotion without archive duplication, native search
suggestions and facets, shared native saved searches, inline classification,
autocomplete quick creation and dismissal, native tag facets, large catalogs,
Smart View shortcuts, sortable URL-backed list ordering, linked-record return
navigation, Trash attribution/deletion gates, and open-detail overflow. Record
the current passed test/assertion count from the command output instead of
copying a historical count into a release claim. Browser review must
additionally exercise real archive data at desktop, tablet, and mobile widths
and report console/network failures honestly.

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

Paperless 3.0.5 reports the Trash timestamp but not the deleting identity
through its supported Trash/history APIs. Odoo therefore records exact
attribution for Odoo-origin actions and an explicit unknown Paperless actor for
direct archive actions. Do not substitute container logs or an administrator
guess as legal audit attribution.
