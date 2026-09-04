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
the writers, uploads both Restic snapshots, verifies their identities, and
marks only the verified durable snapshot as recovery-eligible. The JSON result
contains the full snapshot IDs and timings. Do not use `latest` or abbreviated
snapshot IDs.

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

## Independent staging restore

Staging is restored into uniquely named volumes. Existing staging remains the
rollback generation until the new generation passes every gate.

```bash
scripts/usl-stack restore run \
  --source production \
  --target staging \
  --snapshot <64-character-qualified-snapshot-id> \
  --json
```

The restore performs these steps unattended:

1. validate the target, secrets, source release, and free-space floor;
2. pre-pull every immutable release image;
3. create generation-labeled volumes and a private network;
4. restore both databases and all durable/cache resources while retaining the
   target's isolated Sign identity outside production;
5. neutralize staging and isolate MCP OAuth state;
6. reclaim download scratch before activation;
7. atomically switch staging and retain the previous generation;
8. apply the target's versioned CPU, memory, PID and OOM-priority policy;
9. verify HTTP health, Ollama identity, exact business controls, queues,
   filestore coverage, Paperless originals, OCR, previews, Tantivy, and vectors.

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
uses exact Docker labels and never deletes foreign projects or volumes. On
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
