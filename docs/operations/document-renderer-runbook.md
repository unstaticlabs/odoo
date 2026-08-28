# Governed document renderer runbook

## Local isolated startup

Initialize the immutable package and verify its pin:

```bash
git submodule update --init --recursive services/usl-document-renderer
make document-renderer-check
make document-renderer-certs
```

Use a worktree-specific Compose project, database, ports and volumes. Keep
`USL_EINVOICE_LIVE_ENABLED=0` and `USL_EREPORTING_LIVE_ENABLED=0`.

```bash
COMPOSE_PROJECT_NAME=usl-doc-<worktree> \
ODOO_HTTP_PORT=<unique-port> ODOO_GEVENT_PORT=<unique-port> \
docker compose --profile document-renderer up -d \
  usl-document-renderer odoo
```

The renderer has no host port. Odoo reaches
`https://usl-document-renderer:8443` on the internal
`document-renderer` network using the client certificate mounted at
`/run/secrets/document-renderer`. Use **Settings > Document Templates > Check
renderer** for the application-level health and pinned-revision check.

## Release controls

- `make document-renderer-check` must pass before building or releasing.
- Production supplies `USL_DOCUMENT_RENDERER_IMAGE` as the reviewed registry
  digest. A branch, floating tag or runtime Git checkout is forbidden.
- Record the built image ID and verify its
  `org.opencontainers.image.revision` label before publishing.
- The embedded template revision must equal the submodule gitlink and Odoo's
  `renderer_expected_revision` parameter.
- Production certificates come from the deployment secret store. Local
  credentials under `private/document-renderer-certs` are disposable and
  ignored by Git.
- The service keeps a read-only root filesystem, non-executable bounded tmpfs,
  non-root UID, dropped capabilities and no external network route.
- Never enable shell escape or mount arbitrary template/customer directories.

## Certificate rotation

For a local worktree, move the old ignored certificate directory to a private
backup, run `make document-renderer-certs`, and recreate both renderer and Odoo
containers. Verify that a TLS client without `odoo.crt` is rejected and that
the Settings health action reports the exact pinned revision. In production,
rotate the CA/server/client chain through the secret-management procedure and
keep the previous deployment available for rollback until both probes pass.

## Failure behavior

A renderer outage, revision mismatch, TLS failure, invalid company identity or
conformance failure blocks only new covered renders. Odoo must show an
actionable Settings link and must not fall back to QWeb. Previously persisted
attachments continue to download, mail and display through normal Odoo access
checks.

Renderer logs are safe to retain operationally because they contain only
request ID, template/revision, canonical payload digest, duration and outcome.
Do not add request bodies, TeX output or PDF bytes to logs. Debug compilation
is permitted only with synthetic fixtures in a disposable local container.

## Qualification

Run the standalone package tests and fixture checks, then validate the pinned
image:

```bash
make -C services/usl-document-renderer test
make -C services/usl-document-renderer fixtures
make -C services/usl-document-renderer pdf-check
```

The image acceptance run covers qpdf, pdfinfo, embedded Lato fonts, Unicode
text extraction and veraPDF for every PDF/A family. Invoice acceptance must
also prove that Odoo's Factur-X XML attachment and PDF/A-3 metadata survive the
visual-PDF replacement. Render all golden pages to PNG and complete the
batched visual review before advancing the image digest.
