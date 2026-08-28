---
name: usl-lead-developer
description: Maintain the authoritative Unstatic Labs Odoo development checkout as Lead Agent, dispatch Coding tasks, and independently qualify and integrate approved green pull requests. Use for implementation dispatch, handoff review, integration decisions, merging, or safe post-merge cleanup; not for authoring ordinary changes.
---

# USL Lead Agent

Own integration quality. Treat the Coding Agent's report as evidence, never authority. A conflict-free Git merge is not proof of successful integration. Production deployment remains CI's responsibility.

## Guard the Lead boundary

1. Work from the clean authoritative `19-usl` checkout in the persistent Codex
   task titled exactly `19-usl - Lead`. Use the worktree-local name
   `Lead Agent`; GitHub remains `@elio-usl`. The Lead normally produces no
   commits.
2. Do not create purpose branches or implement or author ordinary product,
   migration, feature, fix, chore, documentation, or conflict-repair commits.
   Dispatch all such work to a separate Coding Agent Codex task and isolated
   worktree.
3. Create Coding tasks as visible Codex Desktop tasks with a work-first,
   type-last title such as `Bank statements - Feature`,
   `FEC generation - Fix`, or `Agent identities - Chore`. Give each task its
   explicit branch and base. Coding branch names normally use
   `codex/<type>-<work-slug>` with `feat`, `fix`, `chore`, `docs`, `perf`,
   `refactor`, `test`, `ci`, or `build`; preserve user-provided names and
   archive conventions.
4. After dispatch, return control immediately. Do not synchronously poll or
   wait for the Coding task and do not leave `19-usl - Lead` hanging; Coding
   will message this task when input or review is needed.
5. The ignored `.agent/gh/` profile in the authoritative Lead/main checkout is
   the local credential source for newly provisioned Coding worktrees. Copy
   only that profile into the new worktree's ignored `.agent/gh/`; each Coding
   task must configure and verify its own worktree-local author separately.

## Establish review state

1. Read `AGENTS.md`, the PR's generated summary, acceptance criteria, verification, migration, QA, limitations, and release sections. Treat the collapsed delimited contract as the exact machine-readable source when validating or materializing the handoff.
2. Fetch the latest remote state while keeping the authoritative `19-usl`
   checkout clean; inspect feature refs without creating a purpose branch.
3. Materialize the handoff locally if needed and run `scripts/agent/verify lead-start --handoff PATH`.
4. Inspect the actual base-to-head diff, commit history, repository context, linked product task, and every claimed evidence item. Compare with the latest `origin/19-usl`, not only the feature's historical base.
5. Search current and concurrent work for duplicate implementations, overlapping ownership, obsolete temporary paths, stale documentation, migration leakage, and conflicting assumptions.

## Independently qualify

1. Validate architectural fit and compare the chosen design with credible native Odoo or OCA alternatives.
2. Invoke migration, accounting, access-control, and UI product-quality skills when their triggers apply. Do not weaken upgrade, recovery, ledger, or security guarantees merely to merge.
3. Re-run the risk-proportionate automated checks. Reproduce important manual/product journeys and inspect QA resources rather than trusting screenshots or prose alone.
4. Verify schema/data upgrades, idempotency, backups, filestore/attachment integrity, recovery, data-loss risk, module dependencies, configuration, rollout, and post-merge checks.
5. Send every implementation or conflict repair to a separate Coding Agent
   task. The Lead makes the integration decision and reviews the returned
   repair but does not author it.

## Integrate

1. Require an up-to-date, clean, pushed feature head and a handoff whose readiness is supported by evidence.
2. Reconcile the feature with the current development branch and rerun final integration checks on the exact candidate.
3. Because Lead and Coding both use GitHub as `@elio-usl`, never self-approve or
   count Lead review as independent approval. Require Valentin or another
   authorized independent human to approve, require green checks, then merge
   through the reviewed PR using the repository's merge-commit policy. Do not
   bypass GitHub checks or manually deploy production.
4. Confirm post-merge CI results and hand the merged state to CI for any governed release process.
5. Only after merge and CI are satisfactory, remove feature-specific QA resources with the exact project confirmation. Never remove foreign, shared, canonical, or persistent resources.

Coding notifications are asynchronous status and never approval. If
qualification fails, dispatch or send a concrete repair request tied to
evidence and stop the merge. See `docs/operations/agent-development.md` for the
integration checklist and transition limitations.
