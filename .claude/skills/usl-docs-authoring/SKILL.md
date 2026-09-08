---
name: usl-docs-authoring
description: Add or change documentation in the USL Odoo distribution — a test-backed how-to or tutorial journey, a hand-written explanation, a reference source, or a decision record — and prove it with the docs check. Use when a change alters what a user sees or does, when a page is out of date, or when a decision needs recording.
---

# Documentation that a test proves

Every user page opens with front matter; the directory is the Diátaxis type;
`scripts/check-docs` is the judge. `docs/README.md` holds the schema. Read the
page you are changing and its neighbours first, then decide which of the four
kinds of change you are making.

## Which kind

| The reader wants to… | Write a… | Where | Proof |
| --- | --- | --- | --- |
| learn the product from nothing | tutorial | `docs/users/TUTORIAL.md`, generated | its journey |
| get a task done | how-to guide | `docs/users/how-to/<slug>.md`, generated | its journey |
| look a fact up | reference | `docs/users/reference/`, generated from code | the generator |
| understand why | explanation | `docs/users/explanation/`, hand-written | `source` links |
| know why we chose this | decision | `docs/product/decisions/NNNN-title.md` | the check |

Do not mix them. A how-to that explains is two pages; an explanation that gives
steps is a how-to that will go stale.

## A how-to or tutorial journey

1. Write the tour under `<module>/static/tests/tours/`, registered in
   `web_tour.tours`, with a stock `id` on every step the reader should see.
   Only stock step keys (`id`, `content`, `trigger`, `run`, `timeout`); any other
   key fails the test. No `expectUnloadPage`: journeys navigate inside the
   web client.
2. Register the journey in `usl_docs.journeys` under the same tour name: `id`
   (the page slug), `type`, `title`, `description`, `persona`, `lang`, and one
   entry per documented step with `text` (the sentence the reader follows,
   Markdown allowed, in the page's language) and `screenshot: true` where a
   screen helps. Steps without an entry run silently.
3. Drive it from a `JourneyCase` test in `<module>/tests/`, as a non-admin
   fixture user with synthetic data. Never production records, prices,
   identities or credentials: screenshots are committed to a public repository.
4. Run `make docs` in the isolated stack. It runs the journey inside the test
   container, writes the page and its screenshots, and refreshes the indexes.
   Commit the page, the screenshots and the tour together.
5. Prove it: `make docs-check` passes; edit one step's `text`, run
   `scripts/docs_generate.py check` and watch it fail; regenerate.

## An explanation

Front matter with `type: explanation`, `generated: false`, and `source`
pointing at the code the page explains. Write with the repository's
`writing-clearly-and-concisely` skill: reader first, result first, one idea per
paragraph, no steps.

## A reference page

Do not edit the page. Add `help=` to the field, `_description` to the model,
or the missing menu, and rerun the generator. Coverage per module is in
`scripts/check-docs --coverage`.

## A decision

Next free number, `NNNN-kebab-title.md`, first line `# N. Title`, then
`Date:` and `Status:` lines, then Context, Decision, Consequences, Alternatives
considered. Say what was rejected and why. Superseding a decision edits the old
record's status and links the new one; it never deletes. Run `make docs`.

## Before handing back

- `make docs-check` passes and `python3 -m unittest scripts.tests.test_user_docs` passes.
- The page reads correctly in the product at `/usl/user-docs/<path>` as the
  intended persona, desktop and mobile, with the right pills.
- Any file change under `custom-addons/` needs the action-risk reseal
  (`.claude/skills/usl-cluster-delivery`).
