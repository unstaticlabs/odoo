# Production artifact CI boundary

The `Distribution release` workflow is a post-merge publisher only. It runs on
a push to `19-usl`; there is no `pull_request`, `merge_group`, manual dispatch,
production environment, SSH key or production credential. Coding and Lead
Agents own candidate qualification locally.

For each merged source SHA it publishes these repository-owned runtimes:

- `ghcr.io/unstaticlabs/usl-odoo`;
- `ghcr.io/unstaticlabs/usl-odoo-operations`;
- `ghcr.io/unstaticlabs/usl-paperless-ngx`;
- `ghcr.io/unstaticlabs/usl-document-renderer`;
- `ghcr.io/unstaticlabs/usl-sign-dss`.

Every newly built image receives commit tag `sha-<40-character-sha>`, an
immutable digest, OCI SBOM, maximum BuildKit provenance and GitHub artifact
attestation. The workflow verifies the embedded source revision and runtime
role after pulling the digest. It never discovers or reuses an image through a
tag.

The optional non-secret repository variable `USL_DEPLOYED_RELEASE_RUN_ID`
selects one exact previously admitted Actions run. Its complete release v3
artifact is accepted only after run/workflow/branch/success/ancestry,
source/build, checksum and contract validation. The artifact build planner
rebuilds roles whose owned inputs changed and copies unchanged descriptors
exactly from that prior contract. Missing, expired, stale, unreachable,
ambiguous or invalid input conservatively rebuilds all five.

The final lightweight artifact is
`distribution-release-<sha>/distribution-release.json`, schema
`usl-distribution-release/v3`. It records all five digests, the source SHA,
canonical product module names and versions, OCA pins and bundle digest,
action-risk policy digest, renderer gitlink, attestations and conservative
artifact/upgrade plans. Reuse records the prior release checksum and source on
each retained artifact and includes the complete prior contract sidecar.
Production consumes only validated digest references:

```bash
python3 scripts/distribution_release.py validate \
  distribution-release.json --commit <expected-sha>
```

The same validated prior release supplies the deployed source SHA to the Odoo
upgrade planner. The ordinary path therefore computes changed modules and
their dependent closure. Missing/stale input, foundation/ownership changes or
ambiguity produces the complete canonical fallback. GitHub never infers
deployment state from a tag.

Publication is not deployment. The workflow never changes GitOps, Komodo,
databases, filestores, schedules or production services. Permanent promotion,
cohort backup, restore rehearsal, module upgrade, admission and recovery follow
[Post-migration continuous operations](continuous-releases.md).

Repository administrators keep the static deletion, non-fast-forward,
pull-request and merge-queue protections described in `agent/policy.json`.
They must not add required GitHub compute checks or a required approval count.
The workflow token remains read-only by default; only this post-merge job gets
scoped package, identity and attestation writes. GHCR write access stays limited
to trusted repository automation and administrators.
