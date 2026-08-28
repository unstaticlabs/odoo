# Production image CI boundary

## Contract

The `Distribution image` GitHub Actions workflow is the repository build
boundary for the future production and recovery pipelines:

```text
19-usl @ <40-character-git-sha>
    -> ghcr.io/unstaticlabs/usl-odoo:sha-<40-character-git-sha>
    -> ghcr.io/unstaticlabs/usl-odoo@sha256:<64-hex-image-digest>

19-usl @ <40-character-git-sha>
    -> ghcr.io/unstaticlabs/usl-odoo-backup:sha-<40-character-git-sha>
    -> ghcr.io/unstaticlabs/usl-odoo-backup@sha256:<64-hex-image-digest>
```

Commit tags are immutable lookup aids. Digest references are the release
identities and are the only references production GitOps may consume. Do not
deploy `19-usl`, `latest`, another branch tag, or a commit tag without resolving
and recording its digest.

The Distribution image embeds and CI verifies the exact commit, pinned OCA bundle digest,
reviewed action-risk policy digest, and `distribution` runtime label. BuildKit
attaches an OCI SBOM and maximum provenance. GitHub also records build
provenance for the pushed digest through its artifact-attestation service.
The backup-tool image independently embeds the commit and pinned PostgreSQL 16
and Restic runtimes. It receives the same SBOM, provenance, digest and
attestation treatment.

## Workflow behavior and outputs

Every pull request runs repository unit checks, the existing action-risk and
delivered-registry qualification, and no-push builds of the Distribution and
backup-tool images. This includes stacked pull requests whose temporary base is
another feature branch; the `19-usl` ruleset still makes this check mandatory at
final integration. The PR job has only `contents: read`, does not log in to
GHCR, and cannot access production credentials.

A push to `19-usl` runs the same qualification and then the separately
permissioned publish job. That job uses the built-in `GITHUB_TOKEN` only,
builds and pushes both images once, verifies them by digest, attests each, and
publishes all of these outputs:

- job outputs `image`, `tag`, `digest`, `digest_reference`, and
  `metadata_artifact`, plus `backup_tool_image`, `backup_tool_tag`,
  `backup_tool_digest`, and `backup_tool_digest_reference`;
- a GitHub step summary showing both digest references;
- artifact `distribution-release-<git-sha>` containing
  `distribution-release.json` with schema `usl-distribution-release/v2`.

The JSON artifact is the stable cross-workflow interface for the future
production GitOps pipeline. That pipeline must select a successful
`Distribution image` run for the intended `19-usl` commit, download the named
artifact, run:

```bash
python3 scripts/distribution_release.py validate \
  distribution-release.json \
  --commit <expected-40-character-sha> \
  --image ghcr.io/unstaticlabs/usl-odoo \
  --backup-tool-image ghcr.io/unstaticlabs/usl-odoo-backup
```

and deploy only `image.digest_reference` and
`backup_tool.digest_reference`. It must also verify both GitHub attestations
before promotion. A rerun reuses an existing commit tag only when
all embedded identity labels match; a conflicting tag or invalid digest fails
closed instead of overwriting release identity.

Manual `workflow_dispatch` runs qualification and both no-push image builds for
the selected ref. It cannot publish. Production deployment, database backup or
upgrade, Komodo orchestration, health verification, and recovery are outside
this workflow.

## GitHub settings requiring an administrator

The repository was inspected on 2026-08-28. It is public, its default branch is
`19-usl`, and active ruleset `no force push no delete` targets the default
branch with deletion and non-fast-forward protections and no user bypass. No
classic branch protection was found. The Elio agent has `MAINTAIN`, not
administrator access, so Actions and organization package policies could not
be read or changed.

After this workflow has run at least once, a repository administrator must:

1. Extend the `19-usl` ruleset to require pull requests, at least one
   independent approval, dismissal of stale approvals, resolved conversations,
   and branches current with `19-usl` before merge.
2. Require `Distribution image / Qualify repository and image` and
   `Agent process / contracts`. Do not require the publish job on PRs because
   it intentionally runs only after merge.
3. Keep force pushes and branch deletion blocked. Restrict bypass to a named,
   audited emergency role if an operational exception is ever necessary.
4. Keep merge commits enabled and disable squash and rebase merging so the
   repository's reviewed integration history is preserved.
5. Set the default workflow token to read-only. Permit this workflow's explicit
   `packages: write`, `id-token: write`, and `attestations: write` only for the
   protected post-merge publish job. Do not add PATs, SSH keys, production
   credentials, or production environments to PR workflows.
6. Confirm the `usl-odoo` GHCR package is linked to this repository, permits
   this repository's Actions workflow to publish, and has the visibility and
   pull policy required by the future production runner. Limit package write
   access to trusted repository automation and administrators.
   Apply the same controls to `usl-odoo-backup`.
7. Create or select a Lead Developer CODEOWNERS team, then protect
   `.github/workflows/**`, `Dockerfile`, `docker/**`,
   `scripts/distribution_release.py`, and future deployment/upgrade tooling
   with required Code Owner review. No team name is guessed in this change.
8. Review the organization's allowed-Actions policy so the exact SHA-pinned
   GitHub and Docker actions used here are permitted. Enable dependency update
   review for those pins; never replace them with mutable major tags.

No GitHub production environment or persistent self-hosted runner is required
for this build boundary. A later deployment feature must create its own
protected environment, credentials, approval gate, migration/backup sequencing,
verification, and recovery contract without changing this digest interface.
