# Production operations

Production runs immutable CI-built images and coordinated persistent data.
The current local runtime is the authoritative working dataset until the
approved VPS cohort is admitted and its first backup restore passes.

## Release contract

Every production release binds:

- the full Git commit on `19-usl`;
- immutable Odoo, backup-tool, Paperless, document-renderer, and Sign DSS OCI digests;
- the pinned Odoo MCP repository, ref, commit, image digest, and compatibility digest;
- pinned OCA, action-risk policy, module, and source identities;
- the accepted evolving-data cohort fingerprint;
- database UUID, company controls, and required external configuration.

Validate the CI release artifact before promotion:

```bash
python3 scripts/distribution_release.py validate \
  distribution-release.json \
  --commit <40-character-commit> \
  --image ghcr.io/unstaticlabs/usl-odoo \
  --backup-tool-image ghcr.io/unstaticlabs/usl-odoo-backup \
  --paperless-image ghcr.io/unstaticlabs/usl-paperless-ngx \
  --document-renderer-image ghcr.io/unstaticlabs/usl-document-renderer \
  --sign-dss-image ghcr.io/unstaticlabs/usl-sign-dss
```

Deploy only digest references from the validated artifact. Never deploy
`latest`, a branch tag, or an unresolved commit tag. Production deployment is
owned by CI/GitOps and requires an authorized human approval.

## Production data boundary

The coordinated recovery unit includes:

- Odoo PostgreSQL and the matching database filestore;
- Paperless PostgreSQL, broker state, media, data/search, Trash, and export;
- Tantivy and vector state;
- Ollama and the pinned BGE model data;
- Sign Step CA and evidence state where applicable.
- the MCP OAuth vault in coordinated production backups, with its matching
  external encryption and authentication secrets managed separately.

Backups, upgrades, and recovery must preserve the cohort identity. An Odoo
dump without its filestore is not a backup. An uploaded archive is not verified
until it has been restored into isolated storage and its application controls
pass.

## Deployment and upgrade

Before a release or module/schema upgrade:

1. confirm the approved release and exact image digests;
2. pause writers and queue submissions;
3. take a coordinated quiesced checkpoint;
4. verify the recovery point and rollback decision;
5. apply only the approved images and module upgrades;
6. keep live external integrations disabled until the post-upgrade gates pass.

For the protected local transition runtime, create that recovery point with:

```bash
migration/manage transition checkpoint \
  --runtime <transition-id> --label before-upgrade
```

This exact private checkpoint includes local identities and secrets and must
never be uploaded as the production-transfer artifact. Final cutoff instead
uses the sanitized, fingerprinted evolved cohort described below.

After startup, verify authentication, company context, ACLs, attachments,
Accounting balance and report controls, Documents/Paperless links, Sign,
queues, module identity, and the delivered product boundary. Stop on an
unexplained critical error or identity mismatch.

French electronic-invoice reception and e-reporting are separate activations.
Follow the dedicated activation procedure; never infer production eligibility
from offline tests.

## Inbound mail

Production uses one confirmed Gmail IMAP server and routes messages by their
original recipient. Leave the server's **Create a New Record** field empty.
Odoo must resolve these aliases before enabling the `inbound_mail` gate:

- `expense@unstaticlabs.com` to employee expenses;
- `purchases@unstaticlabs.com` to the USL vendor-bill journal;
- `purchases-uslmedia@unstaticlabs.com` to the USL Media vendor-bill journal.

Google Workspace may deliver all aliases to one mailbox, but it must preserve
the alias in `Delivered-To`, `To`, `Cc`, or `Resent-To`. The alias-domain
settings remain `catchall`, `bounce`, and `odoo` for replies, bounces, and the
default sender. Those addresses do not replace functional record aliases.

Set `inbound_mail=true` only after Odoo confirms the IMAP login, every required
alias resolves to the intended model and company, and the mailbox has no stale
unread backlog. Admission then enables `mail.ir_cron_mail_gateway_action` and
runtime validation requires a successful poll within 15 minutes.

## Final local tour and VPS promotion

Keep the protected local runtime writable during the final user tour. Never
reconstruct it from Online. Before the tour, create a coordinated checkpoint
and confirm `transition status` is healthy. Tooling-only commits do not require
an Odoo image or database upgrade; the running application must still match
the last product commit.

After Valentin declares the final cutoff:

1. stop local writers and create a final coordinated checkpoint;
2. merge the reviewed branch into `19-usl` and wait for the v4 Distribution
   release artifact and every immutable image digest;
3. update the frozen local database only if merged product modules changed,
   using the exact merged source and a checkpoint first;
4. capture the sanitized evolved cohort with the CI release artifact;
5. independently restore it into fresh volumes and accept its fingerprint;
6. encrypt and checksum it for transfer;
7. run cutover `preflight`, `stage`, `configure`, and `gate` against the VPS
   while ingress and external side effects remain disabled;
8. inspect the staged VPS, then run `admit` with explicit human approval;
9. create and independently restore the first coordinated production backup;
10. only then declare the VPS canonical. Retire the frozen local source later
    through a separate approved operation.

Production uses HTTPS origins, external Pocket ID and ingress networks, fresh
explicit volumes, the admitted shared Ollama service, and digest-pinned Odoo, Paperless,
renderer, Sign, MCP, and supporting images. Keep e-invoicing, e-reporting,
outbound mail, inbound mail, bank-email ingestion, and unapproved jobs disabled until their
separate activation gates pass.

## Odoo backup primitive

The maintained backup tooling separates preparation, upload, isolated restore,
and verification:

```text
prepare -> push -> restore -> verify
```

Routine operator commands are:

```bash
scripts/odoo-backup create --mode live
scripts/odoo-backup list
scripts/odoo-backup list --json
scripts/odoo-backup verify <full-restic-snapshot-id>
scripts/odoo-restore clone <full-restic-snapshot-id>
scripts/odoo-restore destroy <clone-id> --confirm <clone-id>
```

Use a quiesced checkpoint only after Odoo writers have stopped:

```bash
export USL_BACKUP_QUIESCED_CONFIRMED=odoo-writers-stopped
scripts/odoo-backup create --mode quiesced
```

The backup manifest records source commit and image digests, PostgreSQL
version, database identity, exact table counts, dump SHA-256 and size, and
filestore/attachment metadata. Restored counts must match exactly and every
stored attachment reference must resolve to a safe regular file.

Bind database, Restic, and object-storage secrets through the deployment
secret store. Do not place them in Git, Compose scope files, runtime JSON, or
operator documentation. Production and qualification use distinct Restic
repository paths and passwords.

## Failure handling

- `prepared`: private staging contains a complete dump, manifest, and
  filestore; nothing has been uploaded.
- `pushed`: a pending remote snapshot exists but is not a verified backup.
- `fetched` or `restored`: retain the isolated clone and diagnose the failure.
- `restore-verified`: data checks passed; final snapshot registration remains.
- `verified`: the exact snapshot ID is eligible for recovery.

Do not prune, overwrite, or abandon evidence automatically. Resolve and resume
the failed stage, or record a deliberate abandonment before a new scheduled
run can replace its local state.

## Recovery

1. Protect the affected environment and evidence.
2. Select a full verified snapshot ID and state the accepted recovery point.
3. Restore it into an isolated clone.
4. Verify identities, users, companies, Accounting controls, queues, and
   representative originals and attachments.
5. Approve a separate production replacement procedure.
6. Reconfigure production-only identities and secrets.
7. Resume external side effects only after admission checks pass.
8. Record the incident, accepted data gap, and corrective work.

The historical Online source is not a production rollback source. Before VPS
admission, the protected local runtime and its verified coordinated checkpoint
are the recovery authority. After admission, use verified production backups.

## Admission checklist

Production is canonical only when:

- the final cohort was independently restored without OCR, re-ingestion,
  vector rebuild, or model download;
- release and cohort identities match the approved record;
- Accounting, security, multi-company, Documents, Sign, and product-boundary
  gates pass;
- queues have no unexplained pending, processing, or failed work;
- production Pocket ID, secrets, ingress, and disabled integrations match the
  approved configuration;
- the first coordinated production backup has been independently restored and
  verified;
- rollback and incident instructions are available to the operator.

Keep the local source runtime protected and read-only after final cutoff until
this recovery proof passes.

## References

- [Production image CI contract](production-image-ci.md)
- [Historical reconstruction and cohort interface](migration.md)
- [Product and migration boundary](product-migration-boundary.md)
- [Pocket ID operations](pocket-id-sso-runbook.md)
- [Electronic-invoice activation](activate-french-electronic-invoicing.md)
- [Document renderer operations](document-renderer-runbook.md)
