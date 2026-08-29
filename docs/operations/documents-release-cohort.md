# Documents release cohort

This runbook builds and restores one coordinated Odoo, Paperless, Ollama and
Odoo MCP Documents recovery point. A cohort is not a production release until
`accept` succeeds. A bundle built with
`USL_DOCUMENTS_RELEASE_ALLOW_PARTIAL=1` is diagnostic evidence only.

## Architecture decision

Three credible capture approaches were compared.

1. A coordinated quiesced snapshot is selected. Odoo is stopped before
   Paperless finalization. Odoo and Paperless application services are then
   stopped while both databases, the Odoo filestore, Paperless
   media/data/Trash/export state, the qualified Ollama model and the compiled
   MCP Worker are captured. On macOS the builder uses native Metal Ollama and
   archives only the qualified alias plus the exact manifest-referenced blobs;
   unrelated local models, history and generated Ollama identity keys are not
   transferred. Linux capture may use the project-owned Ollama volume. Both
   paths restore the same model archive into containerized Ollama on Linux.
   This preserves stable Paperless IDs, Tantivy, `llmindex.db` plus its WAL/SHM
   state, model blobs and the cross-system relationship ledger.
2. Paperless `document_exporter` plus an Odoo database dump was rejected
   as the only recovery format. The exporter is retained as a sanitized
   portable supplement, but it does not preserve the live Tantivy/vector state
   or prove a no-rebuild restore.
3. Copying only `llmindex.db`, or rebuilding search and embeddings after
   restore, was rejected. A live SQLite file is not a coordinated archive and
   a production rebuild would violate the transferability requirement.

For sanitization, clone-and-sanitize is selected over changing the source
database or exporting QA identities. Odoo and Paperless databases are cloned
while source services are quiesced. Only the clones have local OIDC subjects,
Paperless mappings/tokens, interactive credentials, personal Gemini profiles
and integration configuration removed. Human subjects are disabled and the
non-human Paperless owner needed to retain document ownership remains
non-interactive. The source databases are not modified.

For image portability, native multi-platform or explicit target-platform OCI
artifacts are required. Relabeling an arm64 image as amd64, or assuming that
database/index files are portable without a target-host restore, is rejected.
`images/images.json` therefore reports `partial` until every
resolved image has the declared target architecture and an immutable digest.

## Preconditions

- Both Git worktrees are clean and at the exact commits being released.
- The source dump and filestore are the approved immutable source package.
- The isolated Compose project is owned by this worktree.
- `USL_EINVOICE_LIVE_ENABLED=0` and
  `USL_EREPORTING_LIVE_ENABLED=0`.
- Odoo has no pending, processing or failed eligible archive operations.
- Every eligible attachment has one terminal ledger category.
- Paperless has no active task; sanity passes; Tantivy is current; and the
  vector index covers every live/Trash root.
- The Odoo and MCP checkpoint pointers required by the release identity exist.
- Target-platform image and cross-host restore evidence has passed.

Do not bypass a failed precondition with `ALLOW_PARTIAL` for production.
That flag exists so an incomplete source can produce sealed blocker evidence.

## Build

Use one private environment file outside Git and one private temporary output
root:

```bash
USL_DOCUMENTS_RELEASE_SOURCE_PROJECT=<isolated-project> \
USL_DOCUMENTS_RELEASE_SOURCE_DATABASE=odoo_dev \
USL_DOCUMENTS_RELEASE_ENV_FILE=/secure/path/documents-release.env \
USL_DOCUMENTS_MCP_REPOSITORY=/absolute/path/to/odoo-mcp \
USL_DOCUMENTS_RELEASE_ID=<release-id> \
USL_DOCUMENTS_RELEASE_OUTPUT_ROOT=/private/tmp/usl-documents-release-cohorts \
USL_DOCUMENTS_TARGET_PLATFORM=linux/amd64 \
make documents-release-build SOURCE_DIR=/absolute/path/to/usl-online-dump
```

The build checks Compose ownership and both clean Git trees; captures the
attachment/operation/role/accounting ledger; runs Paperless sanity, Tantivy
`reindex --if-needed`, vector migrate/update and compact without OCR;
sanitizes the official Paperless export and cloned databases; builds the MCP
Worker; records image, BGE and vector identities; and seals mode-0600
artifacts under mode-0700 directories into `manifest.json` and
`SHA256SUMS`. Every Paperless command that writes the export, Tantivy or vector
volumes runs as the `paperless` runtime user. Running those commands as the
container default root can leave SQLite WAL/SHM or index files read-only to the
worker and is not valid release evidence.

The cleanup trap restarts only source services it stopped, drops only the two
release clone databases and removes only its private capture directory. An
interrupted build leaves its incomplete cohort directory for diagnosis and
never overwrites it; use a new release ID after fixing the cause.

## Verify and independent restore

```bash
make documents-release-verify \
  BUNDLE=/private/tmp/usl-documents-release-cohorts/<release-id>

USL_DOCUMENTS_RESTORE_ENV_FILE=/secure/path/documents-restore.env \
USL_DOCUMENTS_RESTORE_DATABASE=odoo_documents_release_restore \
USL_DOCUMENTS_RELEASE_SOURCE_DATABASE=odoo_dev \
make documents-release-restore \
  BUNDLE=/private/tmp/usl-documents-release-cohorts/<release-id> \
  PROJECT=<fresh-isolated-project>
```

The restore environment supplies new URLs, ports and independent secrets. Its
project, containers, network, volumes and database must not already exist.
The command restores the Odoo database/filestore, Paperless
database/media/data/Trash/export and complete Ollama model volume.

It starts with `--pull never`, requires Tantivy
`reindex --if-needed` to be a no-op, runs vector schema migration and
incremental update only as the `paperless` runtime user, and starts Odoo. It
never invokes consumption, OCR, `document_llmindex rebuild` or model
acquisition.

After startup, the command compares:

- Odoo root/link/version, role and attachment-ledger counts;
- posted move/line counts and debit/credit control totals;
- Paperless stable ID range and live/Trash/searchable counts;
- exact vector digest, schema, dimension and row/document counts;
- exact BGE alias manifest digest;
- Tantivy no-op output.

A mismatch writes private external failure evidence but does not modify the
sealed cohort. A complete match writes
`evidence/recovery-rehearsal.txt`, records the pre-restore manifest
digest and reseals the cohort. Verify the new manifest after the restore.

## Acceptance and publication

```bash
make documents-release-accept \
  BUNDLE=/private/tmp/usl-documents-release-cohorts/<release-id>
```

Acceptance re-verifies every checksum and rejects a partial identity, any
nonzero attachment/operation/permission/task/personal-key/unauthorized-result
counter, missing checkpoint identities, non-target-platform images, and
security/accounting/integrity/install/upgrade/boundary/recovery evidence that
is not explicitly passed.

Publication is permitted only after acceptance. Supply an approved age
recipient, never a password:

```bash
USL_DOCUMENTS_RELEASE_AGE_RECIPIENT=<approved-age-recipient> \
make documents-release-publish \
  BUNDLE=/private/tmp/usl-documents-release-cohorts/<release-id> \
  DESTINATION=/approved/private/destination
```

Transfer the resulting `.tar.age` and checksum over authenticated SSH
and verify it at both ends. Personal Gemini keys, Pocket state, browser
sessions, QA subjects, API tokens and generated local credentials are never
bundle artifacts.

## Production restore and rollback

Production restores into new blue/green volumes with traffic stopped. Supply
production URLs and independently managed secrets, rotate the non-human
Paperless credential, configure distinct Odoo/Paperless Pocket clients,
provision approved identities, synchronize object permissions, configure MCP
and run `accept` before traffic. Electronic-invoice reception and
e-reporting remain separate activation decisions.

Rollback is cohort-wide: stop the candidate services and restore the previous
accepted Odoo database/filestore, Paperless database/media/data/Trash, Ollama
volume, exact images and MCP Worker identity together. Never roll back only
one database, only `llmindex.db`, or only an application image.

## 2026-08-25 partial portability evidence

The diagnostic cohort
`usl-documents-20260825-partial-arm64-r6` was built from authoritative
dump SHA-256
`ad313e28586fafa27a4f6a266df57080456613dff1c8c2c6d7e012732bf633b1`.
Its independent local restore passed every comparison above without OCR,
re-ingestion, vector rebuild or model download. The final sealed manifest is
`c3b811e90840b3bc1d69866e80140fd30d7e97393ea604988c034fb9b7501134`.

It is not a production candidate. Acceptance reports 840 eligible attachments
pending, 536 unresolved, one failed operation, 180 pending operations, 23
processing operations, missing C/E/F/G checkpoint pointers and missing amd64
image evidence. The captured images are arm64. This cohort proves the local
snapshot/restore mechanism, not full-source or cross-architecture parity.

The release and production-candidate checkpoint pointers must not be created,
and the bundle must not be published, until those blockers are cleared by a
fresh complete cohort.
