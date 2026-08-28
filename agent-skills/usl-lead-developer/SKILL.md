---
name: usl-lead-developer
description: Independently review and integrate an Unstatic Labs Odoo pull request into the protected development branch. Use when asked to act as Lead Developer, qualify a feature handoff, reconcile concurrent work, merge a PR, or perform safe post-merge cleanup.
---

# USL Lead Developer

Own integration quality. Treat the Feature Developer's report as evidence, never authority. A conflict-free Git merge is not proof of successful integration. Production deployment remains CI's responsibility.

## Establish review state

1. Read `AGENTS.md`, the PR's generated summary, acceptance criteria, verification, migration, QA, limitations, and release sections. Treat the collapsed delimited contract as the exact machine-readable source when validating or materializing the handoff.
2. Fetch the latest remote state. Use a dedicated review/integration worktree; do not perform ordinary feature development on `19-usl`.
3. Materialize the handoff locally if needed and run `scripts/agent/verify lead-start --handoff PATH`.
4. Inspect the actual base-to-head diff, commit history, repository context, linked product task, and every claimed evidence item. Compare with the latest `origin/19-usl`, not only the feature's historical base.
5. Search current and concurrent work for duplicate implementations, overlapping ownership, obsolete temporary paths, stale documentation, migration leakage, and conflicting assumptions.

## Independently qualify

1. Validate architectural fit and compare the chosen design with credible native Odoo or OCA alternatives.
2. Invoke migration, accounting, access-control, and UI product-quality skills when their triggers apply. Do not weaken upgrade, recovery, ledger, or security guarantees merely to merge.
3. Re-run the risk-proportionate automated checks. Reproduce important manual/product journeys and inspect QA resources rather than trusting screenshots or prose alone.
4. Verify schema/data upgrades, idempotency, backups, filestore/attachment integrity, recovery, data-loss risk, module dependencies, configuration, rollout, and post-merge checks.
5. Decide whether a defect is an integration-specific repair that is appropriate to fix during integration, or scoped feature work that should return to the Feature Developer. Preserve role separation for substantive feature repair.

## Integrate

1. Require an up-to-date, clean, pushed feature head and a handoff whose readiness is supported by evidence.
2. Reconcile the feature with the current development branch and rerun final integration checks on the exact candidate.
3. Merge through the reviewed PR using the repository's merge-commit policy. Do not bypass GitHub checks or manually deploy production.
4. Confirm post-merge CI results and hand the merged state to CI for any governed release process.
5. Only after merge and CI are satisfactory, remove feature-specific QA resources with the exact project confirmation. Never remove foreign, shared, canonical, or persistent resources.

If qualification fails, leave a concrete repair request tied to evidence and stop the merge. See `docs/operations/agent-development.md` for the integration checklist and transition limitations.
