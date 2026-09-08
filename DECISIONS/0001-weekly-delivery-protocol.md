# 1. A weekly autonomous delivery run for product feedback

Date: 2026-09-08
Status: accepted

## Context

Two resources were being wasted every week.

Reported product feedback accumulated on the **Odoo Product Feedback** board
(project 19) faster than anyone turned it into pull requests. The board went from
7 cards to 25 in a single day; 17 sat untriaged.

Separately, the Claude subscription's weekly allowance renews every Monday at
14:00 Paris and whatever is unused expires. In a representative week it stood at
16% with six days elapsed.

A first version ran on 2026-09-08 against one card and produced PR #174 — a real
fix with a real regression test — for **393k agent tokens in 52 minutes**. That
measurement is what makes an autonomous weekly run worth designing properly rather
than improvising.

It also invalidated the obvious design. Sizing work against the weekly percentage
fails, because one delivered card moves that counter by roughly one point: a run
optimising against it would conclude there was room for sixty cards a night. The
binding constraints are wall-clock, the three-concurrent-stack Docker ceiling, and
how fast one reviewer can review.

## Decision

A scheduled run every Monday at 03:00 Paris acts as PM and lead developer for the
distribution. It merges what a human has approved, keeps the fork current with
upstream, scores and triages the backlog, allocates work to measured capacity,
reviews what comes back, and records the outcome on the board.

Five decisions inside that are worth recording.

**Capacity is measured in agent-hours, not in percentage of allowance.** One
agent-hour is about 450k agent tokens, refitted weekly from a ledger of real runs.
The weekly percentage becomes only a safety ceiling: stop dispatching at 85%, stop
entirely at 90%.

**Priority is BRICE** — `(Business × Reach × Impact × Confidence) / Effort`, with
effort in agent-hours so scores and capacity share a unit. `VALUES.md` supplies
the ordering that the business-value and impact terms read from, and
`ARCHITECTURE.md` §21 is a veto rather than a low score. The rubric is documented
in `.claude/skills/usl-feedback-triage/SKILL.md`.

**The human gate is the card's Approved state, not CI.** Both branch rulesets
require zero approving reviews by design, so green CI cannot be the gate. A card
reaches **Review** when the lead accepts the agent's work, and only a human moves
it to **Approved** after reading the pull request and its screenshots. Only
Approved cards are merge-eligible.

**The run merges on its own in exactly two cases**: the upstream and dependency
catch-up branch when it is green and its pull request honestly enumerates every
conflict and resolution, and a revert of a batch it merged itself minutes earlier
that turned staging red. Feature work always stops at ready-for-review.

**The protocol lives in the repository as skills**, in `.claude/skills/`, not in
the scheduled task's prompt. Each is invocable by a person at any time. A step that
cannot survive being run by hand at noon will not survive being run unattended at
three in the morning.

## Consequences

The `.gitignore` rule that allowed nothing under `.claude/` except
`settings.json` gains a narrow exception for `skills/`. The rule's stated purpose —
keeping personal overrides and stray transcripts out of a public repository — is
unaffected: `settings.local.json` and transcripts remain ignored.

`custom-addons/usl_feedback` gains delivery metadata: the native `state` field
becomes visible on the board, alongside the branch, the pull request, the score
and the cumulative token cost. The board becomes a delivery board, not only an
intake board.

Anything reading or writing a card must write `stage_id` and `state` as two
separate calls. Odoo resets `state` to *In Progress* on a stage change
(`addons/project/models/project_task.py:429`), so a combined write silently
discards an approval.

An unattended run needs a pre-approved permission allowlist, and read-only SSH
access to observe deployments — the release pipeline hands off to GitLab with a
single webhook and nothing in GitHub records whether a deploy succeeded. Every
mutating deployment verb stays denied.

The run costs real allowance every Monday whether or not the queue justifies it.
The ledger is what makes that spend inspectable: one row per allocated card, with
its estimate, its actual cost, and its outcome.

## Alternatives considered

**Sizing work by weekly percentage** — rejected on the measurement above.

**Keeping the protocol in the scheduled task's prompt**, as the first version did.
Rejected: a single 13KB prompt cannot be tested in pieces, cannot be resumed after
a failure, and forces the orchestrator to hold the entire protocol in context while
doing judgement work.

**Letting the run merge approved feature work unattended.** Rejected: the
approval that matters is a person having read the change and its screenshots, and
nothing else in the pipeline substitutes for it.
