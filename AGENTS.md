# AI Contributor Guide

This branch starts from upstream Odoo `saas~19.3` at
`1765e55b9335da8f3f61e37a19170b1a8bfa2f05`. Keep it close to upstream Odoo:
avoid changes to core Odoo code unless the task explicitly requires a
distribution-level core patch and the tradeoff is documented.

## Repository Context

- Prefer isolated custom add-ons under `custom-addons/` for project-specific behavior.
- Put shared extensions of existing native/OCA Accounting models in
  `usl_accounting`. Existing installed operational `rebuild.*` models and
  stable XML/data ownership may remain in `rebuild_account_migration` until a
  rehearsed ownership migration exists. Its historical technical name does not
  authorize importers, source bindings, parity objects or migration UI in the
  delivered registry. Do not add new source-trace dependencies there.
  `usl_bootstrap` is
  test-only and must not enter a product dependency graph.
- Inspect existing Odoo code, relevant add-ons, and current documentation before editing.
- Research standard Odoo behavior and maintained OCA functionality before implementing custom behavior.
- Product, operations, accounting, and agent specifications live under:
  - `docs/product/`
  - `docs/operations/`
  - `docs/accounting/`
  - `docs/agents/`
- Follow `docs/agents/french-localization.md` for French product terminology
  and translation ownership.
- Use the existing Docker, Dev Container, and helper workflow documented in `README.md`.

## Decision Rules

- Material implementation decisions must compare at least two credible alternatives, including standard Odoo or OCA options where relevant.
- Treat accounting, security, privacy, access control, data integrity, and migration-sensitive changes as risky. Inspect the surrounding model, security, view, migration, and test behavior before changing them.
- Do not make unrelated refactors, broad rewrites, formatting churn, speculative abstractions, or product changes outside the requested scope.

## Product and Migration Boundary

- `custom-addons/` is the delivered product add-ons path. Do not put source
  extraction, import orchestration, reconstruction runs, parity evidence,
  source bindings or migration-only provenance fields there.
- Put one-shot migration machinery under `migration/`. It may use the Odoo ORM
  through a dedicated migration service and temporary add-on path, but it must
  not be available on the normal Odoo add-ons path or become a production
  dependency.
- A finalized target database must not have migration modules installed,
  migration menus or models loaded, or migration-only fields on operational
  models. Store technical evidence outside the delivered database.
- Preserve user-visible business history such as chatter, attachments and
  lifecycle dates in native operational records. Do not confuse that business
  history with technical reconstruction history.
- Keep only behavior required for ongoing work in product modules. Any
  exception requires an explicit product decision, a documented removal plan
  and an automated final-state boundary check.
- Run `make product-migration-boundary` for changes affecting imports,
  reconstruction, add-on paths or product manifests.

## Validation

- Run the narrowest relevant tests or checks for the files and modules changed.
- For custom add-ons, use the helper workflow documented in `README.md` where possible.
- Report every command run and any failures honestly. Do not claim validation that was not performed.

## Electronic-Invoice Safety

- Keep `USL_EINVOICE_LIVE_ENABLED=0` and
  `USL_EREPORTING_LIVE_ENABLED=0` in development, test, staging,
  reconstruction and copied databases.
- Never register or deregister USL, query a live French directory/provider,
  retrieve or send real invoices, or submit e-reporting outside the approved
  production activation runbook.
- Use the synthetic offline fixture and mocked provider calls for validation.
- Reception activation and e-reporting are separate rollouts. Enabling
  reception must not activate auto-registration, regulatory-document,
  lifecycle or e-reporting jobs.
- Record provider eligibility, subscription and live first-invoice checks as
  production prerequisites; never infer them from passing software tests.

## Accounting Milestone Workflow

- Follow `docs/operations/accounting-development-workflow.md` when working on Milestone 13.
- Do not rerun source restore, extraction, target reset or full import loops unless the changed code actually requires that stage.
- For UI, report formatting, menu, permission and documentation changes, prefer
  an Odoo module update on the disposable `odoo_dev` product database plus
  targeted validation.
- Never open the read-only `odoo_online_source_saas_19_3` source database with
  target Odoo code.
- Use `odoo_dev` as the single developer/QA product database. Create exact or
  native validation databases only as explicitly named, automatically cleaned
  on-demand evidence; do not maintain them as parallel environments.
- Preserve current source snapshots and private artifacts, but do not commit private production extracts.

## Commit Discipline

- Make regular scoped commits after validated chunks of work.
- Use Conventional Commits 1.0.0 syntax: `<type>(<scope>): <description>`.
- Include a short body describing validation for non-trivial accounting, migration, reporting or security work.
- Add `AI-generated commit` in the commit message body for agent-authored commits.
