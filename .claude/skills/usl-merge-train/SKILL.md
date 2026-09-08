---
name: usl-merge-train
description: Batch approved pull requests into the staging merge queue, watch the post-merge qualification and release, verify the deploy, and recover from a red batch. Use when clearing the approved queue, or when staging has gone red after a merge.
---

# The merge train

Collect what is approved, land it as one group, and prove it reached users. Then
close the loop on the board.

**Run this first and alone.** Everything downstream — the upstream catch-up branch,
every delivery worktree — is cut from the staging head. Running agents while the
head moves has them building on sand.

## What is eligible

A pull request joins the train when **all** of these hold:

- base is `19-usl-staging`, not draft, mergeable;
- **and either** its feedback card is in stage **Review** with state **Approved**
  (a human read the PR and its screenshots and said yes) **or** the PR carries at
  least one approving GitHub review;
- no unresolved review threads — the staging ruleset sets
  `required_review_thread_resolution: true`, so an open thread blocks the merge.
  Resolve threads you have genuinely addressed; never resolve one to get past it.

Nothing else. A green CI run is not approval. The rulesets require zero approving
reviews *by design*, so the gate is the card's Approved state, not GitHub's.

```bash
gh pr list --repo unstaticlabs/odoo --state open --base 19-usl-staging \
  --json number,title,isDraft,mergeStateStatus,reviewDecision,headRefName
gh pr view <n> --repo unstaticlabs/odoo --json reviewThreads
```

## Batching

**At most five, and only into staging.** The staging queue is
`max_entries_to_merge: 5`, `grouping_strategy: ALLGREEN`,
`min_entries_to_merge_wait_minutes: 5`, `merge_method: MERGE`. ALLGREEN means one
red entry rejects the whole group, so order by confidence: smallest and safest
first.

**Never batch into production.** `19-usl` is `max_entries_to_merge: 1`, and
`scripts/merge-group-pull-request` raises
`merge queue must identify one open PR by its source parent` on a batched group.
Production gets there through the ordinary promotion, never through this train.

```bash
gh pr merge <n> --repo unstaticlabs/odoo --merge --auto
```

Each merge into staging costs a full qualification, build, release and deploy
cycle. Five PRs in one group cost one cycle. That is the whole point of batching.

## Watching it land

The check named **`USL qualification` means three different things**, so key on the
event and ref, never the name alone:

| Event | What `USL qualification` covers |
|---|---|
| pull request | `USL compatibility` only |
| merge queue | `USL compatibility` only |
| **push to `19-usl-staging`** | compatibility **plus** `Clean install and repeated upgrade` |

Only the third is a real gate. The full database job runs *after* the merge lands,
so a bad batch is already on the integration branch when you find out.

After that, `publish / Assemble coordinated release` builds the components and
publishes an immutable release artifact. Release publication is provable without
any server access:

```bash
oras resolve ghcr.io/unstaticlabs/usl-odoo-release:sha-<merge commit>
gh api "repos/unstaticlabs/odoo/actions/artifacts?name=usl-release-<sha>" \
  --jq '.artifacts[] | select(.expired == false) | .created_at'
```

## Proving it deployed

The release workflow fires **one webhook at GitLab and forgets**. Nothing in GitHub
records whether the deploy worked, and a green release check does not mean a green
deploy — the trigger step runs *after* publication, and it has timed out in
production before (01:09 on 2026-09-07; production did not move until a manual
re-run at 07:14).

Read-only, over `ssh odoo`:

```bash
scripts/usl-stack --target staging runtime status --json   # deployment generation
scripts/usl-stack --target staging release status --json   # status == "admitted"
scripts/usl-stack --target staging health --json
scripts/usl-stack --target staging smoke  --json
```

`release status` reports `admitted` only when all sixteen stages completed. The
generation maps back to a commit through
`/var/lib/usl-odoo/runtime/<target>/generations/<generation>/usl-release.json`
and its `commit` field. An active image plus a healthy endpoint is **not**
sufficient — only the admission receipt proves it.

Every mutating `usl-stack` verb is denied to you. Status, health and smoke only.

## When the batch goes red

Staging is already carrying the bad commit. Recover in this order:

1. **Identify the culprit.** The merge group's commit has one parent per entry;
   map each to its PR and read the failure. If the failure names a module, the PR
   that touched it is your first suspect. Confirm before acting — reverting the
   wrong one costs another cycle.
2. **Revert that PR alone, and merge the revert**, to get staging green. This is
   the one merge you may make without asking. It is bounded: only a commit from
   the batch you just merged, and only to restore the state before it.
3. **Open a fix PR** against the reverted work carrying the failure evidence, as a
   draft, assigned to Valentin.
4. **Move the card back to Build, state Changes Requested**, and say plainly in
   the chatter what broke and what happens next.

If you cannot identify the culprit with confidence, revert the **whole group** —
that is always safe — and say so.

## Closing the loop on the board

Stage first, then state, as two separate writes. Odoo resets `state` to *In
Progress* whenever `stage_id` changes, so a combined write silently loses the
approval.

| Moment | Stage | State |
|---|---|---|
| merged to staging | Review | Approved (unchanged) |
| live in production | Release | Done |

Post a chatter note at each: the merge commit for the first, the deployment
generation for the second. Say it in the reporter's language, not CI's.

## Report

Which PRs went in and as what group; the merge commit; the post-merge check
results; the release digest; the deployment generation and admission status; every
card moved; and anything reverted, with the evidence that identified it.
