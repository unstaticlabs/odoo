# AI Contributor Guide

This branch starts from upstream Odoo `saas~19.3` at
`363b4bb23a56139ca237c833a8348a662b8387f6`. Keep it close to upstream Odoo:
avoid changes to core Odoo code unless the task explicitly requires a
distribution-level core patch and the tradeoff is documented.

## Development Workflow

- `19-usl` is the canonical development branch. Ordinary features, fixes and
  upstream syncs must originate in dedicated branches and worktrees and reach
  it through reviewed pull requests. Run `scripts/agent/context` before work.
- Each task owns its implementation, focused verification, scoped commits and
  review evidence in its current worktree. The repository has no Lead/Feature
  roles, task-routing protocol or machine-readable handoff contract.
- Branches normally use `codex/<type>-<work-slug>`, where `<type>` is
  one of `feat`, `fix`, `chore`, `docs`, `perf`, `refactor`, `test`, `ci`, or
  `build`. Preserve explicit user-provided branch names and established archive
  conventions.
- The worktree-local Git author for agent-authored commits is exactly
  `Coding Agent <318050048+elio-usl@users.noreply.github.com>`, authenticated
  to GitHub as `@elio-usl`; its driving-human trailer is exactly
  `ValentinViennot <18735898+ValentinViennot@users.noreply.github.com>`. Configure
  it locally through `scripts/agent/github`; never change global Git identity or
  use global, SSH or Keychain credentials.
- Agents must never use a browser for Git or GitHub work, including repository
  inspection, diffs, branches, commits, PRs, checks, comments, reviews,
  approvals, merges, releases, or authentication. Use terminal Git,
  `scripts/agent/github`, and authenticated GitHub CLI/API or connector
  operations. When device authentication is required, show the URL and code so
  a human can use the browser; the agent must not open it. If a required
  operation has no non-browser path, stop and report the limitation instead of
  using a browser workaround. This does not restrict browser-based Odoo product
  or QA validation.
- Ordinary integration uses GitHub's merge queue. A clean pushed topic head
  is queue-eligible when Git can construct a conflict-free candidate with the
  latest fetched `origin/19-usl`; the topic branch does not need to contain
  that target tip. Manual catch-up is reserved for real conflicts,
  dependency/stack changes or generated-state reconciliation. GitHub does not
  run compute-heavy candidate qualification or require an approval count.
  Provide honest validation and known limitations in the PR. OCI image
  publication runs only after the merge reaches `refs/heads/19-usl`.
- Preserve shared Docker infrastructure, canonical dumps, persistent databases,
  intentional caches and resources owned by other worktrees. Cleanup requires
  proven ownership; uncertainty means preserve and document.
- Every persistent-data or module change must state its forward upgrade,
  verification and credible recovery path. After migration cutover, Community
  production is canonical; the historical Online dump is not a rollback.
- Production deployment belongs to CI. Agents must not manually deploy or SSH
  changes into production. The governed one-off cutover
  remains a documented transitional exception, not a development precedent.
- Treat secrets, production data, destructive actions and genuinely
  irreversible operations as high risk. Prefer reversible, audited operations
  and obtain explicit authority when scope or recovery is uncertain.
- `agent/policy.json` exposes lifecycle phase and enforcement modes to tools.
  Keep GitHub's static deletion, non-fast-forward, pull-request and merge-queue
  rules, but do not add required status checks or a required approval count.
  Post-merge OCI publication is the only GitHub compute workflow.
  Its `github.merge_queue` block is the reviewed desired queue configuration.

Repository-owned Agent Skills have one canonical source under `agent-skills/`.
Codex and Claude discover the same content through `.agents/skills/` and
`.claude/skills/`. Use the migration, accounting, access-control or UI
specialist guidance only when its technical risk domain applies.

Load `writing-clearly-and-concisely` whenever writing or editing text that a
user, operator, reviewer or maintainer will read. This includes product copy,
labels, help and error messages, documentation, comments, reports, commit
messages and pull-request descriptions.

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
- Use `docs/product/fork-overview.md` as the canonical feature-to-module map.
  Update it when a delivered capability, product module, core patch, migration
  stage, user entry point or maturity classification materially changes.
- Follow `docs/agents/french-localization.md` for French product terminology
  and translation ownership.
- Use the existing Docker, Dev Container, and helper workflow documented in `README.md`.

## Decision Rules

- Material implementation decisions must compare at least two credible alternatives, including standard Odoo or OCA options where relevant.
- Treat accounting, security, privacy, access control, data integrity, and migration-sensitive changes as risky. Inspect the surrounding model, security, view, migration, and test behavior before changing them.
- Do not make unrelated refactors, broad rewrites, formatting churn, speculative abstractions, or product changes outside the requested scope.

## UI Design Workflow

- Use the project-local `impeccable` skill for product UI and UX work,
  including new views, redesigns, layout or typography changes, responsive
  behavior, accessibility reviews and frontend polish.
- Load `PRODUCT.md` through the skill before UI work. Existing specifications
  under `docs/product/`, `docs/accounting/`, `docs/operations/` and
  `docs/users/` remain the detailed authorities; do not create a competing
  product or design narrative.
- Treat Odoo application surfaces as **Operate** interfaces. Preserve native
  Odoo interaction patterns, semantics, security and familiar components while
  applying strong hierarchy, clarity, accessibility and responsive behavior.
- The shared workflow is code-first. For a new or materially redesigned
  surface, shape the workflow and direction before implementation. For scoped
  refinements, preserve the incumbent identity and use the narrowest relevant
  Impeccable command.
- Keep the Impeccable detector hook enabled. Resolve real findings rather than
  suppressing them, and document any evidence-backed exception through the
  skill's governed ignore workflow.
- Impeccable live variant mode is not configured for this repository because
  Odoo views and asset bundles are generated through the Odoo runtime rather
  than a supported HMR or static-source pipeline. Do not introduce a parallel
  frontend runtime merely to enable live mode.

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

- Load `usl-commit-work` whenever creating or repairing commits. Create
  agent-authored commits through `scripts/agent/commit`; it reads the
  worktree-local identities and adds the required attribution exactly once.
- Make regular scoped commits after validated chunks of work.
- Use Conventional Commits 1.0.0 syntax: `<type>(<scope>): <description>`.
- Include a short body describing validation for non-trivial accounting, migration, reporting or security work.
- Add `AI-generated commit` in the commit message body for agent-authored commits.
- Agent-authored commits must use the worktree-local identity configured by
  `scripts/agent/github` and include exactly
  `Co-authored-by: ValentinViennot <18735898+ValentinViennot@users.noreply.github.com>`.
