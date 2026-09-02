---
name: usl-upstream-sync
description: Integrate a newer upstream Odoo revision into the USL distribution while preserving ancestry and isolating compatibility work.
---

# Synchronize upstream deliberately

- Record the USL base and exact upstream target before changing history. Read
  intervening Odoo release notes and inspect ORM/schema, assets/OWL, localization,
  removed modules, and Community/Enterprise boundary changes.
- Merge the upstream revision; do not squash, cherry-pick the range, or copy its
  tree. Keep literal conflict resolution in the merge commit. Put USL compatibility
  adaptations in separate Conventional Commits so later syncs can distinguish them.
- Resolve conflicts from the common ancestor and both sides, not from whichever
  file looks newer. Preserve USL behavior unless upstream intentionally replaces it;
  remove a customization only after proving native parity.
- Install custom modules on a clean target and upgrade a recent neutralized
  production restore. Odoo recommends both because clean installation misses
  stored-data and `noupdate` failures, while restore-only testing misses packaging.
- Run affected upstream/custom tests plus accounting, access-control, UI, and
  recovery gates selected by the actual delta. Rehearse the production upgrade;
  do not infer safety from a conflict-free merge.
- Deliver through the normal staging-to-production path. Never combine unrelated
  refactors with an upstream sync.

References: [Odoo customized database upgrades](https://www.odoo.com/documentation/19.0/developer/howtos/upgrade_custom_db.html), [Git merge semantics](https://git-scm.com/docs/git-merge).
