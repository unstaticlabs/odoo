# Accounting development workflow

## Reconstruction performance evidence

Exact Accounting replay records monotonic timings and row counts for
configuration, partners, move creation/posting/cancellation, reconciliation,
payments, analytics, attachments, expenses, assets and final validation in the
private import status. Move creation uses deterministic bounded batches of 250;
source identity lookups and high-volume relation creation are prefetched and
bounded. A temporary partial unique composite source-identity index enforces
one target representation and is removed with migration columns at finalization.

After the committed import the harness runs `ANALYZE`; custom-format cache and
candidate restores default to four `pg_restore` workers. Compare repeated runs
against the historical approximately 42-minute Accounting stage with the exact
same dump. Never trade parity for an artificial wall-clock target, disable
PostgreSQL durability, insert ledger rows with SQL, or modify the read-only
source database.

Last updated: 2026-08-27

Audience: implementation agents and developers working on Milestone 13.

This workflow exists to keep accounting development fast, safe and reviewable. Do not rerun expensive source restores or full target rebuilds unless the change actually needs them.

## Principles

- Keep the source database restored and running while iterating.
- Treat Accounting as one stage of the wider source-truth migration. The
  [current-distribution gate](source-truth-migration.md) must pass before
  accepting `odoo_dev`; the stricter whole-source gate continues to expose
  future application scopes. Accounting and Projects parity alone do not
  certify the complete Distribution.
- Keep OCA add-ons synced with `make oca-addons-sync` before target reset, reporting or reconciliation work.
- Reuse the extracted snapshot when changing only Odoo views, menus, reports, permissions or documentation.
- Rebuild the target only when import behavior, schema assumptions, model creation, or data transformations change.
- Run narrow Odoo module updates for UI/report code changes.
- Commit validated scoped chunks regularly.
- Never commit private production extracts or generated private artifacts.

## Database roles during development

Normal development, module updates and browser QA use one disposable database:
`odoo_dev`. It is the canonical production-shaped target, not a source-only
mirror: business data is reconstructed from Odoo Online, then target-only
configuration such as Pocket ID is applied in a separate finalization stage.

The reconstruction harness creates `odoo_saas_19_3_validation_exact` and
`odoo_saas_19_3_validation_native` only when their explicit pipeline stages
run. They are disposable evidence databases, not alternate development
environments. The restored `odoo_online_source_saas_19_3` database is isolated
in the optional `accounting-source-db` service and is used read-only for
extraction.

Do not enter durable business data in any local database. Recreate `odoo_dev`
from the harness when import or reconstruction behavior changes; for ordinary
code and UI work, update it in place.

The complete canonical lifecycle is:

```text
Online dump → Accounting import/parity → Documents product/security
→ identity/Product/HR → Projects → Paperless archive
→ Paie TESE → Platform Billing
→ uninstall migration modules → product-boundary checks
→ Pocket/Odoo/Paperless identities and target configuration
```

Run the authoritative path from the main checkout with the exact SHA-256 shown
by `make migration-source-inventory`:

```bash
make migrate-production SOURCE_SHA=<exact-dump-sha256>
```

It always rebuilds Paperless from source. Before changing `odoo_dev`, it
requires a clean checkout and proves that every populated source scope and
every attachment has a completed final disposition. This is the only path
accepted for final migration and release qualification.

Normal branch/worktree QA uses `make qa`. It verifies a sealed host-local seed,
restores independent Odoo/Paperless volumes, upgrades the current branch,
recreates Pocket and Paperless identities, and reruns product boundaries.
Create or replace that seed from the main checkout with
`make qa-cache-refresh`; publication is atomic and occurs only after a fresh
reconstruction of all currently shipped product scopes and target finalization
pass. The manifest binds the seed
to source dump and filestore digests, migration code, resolved image IDs, OCR
settings, archive digests and product module versions.
The publication commit is audit provenance, not a cache key. A different
commit may consume the seed only when the content-derived migration identity,
source package, resolved runtime images and sealed artifacts still match. OCA
patch content is part of that migration identity.

This cache contract uses manifest schema `usl-qa-reconstruction-seed-v4`.
Version 4 adds the checksum-locked Collaboration disposition ledger to the
private seed artifacts so an isolated target can re-run the final product
boundary without depending on evidence from the project that created it.
Existing v2 seeds are intentionally rejected; after first deploying this
tooling, run `make qa-cache-refresh` once from the main checkout to publish a
qualified v4 seed before worktrees use `make qa` or `make qa-reuse`. Do not
replace that shared seed from a topic worktree.

After a cold `make qa` succeeds, use `make qa-reuse` for the unchanged
worktree's tight validation loop. It reuses only that worktree's independent
writable volumes and reruns product boundaries and multi-company acceptance.
It never shares a live PostgreSQL or Paperless volume with another checkout.
The state stamp binds the exact seed manifest and migration digest; missing
volumes, a new seed, or relevant code changes force the normal cold path. Its
separate worktree-state digest includes product views, translations and static
assets even though those upgradeable files do not invalidate the portable
seed itself.
Because QA data is writable, plain `make qa` remains mandatory after manual
data mutation and whenever a pristine baseline is part of the test contract.
Retire a finished worktree's writable QA state with `make qa-clean
CONFIRM=qa-volumes`; the confirmation-gated command preserves every shared
cache and refuses foreign projects or active one-shot migration containers.

Use `PROFILE=no-documents` when Documents is irrelevant,
`PROFILE=documents-smoke` for a deterministic relationship-complete source
sample, or `PROFILE=clean-install` for product installability with only
self-contained synthetic fixtures. Source-template TESE and settlement
fixtures remain separate explicit QA commands. Partial profiles
are recorded in `usl.qa.data_profile`, included in timing evidence, and rejected
by pre-production gates. They retain the complete Accounting ledger.
`make target-reconstruct-product` is the uncached fresh developer path for the
currently shipped product perimeter. The older
`target-reconstruct-reuse-documents` command remains a same-project diagnostic.
Superseded private seeds are retained for rollback until an operator runs
`make qa-cache-prune CONFIRM=qa-seeds`; the current qualified seed is preserved.
The source contains no SSO
configuration; Pocket ID is therefore intentionally absent from source parity
and added only after the imported business state passes its controls. This
final step also creates governed Paperless users, maps them to existing Odoo
users by immutable Pocket subject, and synchronizes document-object access; it
never imports Online credentials or user-session state.
Every reconstruction writes a dump-bound run record below ignored
`artifacts/migration/private/runs/`. It records purpose, source SHA-256, Git
commit, migration-code digest, ordered stage outcomes, duration, source
coverage and attachment-ledger evidence. A failed run remains visible and
cannot be confused with a qualified production migration.
Projects, Paie TESE and Platform Billing keep their temporary source bindings
until the Paperless archive and every downstream restore have passed. This
lets a development/QA resume resolve records by exact source identity after an
archive interruption; the global finalization stage then uninstalls those
modules in dependency-safe order. Production still starts from a clean target
and never uses resume evidence.

The canonical command validates and restores the current local dump into the
isolated read-only source service, refreshes source controls and extraction,
then resets `odoo_dev`; it does not depend on a previously running source
container.
The orchestrator keeps the web process stopped between reset, import,
validation and every downstream restoration stage so browser traffic and
scheduled jobs
cannot observe or mutate an intermediate target.

The Accounting importer is the temporary `usl_accounting_restore` add-on under
`migration/accounting_restore/`. It is mounted only by the
`accounting-migration` Compose profile. `scripts/accounting-restore finalize`
requires a passed import and no active P0/P1 restoration discrepancy, compares
business facts before and after uninstall, and validates the database again
through the normal product-only add-ons path. A finalized `odoo_dev` must not
contain its models, source fields, metadata, XML IDs or views.

## Fast iteration matrix

| Change type | Usually rerun | Avoid unless needed |
| --- | --- | --- |
| Markdown docs | `git diff --check` | source restore, target reset |
| Odoo XML menus/views | `scripts/odoo-dev deploy` on `odoo_dev` | source restore, extract |
| Odoo Python report formatting only | module update, targeted Odoo tests, one report export smoke test | source restore |
| Native analytic pivot fields/views | module update, targeted measure/view test, aggregate sign/reconciliation query; focused pivot browser smoke only when interaction changed | source restore, native analytic replay |
| Electronic-invoice readiness/reception | module update, offline UBL reception/deduplication test, cron inactivity query; never register or call a live platform | source restore, live provider activation |
| Security/ACL changes | module update, role-specific access tests | source restore |
| Future currency-rate provider changes | module update, targeted provider tests, `accounting-currency-rate-provider`, then manager/reviewer browser journeys | source restore, extract, native validation replay |
| Importer mapping changes | `accounting-validation-exact-reset`, `accounting-validation-exact-import`, `accounting-validation-exact-validate` | source restore if snapshot unchanged |
| Product expense reconstruction changes | clean disposable `accounting-dev-reset`, `accounting-dev-import`, then `accounting-dev-validate`; promote the same verified flow to the canonical development database only after it passes | source restore when the restored snapshot and filestore are unchanged; broad browser QA when no expense UI changed |
| Company-paid expense bank matching | `/usl_accounting:TestExpenseBankMatching`, module update, one manager and one read-only form check; when source-cache classification changes, also run the clean `accounting-dev-reset`, `accounting-dev-import`, `accounting-dev-validate` sequence | native-validation replay and broad browser QA when native expense/payment/reconciliation behavior is unchanged |
| Attachment/filestore replay changes | `accounting-dev-attachments`, `accounting-attachment-audit`, focused attachment and draft-regeneration tests | ledger reset or full native replay when record mappings are unchanged |
| Source extraction mapping changes | `accounting-extract`, target reset/import/validate | source restore if source DB still running and unchanged |
| native validation expense/document mapping changes | `accounting-validation-native-reset`, `accounting-validation-native-expenses`, `accounting-validation-native-documents` | source restore, exact-validation reset/import |
| native validation native asset changes | native validation reset, `accounting-validation-native-assets`; repeat asset replay for idempotence and run the manager/reviewer browser journey | source restore, extraction, exact-validation reset/import |
| native validation native deferral changes | native validation expenses/documents, then `accounting-validation-native-deferrals`; repeat deferral replay for idempotence and run the manager/reviewer browser journey | source restore, extraction, exact-validation reset/import |
| native validation expense settlement changes | native validation reset, expenses, documents, `accounting-validation-native-expense-settlement`; repeat settlement for idempotence | source restore, exact-validation reset/import |
| native validation document settlement changes | native validation reset, expenses, documents, expense settlement, `accounting-validation-native-document-settlement`; repeat document settlement for idempotence | source restore, exact-validation reset/import |
| native validation General Reconciliation changes | native validation reset, expenses, documents, expense settlement, document settlement, `accounting-validation-native-general-reconciliation`; repeat General Reconciliation for idempotence | source restore, exact-validation reset/import |
| native validation direct bank categorization changes | native validation reset through General Reconciliation, then `accounting-validation-native-bank-categorization`; repeat bank categorization for idempotence | source restore, exact-validation reset/import |
| native validation external-endpoint bank changes | native validation reset through direct bank categorization, then `accounting-validation-native-bank-external`; repeat external bank replay for idempotence | source restore, extraction, exact-validation reset/import |
| native validation analytic changes | Run every posting stage through assets, deferrals and external bank replay, then `accounting-validation-native-analytics`; repeat analytics for idempotence and run the manager/reviewer/native-report browser journeys | source restore, extraction, exact-validation reset/import |
| Source dump or restore script changes | full source restore and downstream stages | none |
| Closing/report parity milestone proof | full `make accounting-compat` rehearsal | partial validation |

## Performance audit and cache boundaries

The 2026-08-27 audit used the private timing records emitted by the
orchestrators plus Docker's read-only resource inventory. Recent successful
cold reconstructions took 862–1,886 seconds. Documents restoration accounted
for as much as 906 seconds; source database restore was only 12–17 seconds.
Qualified-seed QA reduced the run to 313–599 seconds, with seed hydration at
74–127 seconds and branch finalization at 182–353 seconds. Docker reported 199
local volumes (35.23 GB) and 41.55 GB of build cache, so automatically copying
another database image or retaining a second shared live database would worsen
the resource problem.

The selected design has three cache levels:

1. BuildKit retains system, wheel, Node, core-source and product-image layers.
2. One private, immutable, content-addressed QA seed is shared across
   worktrees; every hydrated project receives independent writable volumes.
3. An explicit worktree-local warm state avoids hydration and finalization only
   for an unchanged, already-qualified target.

A database baked into a Docker image was rejected because the private source
would enter image layers and builder caches, image transfer would duplicate
hundreds of megabytes, and database state is not an application build input. A
single writable PostgreSQL/Paperless volume shared by worktrees was rejected
because concurrent servers and mutable QA activity would break isolation and
data integrity. A physical source-volume clone was also not selected: it would
optimize a 12–17 second stage while increasing retained volume storage; the
portable final-state seed eliminates the much larger import and OCR costs.

Pinned OCA repositories use a separate safe optimization. A new worktree seeds
its independent checkout from the main checkout when the exact commit is
already available, avoiding network transfer. Repeated synchronization checks
out and reapplies the tracked patches locally and fetches only if a required
object is absent. On the audited worktree this reduced the repeated sync from
the recorded 14–16 seconds to 1.62 seconds. Excluding tests, translations,
static assets, views and Markdown from the reconstruction identity reduced its
local digest calculation to 0.07 seconds; those product surfaces are upgraded
and validated after seed hydration, while runtime migration code, data,
security, manifests and OCA patches remain cache-invalidating inputs.

## Normal UI/report development loop

From the host shell:

```bash
cd /Users/valentin/Code/odoo
make doctor
make dev
make deploy
```

Plain `make` displays the curated command help and never starts services.
`make doctor` is read-only: it reports the selected checkout, branch, Compose
project, database, ports, container owners and health. `make dev` opens the
existing environment. `make deploy` stops Odoo, updates
`rebuild_account_migration` in `odoo_dev`, recreates the web service and waits
for it to become healthy. The compatibility module update also installs or
updates its declared `usl_accounting` and `usl_expense_batch` dependencies. It
does not restore source data or rebuild the image. Both commands use the local
Pocket ID overlay and keep the canonical target SSO configuration active.
After the target is healthy, the helper sets Odoo's native per-user
`tour_enabled` setting to false for every interactive internal user. This is
dev/QA state only: production tours are not disabled in delivered module code,
and explicit automated browser tours continue to work. Run
`make disable-tours` to reapply the setting without deploying.

The doctor also verifies that `odoo_dev` exists when PostgreSQL is running.
Deployment is an update operation, not a reconstruction shortcut: if the
target is missing it stops before provisioning the surrounding services and
points to `make target-reconstruct-product` or the verified Paperless-reuse
variant.

The canonical Compose project belongs exclusively to this main checkout.
When `make doctor` reports foreign or mixed ownership, ordinary commands stop
before changing anything. From the main checkout, and only after confirming no
migration or test is active, recover with:

```bash
make dev-reclaim CONFIRM=usl-odoo-saas-19-3
make deploy
```

Reclaim lists all affected resources, requires the exact confirmation, removes
project containers only, then restarts the canonical Odoo, Pocket ID and
Paperless runtime. It never removes named databases, filestores, Paperless
archives, Pocket ID state, images or dumps. Linked worktrees must use their own
`COMPOSE_PROJECT` and ports; they cannot reclaim the canonical project.

Use `make rebuild` only after Dockerfile, dependency, system or
core-source changes. Both commands print the development URL:

```text
http://odoo.localhost:8069/web/login?db=odoo_dev
```

## Module and browser refresh contract

Module state is database-specific. Run normal development updates against
`odoo_dev`, the database users and QA review. Update a validation database only
while testing that validation stage. Never update the restored source database.

Two alternatives were considered for this loop:

1. add a helper that always upgrades every accounting database;
2. keep the explicit Odoo command and name the intended target database.

The explicit command is retained because upgrading every disposable database
would blur the separation between product QA, exact-import validation and
native-workflow validation.
It also makes an accidental source-database update easier to detect.

Use this refresh behavior:

| Changed files | Required server action | Required browser action |
| --- | --- | --- |
| Python models, controllers or business logic | Stop the running process, update the module when fields/data are involved, then start Odoo again | Reload the page |
| Backend XML views, menus, actions, security XML or access CSV | Stop the running process, update `rebuild_account_migration`, then start Odoo again | Reload; use a hard refresh if the old view remains open |
| JavaScript or backend QWeb assets already listed in the manifest | Restart Odoo after the module update | Enable `debug=assets` in the URL during development and hard refresh |
| Transactions list navigation | focused model/view test plus `scripts/odoo-dev test-js rebuild_account_migration` | full reconstruction or comprehensive browser suite |
| Manifest dependencies, data files or asset declarations | Stop Odoo, update the module, then start it again | Hard refresh with `debug=assets` enabled |
| Shared native/OCA model extensions in `usl_accounting` | Update `rebuild_account_migration` so the complete product dependency graph is loaded; run `/usl_accounting` plus affected integration tests | Reload only; no reconstruction |
| Files under `docs/users/` | No module update; the development route reads the mounted Markdown on each request | Reload `/usl/user-docs` |
| Other Markdown documentation | No Odoo action | Rebuild or reload the documentation site as applicable |

`--dev=reload,xml,qweb` helps during development, but it is not a substitute
for a module update when an XML record, ACL, menu, action, field or manifest
declaration must be written to the database.

If the UI still looks stale:

1. confirm the URL has the expected `db=` value;
2. confirm only the intended Odoo server owns the browser port;
3. inspect the module-update output for errors;
4. open a new tab with `debug=assets` in the query string and hard refresh;
5. verify the behavior with the intended role before rebuilding accounting
   data.

Do not reset the target, clear asset attachments or rerun source
restore/extraction merely to refresh a view or browser bundle.

Frontend unit tests declared by the Accounting add-on run against the installed
`odoo_dev` module in the dedicated Chromium-enabled `test` image and restore
the normal development service afterward:

```bash
scripts/odoo-dev test-js rebuild_account_migration
```

Installed test tags use the same browser-capable image. This matters for OCA
modules whose Python wrapper launches Hoot tests: the base runtime image does
not include Chromium or `websocket-client`, so using it would skip browser
coverage while appearing successful.

```bash
scripts/odoo-dev test-tag /account_reconcile_oca
```

For a clean disposable module install, provide a database name other than
`odoo_dev`; the helper removes its database, filestore and container and then
restores the development server:

```bash
scripts/odoo-dev test account_reconcile_oca odoo_test_account_reconcile_oca
```

`test-tag` forwards `ODOO_DEV_DB` and its exact `ODOO_DB_FILTER`, so explicitly
selected candidate clones remain testable without falling back to `odoo_dev`.
`test` gives its disposable database an exact temporary filter of its own, so
the browser wrappers cannot be redirected to the development database.

For the Transactions navigation contract and its narrower server-side command,
see [Transactions navigation contract](../accounting/transaction-navigation.md).

## When to run the full pipeline

Run the full pipeline when:

- the source restore code changed;
- extraction logic changed;
- import mappings changed;
- target schema assumptions changed;
- a milestone evidence package is being produced;
- a second clean rehearsal is required;
- prior artifacts are stale or inconsistent.

Host shell:

```bash
make accounting-compat
```

Or staged:

```bash
make oca-addons-sync
make accounting-source-restore
make accounting-extract
make accounting-validation-exact-reset
make accounting-validation-exact-import
make accounting-validation-exact-validate
make accounting-reports
```

## Commit discipline

Use Conventional Commits 1.0.0:

- https://www.conventionalcommits.org/en/v1.0.0/

Commit format:

```text
<type>(<scope>): <short imperative summary>

<body explaining what changed and what was validated>

AI-generated commit
```

Useful types:

- `docs`
- `feat`
- `fix`
- `test`
- `refactor`
- `chore`

Examples:

```text
docs(accounting): define closing report UX target

Capture the reference annual accounts, SIG and tax report expectations for Milestone 13.
Validation: git diff --check.

AI-generated commit
```

```text
fix(accounting): allow accountant reviewer to export FEC

Adjust the FEC export permission path and add role-specific access coverage.
Validation: rebuild_account_migration tests and manual accountant export smoke test.

AI-generated commit
```

Prefer one commit per validated, reviewable chunk. Do not mix docs, importer behavior, report UI, permissions and unrelated cleanup in one commit unless they are inseparable.

## Extending the Accounting Framework

New Controls, Reports and Declarations must extend the governed definition
models rather than introduce a parallel configuration screen. Register a
whitelisted evaluator or engine key in the installed module, seed only missing
shared definitions, and freeze the definition version/snapshot into runtime
results. Shared definitions must not be overwritten on upgrade; use company
overrides and effective dates for operational adaptations.

Run the narrow model/security test for the new definition plus the affected
runtime workflow. A full reconstruction is not required unless ledger import,
schema reconstruction or source extraction behavior changed.

## Validation note for private-use Enterprise parity

The repository still prefers native Community and maintained OCA functionality first. Enterprise source records and user-visible behavior may be studied to understand the required outcome, but copied proprietary implementation should not be committed unless there is a separate explicit legal and maintenance decision.

Private internal use reduces product distribution concerns, but it does not remove upgrade, provenance, review or licensing risk from the repository.

## Before marking a chunk complete

- [ ] Relevant code or docs are updated.
- [ ] Narrow validation ran and passed.
- [ ] The target database used for validation is named in the evidence or commit body.
- [ ] Any skipped full rebuild is justified by the change type.
- [ ] Private artifacts remain ignored.
- [ ] The roadmap or progress report is updated if scope/status changed.
- [ ] A Conventional Commit is created for the validated chunk when the working tree scope is clean enough.
