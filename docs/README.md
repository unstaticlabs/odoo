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
make docs          # regenerate users/README.md, product/decisions/README.md and llms.txt
make docs-check    # front matter, directories, links, images, sources, decision numbering, stale indexes
```

`scripts/check-docs` runs in CI on every pull request. Generated pages and
their screenshots are committed; the qualification run that admits a release
records when each journey last passed, and the viewer shows that next to the
page.

## Adding a page

- **A how-to or the tutorial**: write a journey (a native Odoo tour plus a
  `usl_docs.journeys` entry), run `make docs`, commit the page and its
  screenshots. The skill `.claude/skills/usl-docs-authoring` walks through it.
- **A reference page**: extend the generator's sources (`help=` on fields,
  `_description` on models) rather than the page.
- **An explanation**: write it by hand, with front matter and `source` links.
- **A decision**: `product/decisions/NNNN-title.md`, next free number, sections
  Context, Decision, Consequences, Alternatives considered. Run `make docs`.
