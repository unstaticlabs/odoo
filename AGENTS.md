# AI Contributor Guide

This distribution follows upstream Odoo `saas~19.3`. Prefer isolated custom
add-ons and avoid core changes unless a distribution-level patch is required
and its upgrade cost is documented.

## Repository boundaries

- `custom-addons/` contains delivered product behavior.
- `migration/` contains Online-to-Community reconstruction code. Migration
  modules and source bindings must not enter the normal Odoo add-ons path or a
  finalized database.
- Keep the frozen Odoo Online source read-only. Never start target Odoo against
  the source database and never use the source export as a production rollback.
- Preserve business history, accounting meaning, attachments, company
  ownership, access controls, and audit evidence through migrations and module
  upgrades.
- Keep `USL_EINVOICE_LIVE_ENABLED=0` and
  `USL_EREPORTING_LIVE_ENABLED=0` outside an approved production activation.
  Use offline fixtures for external-provider tests.

## Development

- Inspect existing Odoo, OCA, custom add-ons, tests, and relevant product or
  operations documentation before changing behavior.
- Prefer native Odoo or maintained OCA behavior. Compare credible alternatives
  before adding a custom abstraction.
- Treat Accounting, access control, multi-company behavior, persistent data,
  destructive actions, secrets, and external side effects as high risk.
- Use focused tests. Exercise module upgrades or representative restore paths
  when stored data, manifests, or migration code changes.
- Run `make product-migration-boundary` when product or migration add-on paths,
  manifests, source bindings, or finalization behavior change.
- Preserve foreign Docker projects and persistent resources. Delete only
  resources whose ownership and scope are proven.
- Production deployment belongs to CI. Do not deploy production manually.

## Product references

- Feature and module map: `docs/product/fork-overview.md`
- French terminology: `docs/product/french-localization.md`
- Product and migration boundary: `docs/operations/product-migration-boundary.md`
- Runtime and recovery procedures: `docs/operations/`

Use the repository `writing-clearly-and-concisely` skill for user-facing copy,
documentation, reports, comments, and commit messages.

## Commits

- Keep changes scoped and preserve unrelated worktree changes.
- Agent-authored commits use `scripts/commit`; do not construct messages with
  escaped newlines or repeat attribution text manually.
- Commit subjects follow Conventional Commits:
  `<type>(<scope>): <description>`.
- The helper enforces the worktree-local author
  `Coding Agent <318050048+elio-usl@users.noreply.github.com>`, adds
  `AI-generated commit`, and adds exactly:
  `Co-authored-by: ValentinViennot <18735898+ValentinViennot@users.noreply.github.com>`.
- Use terminal Git and GitHub CLI. Do not use a browser for repository actions.
