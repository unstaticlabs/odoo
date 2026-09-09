---
name: usl-cluster-delivery
description: The contract for delivering one cluster of work as a qualified draft pull request — isolated worktree and Docker stack, reproduce, failing test first, fix, action-risk refresh, browser QA with screenshots, handoff. Use when allocating work to a delivery agent, or when doing that work yourself.
---

# Delivering one cluster

This is the contract a delivery agent works to. The Lead fills in the placeholders
and sends the whole thing; a person running it by hand reads it top to bottom.

Every rule below that names a failure is one that has actually happened here.

## What you deliver

**One draft pull request** against `19-usl-staging`, carrying a regression test
that fails without the fix and screenshots that show the change working.

You do not mark your own pull request ready. You hand back to the Lead, who
reviews and marks it ready.

## The assignment

- **Cluster:** `<slug>` — `<one line>`
- **Cards:** `<task id + title + Odoo link>` for each item in the cluster
- **What the reporter said, verbatim:** `<their own words from the chatter>`
- **Reported against:** `<environment and deployment generation>`
- **Attached screenshot:** `<local path, or none>`
- **Ports:** `<http>` / `<gevent>` — **Base branch:** `<branch to cut from>`

## Environment — isolated, never the canonical one

Worktrees live under `~/Code/odoo-worktrees/`, **never under `/tmp`**. On
2026-09-09 a tmp cleaner emptied every worktree there that had not been written to
for a few days, and six branches' worth of uncommitted work went with it. The
directories survived; the files did not.

```bash
WORKTREE=~/Code/odoo-worktrees/fb-<slug>
BRANCH=<type>/<slug>          # conventional: fix/ feat/ perf/ refactor/ docs/
PROJECT=usl-fb-<slug>         # Compose project, yours alone
DB=odoo_fb_<slug>             # NOT odoo_dev — see below
HTTP_PORT=<assigned>
GEVENT_PORT=<assigned>

mkdir -p ~/Code/odoo-worktrees
git -C /Users/valentin/Code/odoo worktree add "$WORKTREE" -b "$BRANCH" <base branch>
cd "$WORKTREE"

# scripts/commit refuses to run without these, and they are worktree-scoped,
# so a fresh worktree has none.
git config --worktree user.name  "valentinviennot"
git config --worktree user.email "valentinviennot@users.noreply.github.com"
git config --worktree usl.drivingHuman "valentinviennot <valentinviennot@users.noreply.github.com>"
git config --worktree usl.agent "<your model> <noreply@anthropic.com>"

cp .env.example .env
export ODOO_SAAS_COMPOSE_PROJECT="$PROJECT" COMPOSE_PROJECT_NAME="$PROJECT"
export ODOO_DEV_DB="$DB" ODOO_INIT_DB="$DB"
export ODOO_HTTP_PORT="$HTTP_PORT" ODOO_GEVENT_PORT="$GEVENT_PORT"
export USL_EINVOICE_LIVE_ENABLED=0 USL_EREPORTING_LIVE_ENABLED=0

scripts/sync-oca-addons     # seeds from the main checkout, no network fetch
make worktree-env           # the current isolated-stack recipe
make doctor                 # read what it says
make dev && make status
```

Read `docs/operations/worktree-development.md` first — it is the maintained
version of this recipe and it wins over anything here.

Four things that will bite you:

- **`scripts/lib/compose-scope.sh` refuses the canonical project name from a linked
  worktree**, and refuses to touch a project owned by another directory. That is
  what stops you destroying someone else's stack. Never work around it.
- **`ODOO_DEV_DB=odoo_dev` routes `make dev` through Pocket ID** and demands a
  `.pocket-id.env` you do not have. Any other name gives a plain stack with
  `admin` / `admin`.
- **`make status` and `make doctor` print defaults** (`odoo_dev`, port 8069) in a
  fresh shell that has not re-exported the variables, even when your containers are
  correct. Cosmetic, and misleading at four in the morning.
- **The action-risk workflow needs the full module closure**, not your one module.
  Use `make action-risk-db`. A 61-module closure produces tens of thousands of
  false "changed" entries.

Tear down only your own project, and **only after `make action-risk-inventory`
passes** — the refresh needs the running registry, and rebuilding a stack to reseal
costs half an hour:

```bash
docker compose -p "$PROJECT" down --volumes --remove-orphans
```

## Read before changing anything

`AGENTS.md`, `CONTRIBUTING.md`, `DESIGN.md`, the module's docs under `docs/`, and
the playbooks in `.agents/skills/` — at minimum `writing-clearly-and-concisely`
(mandated for all user-facing copy and commit messages) and
`odoo-ui-product-quality` for any interface change.

## The work, in order

### 1. Reproduce, and return a verdict

Walk the reporter's actual journey first — their persona, their screen size, their
permissions. A report is a person telling you what they experienced, which is
always true, and what they think caused it, which sometimes is not. Take the
experience seriously and test the diagnosis.

Then say which it is: **reproduced** (proceed) · **already fixed** (name the commit
or PR) · **not reproducible** (what you tried, as whom, on what data, and what
happened instead) · **misdescribed** (the frustration is real, the stated cause is
not — describe what is actually happening and fix that, saying so) · **works as
designed** (explain the design, and whether it is worth changing anyway).

Never invent a fix for something you could not reproduce. Never dismiss a report
because it did not reproduce on the first attempt.

### 2. Write the failing test first

Before the fix. Python `tests/test_*.py` tagged
`@tagged("post_install", "-at_install", "<tag>")` and registered in
`tests/__init__.py`; HOOT `static/tests/*.test.js`; or a tour under
`static/tests/tours/` driven by `tests/test_*_tour.py`.

Run it and **record the failure verbatim** — it goes in the PR.

A test that passes with and without the fix proves nothing. PR #153 shipped one and
the outage survived its own regression test; #162 had to fix it properly.

### 3. Plan

Write a definition of done in terms of the user journey and the persona, not the
code. Prefer native Odoo or maintained OCA behaviour over a custom abstraction.
Keep the change scoped.

### 4. Fix, and prove it

```bash
scripts/odoo-dev test <module> odoo_test_<slug>   # backend, disposable database
scripts/odoo-dev test-js <module>                 # HOOT — desktop only
scripts/odoo-dev test-tag '/<module>:<Class>.<method>'
python3 -m compileall -q operations scripts custom-addons
python3 -m unittest discover -s scripts/tests -p 'test_*.py'
```

`test-js` runs the desktop suite only; mobile HOOT runs as part of the full backend
`scripts/odoo-dev test` command.

Then **revert the fix, re-run the new test, prove it fails**, and restore. Quote
all three outputs.

### 5. Refresh the action-risk surface

**If you changed any file at all under `custom-addons/<module>/`, do this — even a
pure JavaScript, SCSS or template edit that declares no action.**

The inventory seals the *installed module identity*, not only per-action digests.
CI fails with:

```
Action-risk inventory: FAIL
- Installed module set changed: <old> -> <new>
- Changed installed module identity: <your module>
```

PR #174 hit this on 2026-09-08: discovery correctly reported 0 actions added or
removed with identical digests, the agent reasonably concluded no refresh was
needed, and CI failed anyway. The test is not "did I change an action?" It is
**"did I change a file in the module?"**

```bash
make action-risk-db          # build the full-closure database first
make action-risk-discover    # candidate diff from the running registry
# classify: read_only / operational / recoverable / protected / transport /
# system_internal, with consequence, rationale, domain and evidence.
# Uncertainty resolves to protected.
make action-risk-refresh
make action-risk-inventory   # must pass before you tear anything down
make action-risk-runtime
```

Read `docs/operations/action-risk-inventory.md` before classifying. Say in the PR
how many digests moved and why each one did.

**If discovery shows drift on entries your branch never touched, stop and report
it.** Do not absorb unrelated drift into your reseal — the review gate forbids it,
and it hides someone else's change inside yours.

Run `make product-migration-boundary` if any manifest, module path or source
binding changed.

### 6. Browser QA and screenshots

Exercise the changed journey as a **non-admin fixture user** on **synthetic data
only**, desktop and mobile where the surface supports both. Check empty, loading,
validation and access-denied states. Watch the console and network panel.

**Most `scripts/odoo/*_qa_bootstrap.py` scripts assume a restored database, not an
initialised one, and raise on a fresh stack.** `home_qa_bootstrap.py` requires a
restored `valentin` user and fails without it. Read the script before running it;
if it cannot work, write a small disposable fixture instead and say so.

Odoo caches the compiled JS bundle server-side: editing a `.js` on disk is
invisible to a running container without a restart or `?debug=assets`. That has
already cost one wasted before/after pair.

Capture with the helper — headless, so no address bar, tab strip or local path
reaches the image, and it rewrites the PNG to carry only IHDR, IDAT and IEND:

```bash
node ~/.claude/scheduled-tasks/usl-monday-feedback-burn/qa-screenshot.mjs \
  --url "http://localhost:$HTTP_PORT/odoo/<path>" \
  --out "docs/product/screenshots/<area>/<name>-desktop.png" \
  --viewport desktop --login "<user>:<password>" --db "$DB" \
  --wait-for-selector "<selector that proves the page is ready>" \
  --scenario "<who is doing what>" --expected "<what the reader should see>"
```

Always pass `--wait-for-selector`. The helper verifies its own login now, but the
selector is what proves the *page* is the one you meant.

It writes a `.meta.json` sidecar with viewport, user, scenario and expected result
— **quote it in your handoff, never commit it.** Commit the PNG under
`docs/product/screenshots/<area>/` and follow that directory's README.

Look at every image before committing it. No production records, contact details,
financial data, credentials or local paths.

### 7. Commit, push, open a draft PR

```bash
scripts/commit --type <type> --scope <scope> \
  --summary "<imperative, no trailing period>" \
  --body "<paragraph>" --body "<paragraph>" \
  --validation "<command and result>"
```

`scripts/commit` generates the attribution trailers; it errors if you type them. It
refuses staged paths under `private/`, `usl-online-dump/` and the other private
prefixes.

```bash
git push -u usl "$BRANCH"
gh pr create --repo unstaticlabs/odoo --draft --base 19-usl-staging \
  --title "<conventional commit subject>" --body-file <path>
```

Write the body per `.claude/skills/usl-lead-review/pr-body.md`. Embed screenshots as
`https://github.com/unstaticlabs/odoo/blob/<commit sha>/<path>?raw=true`.

**Never push to `19-usl` or `19-usl-staging`.** A pre-push hook refuses it, and it
is right to.

## Hand back

1. **Verdict** from step 1, with evidence.
2. **PR number and link**, plus the branch name.
3. **What changed**, file by file, and why each file needed to.
4. **Test evidence** — failing before, passing after, failing again when reverted.
5. **Action-risk** — digests moved, your classification for each, and any drift you
   refused to absorb.
6. **Screenshots** — path, viewport, user, scenario, expected result for each.
7. **Risks, and what you deliberately did not do.**
8. **Every place this brief was wrong**, unclear or missing a step. Be specific:
   the command, what it did, what the brief said it would do. The brief's defects
   are worth as much as the pull request.
9. **Your token usage**, if you can see it.

**You cannot reach the Lead mid-flight.** There is no channel back until you
return, and at 3am nobody is awake. When you hit a decision you would normally
ask about, make the most conservative call that still delivers something, write
down the decision and the alternatives you rejected, and put it in the handoff.
Never stall waiting for an answer that cannot come.

## Never

- Mark your own pull request ready for review.
- Push to a release branch, or merge anything.
- Touch production, or any database you did not create.
- Stop or remove a Docker project that is not yours — other people's stacks run on
  this machine.
- Let business data reach the repository: supplier or customer identities, order or
  invoice references, prices, margins, personal data, credentials. Not in code, a
  fixture, a docstring, a commit message or a screenshot.
- Weaken a record rule or an access check to make the interface work.
