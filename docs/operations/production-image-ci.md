# Production image CI

The `Distribution release` workflow builds the immutable artifacts consumed by
future deployment workflows. It does not alter a runtime.

## Content-addressed builds

`scripts/component-build` hashes only the tracked inputs of each independently
released component:

- Distribution;
- backup tool;
- Paperless;
- Sign DSS.

The workflow first checks GHCR for `content-<input-sha256>`. If it exists, the
image is reused without building. Otherwise BuildKit builds it with registry
cache, publishes the content tag, records its digest, SBOM and provenance, and
adds a GitHub attestation. A source-only operations change therefore rebuilds
only the backup tool; unchanged product images normally resolve in about one
minute.

Odoo MCP and the document renderer remain separately owned images. The
Distribution workflow verifies their pinned commits, compatibility metadata,
OCI revision labels, and digest references before assembling a release. After
that verification, the protected Distribution release job adds an artifact
attestation for the exact renderer digest with `unstaticlabs/odoo` as the
trusted integration owner. GitHub stores the public-repository attestation;
the separately owned renderer package does not need to accept a cross-package
write. The workflow neither rebuilds nor retags the renderer.

## Release artifact

The final `usl-release.json` binds:

- the exact repository commit and workflow run;
- every repository-owned component input hash and image digest;
- the pinned MCP ref, commit, compatibility digest, and image;
- the renderer source and image;
- the Ollama image, model digest, and embedding dimension.

Validate an artifact before use:

```bash
scripts/release-manifest validate usl-release.json --commit <full-git-commit>
```

Deploy only its digest references. Branch names, commit tags, and `latest` are
lookup aids, not deployable identities.

## Permissions

The workflow uses the repository `GITHUB_TOKEN` with job-scoped permissions:

- `contents: read` for source;
- `packages: read` when verifying external MCP and renderer images;
- `packages: write`, `id-token: write`, `attestations: write`, and
  `artifact-metadata: write` for repository-owned image publication and the
  exact verified renderer digest's GitHub-stored integration attestation.

Each GHCR package must grant `unstaticlabs/odoo` Actions read access; the four
repository-owned packages must also permit publication from this repository.
No production, SSH, Pocket ID, database, Restic, SMTP, or application secret
belongs in this build workflow.

Configure package access in GitHub under the package's **Package settings →
Manage Actions access**. Grant `unstaticlabs/odoo` read access to the separately
owned `odoo-mcp` and document-renderer packages. Repository-owned Distribution,
backup-tool, Paperless, and Sign packages inherit publication access from
`unstaticlabs/odoo`; their workflow job alone receives `packages: write`.

Deployment admission verifies the renderer digest against the
`unstaticlabs/odoo` attestation owner and this Distribution workflow identity.
It retrieves that renderer bundle from GitHub's attestation store rather than
requiring an OCI referrer write to the separately owned package. The renderer's
own BuildKit provenance remains separate source-build evidence.

The validated repository context is `unstaticlabs/odoo`, not a fork or local
runner. Run 33568552569 at commit `84c8d30159dbc99258c8e44f3316fbdec88bf799`
completed release tests, external image verification, content-addressed
component resolution, and coordinated release assembly in about three minutes.
Unchanged Distribution, Paperless, and Sign images were reused. The backup tool
alone rebuilt because its tracked orchestration inputs changed.

## Deployment boundary

A later protected deployment workflow consumes the release artifact and uses
`scripts/usl-stack` to:

1. verify the running release and take a qualified backup;
2. freeze access and deploy the exact image cohort;
3. run required Odoo module upgrades;
4. run health and business smoke gates;
5. unfreeze on success or restore the pre-release snapshot on failure;
6. recreate staging from the accepted production backup.

Build caching and runtime backup caching are separate: OCI layers stay in
GHCR, while OCR, previews, Tantivy, and vectors use the reusable Restic cache
repository described in [Backup and recovery](backup-and-recovery.md).
