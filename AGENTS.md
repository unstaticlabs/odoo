# AI Contributor Guide

This repository starts from upstream Odoo 19.0. Keep it close to upstream Odoo: avoid changes to core Odoo code unless the task explicitly requires a fork-level patch and the tradeoff is documented.

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
