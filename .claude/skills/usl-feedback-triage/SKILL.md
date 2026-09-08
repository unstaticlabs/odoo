---
name: usl-feedback-triage
description: Score the Product Feedback board with BRICE, set each card's approval state, and write the score and effort estimate back. Use for the weekly triage pass, or to rank a backlog before allocating work.
---

# Triage and score

Turn a board into a ranked queue somebody can allocate from. Project **19**,
"Odoo Product Feedback", on the production Odoo MCP connector.

Score everything not in Icebox. Cards in **Inbox** are scored but not allocatable —
they have to be triaged into the pipeline first.

## BRICE

```
Score = (B × R × I × C) / E
```

B, R, I and C are each 1–5. **E is effort in agent-hours** — that is what makes the
score commensurable with capacity, because capacity is measured in agent-hours too.

**First, the veto.** Anything that lands on `ARCHITECTURE.md` §21 "Explicit
non-goals" scores **0** and goes to Icebox with the non-goal quoted. It is not
ranked low, it is not ranked at all. The same applies to anything that would
weaken an invariant in `docs/product/product-vision.md`.

### B — Business value

Against `ARCHITECTURE.md` §22 architectural success criteria and the
`product-vision.md` mandate. The question is what this is *for*, not how nice it
would be.

| | |
|---|---|
| 5 | Protects accounting truth, evidence, access control or recoverability — the things the distribution exists to guarantee |
| 4 | Materially reduces Valentin's coordination or administrative burden, the stated success criterion |
| 3 | Advances a named `ROADMAP.md` **Now** item |
| 2 | Advances **Next**, or removes real friction from a daily path |
| 1 | Genuine improvement with no line to stated strategy |

### R — Reach

Personas are `PRODUCT.md` §Users: Valentin, Prosper, accountants, operations staff.
Frequency counts as much as headcount — a daily path outranks a monthly one.

| | |
|---|---|
| 5 | Every user, every day |
| 4 | Most users, most days |
| 3 | One persona's daily path, or everyone occasionally |
| 2 | One persona, weekly |
| 1 | One person, rarely |

### I — Impact

Severity when it bites, not how loud the report was.

| | |
|---|---|
| 5 | Silently wrong accounting, lost data, or an access boundary that does not hold |
| 4 | Work is blocked outright, no workaround |
| 3 | Workaround exists but costs real time, or the interface actively misleads |
| 2 | Friction — extra clicks, a confusing label |
| 1 | Polish |

### C — Confidence

Evidence behind the diagnosis, not enthusiasm for the fix.

| | |
|---|---|
| 5 | Reproduced, with a failing test that pins it |
| 4 | Reproduced by hand, journey walked |
| 3 | Reported with a screenshot and deployment identity |
| 2 | Reported once, plausible, unverified |
| 1 | Inferred; nobody has seen it |

A card at C ≤ 2 that scores well is a signal to **reproduce it first** — allocate a
short investigation, not a fix.

### E — Effort, in agent-hours

Estimate the whole contract in `usl-cluster-delivery`: stack, reproduce, failing
test, fix, action-risk refresh, browser QA, screenshots, PR. Not just the diff.

Use 0.5, 1, 2, 4, 8. Measured reference: one small front-end fix with a HOOT test
and two screenshots cost **393k tokens in 52 minutes** end to end — about one
agent-hour. Anything you would estimate above 8 is not a card, it is a project:
say so and split it.

**1 agent-hour ≈ 450k agent tokens.** Refit this from `ledger.jsonl` each week
rather than trusting the number.

## Stage multipliers

Applied after the score, so work already in flight finishes before new work starts.

| Stage and state | × |
|---|---|
| Review, not yet approved | **3** |
| Build | **2** |
| Triage, approved | **1** |
| Inbox, or Triage not approved | not allocatable — triage it first |

## Setting state

You may set **Changes Requested** and, for cards you are confident about,
**Approved** — meaning approved *to be worked on*, which is a different question
from approving a finished pull request. That second approval is Valentin's alone.

When you genuinely cannot tell whether we want to do something — the value is
unclear, it conflicts with something else, it needs a product decision — set
**Changes Requested**, assign Valentin, and put the actual question in the chatter.
One question, specific, answerable. Do not guess to keep the queue moving.

**Write stage first, then state, as two separate calls.** Odoo resets `state` to
*In Progress* whenever `stage_id` changes
(`addons/project/models/project_task.py:429`), so a combined write silently loses
the approval. This has to be two calls every time.

## Writing back

Per card: `usl_feedback_score` (the multiplied score), `allocated_hours` (E), and
a chatter note giving the five components, the score, and one sentence of
reasoning. The note is what makes the score arguable — a number nobody can
challenge is worse than no number.

Append one row per card to `ledger.jsonl`: task id, B, R, I, C, E, raw score,
multiplier, final score, stage, state, timestamp.

## Report

The ranked queue with scores; what you iceboxed and which non-goal it hit; what
you sent back to Valentin and the question you asked; and any card whose score you
distrust, with why.
