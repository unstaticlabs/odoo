# AI Contributor Guide

This branch starts from upstream Odoo `saas~19.2` at
`8a44ecc8da96e341ac472fec27352d138ed2edd7`. Keep it close to upstream Odoo:
avoid changes to core Odoo code unless the task explicitly requires a
fork-level patch and the tradeoff is documented.

## Repository Context

- Prefer isolated custom add-ons under `custom-addons/` for project-specific behavior.
- Inspect existing Odoo code, relevant add-ons, and current documentation before editing.
- Research standard Odoo behavior and maintained OCA functionality before implementing custom behavior.
- Product, operations, accounting, and agent specifications live under:
  - `docs/product/`
  - `docs/operations/`
  - `docs/accounting/`
  - `docs/agents/`
- Use the existing Docker, Dev Container, and helper workflow documented in `README.md`.

## Decision Rules

- Material implementation decisions must compare at least two credible alternatives, including standard Odoo or OCA options where relevant.
- Treat accounting, security, privacy, access control, data integrity, and migration-sensitive changes as risky. Inspect the surrounding model, security, view, migration, and test behavior before changing them.
- Do not make unrelated refactors, broad rewrites, formatting churn, speculative abstractions, or product changes outside the requested scope.

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
  an Odoo module update on `odoo_saas_19_2_candidate_01` plus targeted
  validation.
- Never run this branch against the preserved Odoo 19 `odoo_dev` database or
  the read-only `odoo_online_source_saas_19_2` source database.
- Use `odoo_saas_19_2_candidate_01` as the initial developer/QA candidate.
  Keep `odoo_saas_19_2_validation_exact` and
  `odoo_saas_19_2_validation_native` isolated as disposable pipeline proofs.
  Do not replace the canonical `odoo_dev` until two clean reconstructions and
  the accounting parity gates pass.
- Preserve current source snapshots and private artifacts, but do not commit private production extracts.

## Commit Discipline

- Make regular scoped commits after validated chunks of work.
- Use Conventional Commits 1.0.0 syntax: `<type>(<scope>): <description>`.
- Include a short body describing validation for non-trivial accounting, migration, reporting or security work.
- Add `AI-generated commit` in the commit message body for agent-authored commits.
