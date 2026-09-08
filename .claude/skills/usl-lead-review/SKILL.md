---
name: usl-lead-review
description: Review a delivery agent's handoff — code, tests, action-risk and screenshots as user journeys — then take the PR out of draft, assign reviewers, and close the loop on the Odoo card. Use when a delivery agent returns, or when reviewing any PR from an agent.
---

# The Lead review

You own the outcome. A pull request leaves draft only because you read it.

## Code

Read the whole diff, not the summary.

**Verify the regression test genuinely fails without the fix.** The agent must have
shown you three outputs: failing before, passing after, failing again with the fix
reverted. If the third is missing, the test proves nothing — PR #153 shipped a
regression test that passed with *and* without its fix, and the outage survived it.
Ask for the third output rather than assuming.

**Check the action-risk refresh is scoped.** Digests moved should belong to methods
this branch actually changed. If the agent absorbed drift on entries it never
touched, reject: that hides someone else's change inside this one. An agent that
*noticed* unrelated drift and refused to absorb it did the right thing — that goes
in the PR as a known item, not into the reseal.

**Check the change is scoped.** Unrelated refactors, opportunistic renames and
drive-by formatting all cost review attention and hide the real change.

## Screenshots, as journeys

Each arrives with a viewport, a user, a scenario and an expected result. Read it as
the person in that scenario, against `DESIGN.md` and
`.agents/skills/odoo-ui-product-quality`.

- Does the image actually show the scenario it claims? A screenshot of a login page
  captioned as a signed-in journey is fabricated evidence, and it has nearly
  happened here.
- Are the states people really hit covered — empty, loading, validation, access
  denied?
- Reject immediately: any production record, contact detail, financial figure,
  credential, local path, or browser address bar. The repository is public.

## Accepting

```bash
gh pr ready <n> --repo unstaticlabs/odoo
gh pr edit <n> --repo unstaticlabs/odoo \
  --add-reviewer ValentinViennot --add-assignee ValentinViennot
# add the reporter too when it is not Valentin: rogerxaic for Roger
```

`gh` authenticates as `elio-usl`, so the PR author and the reviewer are different
accounts — GitHub refuses a review request from the author, and this is why it
works. Both rulesets require zero approving reviews by design: the request is a
courtesy so the right person sees it, never a gate.

**You never merge feature work.** Ready for review is where you stop.

## Rejecting

Say what is wrong, what right looks like, and which of the three you are asking
for: more evidence, a different fix, or a smaller change. A rejection without a
concrete next step just costs another cycle. Writing it is only half of it —
getting it to somebody who will act on it is the next section, and a review
nobody receives is the same as no review.

## Getting the feedback back to an agent

A delivery agent has no channel to you while it works — `usl-cluster-delivery`
tells it so, and tells it to make the conservative call rather than stall. So the
loop only closes if *you* reopen it, deliberately, in this order.

**1. Write it down first, on the pull request.** Before you message anybody, post
the review as a comment on the PR, with an inline thread on each line you are
talking about:

```bash
gh pr comment <n> --repo unstaticlabs/odoo --body-file <notes.md>
```

GitHub will not accept an approving or changes-requested *review* from the
account that opened the pull request, and `gh` opened it — so this is a comment,
not a review. That costs nothing: an unresolved inline thread blocks the merge
anyway (`required_review_thread_resolution: true` on the staging ruleset), so the
threads you open here are a real gate, and the agent closing them is real
evidence it addressed each point.

**2. Then send the agent at it.** Two cases, and you find out which by trying:

- **The session is still resumable.** Continue it by name or id with
  `SendMessage` — the context, the worktree, the branch and everything it learned
  are still there, and it is much cheaper than re-explaining. Send the review as
  instructions plus the PR link, not instead of it.
- **The session is gone.** Allocate a fresh delivery agent with
  `usl-cluster-delivery`, filling the assignment placeholders as usual and adding
  the PR number and the review notes. It can pick up the existing work: the
  worktree under `/private/tmp/odoo-fb-<slug>` and its branch live on disk
  independently of any session, so a new agent continues the same branch instead
  of starting the change again.

**When the two disagree, the pull request wins.** A chat message reaches one
session and dies with it; the PR thread and the card outlive every agent that
touches them, and they are what the next agent, Valentin, or you-in-a-week will
actually read. If you say something in a message that is not on the PR, it is not
part of the review. Put it on the PR and point at it.

**3. Move the card**, so the board says what is true: **Build / Changes
Requested**, with a chatter note in the reporter's language saying it is going
back for another pass and roughly why.

The agent's revision comes back the same way the first one did — a handoff, not a
merge. Re-review it from the top of this skill: the regression-test evidence in
particular has to be shown again, because a fix that moved may no longer be the
fix the test was pinned to.

## Closing the loop on the card

Write `stage_id` and `state` in a single call: Odoo's `write()` resets `state`
only when the stage changes *without* a state in the same values
(`addons/project/models/project_task.py:1375`). Writing the stage alone is what
loses an approval.

| Moment | Stage | State | Also |
|---|---|---|---|
| allocated | Build | In Progress | `usl_feedback_branch`, assignee Elio |
| draft PR open | Build | In Progress | `usl_feedback_pr_url`, chatter note |
| you send it back | Build | **Changes Requested** | review notes on the PR, agent resumed or re-allocated, chatter note |
| the agent returns a revision | Build | In Progress | re-review from the top |
| you accept | Review | In Progress | PR ready, reviewers assigned, chatter note mentioning the reporter, **Approval activity** for the reporter |
| Valentin approves | Review | **Approved** | *his step — the merge train's input* |
| queued, deployed, released | Release | *see `usl-merge-train`* | the train owns the card from here |

The chatter note is for the person who reported the problem, not for CI. One
paragraph: what changes for them, and what to look at in the screenshots.
Mention them so it reaches their inbox. Say plainly anything deliberately left
undone — a known limitation named in the note is a decision; the same thing found
later is a defect.

Schedule the **Approval** activity (`mail.activity.type` id 11, scoped to
`project.task`) on the reporter, so reviewing the pull request lands in their Odoo
to-do rather than depending on them noticing a notification.

Add the agent's token usage to `usl_feedback_tokens`, and append a row to
`ledger.jsonl`. **Take the token count from the harness**, which reports it
authoritatively when the agent finishes; the agent's own figure is a cross-check,
not the source.

Cards that produced no pull request are documented too — the verdict, the evidence,
and a move to Release or Icebox. Never silently. A false alarm still deserves a
reply.

## Report

Per PR: accepted or rejected and why; the test evidence you actually saw; what the
screenshots showed; digests moved; the card transitions you made; tokens recorded.
For anything sent back: where the review notes live, whether you resumed the
original agent or allocated a fresh one, and what you asked for.
