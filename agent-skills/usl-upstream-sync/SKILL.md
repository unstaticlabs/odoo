---
name: usl-upstream-sync
description: Synchronize the USL Odoo distribution with a newer upstream Odoo revision while preserving ancestry and isolating compatibility work. Use for upstream catch-up, merge preparation, conflict qualification, or sync review.
---

# USL Upstream Sync

Treat upstream synchronization as a dedicated feature delivered by PR. Never merge it into `19-usl` yourself and never rewrite upstream history.

1. Start a clean `codex/`, `feat/`, or `chore/` sync branch/worktree from the latest `origin/19-usl`.
2. Fetch the configured upstream remote. Record the previous USL head and exact upstream target SHA before modifying history.
3. Inspect incoming release notes, module removals, ORM/API changes, JavaScript/assets changes, schema effects, enterprise/community differences, and conflicts with custom add-ons.
4. Preserve upstream ancestry with a merge commit. Do not squash, cherry-pick the upstream range, or copy upstream files into an unrelated commit.
5. Resolve conflicts in the merge commit when they are direct reconciliation. Put USL compatibility adaptations that are not literal conflict resolution in separate, scoped commits after the merge.
6. Run upstream-appropriate tests plus USL product-boundary, migration, accounting, security, and custom-add-on checks. Use `odoo-migration-upgrade-safety` for database or module upgrade impact.
7. Record the upstream range, conflicts, USL compatibility commits, upgrade/recovery plan, failures, and unverified areas in the PR evidence.
8. Push through `scripts/agent/github push`, open or update the PR, and leave integration to review and the merge queue.

Never use an upstream sync to smuggle unrelated refactors or to erase USL commits. Production upgrades remain governed by CI after cutover.
