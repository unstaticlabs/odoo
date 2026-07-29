# Paperless-backed Documents operations

## Qualified deployment

The `paperless` Compose profile pins `ghcr.io/paperless-ngx/paperless-ngx:3.0.4`
(qualified image digest
`sha256:3838b9a4260d23acc5bb63aed407138435e70b56e5806f4baa350ca184e57582`),
PostgreSQL 16, and Valkey 8.1.3. Paperless 3.x REST API version 10 is the
supported contract. Odoo rejects another API version or server major and shows
the detected versions in Settings diagnostics.

Start only this profile with:

```bash
docker compose --profile paperless up -d --wait
```

Paperless binds to loopback by default. Production must route it only through
the secured ingress and individual SSO. Set all placeholders in `.env` from the
secret manager, including a long random secret key, database password, initial
administrator credentials, allowed hosts, CORS/trusted origins, and the Odoo
service token. Never expose that token to the browser.

Named volumes separate PostgreSQL, broker state, Paperless data/search state,
authoritative media, portable export, and consume staging. Odoo has no
`depends_on` relationship to Paperless and uses a separate filestore.

## Identity and permissions

Create a non-human Paperless integration owner with only the API model
permissions required for documents, tasks, metadata reads, and document object
permission updates. Store its token only in Odoo system parameters/secret
injection. Map every direct Paperless user under **Documents > Paperless
identities**. Never map users to a shared administrator.

Permission synchronization is fail closed: a failed sync blocks Paperless deep
links and marks the document unsafe. Test actual document object permissions;
tag or correspondent permissions alone are insufficient.

## Monitoring

Monitor the Odoo and Paperless health endpoints, worker queue depth, failed
tasks, consume errors, storage capacity, last successful Odoo sync, permission
sync failures, missing documents, and backup age. A Paperless outage must page
the archive owner but must not restart or block Odoo.

## Upgrade and rollback

1. Read the release and migration notes. Paperless 3 requires an upgrade from
   2.20.15; do not skip the supported source version.
2. Produce coordinated Odoo/Paperless backups, portable export, and integrity
   manifest; record the current image digest.
3. Restore the backup into an isolated rehearsal and run migrations, reindexing
   where the release requires it, API contract tests, permissions, preview,
   download, OCR search, ingestion, and manifest comparison.
4. Pin the new exact tag and digest, deploy Paperless independently, and run
   **Test connection** plus a full Odoo reconciliation.
5. On failure, stop Paperless, restore its database/media/data set together,
   restore the previous pinned image and configuration, then reconcile. Do not
   roll back only the database or only media.

## Backup sets

For the same backup ID and maintenance window capture:

- Odoo PostgreSQL, complete filestore, configuration/secrets, installed modules,
  and git revision;
- Paperless PostgreSQL, media/originals, data/search state, configuration/secrets,
  and pinned image digest;
- a Paperless `document_exporter` export with originals, archive files,
  thumbnails, and split JSON manifests;
- the cross-system JSON generated with:

```bash
USL_BACKUP_ID=2026-07-29T0900Z \
docker compose exec -T odoo odoo shell -d "$ODOO_DB" --no-http \
< scripts/odoo/documents_integrity_manifest.py > integrity.json
```

Encrypt backups and exports at rest, restrict host access, keep them off-host,
and do not treat the exporter as the operational backup.

## Restore acceptance

Restore into isolated networks and new database/volume names. Confirm database
integrity, Paperless `document_sanity_checker`, representative media SHA-256
checksums, API v10, object permissions, Odoo filestore access, linked-document
and relationship counts, no unexplained missing/orphaned IDs, and successful
opening of accounting, legal, and operational evidence. Record the result and
timestamp in the backup inventory. Starting containers alone is not acceptance.

During restore tests keep electronic invoice and e-reporting live flags at `0`.

