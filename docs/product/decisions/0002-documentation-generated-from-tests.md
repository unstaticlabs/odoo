# 2. Documentation generated from tests and served with the release

Date: 2026-09-08
Status: accepted

## Context

The user guide already travels with every release: `docs/users` is copied
into the distribution image and served at `/usl/user-docs`, which the Help
menu opens. Its tree was already shaped like Diátaxis. Everything else about
it was weak.

Every page was written by hand and nothing proved a page still matched the
product. Three copies of the index existed (`docs/users/README.md`, an
`mkdocs.yml` navigation that no workflow ever built, and the viewer's
filesystem listing) and they had already drifted: a `guides/` directory
duplicated `how-to/`. Decision records sat in a root `DECISIONS/` folder
nothing linked to. Whole product areas had no user page at all: Home, Projects,
agent access, navigation, multi-company work.

On 2026-09-08 a user asked how to post an expense batch that was already
posted and paid. The screen said "Needs attention · 16". The documentation
could not have helped, because nothing tied a page to the behaviour it
described, and nothing would have told the reader that the page had last been
true three releases ago.

The distribution is meant to be the blueprint for how Unstatic Labs builds
products. The documentation discipline is part of that blueprint.

## Decision

**Documentation is generated from the product's own tests wherever a test can
carry it, and every page says what proves it.** The four Diátaxis types keep
their directories, and each page opens with a small front matter block
(`title`, `type`, `description`, `lang`, `persona`, `journey`, `source`,
`generated`) that the viewer, the indexes and the checks all read. The
directory and the declared type must agree.

**Tutorials and how-to guides are generated from browser journeys written as
native Odoo tours driven by `HttpCase`.** A journey names its slug, persona
and language and marks the steps whose screen the reader should see; the
harness runs the tour, captures those screens, and a generator writes the
page. Playwright was not adopted: it would add a second browser runtime and a
second container to CI, and it cannot reach the fixtures Odoo tests already
have. Steps carry only stock tour keys, because `web_tour` rejects any other
key with a console error that fails the test; the journey metadata lives in a
sibling registry keyed by tour name and step id.

**Generated Markdown and screenshots are committed.** Pull requests then show
documentation diffs, GitHub shows the screenshots, and a CI check proves the
committed pages match what the tests produce, the way
`custom-addons/usl_access_control/policy/action_surface.json` is already
committed and sealed. Screenshots are replaced only when they differ
materially (a pixel-difference threshold, never a byte comparison, because
Chromium's PNG encoding is not stable), and only ever captured inside the test
container so local and CI captures come from the same browser build.

**Freshness comes from the qualification run, never from git.** The
qualification workflow always runs every journey, writes a docs-evidence file
(commit, run, timestamp, per-journey outcome and screenshot digests), and the
release carries it in the release manifest the running stack already receives.
The viewer stamps each generated page "Last tested N days ago · proof", where
the proof is an in-product evidence page that outlives GitHub's run retention.
The evidence is not copied into the image: distribution images are
input-addressed and reused when their inputs match, so a time-varying file
inside them would either go stale or break that identity.

**Reference pages are generated from the code**: model descriptions, field
labels and `help=` texts, selection values, groups, menus and settings, read
from the Odoo registry. That makes `help=` coverage a documentation debt with
a number attached, not an opinion.

**Explanations and decisions stay hand-written.** Explanations are the
understanding a test cannot carry. Decision records live in
`docs/product/decisions/NNNN-title.md`, numbered without gaps, with the Nygard
sections Context, Decision, Consequences and Alternatives considered; a
superseded record keeps its number and changes its status.

**A dedicated module, `usl_docs`, owns the viewer and the journey harness.**
The viewer moved out of the accounting module because the guide covers Sign,
Documents, Feedback and Home as well, and it is now readable by every internal
user rather than only accounting readers. MkDocs was retired: one renderer,
inside the product, is enough, and its navigation is derived from front matter
rather than maintained by hand.

**The repository is agent-native.** `docs/README.md` maps the areas and the
commands; `docs/llms.txt` indexes every page by type; the viewer returns the
committed Markdown to `Accept: text/markdown`; `CLAUDE.md` imports `AGENTS.md`
so Claude Code and Codex read one set of rules; and
`.claude/skills/usl-docs-authoring` tells an agent how to add a journey, an
explanation, a reference source or a decision, and which check proves it.

## Consequences

`scripts/check-docs` runs in the compatibility job on every pull request. It
fails when a page lacks front matter, sits in the wrong directory, links to a
page or image that does not exist, names a source that does not exist, claims
a journey another page already claims, or when a generated index is stale.
`make docs` regenerates the indexes; `make docs-check` runs the check.

Pages nobody has converted yet stay visible and say "Not test-backed". The
coverage is measured (`scripts/check-docs --coverage`) rather than hidden, and
it is the backlog for the area-by-area conversion. On 2026-09-08 it read
0 test-backed pages of 39.

Every journey runs in every qualification, whatever the focused test plan
selects, so a change in one module can fail on a journey through another.
That is the price of "always tested", and the failure names the journey and
the step first. The database job's timeout rises to absorb it; mobile
captures are opt-in per journey to keep the cost bounded.

A Chromium upgrade in the base image will re-baseline many screenshots at
once. That is an expected, one-off re-baseline pull request, not a defect.

Adding `usl_docs` costs one action-risk registration and a
`PRODUCT_MODULES` entry in every registry (`operations/release_identity.py`,
`scripts/odoo/release_identity.py`, `scripts/odoo/product_database_boundary.py`,
`scripts/action_risk_inventory.py`). The old controller path in
`rebuild_account_migration` remains as a re-export so restore-stage tests keep
importing it.

## Alternatives considered

**Playwright specs as the journey format.** Rejected: a second runtime and CI
container, no access to Odoo test fixtures, and the existing eight tours would
have had to be rewritten rather than reused.

**An external evidence host with a manifest fetched at render time.**
Rejected: it adds hosting, a network dependency inside the product, and an
access-control question, to gain freshness updates without a release, which
the weekly delivery run already provides.

**Generating everything at build time and committing nothing.** Rejected: no
documentation diffs in pull requests, no screenshots when reading the
repository on GitHub, and nothing for an agent to read without a build.

**Committing screenshots by content hash.** Rejected: Chromium's encoder is not
byte-stable, so a hash would replace every image on every run. Pixel
comparison with a tolerance is what "unchanged" has to mean.

**Keeping MkDocs beside the in-product viewer.** Rejected: a second renderer
that no workflow ran, with a third hand-maintained copy of the navigation.
