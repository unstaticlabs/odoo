# Backup and recovery

`scripts/usl-stack` is the only supported backup and restore interface. It
operates on versioned targets in `operations/targets/`; it does not infer a
project, database, secret file, port, or release from ambient variables.

## Recovery unit

One cohort contains the Odoo and Paperless PostgreSQL dumps, Odoo filestore,
Paperless originals, Trash and consume state, complete Sign recovery secrets
and evidence, and MCP OAuth state for production. Sign recovery includes the
Step CA database, offline root, provisioner, mTLS material, and DSS sealing and
manifest keystores. OCR output, previews, Tantivy, and vector indexes are stored
in the cohort's reusable cache repository. The shared Ollama volume is not
copied: the cohort records and verifies its image, BGE model digest, and
1,024-dimension contract.

The encrypted durable repository is therefore signing-key-sensitive. Never
expose its credentials, Restic password, restored files, or secret contents in
logs or evidence. Production Sign secrets are restored only to production.
Staging and local runtimes keep their own isolated signing identities.

Staging stores encrypted Restic repositories on the VPS at
`/var/lib/usl-odoo/restic/staging/durable` and
`/var/lib/usl-odoo/restic/staging/cache`. Backup, verification and restore
containers mount these persistent paths; snapshot qualification and checkpoint
receipts remain required. These backups survive container replacement, but not
loss of the VPS storage. Production backups and production-sourced recovery
proofs continue to use R2. Existing staging snapshots in R2 are not migrated or
deleted by this change. Staging snapshots expire after 24 hours in both local
repositories. `scripts/usl-stack --target staging backup prune --json` applies
this policy under the same operation lock used by backup and restore. GitOps
calls it after a successful staging upgrade and reopening, like production's
deployment retention stage. There is no separate timer: on idle days snapshots
remain until the next deployment cleanup. Production retention remains unchanged.
`backup prune` also deletes stale local capture directories. See
[Local capture retention](#local-capture-retention).

## Backup

The target must reference a validated release manifest whose images exactly
match the running Odoo, Paperless, Sign, MCP, and renderer containers. A
mismatch stops before writers are paused.

```bash
scripts/usl-stack health --target production
scripts/usl-stack smoke --target production
scripts/usl-stack backup create --target production --json
```

The command pre-pulls the backup tool, pauses the application writers and Step
CA, dumps both databases, captures durable and reusable-cache state, restarts
the writers, writes both Restic snapshots to the target repositories, verifies their identities, and
marks only the verified durable snapshot as recovery-eligible. The JSON result
contains the full snapshot IDs and timings. Do not use `latest` or abbreviated
snapshot IDs.

The host retains `capture.json` and `receipt.json` under the exact
`<state-directory>/backup-runs/<run-id>/` directory. A retry resumes only that
run and rejects a missing or different capture manifest. The final receipt is
content-digested and binds the captured cohort, durable snapshot and reusable
cache snapshot.

Progress and capacity messages are written to stderr; the final result remains
valid JSON on stdout. Every phase reports its start, completion, and elapsed
time. A failure ends with one concise cause. Activation failures also state
whether the previous generation was restored successfully.

List and recheck recovery points without changing a runtime:

```bash
scripts/usl-stack backup list --target production --json
scripts/usl-stack backup select --target production --json
scripts/usl-stack backup verify --target production \
  --snapshot <64-character-qualified-snapshot-id> --json
```

`backup select` is the unattended-controller interface. It rejects malformed or
ambiguous Restic timestamps, recovery tags from another target, legacy cohorts,
and any verification result that does not name the exact selected production
snapshot. Operators can still use `list` for inspection, but automation must not
reimplement selection from its untrusted rows.

## Independent staging recovery restore

This low-level command is for an explicitly authorized recovery or disposable
restore, not the ordinary staging release path. A persistent production-derived
staging refresh must use `release staging-reset-from-production`; that command
requires the pre-production staging intent, admitted production receipt, exact
production attempt claim, and a newer post-admission qualified backup. Ordinary
staging releases use `release reconcile-staging` and preserve current staging
data. Staging is restored into uniquely named volumes, and the existing staging
generation remains the rollback generation until the candidate passes every
gate.

```bash
scripts/usl-stack restore run \
  --source production \
  --target staging \
  --snapshot <64-character-qualified-snapshot-id> \
  --json
```

The restore performs these steps unattended:

1. validate the target, secrets, source release, and rendered authentication
   contract before materialization; staging must identify itself explicitly and
   always require Pocket ID for Odoo. Paperless may remain an internal-only
   commissioning service bound exactly to its loopback endpoint, absent from
   the public ingress network, and with OIDC disabled. If any non-loopback
   Paperless URL is configured instead, admission requires HTTPS, a distinct
   Pocket ID client, exact callback/base URLs, disabled regular login, and the
   complete OIDC configuration. During
   first-v3 staging adoption the fixed launcher also transfers public ingress
   to the stable gateway and proves HTTP and websocket maintenance responses;
2. pre-pull every immutable release image;
3. create generation-labeled volumes and a private network;
4. restore both databases and all durable/cache resources while retaining the
   target's isolated Sign identity outside production;
5. run required module upgrades with the approved staging runtime identity and
   both regulatory live flags forced off;
6. neutralize staging and isolate MCP OAuth state;
7. explicitly reconcile Pocket ID from the approved staging environment, then
   admit the enabled Odoo provider, issuer, public URL, client identity, scopes
   and empty database secret without logging credentials. Synthetic
   authorization and deliberately invalid authorization-code requests prove the
   Odoo client, redirect and secret without creating a login session or token.
   The same checks run for Paperless only in public OIDC mode; internal-only
   Paperless is admitted by proving its loopback binding and lack of public
   ingress instead;
8. reclaim download scratch before activation;
9. atomically switch staging and retain the previous generation;
10. apply the target's versioned CPU, memory, PID and OOM-priority policy;
11. verify HTTP health, Ollama identity, exact business controls, queues,
   filestore coverage, Paperless originals, OCR, previews, Tantivy, and vectors.

Attempt-scoped staging releases also retain the redacted Compose authentication,
Pocket ID client-admission, and any staging OAuth-preservation evidence in the
generation directory. The admission receipt binds that evidence digest, and an
idempotent retry returns it only after exact identity and digest validation.

The first v3 activation also records the exact validated v2 Compose identity.
If a post-activation gate fails, rollback accepts only the staging validation
directory, generation overlays and environment file allowed by the transition
contract. It removes the stopped canonical anchor before restarting the legacy
service, recreates that service without starting its legacy gateway, and
disconnects it from Cloudflare before start. The legacy Odoo remains reachable
through `odoo-staging-app` behind the stable gateway, so later runtime
inspection cannot see two competing Odoo anchors or public aliases. Gateway
start, alias transfer, or rollback failure leaves maintenance closed and is
safe to retry after interruption or reboot.

Ordinary generation activation and rollback never pass an unqualified Compose
`up` or `stop`. They name only the target's application services; the stable
gateway stays attached with the same container identity and IP. Gateway drift
is admitted semantically, including the SHA-256 content of its Nginx
configuration, because Compose's raw service hash changes when an identical
relative bind mount is rendered from a different immutable checkout path. New
gateway creation mounts a persistent content-addressed copy, while an existing
gateway is verified directly even after the launcher's temporary GitOps
snapshot has been removed.

Admission also records the MCP server version and OAuth-vault schema reported
by its versioned readiness endpoint. Production and staging require the vault
to be ready. Sign admission performs trusted, read-only Step CA and DSS health
requests and records only the public trust-bundle digests and DSS engine
version. It never issues a certificate, signs a document, or exposes a key.

Production replacement additionally requires explicit confirmation:

```bash
scripts/usl-stack restore run \
  --source production \
  --target production \
  --snapshot <64-character-qualified-snapshot-id> \
  --replace --confirm production --json
```

Use production replacement only from an approved deployment or incident
workflow. Ordinary operators should prove the snapshot in staging first.

## Disposable daily recovery proof

The no-change daily path takes a new qualified production backup and restores
that same-release cohort without changing staging or activating a production
generation:

```bash
scripts/usl-stack recovery-proof run \
  --target production \
  --proof-id daily-20260904-0400 \
  --evidence-directory /var/lib/usl-recovery-proofs \
  --json
```

The controller provides a unique, stable `--proof-id` for the scheduled
attempt. Reusing the ID after interruption recovers only resources bearing that
exact proof ownership label and reuses the retained backup receipt. A completed
retry returns the immutable receipt without inspecting or changing the runtime.

The evidence directory must be an absolute, dedicated host path. The command
rejects paths that overlap the production runtime directory, storage tiers,
operation secrets or Sign state. The directory retains canonical state,
failure and completion receipts. It never retains repository credentials,
database passwords, restored files or Sign keys.

The proof creates uniquely named Docker volumes, one private network and two
database-only containers. Every persistent resource carries
`com.unstaticlabs.recovery-proof.owner=usl-disposable-recovery-proof`, the exact
proof ID, production source and logical role. It does not use Compose, attach a
gateway, start Odoo or Paperless workers, write `active.json`, use a release
candidate or read staging configuration. The materializer restores the exact
qualified durable and cache snapshots, including OCR, previews, Tantivy and
vector indexes. It then queries the restored databases independently and
compares Accounting, Documents, access, queue, cron and release controls with
the quiesced capture manifest.

Restic materialization runs first on a separately labelled, short-lived egress
network so it can reach the backup repository. No application or database
container joins that network, and the command removes it before starting the
restored runtime. Secret-bearing environment and evidence content is delivered
to atomic host writers over standard input; it is never placed in process
arguments or command-failure text.

The materializer restores the exact qualified durable and cache snapshots,
including the Odoo filestore, Paperless originals, OCR archive, thumbnails,
Tantivy and vector indexes, the MCP OAuth vault, and Sign recovery material.
The production cohort also carries the OAuth vault's matching Better Auth and
credential-encryption keys, the Paperless Personal-AI keyring, and the
renderer's CA, server and client credentials.
The proof rejects a cohort that restores either state without its matching key
material; those credentials remain inside the encrypted backup and disposable
proof workspace and are removed during exact cleanup.
Before any application worker starts, the proof neutralizes the disposable
Odoo database. It then starts and probes Odoo HTTP, Paperless web and Redis,
MCP OAuth readiness and its unauthenticated boundary, Step CA, DSS, the document
renderer, Gotenberg, and Tika. Read-only smoke checks compare the restored
Accounting, Documents, access, queue, cron, and release controls with the
quiesced capture manifest and sample durable files and the OAuth SQLite
database. The proof never writes `active.json`, uses a release candidate, reads
staging configuration, or attaches the production gateway.
For a non-empty OAuth vault, the MCP `prepare` operation decrypts every active
enrollment with the restored key and the receipt requires refreshed plus cached
principals to equal the active SQLite row count with zero unavailable entries.
An empty vault is recorded explicitly. Durable samples require at least one
file and hash its bytes rather than its pathname.

Before allocating the restored proof, capacity includes the capture that has
already been written. All disposable proof volumes use Docker's filesystem:
the proof reserves the largest configured storage-tier reserve on that same
filesystem (15 GiB when Docker lives on the production bulk disk). Shared tiers
do not add their reserves together. A local or unmatched Docker filesystem
retains the existing 2 GiB minimum. An optional rehearsal must fit this budget;
it does not borrow the deployment reserve or change backup retention.

The command has a hard 1,800-second total deadline. Cleanup runs after success
and failure. Before final cleanup it enters a durable `finalizing` phase and
arms a failure receipt, so cleanup, production runtime-CAS, or final receipt
write failures retain evidence even if the final write itself fails. A process
crash leaves a digest-bound state receipt; the retry validates ownership before
removing only the named proof resources. A missing label or name collision
fails closed.
Operators can exercise each durable boundary without changing the algorithm:

```bash
scripts/usl-stack recovery-proof run \
  --target production \
  --proof-id drill-20260904-material \
  --evidence-directory /var/lib/usl-recovery-proofs \
  --failure-after materialized \
  --json
```

Allowed boundaries are `backup-qualified`, `resources-created`, `materialized`
and `validated`. Use failure injection only in an approved drill. Each injected
failure cleans owned resources and retains a failure receipt.

## Capacity and cleanup

Restore refuses to start or activate below 2 GiB free and reports a critical
warning below 8 GiB. Image pulls happen before new data volumes are created.
Temporary download trees are deleted before activation.

Preview exact generation-owned cleanup candidates:

```bash
scripts/usl-stack cleanup plan --target staging --json
```

Apply only the displayed plan:

```bash
scripts/usl-stack cleanup apply --target staging --confirm staging --json
```

The active and immediately previous generations are protected. The command
uses exact Docker labels and never deletes foreign projects or volumes.
Housekeeping can also inspect an admitted on-demand target after its application
anchor container has been removed: it validates the retained admission and every
active volume's exact ownership, and protects any remaining retained containers.
It neither wakes that target nor reports it healthy. A missing or corrupt
admission, foreign volume, or ordinary production runtime error still refuses
cleanup. Normal wake and release identity checks remain unchanged. On
production, the same plan also applies paired Restic retention: 14 daily, 8
weekly, 24 monthly, and 10 yearly recovery points. A retained durable snapshot
always protects its exact reusable-cache snapshot. Cache snapshots are also
kept for at least 30 days and the two newest release identities are protected.
Unqualified and historical application-data-only evidence is never silently
promoted into, or deleted as though it were, a complete recovery point.

The automated release controller runs runtime-only cleanup before retrying an
interrupted candidate, then applies full backup retention only after production
has reopened and staging has been refreshed. Both mutations are serialized by
the target lock; pruning cannot overlap backup or deployment.

### Local capture retention

Each backup run writes a local capture directory to
`/var/lib/usl-odoo/runtime/<environment>/<run_id>/`. The directory holds the
database dumps, the durable tree, the reusable cache, `manifest.json`, and
`state.json`. One capture is about 900 MB. Restic holds the recovery copy. The
local directory is only the upload workspace.

`cleanup plan` lists the captures that retention deletes in `delete_captures`.
It lists the captures that retention keeps in `protected_captures`.
`cleanup apply` deletes the listed directories. On staging, `backup prune`
reports and deletes the same sets. The production `retention` release stage
runs `cleanup apply`. The staging stage runs `backup prune`.

Retention keeps a capture when one of these conditions is true:

- `state.json` does not report the status `qualified`. The cohort tool writes
  `qualified` only after it restored and verified both snapshots from the
  repositories.
- The attempt of the capture has a live release run state,
  `runs/release-<identity>.json`, with the status `running` or `failed`. A
  failed run can resume, and the resumed upload needs the capture. The launcher
  keeps its superseded records beside the live one, as
  `release-<identity>.<suffix>.json`; retention ignores them, because a
  historical failure of a release that later succeeded must not protect its
  capture for ever.
- On production, the attempt claimed the active generation or the previous
  generation. The claim is `attempts/<attempt>/claim.json`.
- On staging, the capture is one of the two newest qualified captures.

Retention manages only the run names that the release launcher creates:
`release-pre-<attempt>`, `release-candidate-<attempt>`, and
`release-admitted-<attempt>` on production, and `<attempt>` on staging.
`<attempt>` is `intent-` followed by 48 hexadecimal characters. Retention never
lists or deletes other entries of the state directory. Before it deletes a
directory, `cleanup apply` checks again that the path is a directory, not a
symlink, and that the capture is still `qualified`.

### Retire an explicitly reviewed legacy capture

Ordinary retention deliberately ignores manual and historical capture names.
Do not rename those directories to make them look managed, or delete them by
age. Review one exact legacy capture against a newer qualified managed capture:

```bash
scripts/usl-stack cleanup retire-plan --target production \
  --capture manual-example --replacement proof-daily-0123456789abcdef \
  --owner 'CIO #2374' --reason 'Superseded local upload workspace' --json
```

After checking the identities, run the same command with `retire-apply` and
`--confirm production`. Both operations take the existing lifecycle lock.
The tool refuses symlinks, mounted capture roots or descendants (including
same-filesystem bind mounts), unmanaged replacements, unqualified or older
replacements, and captures from the active or previous release commit. It
persists exact manifest and paired snapshot identities before deletion,
rechecks them under the lock, and retains the completion receipt in
`retired-captures/<capture>.json` outside the retired payload. The plan records
allocated payload bytes; the completion verifies the directory is gone and
records zero remaining payload bytes. These are scoped payload measurements,
not a promise that host-wide free space rises by the same amount.

Only the named local capture directory is removed. Remote Restic snapshots,
current/previous generation data, the replacement capture and frozen historical
source packages are untouched. A successful upload by itself is insufficient:
`qualified` must already record the cohort tool's restore and verification of
both snapshots. These local retirement checks do not establish that a remote
repository still exists or meets the recovery objective; check the current
recovery proof and remote inventory before operational retirement.

Receipts contain recovery metadata and belong on the private host and Odoo
tracking card, not in a public pull request. The synthetic regression exercises
backup, restore, cleanup and a second restore, plus a receipt-write failure.
It does not certify a private production restore or current fleet-wide RPO/RTO.

### Relocate an exact historical archive

Some historical v2 captures predate the keys now required for complete modern
recovery, even though their original receipt says `qualified`. Keep modern
cohort validation strict. Do not rewrite those manifests, relabel them as
current recovery points, or require a new staging runtime simply to move an
identical historical copy off the VPS.

For an explicitly reviewed historical payload, the native helper
`python3 -m operations.historical_capture` uses ordinary encrypted Restic. Under
the target's existing operation lock, mount only that source read-only, provide
owned disposable scratch and a separate private receipt directory, and use the
existing production durable repository and its normal backup environment:

```bash
python3 -m operations.historical_capture /historical-capture \
  --scratch /owned-scratch --receipt /receipts/manual-example.json \
  --target staging --capture manual-example \
  --repository '<existing-production-durable-repository>'
```

The helper uploads all original bytes, including `manifest.json`, `state.json`,
embedded release, database dumps, keys and cache. It restores the new snapshot
independently and compares native file-tree identity plus modes, ownership,
modification times and extended attributes. It also verifies the source did not
change during archival. A successful retry reuses the verified snapshot; a
failed read-back leaves scratch for the owner's bounded investigation window.
The caller must classify that scratch using the disposable-artifact policy.

Archive snapshots carry `usl-historical-capture-archive`, `cio-2374`, target and
capture tags. They carry **no** `usl-cohort`, `durable` or `recovery-eligible`
tags. Native cohort retention ignores them; their referenced packs remain
protected during scoped Restic pruning. These finite historical preservation
anchors are retained until a maintainer explicitly reviews their own retention.
This does not introduce another periodic backup stream.

Store the verified, fsynced receipt at
`<target-state-directory>/historical-archives/<capture>.json`. Then review:

```bash
scripts/usl-stack cleanup retire-plan --target staging \
  --capture manual-example \
  --historical-archive-receipt /var/lib/usl-odoo/runtime/staging/historical-archives/manual-example.json \
  --owner 'CIO #2374' --reason 'Exact historical bytes verified in encrypted archive' --json
```

Apply the reviewed plan with `retire-apply --confirm staging`. This path binds
the exact local byte and metadata fingerprint to the separate remote archive
receipt, retaining both the archival and retirement receipts. Immediately before
removal it also checks that the exact immutable remote snapshot still exists
with the archive tag and expected source path, using the existing production
backup credential context. An unreachable or missing anchor preserves the local
payload. It preserves active and previous releases, data-cohort references,
mount sources, named-volume devices and lifecycle locks. A stable gateway's
old Compose label alone does not make a separately archived capture current
business data. Ordinary cleanup still protects that gateway generation and its
configuration; any physical reference to the capture still prevents retirement. It does not require a modern replacement because it relocates the same
historical copy; it explicitly reports `modern_recovery_certified: false`.
The modern replacement path above retains its newer-qualified-cohort requirement.
Neither path removes active runtime data or modifies original historical evidence.

## Host filesystem observations during cleanup

The operations image declares its execution context. Each target declares
`filesystem_scope`: `host` for the deployment host, or `namespace` for local
development data. Native host and SSH execution read their own host metadata;
explicit namespace targets keep their own filesystem view.

A host-scoped local operations image obtains canonical paths and the current
host mount table through the root-only Unix socket at
`/var/lib/usl-odoo/host-filesystem-metadata/observe.sock`. GitOps installs and
smoke-tests that native host service before publishing an operations image
that requires it. The existing state-directory bind carries the socket into
both current and retained operations constructors.

The service accepts bounded absolute paths and returns canonical paths plus a
fresh structured mount inventory. It checks that its own mount namespace is
PID 1's before and after observing. The interface returns metadata and has no
file-content, command-execution or filesystem-write operation. Requests time out
after five seconds of total monotonic elapsed time and are chunked by path count
and encoded size. Protocol v1 response fields remain stable; future metadata
extensions use a coordinated versioned provider and client.

Validation before capture and workspace deletion refuses unavailable or malformed
observations and observations from the wrong namespace before removing data. A real local image executor with
an older custom target must declare its scope explicitly; native host and SSH
readers continue accepting older target files. Missing-provider errors name the
host observation service as the next repair. The cleanup guard does not infer
host visibility from the container's mount table or resolve host aliases inside
the image's filesystem.

Qualification includes a host-global bind created after the operations executor
has started, an unused volume referencing an external host symlink, and the same
fixtures after their exact references are removed. Evidence records the tested
executor as well as the source revision. Synthetic namespace fixtures declare
their placement explicitly and do not certify host cleanup.

## Service-level objective

The first clean production-to-staging benchmark completed on 2 September 2026
in 332.411 seconds. It reused OCR and vector state and matched all recorded
business controls. The unattended backup plus fresh restore target is under 30
minutes; unchanged content-addressed images should be reused rather than built.

Expected warm-path timing is 5–10 minutes for restore and validation, plus the
backup upload time determined by changed durable content. A missing component
image may add several minutes. A run approaching 30 minutes is abnormal: use
the phase timings to identify image transfer, Restic transfer, materialization,
activation, or validation before retrying.

Operation events are stored under each target's private runtime directory.
Secret values are never written to cohort manifests or logs; manifests contain
only resource identities and digests. Cohort v1 snapshots predate complete Sign
secret capture and must not be used as complete production recovery points.


After a failed candidate, recovery also handles the case where the generation
never switched: it restores the recorded active cohort, proves the baseline healthy,
and archives the failed claim under `aborted-attempts`. The same release inputs
can then be retried without deleting the failure evidence. Production that has
crossed activation is repaired forward; this abort path cannot roll it back.

The operations image includes the boundary scripts and `rsync` used to preserve
staging's OAuth vault during a production-data refresh. The copy verifier is
exercised as a real subprocess in repository tests.


### Existing failed work in an isolated recovery proof

A usable-service recovery proof checks that source workflow failures survive
unchanged. It reads typed identity, state and error fingerprints from the
verified restored database before quarantine or application startup, and checks
them again afterward. A replacement row, changed error, missing failure or new
failure rejects the proof even when the total count stays the same. PostgreSQL
returns only counts and SHA-256 fingerprints; record IDs and error text remain
inside the database. Cron activation is excluded because quarantine disables
jobs; job identity, failure counters and first-failure timestamps remain checked.

Completed proofs use `usl-disposable-recovery-proof/v3` and retain the preserved
source failures as private context. Existing v2 passed receipts remain readable.
This recovery comparison does not clear, retry or acknowledge business work.
Generic release, admission and smoke validation retain their zero-failed-work
rule, so the existing backlog still needs its own operational follow-up.
