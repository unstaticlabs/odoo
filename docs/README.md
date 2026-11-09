# Documentation

Four areas, one rule each. Read this page first; a running product serves the
user guide at `/usl/user-docs`, with `llms.txt`, `llms-full.txt` and
`index.json` beside it for agents.

| Area | For whom | Rule |
| --- | --- | --- |
| [`users/`](users/README.md) | People using the product, and the Help menu | Diátaxis. Tutorials and how-to guides are generated from browser journeys; reference is generated from the code; explanations are written by hand. Served with every release. |
| [`product/`](product/README.md) | Anyone deciding what the product does | Evergreen specifications and, under [`decisions/`](product/decisions/), the numbered decision records. |
| [`accounting/`](accounting/README.md) | Anyone changing accounting behaviour | The accounting truths a change, upgrade or release must preserve. |
| [`operations/`](operations/README.md) | Operators and release engineers | Runbooks and contracts. |

Dated records (incident reviews, QA captures, upstream-sync logs) live under
`archive/` once they stop being maintained.

## User pages carry front matter

Every page under `users/` opens with a block the viewer and the checks read:

```yaml
---
title: Match a bank transaction
type: how-to                 # tutorial | how-to | reference | explanation
description: Match a bank line against invoices, bills or a suggested counterpart.
lang: en                     # en | fr
persona: accountant          # everyone | ceo | accountant | finance_operator | employee | manager | administrator
app: Accounting              # groups the navigation
models:                      # what the page is about; the Help menu matches the current screen against them
  - account.bank.statement.line
actions:                     # the actions (XML ids or paths) the page is about; an action match wins over a model match
  - account.action_bank_statement_tree
screen: /odoo/action-account.action_bank_statement_tree   # "Open this screen in Odoo"
translation_of: how-to/reconcile-bank-transaction.md      # on a translated page only
journey: usl_docs.bank_matching   # generated pages only: the journey that proves the page
source:                      # repository paths the page is about; linked at the running release
  - custom-addons/usl_accounting/static/tests/tours/bank_matching_journey.js
generated: true              # true: regenerate with make docs, never edit by hand
---
```

The directory is the type: `TUTORIAL.md`, `how-to/`, `reference/`,
`explanation/`. A page whose `type` disagrees with its directory fails the
check. `users/README.md` is the only page without front matter. Pages without a
`journey` show "Not test-backed" in the product until a journey lands; that is
the conversion backlog, measured by `scripts/docs check --coverage`.

There are no committed indexes. The viewer derives the navigation, `llms.txt`
and `index.json` from the front matter at request time, so two pull requests
that add pages never touch the same file.

## Commands

```bash
make docs            # run the journeys in the test container, then render pages and screenshots
make docs-journeys   # only run the journeys on the product module set, as the qualification run does (MODULE=name narrows it for a quick look, but only the full set yields captures CI accepts); records land in artifacts/usl-docs
make docs-render     # only render from recorded journeys (scripts/docs generate render)
make docs-reference  # extract the registry from the action-risk closure and render reference/generated/
make docs-mcp        # render reference/mcp-tools.md from the pinned odoo-mcp commit (ODOO_MCP_REPOSITORY=../odoo-mcp)
make docs-check      # front matter, directories, links, images, sources, decision numbering
scripts/docs index   # print the llms.txt index with repository paths
```

`scripts/docs check` runs in CI on every pull request. The qualification
database job runs every journey, proves the committed pages and screenshots
match them (`scripts/docs generate check`), proves the reference pages match
the registry (`scripts/docs reference check`) and writes `docs-evidence.json`
(`scripts/docs evidence create`). The release workflow proves the MCP page
matches the pinned server (`scripts/docs mcp check`), downloads the evidence,
verifies it against the qualified commit and run, and embeds it as
`qualification.docs_evidence` in the release manifest; the deployment injects
it into the odoo service as `USL_DOCS_EVIDENCE_JSON`, and the viewer reads it to
say when each page last passed. A recovery release carries evidence that proves
nothing, and the guide says "Recovery build". Screenshots are compared by
pixels (a small tolerance absorbs font and antialiasing drift) and only replaced
when the screen changed; a Chromium upgrade in the base image re-baselines many
of them at once, and that is a normal, one-off pull request.

## How a journey becomes a page

1. A stock tour in `<module>/static/tests/tours/` gives an `id` to every step
   the reader should see. Only stock step keys; no `expectUnloadPage`.
2. A `usl_docs.journeys` registry entry under the same tour name names the
   page (`id` slug, `type`, `title`, `description`, `persona`, `lang`, `app`,
   `models`) and, per step id, the `text` the reader follows and
   `screenshot: true` where a screen helps (`mask: [selectors]` hides volatile
   elements, `scrollTo: selector` brings a detail below the fold into view).
3. A `JourneyCase` test (`custom-addons/usl_docs/tests/journey.py`) tagged
   `usl_docs_journey` runs it with `run_journey(tour, url, login)`. The URL is
   the page's `screen`, so use an XML id (`/odoo/action-<module>.<action>`);
   a journey that opens one record starts on `/odoo/action-<xml_id>/<id>` and
   passes `screen="/odoo/action-<xml_id>"` so the page never carries a
   database id. The test is skipped unless `USL_DOCS_JOURNEYS=1` and
   `usl_docs` is installed, so ordinary module suites never pay for it.
4. `make docs` writes `docs/users/how-to/<slug>.md` (or `TUTORIAL.md`), its
   `<slug>/NN-step.png` screenshots, and `<slug>/journey.json`: the committed
   facts of the journey (steps, assertions, triggers, where the tour and the
   test live, screenshot digests) that the product's test record page shows
   with deep links into the code at the qualified commit.

Each module owns its journeys; the pages they produce are named after the
journey, so parallel work on two modules never collides.

## Adding a page

- **A how-to or the tutorial**: write a journey (a native Odoo tour plus a
  `usl_docs.journeys` entry), run `make docs`, commit the page and its
  screenshots. The skill `.agents/skills/usl-docs-authoring` walks through it.
- **A reference page**: the pages under `users/reference/generated/` come from
  the product's registry (`make docs-reference`, checked in CI): fields per
  module, states, roles, menus, settings and the `help=` coverage. Change the
  field, group, menu or setting, never the page. The hand-written reference
  pages beside them explain what the values mean; the generated ones enumerate.
- **An explanation**: write it by hand, with front matter and `source` links.
- **A decision**: `product/decisions/NNNN-title.md`, next free number, sections
  Context, Decision, Consequences, Alternatives considered. The directory
  listing is the index.
