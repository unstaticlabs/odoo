# 3. Documentation generated from tests, served with the release, and driving the fork's simplification

Date: 2026-11-08
Status: accepted

Supersedes the draft "documentation generated from tests" of 2026-09-08, which
never left its pull request stack (#206 to #209).

## Context

The user guide already travels with every release: `docs/users` is copied
into the distribution image and served at `/usl/user-docs`, which the Help
menu opens. Its tree was shaped like Diátaxis. Everything else about it was
weak.

Every page was written by hand and nothing proved a page still matched the
product. Three copies of the index existed (`docs/users/README.md`, an
`mkdocs.yml` navigation that no workflow ever built, and the viewer's
filesystem listing) and they had drifted: a `guides/` directory duplicated
`how-to/`. Decision records sat in a root `DECISIONS/` folder nothing linked
to. Whole product areas had no user page at all: Home, Projects, agent
access, navigation, multi-company work. On 2026-09-08 a user asked how to post
an expense batch that was already posted and paid; the screen said "Needs
attention · 16" and no page could have told the reader which was true.

A first implementation (four stacked pull requests, September 2026) proved
the mechanism end to end but fell two months behind the integration branch.
Reviewing it on 2026-11-08, with an audit of the whole fork, showed three
design choices to correct (HTML built in Python, five scripts for one job,
committed indexes that every content pull request had to touch) and a wider
truth: the repository carried 200 pages and 24 MB of binaries of which a
quarter were one-shot records, four hand-maintained copies of the
product-module registry that had already drifted, a dead MkDocs toolchain,
and core divergences the fork overview did not admit.

The distribution is meant to be the blueprint for how Unstatic Labs builds
products. The documentation discipline is part of that blueprint, and the
documentation is the lens through which the repository is kept small.

## Decision

**Documentation is generated from the product's own tests wherever a test can
carry it, and every page says what proves it.** The four Diátaxis types keep
their directories under `docs/users/`, and each page opens with a front matter
block (`title`, `type`, `description`, `lang`, `persona`, `app`, `models`,
`actions`, `screen`, `translation_of`, `journey`, `source`, `generated`) that
the viewer and the checks read. The directory and the declared type must
agree.

**Tutorials and how-to guides are generated from browser journeys written as
native Odoo tours driven by `HttpCase`.** A journey is a tour plus a
`usl_docs.journeys` registry entry beside it, in the module that owns the
behaviour: it names the page, its persona, language, application and models,
and marks the steps whose screen the reader should see. The harness runs the
tour, captures those screens inside the test container, and `scripts/docs
generate` writes the page, its screenshots and a `journey.json` sidecar of
committed facts. Steps carry only stock tour keys, because `web_tour` rejects
any other with a console error that fails the test.

**Generated Markdown and screenshots are committed, and CI proves them
current.** Pull requests show documentation diffs, GitHub shows the
screenshots, and `scripts/docs generate check` fails when a committed page or
image no longer matches what the tests produce. Screenshots are compared by
pixels with a tolerance, never by bytes, because Chromium's encoder is not
byte-stable.

**Freshness comes from the qualification run, never from git.** The
qualification workflow always runs every journey, writes a docs-evidence file,
the release manifest carries it, the deployment injects it into the odoo
service, and the viewer stamps each generated page "Last tested N hours ago",
linking to an in-product test record that outlives GitHub's run retention: the
qualified commit, the run, each step with its assertion and trigger, the tour
and the test at that commit, the screens with their digests. The word "proof"
does not appear on screen.

**Reference pages are generated from the code**: fields per module, states,
groups, menus and settings from the Odoo registry, and the MCP tool catalogue
from the pinned `odoo-mcp` commit. `help=` coverage becomes a page with a
number, not an opinion. The hand-written reference pages stay: they explain
what values mean, which no generator carries.

**Explanations and decisions stay hand-written.** Decision records live in
`docs/product/decisions/NNNN-title.md`, numbered without gaps; a superseded
record keeps its number and changes its status.

**Pages live in one tree; modularity comes from ownership, not from paths.**
Valentin chose on 2026-11-08 to keep every page under `docs/users/` rather
than inside each module. Each module owns its journeys and tests; the pages
they produce are named after the journey; and nothing is indexed by hand, so
two pull requests that document two modules never touch the same file. The
viewer derives the navigation, `llms.txt`, `llms-full.txt` and `index.json`
at request time.

**A dedicated module, `usl_docs`, owns the viewer and the harness.** The
viewer is QWeb templates with one stylesheet and one script in their own asset
bundle, readable by every internal user. It is contextual: the Help entry of
the user menu carries the current action and model, and the guide lands on the
page that declares them. It is native to agents: `?format=md`, `?format=json`,
`llms.txt`, `index.json`, `search.json`, and an `usl.docs.guide` model whose
`search_pages`, `read_page` and `list_pages` are reachable through the MCP's
generic `odoo_call_method`.

**One command.** `scripts/docs` (`check`, `generate`, `reference`, `mcp`,
`evidence`, `index`) replaces five scripts and four modules; `make docs`,
`make docs-check`, `make docs-reference` and `make docs-mcp` wrap it. MkDocs is
retired.

**The documentation drives the repository's simplification.** Delivered as
separate pull requests in parallel: the one-shot records and capture folders
move to `docs/archive/` and unreferenced images are deleted; captures of a
change live in its pull request, not in the tree; the product-module registry
has one source derived from the manifests; `usl_bootstrap` leaves
`custom-addons/`; the temporary session trace leaves the core and
`fork-overview.md` lists every real core divergence. Renaming
`rebuild_account_migration` and folding the micro-modules were weighed and
deferred: each needs a database migration whose cost exceeds the gain today.

## Consequences

`scripts/docs check` runs in the compatibility job on every pull request. It
fails when a page lacks front matter, sits in the wrong directory, links to a
page or image that does not exist, names a source that does not exist, claims
a journey another page already claims, or when a decision record is
malformed or misnumbered.

Pages nobody has converted yet stay visible and say "Not test-backed". The
coverage is measured (`scripts/docs check --coverage`) rather than hidden, and
it is the backlog for the area-by-area conversion. On 2026-11-08 it read
1 test-backed how-to of 29, with ten more generable from existing tours.

Every journey runs in every qualification, whatever the focused test plan
selects, so a change in one module can fail on a journey through another.
That is the price of "always tested", and the failure names the journey and
the step first. The database job grows by a few minutes.

A Chromium upgrade in the base image re-baselines many screenshots at once:
an expected, one-off pull request, not a defect.

Adding `usl_docs` costs one action-risk registration and a product-module
registry entry; once the registry derives from the manifests, the entry is the
manifest itself.

## Alternatives considered

**Rebasing the four September pull requests one by one.** Ten known
conflicts, two months of drift in the sealed action surface, and three design
choices to undo anyway. Longer and worse than one rebuild on today's branch.

**Pages inside each module (`custom-addons/<module>/docs/`).** Proposed for
modularity; rejected by Valentin for the readability of one tree and one copy
in the image. The same isolation is obtained by deriving every index at
request time.

**Playwright specs as the journey format.** A second runtime and CI container,
no access to Odoo test fixtures, and the existing thirty-one tours would have
had to be rewritten rather than reused.

**An external evidence host with a manifest fetched at render time.** Hosting,
a network dependency inside the product, and an access-control question, to
gain freshness updates without a release, which the weekly delivery already
provides.

**Generating everything at build time and committing nothing.** No
documentation diffs in pull requests, no screenshots on GitHub, nothing for an
agent to read without a build.

**Committing screenshots by content hash.** Chromium's encoder is not
byte-stable; pixel comparison with a tolerance is what "unchanged" has to
mean.

**Keeping MkDocs beside the in-product viewer.** A second renderer that no
workflow ran, with a third hand-maintained copy of the navigation.

**A separate documentation MCP server.** Contrary to `odoo-mcp`'s invariant of
one capability registry. The guide is readable through the generic tool now,
and a native `docs_search` tool in `odoo-mcp` follows.
