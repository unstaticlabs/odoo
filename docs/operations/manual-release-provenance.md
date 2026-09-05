# Manual recovery release provenance

An explicitly authorized operator can build the same component Dockerfiles
locally and transfer OCI archives to the deployment host. Preserve immutable
image digests, component input hashes, BuildKit provenance and SBOM metadata.
Retain the source commit, build commands, qualification logs and their hashes
in an evidence document before creating the release manifest.

For these releases, use a `refs/tags/recovery-*` source reference and pass
`--operator-run-id` and `--operator-evidence-sha256` to `scripts/release-manifest
create`. The evidence hash identifies the retained document. Do not supply or
invent GitHub workflow run metadata. The manifest records this as explicit
operator provenance and still validates all component, compatibility,
foundation, qualification and release identity fields.

This describes an operator deployment; it does not qualify an unattended
promotion. GitLab intake and promotion still require the normal GitHub source,
workflow and attestation evidence. Reconcile the live release through normal
publication and GitOps promotion when proving the hosted path.

Manual production deployment retains the ordinary qualified backup, candidate
upgrade, business preservation, admission and recovery checks. The existing
operator authorization and current GitOps desired-state requirements apply.
