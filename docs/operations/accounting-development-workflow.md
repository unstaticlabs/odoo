# Accounting development workflow

Last updated: 2026-07-28

Audience: implementation agents and developers working on Milestone 13.

This workflow exists to keep accounting development fast, safe and reviewable. Do not rerun expensive source restores or full target rebuilds unless the change actually needs them.

## Principles

- Keep the source database restored and running while iterating.
- Keep OCA add-ons synced with `make oca-addons-sync` before target reset, reporting or reconciliation work.
- Reuse the extracted snapshot when changing only Odoo views, menus, reports, permissions or documentation.
- Rebuild the target only when import behavior, schema assumptions, model creation, or data transformations change.
- Run narrow Odoo module updates for UI/report code changes.
- Commit validated scoped chunks regularly.
- Never commit private production extracts or generated private artifacts.

## Database roles during development

Normal development, module updates and browser QA use one disposable database:
`odoo_dev`.

The reconstruction harness creates `odoo_saas_19_2_validation_exact` and
`odoo_saas_19_2_validation_native` only when their explicit pipeline stages
run. They are disposable evidence databases, not alternate development
environments. The restored `odoo_online_source_saas_19_2` database is isolated
in the optional `accounting-source-db` service and is used read-only for
extraction.

Do not enter durable business data in any local database. Recreate `odoo_dev`
from the harness when import or reconstruction behavior changes; for ordinary
code and UI work, update it in place.

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

## Normal UI/report development loop

From the host shell:

```bash
cd /Users/valentin/Code/odoo
make dev
make deploy
```

`make dev` opens the existing environment. `make deploy` stops Odoo, updates
`rebuild_account_migration` in `odoo_dev`, recreates the web service and waits
for it to become healthy. The compatibility module update also installs or
updates its declared `usl_accounting` and `usl_expense_batch` dependencies. It
does not restore source data or rebuild the image.

Use `make rebuild` only after Dockerfile, dependency, system or
core-source changes. Both commands print the development URL:

```text
http://localhost:8069/web/login?db=odoo_dev
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
