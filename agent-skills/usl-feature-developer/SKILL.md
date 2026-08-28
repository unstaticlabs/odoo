---
name: usl-feature-developer
description: Own an Unstatic Labs Odoo feature or fix from isolated worktree startup through implementation, verification, QA, pull request, and structured Lead Developer handoff. Use when asked to implement work as Feature Developer, catch up a feature branch, prepare a feature for review, or close out a feature without merging it.
---

# USL Feature Developer

Own the requested feature through a merge-ready pull request, then stop. Never merge your own work or deploy production.

## Start safely

1. Read `AGENTS.md`, then the product, accounting, operations, or user specifications relevant to the task.
2. Run `scripts/agent/context` and `scripts/agent/verify feature-start` before editing.
3. Work only in a dedicated branch and worktree, normally based on current
   `origin/19-usl`. For an explicitly requested stacked change, base it on the
   approved parent feature head and record that exact base in the handoff.
   Preserve other worktrees, shared Docker infrastructure, databases, dumps,
   caches, and secrets.
4. Compare at least two credible implementation choices for material decisions, including native Odoo and maintained OCA behavior where relevant.

If a branch must catch up, fetch first, inspect both histories and local state, and choose a rebase or merge deliberately. Never discard uncommitted work. Re-run relevant validation after resolving conflicts. Do not force-push unless the branch is agent-owned and rewriting it is explicitly acceptable.

## Implement and qualify

1. Keep changes scoped. Prefer isolated custom add-ons; follow repository module and migration boundaries.
2. Add or update the narrowest useful automated tests. Tests are necessary evidence, not sufficient evidence for a user-facing change.
3. For migration or upgrade impact, use `odoo-migration-upgrade-safety`. For accounting or access-control impact, use the corresponding specialist skill.
4. For meaningful forms, lists, dialogs, dashboards, OWL components, responsive surfaces, or journeys, use `odoo-ui-product-quality`. Exercise the journey in a real browser where possible and repeat browser → screenshot → critique → repair until the result is sound.
5. Use `scripts/agent/qa-up` when runtime/product QA is justified. Record the exact profile, environment, authentication instructions, URL, SHA, and evidence. Do not destroy the environment after testing; the Lead Developer owns safe post-merge cleanup.
6. Report failures, limitations, and unverified assumptions explicitly. Never convert absence of evidence into a success claim.

## Commit, publish, and hand off

1. Make scoped Conventional Commits after validated chunks. Include `AI-generated commit` in each agent-authored commit body and the current driving human's `Co-authored-by` trailer supplied by `scripts/agent/github configure`.
2. Run `scripts/agent/github status`, then publish only through `scripts/agent/github push`. Do not use the existing SSH remote or a human credential profile.
3. Create the v1 contract with `scripts/agent/handoff init`, replace its draft evidence with actual results, and validate it with `scripts/agent/handoff validate PATH --repository`.
4. Make the worktree clean, push the exact head, and run `scripts/agent/verify feature-ready --handoff PATH`.
5. Open or update the ready PR with `scripts/agent/github pr --handoff PATH`.
   The helper uses the validated `feature.base` as the GitHub PR base, including
   for stacked PRs, and renders a review-first GitHub Markdown body from the
   contract; do not paste raw JSON or hand-edit generated evidence. The
   machine-readable contract remains available in a collapsed section, and the
   generated artifact remains ignored local state.
6. Leave the branch, worktree, QA resources, and other named evidence available for independent review. Stop without merging or deploying.

Use `docs/operations/agent-development.md` for command examples, the contract field guide, and transition details.
