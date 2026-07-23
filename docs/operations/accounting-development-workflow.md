# Accounting development workflow

Last updated: 2026-07-23

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

Use four separate states:

- `odoo_online_source_saas_19_2`: restored source backup, read-only for extraction.
- `odoo_rebuild_accounting_test`: disposable imported target used for Milestone 13 validation.
- `odoo_rebuild_accounting_track_b`: disposable native-engine target for current-period document, expense and reconciliation proof.
- `odoo19`: general development or synthetic database; do not use it as production-derived parity evidence unless explicitly rebuilt.

## Fast iteration matrix

| Change type | Usually rerun | Avoid unless needed |
| --- | --- | --- |
| Markdown docs | `git diff --check` | source restore, target reset |
| Odoo XML menus/views | `odoo --update=rebuild_account_migration --stop-after-init` on `odoo_rebuild_accounting_test` | source restore, extract |
| Odoo Python report formatting only | module update, targeted Odoo tests, one report export smoke test | source restore |
| Security/ACL changes | module update, role-specific access tests | source restore |
| Future currency-rate provider changes | module update, targeted provider tests, `accounting-currency-rate-provider`, then manager/reviewer browser journeys | source restore, extract, Track B replay |
| Importer mapping changes | `accounting-target-reset`, `accounting-target-import`, `accounting-target-validate` | source restore if snapshot unchanged |
| Source extraction mapping changes | `accounting-extract`, target reset/import/validate | source restore if source DB still running and unchanged |
| Track B expense/document mapping changes | `accounting-track-b-reset`, `accounting-track-b-expenses`, `accounting-track-b-documents` | source restore, exact-target reset/import |
| Track B native asset changes | Track B reset, `accounting-track-b-assets`; repeat asset replay for idempotence and run the manager/reviewer browser journey | source restore, extraction, exact-target reset/import |
| Track B native deferral changes | Track B expenses/documents, then `accounting-track-b-deferrals`; repeat deferral replay for idempotence and run the manager/reviewer browser journey | source restore, extraction, exact-target reset/import |
| Track B expense settlement changes | Track B reset, expenses, documents, `accounting-track-b-expense-settlement`; repeat settlement for idempotence | source restore, exact-target reset/import |
| Track B document settlement changes | Track B reset, expenses, documents, expense settlement, `accounting-track-b-document-settlement`; repeat document settlement for idempotence | source restore, exact-target reset/import |
| Track B General Reconciliation changes | Track B reset, expenses, documents, expense settlement, document settlement, `accounting-track-b-general-reconciliation`; repeat General Reconciliation for idempotence | source restore, exact-target reset/import |
| Track B direct bank categorization changes | Track B reset through General Reconciliation, then `accounting-track-b-bank-categorization`; repeat bank categorization for idempotence | source restore, exact-target reset/import |
| Track B external-endpoint bank changes | Track B reset through direct bank categorization, then `accounting-track-b-bank-external`; repeat external bank replay for idempotence | source restore, extraction, exact-target reset/import |
| Track B analytic changes | Run every posting stage through assets, deferrals and external bank replay, then `accounting-track-b-analytics`; repeat analytics for idempotence and run the manager/reviewer/native-report browser journeys | source restore, extraction, exact-target reset/import |
| Source dump or restore script changes | full source restore and downstream stages | none |
| Closing/report parity milestone proof | full `make accounting-compat` rehearsal | partial validation |

## Normal UI/report development loop

Host shell:

```bash
cd /Users/valentin/Code/odoo
docker compose ps
make oca-addons-sync
```

Keep these services running:

```text
db
accounting-source-db
devcontainer
```

If only Odoo code changed, update the add-on inside the Dev Container:

```bash
odoo --config=/etc/odoo/odoo.conf \
  --addons-path=/workspace/odoo/addons,/workspace/odoo/odoo/addons,/workspace/odoo/custom-addons,/workspace/odoo/oca-addons \
  --database=odoo_rebuild_accounting_test \
  --update=rebuild_account_migration \
  --stop-after-init
```

Stop any manually started Odoo process for this database before the update.
The update runs in a separate process: it refreshes database records such as
views, menus, ACLs and module data, but it cannot replace Python already loaded
by another running server.

Then run the server:

```bash
odoo --config=/etc/odoo/odoo.conf \
  --addons-path=/workspace/odoo/addons,/workspace/odoo/odoo/addons,/workspace/odoo/custom-addons,/workspace/odoo/oca-addons \
  --database=odoo_rebuild_accounting_test \
  --dev=reload,xml,qweb
```

Open:

```text
http://localhost:8069/web/login?db=odoo_rebuild_accounting_test
```

## Module and browser refresh contract

Module state is database-specific. Run the update against the database that
will be reviewed. Use `odoo_rebuild_accounting_test` for the exact imported
target or substitute `odoo_rebuild_accounting_replacement` when reviewing the
hybrid candidate. Never update the restored source database.

Two alternatives were considered for this loop:

1. add a helper that always upgrades every accounting database;
2. keep the explicit Odoo command and name the intended target database.

The explicit command is retained because upgrading every disposable database
would blur the separation between exact import, Track B and hybrid evidence.
It also makes an accidental source-database update easier to detect.

Use this refresh behavior:

| Changed files | Required server action | Required browser action |
| --- | --- | --- |
| Python models, controllers or business logic | Stop the running process, update the module when fields/data are involved, then start Odoo again | Reload the page |
| Backend XML views, menus, actions, security XML or access CSV | Stop the running process, update `rebuild_account_migration`, then start Odoo again | Reload; use a hard refresh if the old view remains open |
| JavaScript or backend QWeb assets already listed in the manifest | Restart Odoo after the module update | Enable `debug=assets` in the URL during development and hard refresh |
| Manifest dependencies, data files or asset declarations | Stop Odoo, update the module, then start it again | Hard refresh with `debug=assets` enabled |
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
make accounting-target-reset
make accounting-target-import
make accounting-target-validate
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
