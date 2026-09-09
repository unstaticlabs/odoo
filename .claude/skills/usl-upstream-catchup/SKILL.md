---
name: usl-upstream-catchup
description: Bring the distribution current with upstream Odoo saas-19.3 on a catch-up branch, resolving conflicts deliberately and reviewing for overlap with our own work, then absorb open Dependabot updates. Use for the weekly upstream sync or before a batch of delivery work.
---

# Upstream catch-up

One branch per run, cut from the current staging head, carrying the upstream merge
and the dependency updates. Delivery agents rebase onto it so they inherit upstream
once instead of five times.

`.agents/skills/usl-upstream-sync/SKILL.md` is the governing playbook and it wins
over anything here. `docs/operations/chore-stack-20260905.md` is the worked
precedent — read it before starting, it shows the shape of a good result.

```bash
BRANCH=chore/catchup-$(date +%Y%m%d)
mkdir -p ~/Code/odoo-worktrees
git worktree add ~/Code/odoo-worktrees/catchup-$(date +%Y%m%d) -b "$BRANCH" usl/19-usl-staging
git fetch upstream saas-19.3
git rev-list --count usl/19-usl-staging..upstream/saas-19.3   # how far behind
```

## The rules that matter

**Merge, never squash or cherry-pick.** Ancestry is the point: a later sync has to
be able to tell what came from upstream and what we did to accommodate it. Keep the
literal conflict resolution in the merge commit, and put every USL compatibility
adaptation in its own Conventional Commit afterwards.

**Resolve from the common ancestor and both sides**, not from whichever file looks
newer. Preserve USL behaviour unless upstream deliberately replaces it, and remove
one of our customisations only after proving native parity — then say so in the PR.

**Review for overlap, which is the part a merge tool cannot do.** Read the
intervening release notes and look specifically for upstream now doing something we
built ourselves: ORM and schema changes, assets and OWL, localisation, removed
modules, the Community/Enterprise boundary. Every overlap you find is either a
customisation we can delete or a collision we have to reconcile. List them in the
PR even when the answer is "keep ours for now".

**Qualify both paths.** A clean install misses stored-data and `noupdate` failures;
an upgrade-only test misses packaging. CI's `Clean install and repeated upgrade`
covers the first. Say explicitly which you ran and which you did not.

**Never combine unrelated refactors with an upstream sync.**

## Dependabot

After the upstream merge is clean, absorb the open Dependabot pull requests onto
the same branch — they all target `19-usl-staging` anyway, and folding them in
costs one release cycle instead of five.

```bash
gh pr list --repo unstaticlabs/odoo --state open --author app/dependabot \
  --json number,title,headRefName
```

Take only what is relevant and safe. `.github/dependabot.yml` already pins some
things deliberately (`cryptography`/`pyopenssl` split out, `lxml-html-clean` 0.4.5
ignored, `apache/tika` majors ignored) — respect those. Close absorbed PRs with a
comment pointing at the catch-up PR so nobody re-opens them.

Production only ever accepts `19-usl-staging` or `urgent/**`, so these reach
production through the ordinary nightly promotion. Never target `19-usl` directly.

## Merging it

**This branch may auto-merge when green.** It is one of the two exceptions to the
rule that the routine does not merge on its own — the other is a revert of its own
bad batch.

The PR body has to earn that. It must enumerate:

- the upstream range merged, with the base and target commits;
- **every conflict, how it was resolved, and why** — from the ancestor, not from
  taste;
- every overlap found between upstream and our distribution, with the decision;
- every dependency absorbed and every one deliberately left;
- exactly which qualification paths ran, and which did not.

If any of those cannot be written honestly, the PR does not auto-merge. Leave it
for Valentin and say what is unresolved.

## Report

Commits merged, conflicts and resolutions, overlaps and decisions, dependencies
absorbed and skipped, qualification run, and the PR number — plus the branch name,
because delivery agents rebase onto it.
