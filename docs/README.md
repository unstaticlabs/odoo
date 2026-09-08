# Documentation

Four areas, one rule each. Read this page first; `llms.txt` beside it indexes
every user page and decision for an agent (a running product serves its own at
`/usl/user-docs/llms.txt`).

| Area | For whom | Rule |
| --- | --- | --- |
| [`users/`](users/README.md) | People using the product, and the Help menu | Diátaxis. Tutorials and how-to guides are generated from browser journeys; reference is generated from the code; explanations are written by hand. Served at `/usl/user-docs` with every release. |
| [`product/`](product/README.md) | Anyone deciding what the product does | Evergreen specifications and, under [`decisions/`](product/decisions/README.md), the numbered decision records. |
| [`accounting/`](accounting/README.md) | Anyone changing accounting behaviour | The accounting truths a change, upgrade or release must preserve. |
| [`operations/`](operations/README.md) | Operators and release engineers | Runbooks, contracts and incident reviews. |

## User pages carry front matter

Every page under `users/` opens with a block the viewer, the indexes and the
checks all read:

```yaml
---
title: Match a bank transaction
type: how-to                 # tutorial | how-to | reference | explanation
description: Match a bank line against invoices, bills or a suggested counterpart.
lang: en                     # en | fr
persona: accountant          # everyone | ceo | accountant | finance_operator | employee | manager | administrator
journey: usl_docs.bank_matching   # generated pages only: the journey that proves the page
source:                      # repository paths the page is about; linked at the running release
  - custom-addons/usl_accounting/static/tests/tours/bank_matching_journey.js
generated: true              # true: regenerate with make docs, never edit by hand
---
```

The directory is the type: `TUTORIAL.md`, `how-to/`, `reference/`,
`explanation/`. A page whose `type` disagrees with its directory fails the
check. Pages without a `journey` show "Not test-backed" in the product until a
journey lands; that is the conversion backlog, measured by
`scripts/check-docs --coverage`.

## Commands

```bash
make docs            # run the journeys in the test container, render pages and screenshots, refresh the indexes
make docs-journeys   # only run the journeys (MODULE=name limits the installed modules); records land in artifacts/usl-docs
make docs-render     # only render from recorded journeys (scripts/docs-generate render)
make docs-check      # front matter, directories, links, images, sources, decision numbering, stale indexes
```

`scripts/check-docs` runs in CI on every pull request. The qualification
database job runs every journey, proves the committed pages and screenshots
match them (`scripts/docs-generate check`), and writes `docs-evidence.json`
(`scripts/docs-evidence create`), which the release carries and the viewer
reads to say when each page last passed. Screenshots are compared by pixels
(a small tolerance absorbs font and antialiasing drift) and only replaced when
the screen changed; a Chromium upgrade in the base image re-baselines many of
them at once, and that is a normal, one-off pull request.

## How a journey becomes a page

1. A stock tour in `<module>/static/tests/tours/` gives an `id` to every step
   the reader should see. Only stock step keys; no `expectUnloadPage`.
2. A `usl_docs.journeys` registry entry under the same tour name names the
   page (`id` slug, `type`, `title`, `description`, `persona`, `lang`) and, per
   step id, the `text` the reader follows and `screenshot: true` where a screen
   helps (`mask: [selectors]` hides volatile elements).
3. A `JourneyCase` test (`custom-addons/usl_docs/tests/journey.py`) tagged
   `usl_docs_journey` runs it with `run_journey(tour, url, login)`. It is skipped
   unless `USL_DOCS_JOURNEYS=1`, so ordinary module suites never pay for it.
4. `make docs` writes `docs/users/how-to/<slug>.md` (or `TUTORIAL.md`) and its
   `<slug>/NN-step.png`, with the screenshot digests in the front matter.

## Adding a page

- **A how-to or the tutorial**: write a journey (a native Odoo tour plus a
  `usl_docs.journeys` entry), run `make docs`, commit the page and its
  screenshots. The skill `.claude/skills/usl-docs-authoring` walks through it.
- **A reference page**: extend the generator's sources (`help=` on fields,
  `_description` on models) rather than the page.
- **An explanation**: write it by hand, with front matter and `source` links.
- **A decision**: `product/decisions/NNNN-title.md`, next free number, sections
  Context, Decision, Consequences, Alternatives considered. Run `make docs`.
