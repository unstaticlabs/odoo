# Backup and recovery runbook

## Recovery boundary

After Community production admission, PostgreSQL and the database-specific
Odoo filestore are the canonical Odoo recovery unit. A dump without its
filestore is not a backup. A successful Restic upload is still only a pending
snapshot: the snapshot becomes verified only after a real isolated restore,
native Odoo neutralization and data/attachment checks.

Odoo recommends daily database and filestore backups copied to a remote
archive server that is not accessible from the application server. See the
[Odoo 19 deployment guidance](https://www.odoo.com/documentation/19.0/administration/on_premise/deploy.html).
Odoo's own duplicate-database procedure uses each installed module's
`data/neutralize.sql`; this implementation invokes that native mechanism from
the exact source image digest. See [Odoo on-premise duplicate database
neutralization](https://www.odoo.com/documentation/19.0/administration/on_premise.html).

This Odoo-only primitive does not replace the coordinated Odoo/Paperless
recovery point required when a release changes both systems. The pre-admission
portable migration candidate remains restart material only and the old Online
dump is not a post-admission rollback source.

## Trust model

The pipeline is deliberately split:

```text
prepare -> push -> restore -> verify
```

- the Prepare stage first runs `preflight`, which sees only the Restic
  configuration and fails on absent or cross-environment bindings before any
  source data is touched;
- `prepare` can reach the production PostgreSQL network and mounts the Odoo
  data volume read-only. It has no R2 credentials.
- `push` can read only staged artifacts and R2 bindings. It cannot reach the
  production database or filestore.
- `restore` downloads an exact 64-character Restic snapshot into named scratch
  storage and restores it into a dedicated PostgreSQL 16 clone plus isolated
  filestore.
- `verify` checks restored data and files, then changes the Restic state from
  `pending` to `verified`. Finalization requires the durable successful-restore
  receipt.

The restored PostgreSQL and Odoo containers have no published ports and use an
internal Docker network without outbound access. The only reset operation is
hard-coded to `clone-db` and database `odoo_restore`, and requires
`USL_RESTORE_RESET_CONFIRMED=isolated-odoo-restore`. There is intentionally no
command that overwrites production.

Compared alternatives:

- The QA stack's inline scripts were proven operational, but copying them into
  another infrastructure repository would create divergent logic. The chosen
  versioned runtime and manifest stay with the Odoo release.
- Sending the live filestore directly to Restic is simpler, but can capture DB
  references to files not yet copied. The chosen flow finishes `pg_dump -Fc`
  first and then stages the filestore, preferring harmless orphan files.
- A guarded live restore was rejected for v1. Clone-only restoration makes the
  safe operation the only available operation.

## Backup identity and manifest

The release metadata artifact supplies both immutable images:

```text
ghcr.io/unstaticlabs/usl-odoo@sha256:<digest>
ghcr.io/unstaticlabs/usl-odoo-backup@sha256:<digest>
```

Tags are lookup aids only. Production and recovery configuration must use
digest references. Every backup carries a manifest conforming to
`operations/contracts/odoo-backup-manifest-v1.schema.json` with its timestamp,
consistency mode, database/PostgreSQL version, exact Git and image identities,
row counts, dump SHA-256/size and filestore/attachment metadata.

`prepare` opens a repeatable-read transaction, exports its PostgreSQL snapshot,
counts rows from that snapshot and supplies the same snapshot to `pg_dump`.
Consequently all restored counts must match exactly; one-sided "at least"
comparisons are not accepted. These source tables are required and non-empty:
`res_users`, `res_company`, `ir_module_module`, and `res_partner`.
`account_move` and `ir_attachment` may legitimately be empty but must exist and
match. Every non-null `ir_attachment.store_fname` must be a safe relative path
and resolve to a regular restored file.

## Operator commands

After supplying the bindings below:

```bash
scripts/odoo-backup create --mode live
scripts/odoo-backup list
scripts/odoo-backup list --json
scripts/odoo-backup verify <full-restic-snapshot-id>
scripts/odoo-restore clone <full-restic-snapshot-id>
scripts/odoo-restore destroy <clone-id> --confirm <clone-id>
```

`create` runs all four stages and prints the final verified snapshot ID. A
failed restore leaves the snapshot tagged `pending` and retains the clone for
inspection. `verify` repeats the full remote restore and verification; it does
not merely run `restic check` or inspect a dump catalog. `clone` leaves its
isolated resources available and prints the exact cleanup command. Cleanup
refuses a wrong confirmation or a volume without the expected Compose project
ownership label.

Use a quiesced checkpoint only after Odoo writers have been stopped:

```bash
export USL_BACKUP_QUIESCED_CONFIRMED=odoo-writers-stopped
scripts/odoo-backup create --mode quiesced
```

Quiesced mode also rejects every other client connection to the source
database. This is the primitive the future deployment pipeline will call
before an upgrade. It is not the deployment pipeline itself.

## Required configuration and secrets

The following non-secret configuration is mandatory:

- `USL_BACKUP_TOOL_IMAGE` and `USL_BACKUP_TOOL_IMAGE_DIGEST`: the same backup
  tool digest reference;
- `USL_SOURCE_GIT_SHA`: full 40-character deployed commit;
- `USL_SOURCE_IMAGE_DIGEST` and `ODOO_SOURCE_IMAGE`: the same Odoo distribution
  digest reference;
- `ODOO_PRODUCTION_DB_NAME`, host, port and user;
- `ODOO_PRODUCTION_FILESTORE_VOLUME` and `ODOO_PRODUCTION_DB_NETWORK`;
- staging, state, and cache volumes carrying the immutable label
  `com.unstaticlabs.owner=odoo-production-backup` (the wrappers create missing
  volumes with this label and reject pre-existing foreign volumes);
- `RESTIC_REPOSITORY`, ending in
  `/usl-backups/odoo-production/prod` for production or a distinct
  `/qualification/<id>` path for acceptance testing.

Bind secrets from the deployment secret store, never Git:

- `ODOO_PRODUCTION_DB_PASSWORD` or its `_FILE` form;
- `ODOO_PRODUCTION_RESTIC_PASSWORD` or its `_FILE` form;
- `R2_ACCESS_KEY_ID` and `R2_SECRET_ACCESS_KEY`, or their `_FILE` forms.

The production Restic password must be new, at least 20 characters and must not
equal the QA password. The intended Infisical coordinate is
`/backups/restic/odoo-production`. The existing shared R2 credentials may be
bound, but only the dedicated Restic repository path/password protects this
backup's encryption boundary. Empty bindings fail before Restic is invoked;
the runtime never prints secret values.

### Disposable qualification only

The wrappers accept one repository-owned test switch. It cannot name an
arbitrary Compose file:

```bash
export ODOO_BACKUP_QUALIFICATION=local  # named-volume Restic repository
export ODOO_BACKUP_QUALIFICATION=r2     # real R2 qualification namespace
```

Both modes substitute a locally built Odoo image only to exercise native
neutralization before an immutable release image exists. `local` additionally
mounts the explicitly named qualification Restic volume. `r2` retains the
normal production Restic transport but cannot add volumes, networks, source
data, or production database access. Neither overlay is valid GitOps or
production configuration; leave `ODOO_BACKUP_QUALIFICATION` unset there.

## Failure handling

- `prepared`: the named staging volume holds a complete dump, manifest and
  copied filestore; nothing has reached R2.
- `pushed`: a pending Restic snapshot exists but is not a successful backup.
- `fetched` or `restored`: preserve the isolated clone and diagnose restore or
  neutralization failure.
- `restore-verified`: data verification passed, but Restic finalization must
  still succeed.
- `verified`: the exact final snapshot ID is usable.

The named state volume prevents a new scheduled run from overwriting an
unfinished run. Resume the failed stage after repair. If an operator formally
abandons it, preserve the pending snapshot and evidence, then run:

```bash
scripts/odoo-backup stage abandon <backup-id>
```

No command in this feature forgets snapshots or prunes repository packs. The
retention policy remains an explicit unresolved production decision.

## Later Komodo/GitOps activation

This repository contains the canonical Compose services, but this feature does
not modify the separate GitOps repository and does not activate a schedule.
After production admission, the Lead Developer must add a dedicated backup
stack using `deploy/odoo-backup/compose.yaml`, bind the exact v2 release
artifact digests and configuration above, and keep `clone-db` running only on
the internal clone network.

Declare one Komodo procedure with concurrency limited to one run, failure
alerts enabled, timezone `Europe/Paris`, and these four stages:

1. **Prepare:** `RunStackService(preflight)`, then
   `RunStackService(prepare)`.
2. **Push:** `RunStackService(push)`.
3. **Restore:** `RunStackService(restore-fetch)`, then
   `RunStackService(restore-apply)`, then `RunStackService(neutralize)`.
4. **Verify:** `RunStackService(verify)`, then
   `RunStackService(finalize)`.

Schedule it daily at `00:30 Europe/Paris`. This avoids the existing backup
chain on `prod-odoo-nbg1-2` beginning with Paperless at 02:00; the separate
Immich host may use the same wall-clock slot because it has independent compute
and a different backup repository. Do not enable the schedule until a manual
non-empty production-path qualification has passed prepare, R2 push, exact
restore, native neutralization and verification twice, and missed-run alerts
are visible.

## Incident recovery

When recovery is required:

1. protect the damaged environment and evidence;
2. select a full verified snapshot ID and state its timestamp/RPO;
3. run `scripts/odoo-restore clone` and independently inspect the clone;
4. validate users, companies, accounting controls and representative files;
5. design and approve the separate production replacement procedure;
6. resume external side effects only after explicit authorization;
7. record the incident, accepted data gap and follow-up work.

Production recovery is intentionally not automated here. A later deployment
pipeline may consume these primitives, but it must retain an approval boundary
before replacing canonical production data.
