# Values

`PRODUCT.md` says what we build and `ARCHITECTURE.md` says how it is allowed to
work. This file says what we do when two good things conflict, and how that
ordering turns into a number a delivery agent can sort by.

It adds no new values. It orders the ones already written down, and names where
each is enforced so a claim can be checked rather than believed.

## The ordering

When two of these pull against each other, the higher one wins. This is the whole
point of the file: every value below is real, and without an order they cannot
settle an argument.

1. **Accounting truth and evidence.** Posted history stays controlled and
   traceable; no parallel ledger; no silent alteration. Enforced by
   `.agents/skills/odoo-accounting-integrity`, `CONTRIBUTING.md` §Accounting, and
   the `rebuild_account_migration` suites.
2. **Authority and privacy boundaries.** Sensitive actions require authority
   defined by policy; company and privacy boundaries hold. Enforced by
   `usl_access_control`, the action-risk inventory, and
   `.agents/skills/odoo-access-control-safety`.
3. **Recoverability.** Production changes require demonstrated integrity,
   recoverability and explicit admission. Enforced by the release controller's
   sixteen stages and the qualified recovery cohorts.
4. **Upstream compatibility.** We do not accept irreconcilable divergence from
   upstream Odoo. Enforced by `.agents/skills/usl-upstream-sync` and the
   distribution's merge-not-squash rule.
5. **Human attention.** Reserve it for prepared decisions, approvals and genuine
   ambiguity. This is the one we are most often tempted to spend cheaply.
6. **Clarity.** State, responsibility, evidence and the next action are
   immediately visible. Failed automation leaves actionable state, never silence.
7. **Opinionated simplicity.** Progressive disclosure over configuration; fewer
   choices, better defaults.

Two rules cut across all seven:

- **Work is not complete until the required real-world state exists.** An
  attempted action is not completion, and neither is a merged pull request.
- **Automation stays bounded, visible and safe.** An agent that cannot explain what
  it did, or that a person cannot stop, has failed regardless of its output.

The canonical statements live in `PRODUCT.md` §Product Principles and
`docs/product/product-vision.md` §Invariants. If this file ever disagrees with
them, they win and this file is wrong.

## The veto

`ARCHITECTURE.md` §21 lists twelve explicit non-goals. Work that lands on one is
not ranked low — it is **not ranked at all**. It goes to Icebox with the non-goal
quoted.

The two that catch the most plausible-sounding proposals:

- *prioritize visual modernization over accounting correctness*
- *automate every human decision*

A proposal that trips a non-goal can still be right — but then the non-goal is what
has to change first, deliberately, in `ARCHITECTURE.md`.

## How this becomes a score

Delivery work is ranked by BRICE — `Score = (B × R × I × C) / E` — defined in
`.claude/skills/usl-feedback-triage/SKILL.md`. Two of its five terms are judgements
about value, and this file is what makes them arguable instead of arbitrary:

- **B, business value**, reads down the ordering above. Work that protects
  accounting truth, authority or recoverability sits at the top; work that reduces
  coordination burden — the stated success criterion in `ARCHITECTURE.md` §22 —
  next; then named `ROADMAP.md` items; then everything else.
- **I, impact**, reads the same ordering as severity. Silently wrong accounting
  outranks blocked work, which outranks friction, which outranks polish. The
  ordering is why: a wrong number violates value 1, a slow page violates value 7.

A score is only as good as the sentence that justifies it, so every scored card
carries its reasoning in the chatter. A number nobody can challenge is worse than
no number.
