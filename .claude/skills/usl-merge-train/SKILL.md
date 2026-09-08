---
name: usl-merge-train
description: Batch approved pull requests into the staging merge queue, watch the post-merge qualification and release, prove the deploy, promote the qualified staging head to production, and recover from a red batch. Use when clearing the approved queue, when promoting staging to production, or when staging has gone red after a merge.
---

# The merge train

Collect what is approved, land it as one group, prove it reached users, and carry
it on to production. Then close the loop on the board.

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

Nothing else. **Either signal is sufficient on its own.** An approving GitHub
review is a person saying yes on the pull request; the card's **Approved** state
is a person saying yes on the board. A PR approved on GitHub is eligible even
when its card never reached *Review / Approved*, and a card approved on the board
is eligible even when nobody clicked Approve on GitHub.

What is never approval is a green CI run: it says the tree builds and its tests
pass, not that anyone read it. The rulesets require zero approving reviews *by
design*, so GitHub will never hold the merge back on your behalf — which is
exactly why you check for one of these two human signals yourself instead of
letting the queue decide.

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
Production gets there through the single-entry promotion below, never through a
batch.

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

**A cancelled run on your own merge commit is not a failure.** `qualification.yml`
sets `cancel-in-progress: true` keyed on the branch, so when merges land in a burst
every run but the newest is killed. On 2026-09-08 four landed in six minutes and the
day's tally was 6 successful push runs against 8 cancelled — while releases
published normally throughout. The survivor qualifies the cumulative tree; the
cancelled ones were redundant, not lost.

So the assertion is **not** about your commit's own run:

> wait for a push run on `19-usl-staging` that is `status == "completed"` **and**
> `conclusion == "success"`, whose `head_sha` has your merge commit as an ancestor
> (`git merge-base --is-ancestor <your sha> <run head_sha>`).

Reverting on a cancelled run would revert healthy work because somebody merged
behind you — the worst thing this skill's revert authority could do unattended.

And assert the terminal state you want, never the absence of the in-flight states
you happen to remember: statuses include `queued`, `pending`, `waiting`,
`requested` and `in_progress`, and a test written as a negation will call an
unstarted run finished.

After that, `publish / Assemble coordinated release` builds the components and
publishes an immutable release artifact. Release publication is provable without
any server access:

```bash
oras resolve ghcr.io/unstaticlabs/usl-odoo-release:sha-<merge commit>
gh api "repos/unstaticlabs/odoo/actions/artifacts?name=usl-release-<sha>" \
  --jq '.artifacts[] | select(.expired == false) | .created_at'
```

## Proving it deployed

The release workflow fires **one webhook at GitLab and forgets**, and a green
release check does not mean a green deploy: the trigger step runs *after*
publication, and it has timed out in production before (01:09 on 2026-09-07;
production did not move until a manual re-run at 07:14).

**GitHub does record the outcome, and it is commit-exact.** The deploy reports
back as a deployment on the `staging-release` environment. That status — not a
health endpoint, not a published artifact — is what proves a *given commit*
reached staging:

```bash
scripts/check-staging-deployment --repository unstaticlabs/odoo \
  --sha <merge commit> --head-ref 19-usl-staging
```

It exits non-zero and names the reason when no `staging-release` deployment
exists for that sha, or when the newest status on it is not `success`
(`operations/staging_deployment.py`). The production promotion runs this exact
check as a required step in `.github/workflows/qualification.yml`, so a tree
staging never deployed cannot be promoted. That gate exists because on
2026-09-08 a promotion merged fully green at 08:49 while the same commit's
staging deployment had read `failure` since 04:55, and production then failed
identically.

The chain can also stall rather than fail, and a check looking only for failure
misses it:

```bash
scripts/check-release-health --repository unstaticlabs/odoo
```

`operations/release_health.py` calls an environment unhealthy when its newest
deployment failed **or** never reached a terminal state within two hours — the
hand-off is single-attempt HTTP and a lost one leaves a deployment pending
forever. The `Release health` workflow runs it hourly, so a red scheduled run is
the alert. Run it yourself before merging anything further: a merge into staging
supersedes a release already in flight.

Then the runtime itself, read-only:

```bash
scripts/usl-stack-observe staging release    # status == "admitted"
scripts/usl-stack-observe staging health
scripts/usl-stack-observe staging smoke
scripts/usl-stack-observe staging runtime    # deployment generation
```

`release status` reports `admitted` only when all sixteen stages completed. The
generation maps back to a commit through
`/var/lib/usl-odoo/runtime/<target>/generations/<generation>/usl-release.json`
and its `commit` field. An active image plus a healthy endpoint is **not**
sufficient — only the admission receipt proves it.

**Staging's runtime lookup is currently broken, and it is not staging that is
broken.** As of 2026-09-08 `usl-stack-observe staging runtime` returns
`expected one usl-odoo-staging-main/odoo container, found 0` while
`https://odoo-staging.unstaticlabs.com/web/health` answers Odoo's own
`{"status": "pass"}` from origin. `operations/targets/staging.json` names a
compose project nothing on the host answers to; the identical lookup resolves for
production. Do not read that failure as "not deployed" and do not revert anything
over it — `check-staging-deployment` answers that question without touching the
host. It is a known, separate defect. Do not fix it in the middle of a merge
train; report it.

Use `scripts/usl-stack-observe`, never `scripts/usl-stack`. The observe wrapper
builds its own argument list and there is no argument by which a caller reaches a
mutating verb, whereas a permission rule allowing `scripts/usl-stack` also allows
`restore`, `backup` and `release run` — it opens the SSH connection itself.
**Every mutating `usl-stack` verb is denied to you.** Status, release, health,
smoke and storage only.

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
4. **Move the card to Release / Changes Requested** — it entered the chain and
   fell out of it, and that is what the state means. Say plainly in the chatter
   what broke and what happens next. It returns to **Build / In Progress** when
   the fix is actually allocated to someone, not before: a card parked in Build
   with nobody on it reads as work in progress that is not.

If you cannot identify the culprit with confidence, revert the **whole group** —
that is always safe — and say so.

## Promoting to production

**What made it to staging should make it to production.** Promotion is the last
stage of the train, not a separate errand someone else runs: one pull request
carrying the whole staging head, armed to merge when ready, and watched through
CI.

```bash
gh pr create --repo unstaticlabs/odoo --base 19-usl --head 19-usl-staging \
  --title 'chore(release): promote qualified staging' --body '<what is in it>'
gh pr merge <n> --repo unstaticlabs/odoo --merge --auto
```

One entry at a time — `19-usl` is `max_entries_to_merge: 1`, and the promotion
carries the whole staging head, never a cherry-picked subset. Then watch it the
same way you watched the staging merge: the promotion's own `USL qualification`
run (production re-qualifies the commit even when staging already passed), the
release, and finally the deploy — `check-release-health`,
`check-staging-deployment`'s production counterpart in the deployment API, and
`usl-stack-observe production release | health | smoke | runtime` for the final
VPS state.

**When it goes wrong, fixing the pipeline is part of the stage.** A promotion
blocked by a defect in a gate, a workflow or a check is yours to repair and retry.
A promotion left sitting red is a staging tree users never receive, so stopping at
"CI is broken" does not finish the job.

**This stage is the pull request and the pipeline. It is not the machine.**

- Every mutating `usl-stack` verb stays denied. You do not deploy, restore, roll
  back, start, stop or run a release on the VPS by hand. Read the final state
  with `usl-stack-observe`; if what you find needs a mutation, say so and stop.
- You never push to `19-usl`. The pre-push guard refuses it, and a direct push
  skips qualification while Git pushes as an administrator, so nothing on GitHub
  would catch it.
- Before you change a promotion gate, read `operations/source_policy.py`.
  Production accepts `19-usl-staging` **or** `urgent/**`, and an urgent fix
  reaches staging *after* production by design. A gate demanding staging evidence
  of every promotion welds the emergency path shut at the moment it is needed —
  which is why `check-staging-deployment` exempts `urgent/**` explicitly.

## Closing the loop on the board

Write `stage_id` and `state` in the same call. Odoo's `write()` resets `state` to
*In Progress* only when the stage changes with no state alongside it
(`addons/project/models/project_task.py:1375`); writing the stage alone is what
loses an approval.

| Moment | Stage | State |
|---|---|---|
| added to the merge queue | Release | In Progress |
| proven deployed to staging | Release | **Approved** |
| failed staging, and was reverted or blocked | Release | **Changes Requested** |
| proven deployed to production | Release | **Done** |

**The card enters Release when you queue it, not when it reaches production.**
Earlier revisions of this skill said the card stayed at *Review / Approved* until
production; it does not, and the two statements cannot both be true. *Review /
Approved* is this train's **input** — the signal that let the pull request in —
and queuing consumes it. From that moment the card reports where the change sits
in the release chain, and every row above is a fact you can point at: a
merge-queue entry, a successful `staging-release` deployment, a revert commit, a
successful `production-release` deployment.

*Changes Requested* is where a card stops until someone acts. It means the change
is out of the chain, not merely late: use it when you reverted the PR or blocked
it, never for a batch that is simply still in flight.

Post a chatter note at each step: the merge commit when you queue it, the
deployment evidence when staging and production accept it, and — if it comes to
that — what broke and what happens next. Say it in the reporter's language, not
CI's.

## Report

Which PRs went in and as what group; the merge commit; the post-merge check
results; the release digest; the staging and production deployment evidence you
actually read, named; the promotion pull request and anything you fixed in the
pipeline to get it through; the deployment generation and admission status; every
card moved; and anything reverted, with the evidence that identified it.
