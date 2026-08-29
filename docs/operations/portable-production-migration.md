# Portable production migration candidate

## Purpose and non-negotiable boundaries

This runbook compiles a fully qualified local reconstruction into sanitized,
portable Odoo and Paperless assets, then restores them into fresh dedicated
production application volumes. It does **not** copy a live development volume,
manage Pocket ID, or promote a rehearsal after the Online source changed.

The following remain mandatory through stage and initial admission:

- `USL_EINVOICE_LIVE_ENABLED=0`;
- `USL_EREPORTING_LIVE_ENABLED=0`;
- Odoo cron, mail, provider jobs and application ingress paused while staging;
- strict whole-source and attachment gates complete;
- Odoo Online retained read-only as the post-cut-over reference.

The candidate contains a sanitized custom-format Odoo dump, its filestore, and
an official sanitized Paperless export with originals, archived/OCR derivatives,
metadata and thumbnails. Passwords, sessions, API keys, OAuth/OIDC tokens,
Pocket subjects/audit events, Paperless integration tokens, environment URLs
and client secrets are removed. Migration modules, fields and models must
already be absent; sanitation refuses to hide an incomplete boundary.

Private dumps, candidates, policies and evidence stay below ignored
`artifacts/migration/private/` storage (or explicitly approved private external
storage). Candidate directories are mode `0700`; files are mode `0600`; symlinks
and unsafe archive members are rejected.

## Architectural decision

Two faster Accounting alternatives were rejected:

1. direct SQL insertion into ledger/reconciliation tables; and
2. raw copying of source tables into Community.

Both bypass Odoo posting, computed fields, reconciliation, audit and future
upgrade invariants. Exact replay instead uses deterministic 250-move ORM
batches, bounded relation batches, source-identity prefetching and a temporary
partial composite unique index. The index disappears when finalization drops
the migration source columns. Private reconstruction evidence records monotonic
sub-stage timings and row counts. Compare repeatable timings with the historical
approximately 42-minute Accounting import; retain optimizations only when exact
parity still passes.

Repository-managed encryption/signing was also rejected for this delivery.
Use approved SSH transport and storage controls. A person independent from the
builder must approve the printed 64-hex candidate fingerprint before import.

## 1. Qualify and publish the reusable QA seed

This is a cache, **not production migration evidence**. It may be published
after the optimized code is merged into `19-usl`:

```bash
cd /Users/roger/projects/odoo
git switch 19-usl
git pull --ff-only origin 19-usl
export COMPOSE_PROJECT_NAME=usl-odoo-migration-qa-seed
export USL_ONLINE_DUMP_DIR=/Users/roger/projects/odoo/usl-online-dump
make qa-cache-refresh
```

Schema v2 rejects old/incompatible seeds. Its manifest binds migration/runtime
identity, source dump and filestore hashes, artifact hashes, module versions,
full-profile Accounting timings and Documents controls.

Hydrate twice in different isolated projects. Each run compares the sealed
Accounting/Documents controls and proves the official importer added zero
broker tasks and zero task-result records:

```bash
COMPOSE_PROJECT_NAME=usl-odoo-qa-hydration-a make qa PROFILE=full
COMPOSE_PROJECT_NAME=usl-odoo-qa-hydration-b make qa PROFILE=full
```

Do not publish a seed from a feature worktree or represent a `qa-cache`
source perimeter as whole-source production evidence.

## 2. Final Online freeze and reconstruction

1. Announce downtime and freeze Odoo Online read-only.
2. Take a new final database dump and complete filestore export.
3. Compute and record both digests. Do not reuse the rehearsal SHA after source
   activity continued.
4. From clean `19-usl`, use a new isolated migration Compose project and the
   final source SHA. The production resource preflight requires a dedicated
   Docker runtime; it reports foreign Compose projects but never stops them:

```bash
export COMPOSE_PROJECT_NAME=usl-odoo-migration-final-YYYYMMDD
export USL_ONLINE_DUMP_DIR=/approved/private/final-online-export
export USL_MIGRATION_CONFIRM_SOURCE_SHA="$(shasum -a 256 "$USL_ONLINE_DUMP_DIR/dump.sql" | awk '{print $1}')"
USL_MIGRATION_PURPOSE=production USL_QA_DATA_PROFILE=full \
  scripts/target-reconstruct
```

Production reconstruction always runs fresh full Documents ingestion. It may
not resume Accounting or reuse a Documents checkpoint. The resulting run JSON
must report `purpose=production`, `profile=full`, `outcome=passed`,
`validation_level=production-source-wide`, and complete source/attachment gates.

### Protected local working transition

When the approved workflow includes a local Accounting hygiene period, retain
the sealed Online-source candidate as deterministic migration evidence, but do
not treat it as the later transfer payload. The fixed local project must use a
`usl-odoo-transition-*` name and must be marked immediately after its fresh
production-purpose reconstruction and finalization:

```bash
export COMPOSE_PROJECT_NAME=usl-odoo-transition-YYYYMMDD
scripts/transition-live mark "$COMPOSE_PROJECT_NAME"
```

The ignored state is mode `0700`/`0600`, has no unmark operation, and makes
canonical reconstruction, Accounting target reset, QA environment reset,
QA-seed publication, synthetic bootstrap and test helpers fail closed for the
project. Normal work and rehearsed module upgrades remain possible only through
the checkpointed transition procedure. At final cutoff, quiesce all writers and
make the guard read-only:

```bash
scripts/transition-live freeze "$COMPOSE_PROJECT_NAME"
```

Local macOS transition and QA stacks use the native Ollama endpoint when it is
available. Linux production and independent Linux recovery use the sealed
containerized Ollama/BGE volume and immutable runtime identity.

## 3. Build the candidate

Use the exact immutable Odoo Distribution, Paperless overlay and Ollama runtime
digests qualified for the release. The Distribution image labels must match the
release commit, OCA bundle and qualified action-risk policy:

```bash
export COMPOSE_PROJECT_NAME=usl-odoo-migration-final-YYYYMMDD
export USL_CANDIDATE_IMAGE='ghcr.io/unstaticlabs/usl-odoo@sha256:<digest>'
export PAPERLESS_IMAGE='ghcr.io/unstaticlabs/usl-paperless-ngx@sha256:<digest>'
export OLLAMA_IMAGE='ollama/ollama@sha256:<digest>'
make migration-candidate-build SOURCE_DIR="$USL_ONLINE_DUMP_DIR"
```

`build` refuses dirty/non-`19-usl` main checkouts, non-production evidence,
partial source/attachment gates and mutable or mismatched Odoo/Documents
runtime images. It clones the finalized Odoo database, sanitizes only the
clone, records the immutable release identity, runs the maintained Odoo
neutralization SQL for every installed module, and removes environment
identity/credential state. The filestore
archive is rebuilt from the sanitized database's exact `ir.attachment`
inventory: orphaned files are excluded and every retained file's SHA-1/size is
verified. It then exports Paperless, captures parity controls, seals checksums
and publishes atomically. It never runs OCR or calls a Pocket mutation API.
This is the v2 candidate contract; v1 candidates that bound only the Odoo image
are deliberately incompatible and must be rebuilt from the frozen source.

Record the printed fingerprint in the change record. Have the independent
approver verify locally:

```bash
make migration-candidate-verify \
  CANDIDATE=/approved/private/candidate \
  FINGERPRINT=<approved-64-hex> \
  SOURCE_DIR="$USL_ONLINE_DUMP_DIR"
```

Transfer the unchanged candidate directory over approved SSH/private storage.
Do not upload it to CI or a public artifact service.

## 4. Prepare the production host

Check out the exact release commit and pull all three candidate-bound image
digests. Provision the Personal Gemini envelope-key ring as a mode-`0600`
regular file at the absolute path configured by
`USL_PERSONAL_AI_MASTER_KEYS_HOST_PATH`.
Also pull the digest-pinned Sign document-renderer, Step CA and DSS images.
Provision the renderer certificate directory and the Step CA, DSS and Odoo
Sign secret directories at the absolute paths named in the production env
template. Each directory must be private to its owner and contain the complete
certificate/key material created by the Sign provisioning runbook; preflight
checks file presence and permissions without printing secret values.
Copy these templates outside the repository, substitute owner-approved values,
then set mode `0600`:

- `deploy/production.external-pocket-id.env.example`;
- `deploy/production.identity-policy.example.json`;
- `deploy/production.browser-journeys.example.json` (filled after journeys).

The identity policy must classify every named Odoo user and Paperless identity.
It also supplies the explicit initial cron allowlist; an empty list is valid and
safest. `outbound_integrations_enabled` must remain `false`. Do not put Pocket
encryption/static API credentials in this stack.

The environment points to existing identity/ingress networks and fresh,
project-prefixed application volume names. The rendered topology contains no
Pocket service or Pocket dependency. Odoo and Paperless publish loopback ports
only during stage.

## 5. Preflight, stage and configure

```bash
make production-cutover-preflight \
  ENV_FILE=/approved/private/production.env \
  CANDIDATE=/approved/private/candidate \
  FINGERPRINT=<approved-64-hex>

make production-cutover-stage \
  ENV_FILE=/approved/private/production.env \
  CANDIDATE=/approved/private/candidate \
  FINGERPRINT=<approved-64-hex>

make production-cutover-configure \
  ENV_FILE=/approved/private/production.env \
  CANDIDATE=/approved/private/candidate \
  IDENTITY_POLICY=/approved/private/identity-policy.json \
  FINGERPRINT=<approved-64-hex>
```

Preflight is read-only with respect to application/Pocket data. It rejects
changed files/fingerprints, wrong commit/image/OCA/modules/source, unsafe DB
names or URLs, public database manager, default secrets, live regulatory flags,
missing external networks, existing target data, foreign volumes and any
managed Pocket service. It also rejects missing or mutable Sign service images,
incomplete Sign secret directories, and a checkout, candidate or image whose
canonical action-risk policy digest differs from the qualified release.

Stage uses `pg_restore --jobs=4`, analyses the committed Odoo database, restores
the exact filestore and official Paperless export, rechecks every stored Odoo
attachment against its database inventory, proves zero OCR/background
submissions and reproduces sealed Accounting/Documents controls. Application
workers remain paused afterward.

Configure performs Odoo Pocket policy dry-run/apply/dry-run, cron/outbound
policy dry-run/apply/dry-run, then creates Paperless identities, integration
identity, Odoo mappings and object permissions. It only consumes the existing
issuer/client settings and performs normal OIDC discovery; it never calls a
Pocket mutation API.

## 6. Gate and admit

Before gate, execute the required browser journeys against loopback/staging
ingress for administrator, collaborator, read-only accountant, multi-company
isolation and Paperless Documents. The identity owner takes approved read-only
Pocket state snapshots before/after the rehearsal; their hashes must match.
Write the mode-`0600` journey evidence without secrets or raw subjects.

```bash
make production-cutover-gate \
  ENV_FILE=/approved/private/production.env \
  CANDIDATE=/approved/private/candidate \
  JOURNEY_EVIDENCE=/approved/private/browser-journeys.json \
  FINGERPRINT=<approved-64-hex>

make production-cutover-admit \
  ENV_FILE=/approved/private/production.env \
  CANDIDATE=/approved/private/candidate \
  FINGERPRINT=<approved-64-hex>
```

Gate starts the pinned internal renderer, Step CA and DSS services and requires
the Odoo Sign service smoke check to pass. It then rechecks
product/migration boundaries, database/image release identity,
the exact installed action registry, Accounting totals/reconciliation,
multi-company roles, Paperless sanity, links/checksums/object permissions,
service health and journey evidence.

Admission records the fingerprint in Odoo and private cut-over state before
starting the approved cron worker policy. It permanently disables candidate
reset. Ingress activation remains an infrastructure-owner action. Mail,
provider credentials, Paperless mail/webhooks and regulatory integrations are
still absent/disabled until separate reviewed activation runbooks.

## 7. Restart before admission and rollback after admission

Before admission only, a fingerprint-confirmed reset removes exactly the
candidate-owned application containers/volumes and returns state to preflight:

```bash
make production-cutover-reset \
  ENV_FILE=/approved/private/production.env \
  CANDIDATE=/approved/private/candidate \
  FINGERPRINT=<approved-64-hex>
```

It refuses foreign volumes and never removes external networks, Pocket ID or
another Compose project. After admission, this command is permanently refused.
Use the normal coordinated Odoo/Paperless backup-and-recovery procedure for an
admitted system. If final cut-over has not been accepted, keep Online frozen and
read-only; do not resume writes in two systems. If Online activity is resumed,
discard the candidate and rebuild from a new exact final export.
