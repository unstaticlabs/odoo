# How to write the pull request

Two documents. The first is scratch and never leaves the run directory. The second
is what lands on GitHub.

---

## 1. The pyramid — thinking, not output

Write this first, in `runs/<date>/<slug>/pyramid.md`. It is a tool for finding the
argument, not a format for presenting it. **Never paste it into a pull request.**

```
Audience:     who reads this, and why are they reading it?
Question:     what question is in their mind when they open it?
Answer:       the one-line answer to that question.

Situation:    a statement they already accept, uncontroversially.
Complication: what changed, which is what makes them ask the question.

Argument 1:   <supporting argument>
  Evidence:   <fact> / <fact> / <fact>
Argument 2:   <supporting argument>
  Evidence:   <fact> / <fact> / <fact>
Argument 3:   <supporting argument>
  Evidence:   <fact> / <fact> / <fact>
```

Rules that keep it honest: every piece of evidence is a fact you actually observed —
a command and its output, a line of code, a measured number. If an argument has no
three pieces of evidence, it is not an argument yet. If the answer needs more than
one line, you have not found it yet.

---

## 2. The pull request — natural prose

Follow `.github/PULL_REQUEST_TEMPLATE.md`: **Outcome, Changes, Validation, Risk and
recovery, Review notes.** Write ordinary paragraphs under those headings. No pyramid
scaffolding, no argument numbering, no "Evidence #1".

The voice to match is PR #162. Read it before writing your first one.

**Open with the person, not the code.** The first sentence says what someone
experienced and what they will experience now. Name the reporter and link their
card.

**Plain language.** "The keyboard reopened after sending" beats "an unintended
focus restoration occurred post-submission". Short sentences. Say the thing.

**Be exhaustive about what changed and why**, file by file, including anything a
reviewer would be surprised to find in the diff — a moved digest, a renamed
selector, a test helper. Surprises in a diff cost more review time than paragraphs.

**Numbers, not adjectives.** "115 tests, 0 failed" beats "tests pass". "17 digests
accepted, every one a method changed here" beats "the surface was refreshed".

**Say what you deliberately did not do**, and why. A known-and-deferred problem
named in the PR is a decision; the same problem found later is a defect.

**Show the test doing its job.** Quote the failure before the fix, the pass after,
and the failure again with the fix reverted. That third one is the part that proves
the test is real.

**Screenshots inline**, each with one line saying who is on screen, on what
viewport, doing what, and what to look at:

```markdown
![Collaborator opens Home on a phone](https://github.com/unstaticlabs/odoo/blob/<sha>/docs/product/screenshots/<area>/<name>-mobile.png?raw=true)
*390×844, signed in as a project collaborator. After tapping through to Projects,
Back returns to the previous app instead of Home.*
```

**Close with attribution**, as `AGENTS.md` requires: the driving human and the agent
that did the work.

---

## Title

The conventional commit subject, nothing else: `<type>(<scope>): <description>`,
imperative, no trailing period. Types allowed by `scripts/commit`: build, chore, ci,
docs, feat, fix, perf, refactor, revert, style, test.

## Reviewers

Request and assign `ValentinViennot`, plus the reporter's GitHub handle if the
reporter is someone else (`rogerxaic` for Roger). Deduplicate. Note that both
rulesets require zero approving reviews by design — the request is a courtesy so the
right person sees it, never a gate.
