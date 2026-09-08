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
for: more evidence, a different fix, or a smaller change. Then re-dispatch. A
rejection without a concrete next step just costs another cycle.

## Closing the loop on the card

Write `stage_id` and `state` in a single call: Odoo's `write()` resets `state`
only when the stage changes *without* a state in the same values
(`addons/project/models/project_task.py:1375`). Writing the stage alone is what
loses an approval.

| Moment | Stage | State | Also |
|---|---|---|---|
| allocated | Build | In Progress | `usl_feedback_branch`, assignee Elio |
| draft PR open | Build | In Progress | `usl_feedback_pr_url`, chatter note |
| you accept | Review | In Progress | PR ready, reviewers assigned, chatter note mentioning the reporter, **Approval activity** for the reporter |
| Valentin approves | Review | **Approved** | *his step — the merge train's input* |

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
