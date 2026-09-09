# AI Contributor Guide

This distribution follows upstream Odoo `saas~19.3`. Prefer isolated custom
add-ons and avoid core changes unless a distribution-level patch is required and
its upgrade cost is documented.

## Invariants that fail late

Each of these has cost a failed release or a silent wrong result. The constraint
lives somewhere the file you are editing never mentions, so read the named file
before you finish the change — not after CI disagrees.

| Editing | Read first | Because |
|---|---|---|
| an app icon, or anything writing `ir_attachment`, `mail_message`, `project_project`, `res_groups_users_rel` | `operations/upgrade_preservation.py` | a release refuses any change to a row that already existed; the rollback restores the cause, so every retry fails identically |
| `custom-addons/*/security/*.xml` | `docs/operations/distribution-access-control-runbook.md` | the file is `noupdate=1`, so an `implied_ids` edit reaches a fresh install and no existing database; it needs a `migrations/` post script |
| any `custom-addons/` source | `docs/operations/action-risk-inventory.md` | the sealed action surface is invalidated; the refresh needs a *runtime* discovery, since static-only discovery drops routes and server actions |
| a new addon directory | `operations/release_identity.py` | no pull-request gate sees a new module; it merges green and fails on the release branch unless it is named in all four product registries |
| a release or promotion gate | `operations/source_policy.py` | production accepts `19-usl-staging` **or** `urgent/**`; an urgent fix reaches staging *after* production, so a gate demanding staging evidence welds the emergency path shut |

Before adding a gate, enumerate every input class it will judge and prove one
real historical example of each. A gate that refuses something legitimate
usually has no failing test — nothing exercises the path until it is needed.

`make preservation-check` runs the release's preservation gate against the
development database, which is the cheap version of the loop above.

Claude sessions also get these as warnings at the moment of the edit, from
`.claude/hooks/edit_tripwires.py`. The warnings are a convenience; this table is
the contract.

## Repository boundaries

- `custom-addons/` contains delivered product behavior.
- `migration/` contains historical Online-to-Community reconstruction and
  cohort-promotion code. It is not the ordinary product workflow. Migration
  modules and source bindings must not enter the normal Odoo add-ons path or a
  finalized database.
- Keep the frozen Odoo Online source read-only. Never start target Odoo against
  the source database and never use the source export as a production rollback.
- The current production dataset is authoritative. Never reset it from the
  Online export. Preserve business history, Accounting meaning, attachments,
  company ownership, access controls and audit evidence through upgrades and
  repairs.
- Keep `USL_EINVOICE_LIVE_ENABLED=0` and `USL_EREPORTING_LIVE_ENABLED=0` outside
  an approved production activation. Use offline fixtures for external-provider
  tests.
- This repository is public and business data never enters it: supplier and
  customer identities, order and invoice references, unit prices, margins,
  freight, personal data and access credentials. Code carries the contract and
  the exact SHA-256 of the evidence; the values live with the frozen source
  package under `usl-online-dump/supplemental/`, loaded through
  `usl_b2c_restore.private_evidence`. When a reviewed fact is needed to make a
  decision, pin the file and read it at run time. Never inline it — not in a
  test fixture, a docstring, a comment, a commit message or a runbook example.

## Development

- Inspect existing Odoo, OCA, custom add-ons, tests and relevant product or
  operations documentation before changing behavior.
- Prefer native Odoo or maintained OCA behavior. Compare credible alternatives
  before adding a custom abstraction.
- Treat Accounting, access control, multi-company behavior, persistent data,
  destructive actions, secrets and external side effects as high risk.
- Use focused tests. Exercise module upgrades or representative restore paths
  when stored data, manifests, release or recovery code changes.
- Test the observable outcome, not the shape of the implementation. A test
  asserting that two functions agree passes when both are wrong together.
- Run `make product-migration-boundary` when product or migration add-on paths,
  manifests, source bindings or finalization behavior change.
- Preserve foreign Docker projects and persistent resources. Delete only
  resources whose ownership and scope are proven.

## Delivery

- Protected CI/GitOps is the default delivery path, not an exclusive one. When
  the user explicitly authorizes it, an operator may deploy staging or
  production manually and may bypass CI. Before a production mutation, verify a
  current qualified, restorable backup and confirm that the current GitOps
  checkout and desired-state ledgers already describe the intended release.
- A merge into a release branch supersedes any release still deploying, so
  batch what is ready rather than merging one pull request at a time.
- Release branches intentionally require zero approving reviews so qualified
  merges and promotions run unattended. Do not propose a human-review gate
  merely as a generic production safeguard.
- Builds generate SBOM metadata, but this distribution does not enforce or gate
  releases on SBOM policy.

## Commits

- Keep changes scoped and preserve unrelated worktree changes.
- Agent-authored commits use `scripts/commit`; do not construct messages with
  escaped newlines or repeat attribution text manually.
- Commit subjects follow Conventional Commits: `<type>(<scope>): <description>`.
- The history names the person who asked for the work and the agent that made
  it. The commit author is the driving human of the session; the helper adds
  `AI-generated commit` and `Co-authored-by:` with the agent identity. Both come
  from worktree-local Git settings that every session sets once: `user.name`,
  `user.email` and `usl.drivingHuman` for the human
  (`Name <id+login@users.noreply.github.com>`), and `usl.agent` for the agent
  (for example `Claude Fable 5.1 <noreply@anthropic.com>` or
  `Coding Agent <318050048+elio-usl@users.noreply.github.com>` for Codex).
  Never hard-code a person and never commit as the agent account.
- Open pull requests directly, with whichever GitHub account the session holds.
  An agent does not wait for a person to open a pull request on its behalf; the
  merge queue and the qualification check are the gate, not the authorship. Name
  the driving human and the agent in the pull request body.
  `.claude/settings.json` carries the matching permission so a session does not
  have to stop and ask; put personal overrides in `.claude/settings.local.json`,
  which is ignored.
- Never push directly to a release branch. The pre-push guard refuses it because
  a direct push skips the qualification check and git pushes as an
  administrator, so nothing on GitHub would stop it.
- Use terminal Git and GitHub CLI. Do not use a browser for repository actions.

## Skills

A skill is a procedure somebody already got wrong once and wrote down. Read the
one that matches before you start, not after a review sends you back. They are
plain Markdown, so every agent can use them; Claude Code additionally loads the
`.claude/skills/` ones on its own and offers them as `/usl-merge-train` and
friends. Nothing else depends on that, and the paths below always work.

| When you are | Read |
|---|---|
| landing approved pull requests, promoting staging to production, or recovering a staging head that went red | `.claude/skills/usl-merge-train/SKILL.md` |
| bringing the fork current with upstream `saas~19.3`, or absorbing Dependabot | `.agents/skills/usl-upstream-sync/SKILL.md`, which governs, then `.claude/skills/usl-upstream-catchup/SKILL.md` |
| ranking the Product Feedback board before allocating work from it | `.claude/skills/usl-feedback-triage/SKILL.md` |
| delivering one piece of work as a qualified pull request | `.claude/skills/usl-cluster-delivery/SKILL.md` |
| reviewing an agent's work and taking its pull request out of draft | `.claude/skills/usl-lead-review/SKILL.md` |
| changing ACLs, record rules, multi-company boundaries, `sudo` use or destructive actions | `.agents/skills/odoo-access-control-safety/SKILL.md` |
| changing accounting data, currencies, reconciliation, taxes, reports or lock dates | `.agents/skills/odoo-accounting-integrity/SKILL.md` |
| building or qualifying forms, lists, dialogs, dashboards, OWL components or navigation | `.agents/skills/odoo-ui-product-quality/SKILL.md` |
| designing, critiquing or polishing any interface | `.agents/skills/impeccable/SKILL.md` |
| writing user-facing copy, documentation, reports, comments or commit messages | `.agents/skills/writing-clearly-and-concisely/SKILL.md` |
| testing a parser, a normalization, a roundtrip, a validator or a state invariant | `.agents/skills/designing-property-based-tests/SKILL.md` |
| hunting other instances of a defect whose root cause you already know | `.agents/skills/finding-bug-variants/SKILL.md` |
| asked for trust boundaries, abuse paths or AppSec design risk | `.agents/skills/threat-modeling-repositories/SKILL.md` |

The five delivery skills score and choose work against `VALUES.md`, `ROADMAP.md`
and `DECISIONS/0001-weekly-delivery-protocol.md`. Read those before deciding what
is worth building; read the skill before building it.

## References

- Feature and module map: `docs/product/fork-overview.md`
- French terminology: `docs/product/french-localization.md`
- Product and reconstruction boundary: `docs/operations/product-migration-boundary.md`
- Delivery and promotion: `docs/operations/continuous-delivery.md`
- Runtime and recovery procedures: `docs/operations/`
