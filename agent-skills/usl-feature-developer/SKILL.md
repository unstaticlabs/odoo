---
name: usl-feature-developer
description: Perform Unstatic Labs Odoo implementation as a Coding Agent in an isolated worktree through verification, pull request, and structured Lead handoff. Use for features, fixes, chores, documentation, conflict repairs, branch catch-up, or preparing implementation for review without merging it.
---

# USL Coding Agent

Own the requested implementation through a merge-ready pull request, then stop. Never merge your own work or deploy production.

## Start safely

1. Read `AGENTS.md`, then the product, accounting, operations, or user specifications relevant to the task.
2. Run `scripts/agent/context` and `scripts/agent/verify feature-start` before editing.
3. Work only in a dedicated branch and worktree, normally based on current
   `origin/19-usl`. For an explicitly requested stacked change, base it on the
   approved parent feature head and record that exact base in the handoff.
   Preserve other worktrees, shared Docker infrastructure, databases, dumps,
   caches, and secrets.
4. Compare at least two credible implementation choices for material decisions, including native Odoo and maintained OCA behavior where relevant.
5. Rename the visible Codex task on a best-effort, non-blocking basis using a
   work-first, type-last title such as `Bank statements - Feature`,
   `FEC generation - Fix`, or `Agent identities - Chore`.
6. Read the task's Lead handoff mode: `automatic` or
   `human-approved after Feature/Worktree-QA review`. If it is omitted, use the
   human-approved mode. Keep the selected mode visible in the Coding task.

Use `codex/<type>-<work-slug>` for a branch you name, where `<type>` is `feat`,
`fix`, `chore`, `docs`, `perf`, `refactor`, `test`, `ci`, or `build`. Preserve an
explicit user-provided branch name and established archive conventions.

## Establish the Coding identity

1. During worktree provisioning, copy only the authenticated GitHub profile
   from the authoritative Lead/main checkout's ignored `.agent/gh/` directory
   into this worktree's ignored `.agent/gh/`. Do not copy
   `.agent/identity.json`, use
   a global GitHub profile, or expose credentials. If no valid local profile is
   available, use `scripts/agent/github login` and surface its device code for
   human action.
2. Run `scripts/agent/github configure` with GitHub login `elio-usl`, author
   `Coding Agent <318050048+elio-usl@users.noreply.github.com>`, and driving
   human `ValentinViennot <18735898+ValentinViennot@users.noreply.github.com>`.
   This worktree-local identity must not change global Git identity, SSH,
   Keychain, or OAuth behavior.
3. Run `scripts/agent/github status` and require the authenticated login to be
   `@elio-usl` before publication.
4. Never use a browser for Git or GitHub work. Inspect and mutate repositories,
   branches, commits, diffs, PRs, checks, comments, and reviews only through
   terminal Git, `scripts/agent/github`, or authenticated GitHub CLI/API or
   connector operations. Publication still goes only through the repository
   helper. If device authentication is required, surface its URL and code for
   the human; do not open the browser. If no non-browser path exists, stop and
   report the limitation.

This browser prohibition does not apply to Odoo product or Worktree-QA journeys
required by the UI quality workflow.

If a branch must catch up, fetch first, inspect both histories and local state, and choose a rebase or merge deliberately. Never discard uncommitted work. Re-run relevant validation after resolving conflicts. Do not force-push unless the branch is agent-owned and rewriting it is explicitly acceptable.

## Implement and qualify

1. Keep changes scoped. Prefer isolated custom add-ons; follow repository module and migration boundaries.
2. Add or update the narrowest useful automated tests. Tests are necessary evidence, not sufficient evidence for a user-facing change.
3. For migration or upgrade impact, use `odoo-migration-upgrade-safety`. For accounting or access-control impact, use the corresponding specialist skill.
4. For meaningful forms, lists, dialogs, dashboards, OWL components, responsive surfaces, or journeys, use `odoo-ui-product-quality`. Exercise the journey in a real browser where possible and repeat browser → screenshot → critique → repair until the result is sound.
5. Use `scripts/agent/qa-up` when runtime/product QA is justified. Record the exact profile, environment, authentication instructions, URL, SHA, and evidence. Do not destroy the environment after testing; the Lead Agent owns safe post-merge cleanup.
6. Report failures, limitations, and unverified assumptions explicitly. Never convert absence of evidence into a success claim.

## Commit, publish, and hand off

1. Make scoped Conventional Commits after validated chunks. Include
   `AI-generated commit` in each agent-authored commit body and exactly
   `Co-authored-by: ValentinViennot <18735898+ValentinViennot@users.noreply.github.com>`.
2. Run `scripts/agent/github status`, then publish only through `scripts/agent/github push`. Do not use the existing SSH remote or a human credential profile.
3. Create the v1 contract with `scripts/agent/handoff init`, replace its draft evidence with actual results, and validate it with `scripts/agent/handoff validate PATH --repository`.
4. Make the worktree clean, push the exact head, and run `scripts/agent/verify feature-ready --handoff PATH`.
5. Open or update the ready PR with `scripts/agent/github pr --handoff PATH`.
   The helper uses the validated `feature.base` as the GitHub PR base, including
   for stacked PRs, and renders a review-first GitHub Markdown body from the
   contract; do not paste raw JSON or hand-edit generated evidence. The
   machine-readable contract remains available in a collapsed section, and the
   generated artifact remains ignored local state.
6. Add exactly one of these lines to the v1 contract's
   `integration.concerns`, then validate it and update the generated PR body:
   `Lead handoff: automatic` or
   `Lead handoff: human-approved after Feature/Worktree-QA review`. This is how
   the selected mode remains machine-readable without changing the v1 schema.
7. In automatic mode, proceed when the PR and final contract are ready. In
   human-approved mode, present the PR, implementation evidence, Worktree-QA
   status, validation, and blockers to the designated human and explicitly ask
   approval to hand off to Lead. Stop without messaging Lead until approval is
   affirmative. That approval authorizes handoff only; it is not GitHub PR
   approval and does not authorize merge.
8. When the gate is open, read the final contract after it contains the final
   head SHA and PR URL, and send the complete JSON verbatim through the
   supported task-to-task messaging capability to the persistent Codex task
   titled exactly `19-usl - Lead`. Accompany it with branch, commit, PR URL,
   validation, and blockers. A summary or PR link without the full contract is
   not an effective handoff. Send the same structured facts earlier whenever a
   genuine Lead decision blocks progress, but do not mislabel that request as
   the final handoff.
9. If direct messaging is unavailable, state that limitation in the final
   report and include the complete contract for manual delivery. Otherwise,
   treat the message as asynchronous handoff, not approval. Leave the branch,
   worktree, QA resources, and other named evidence available and stop without
   polling, waiting for a reply, merging, or deploying. Wait only when
   genuinely blocked on a Lead decision.

Use `docs/operations/agent-development.md` for command examples, the contract field guide, and transition details.
