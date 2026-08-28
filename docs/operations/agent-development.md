# Agent-native development

This is the operating guide for human and coding-agent feature delivery. The
stable constitution is in `AGENTS.md`; machine-readable phase and enforcement
settings are in `agent/policy.json`; procedures are canonical Skills under
`agent-skills/`.

## Operating model

Normal work follows one direction:

```text
Odoo Project task -> Lead Agent dispatch -> Coding task + isolated worktree
                  -> PR + QA + handoff -> Lead review -> 19-usl -> CI -> production
```

The Odoo Project task owns product intent, priority, acceptance, decisions and
work state. The GitHub PR owns the engineering diff, review, CI and technical
handoff. Record links in both systems manually until orchestration is added.

The repository remains in `migration-transition`. The one-off Online to
Community cutover is governed by its existing runbooks. GitHub keeps static
branch and merge-queue rules but runs no PR or merge-group compute checks and
requires no approval count. Production deployment is never an ordinary Coding
or Lead Agent action.

The persistent Codex task titled exactly `19-usl - Lead` owns the Lead Agent
role and the clean authoritative `19-usl` checkout. The Lead reviews, runs QA,
makes integration decisions, and merges only reviewed, merge-ready PRs. It does not
create purpose branches or implement or author ordinary product, migration,
feature, fix, chore, documentation, or conflict-repair commits. Its local Git
name is `Lead Agent`, it normally produces no commits, and GitHub remains
`@elio-usl`.

All implementation belongs to Coding Agents in isolated Codex worktrees. The
Lead creates a visible Codex Desktop task for each implementation or repair,
then returns control immediately so work can proceed in parallel. It must not
poll, synchronously wait, or leave `19-usl - Lead` hanging. Coding sends that
persistent task an asynchronous message when a Lead decision is genuinely
needed. Final handoff follows the routing gate described below; notification is
not approval.

## Start Coding Agent work

A human creates or selects a dedicated worktree from the current remote tip:

```bash
git fetch origin 19-usl
git worktree add ../odoo-my-feature -b codex/feat-my-feature origin/19-usl
cd ../odoo-my-feature
scripts/agent/context
scripts/agent/verify feature-start
```

Ask the agent: `Implement <task> as Coding Agent.` Codex discovers
repository Skills from `.agents/skills/`; Claude Code discovers them from
`.claude/skills/`. Both are relative symlinks to `agent-skills/`, so there is no
second editable Skill body. Explicitly name `usl-feature-developer` when a
client does not automatically select it.

The exposure layout follows the [Codex repository Skill
documentation](https://learn.chatgpt.com/docs/build-skills) and [Claude Code
Skill documentation](https://code.claude.com/docs/en/skills). Structural
verification is deterministic; a live discovery probe additionally requires
the corresponding client to be installed, sandbox-permitted and logged in.

The Coding Agent inspects relevant product specifications, considers
native Odoo and maintained OCA options, implements the narrow scope, tests it,
and uses specialist Skills for UI, migration, accounting and access-control
risk. Fetch `19-usl` before readiness, but do not routinely rewrite a clean
pushed feature merely because the target advanced. The branch is queue-eligible
when Git can construct a conflict-free candidate. Manual catch-up is required
only for a real conflict, dependency or stack change, or generated-state
reconciliation; then inspect both
deltas, preserve uncommitted work, choose merge or rebase deliberately, and
rerun affected checks. Never edit another worktree or discard published
history for cosmetic reasons.

Coding task titles are work-first and type-last; use, for example,
`Bank statements - Feature`, `FEC generation - Fix`, or
`Agent identities - Chore`. Renaming is best-effort and never blocks delivery.
Branches named by the workflow use `codex/<type>-<work-slug>`, where `<type>` is
`feat`, `fix`, `chore`, `docs`, `perf`, `refactor`, `test`, `ci`, or `build`.
Preserve explicit user-provided names and archive conventions.

Every started Coding task states one of these exact Lead handoff modes in its
prompt:

- `automatic`: send the final contract to Lead as soon as the PR and handoff
  are ready.
- `human-approved after Feature/Worktree-QA review`: present the ready work to
  the designated human and wait for explicit approval before sending the final
  contract to Lead.

If the task does not specify a mode, use the human-approved mode. This gate
controls only when the Lead review task receives the work. Human handoff
approval is not GitHub PR approval and does not authorize Lead to merge.

## Isolated GitHub identity

Agent publication fails closed until `@elio-usl` has MFA and repository write
access and is authenticated in the worktree's ignored profile. The reviewed
`agent/policy.json` `github.agent_login` field remains the trust anchor.

When provisioning a Coding worktree, copy only the authoritative Lead/main
checkout's ignored `.agent/gh/` directory into the new worktree's ignored
`.agent/gh/`, preserving private permissions. Do not copy
`.agent/identity.json`: each worktree must configure its role-specific Git
identity. Do not fall back to a global GitHub profile, SSH credential, Keychain
credential, or global Git author. If the inherited profile is unavailable,
expired, or authenticates the wrong account, use the helper's device login and
surface the code for human action.

Configure every Coding worktree with the exact identities:

```bash
scripts/agent/github configure \
  --login elio-usl \
  --author-name "Coding Agent" \
  --author-email "318050048+elio-usl@users.noreply.github.com" \
  --coauthor-name "ValentinViennot" \
  --coauthor-email "18735898+ValentinViennot@users.noreply.github.com"
scripts/agent/github status
```

`configure` enables Git worktree-specific author config, writes non-secret
identity metadata to `.agent/identity.json`, and stores `gh` credentials only
under `.agent/gh/`. It refuses identical agent and human identities. `push`
uses an explicit HTTPS repository URL and only that isolated `gh` credential
helper; it never uses the configured SSH origin or a human keychain profile.

## No browser for Git or GitHub

Coding and Lead Agents never use a browser or browser-control capability for
Git or GitHub work. This includes repository browsing, file and diff review,
branch and commit inspection, PR creation or editing, checks, comments,
reviews, approval-state inspection, merges, releases, and authentication.

Use terminal Git for local and remote repository state. Use
`scripts/agent/github` for Coding identity verification, push, and PR
publication. For GitHub inspection or operations not exposed by that helper,
use an authenticated GitHub CLI, API, or non-browser connector. If a required
operation has no supported non-browser path, stop and record the limitation;
do not fall back to a browser.

Device authentication is a human browser action: the agent prints the URL and
one-time code, then waits for the human to complete it. Independent humans may
also use the GitHub web UI to review or approve. This prohibition applies to
agents doing Git/GitHub work, not to browser-based Odoo product journeys or
Worktree-QA required by the UI quality workflow.

Dependabot is a separate authenticated GitHub bot, not a Coding Agent. Its PRs
skip only Coding-Agent branch naming, commit attribution and structured-handoff
metadata. The actor check is exactly `dependabot[bot]`; a branch named
`dependabot/*` receives no exemption. Repository contract tests and the full
Distribution image qualification still run, and stateful-service candidates
retain their documented clean-install, migration and recovery review gates.

Every Coding Agent commit uses Conventional Commits, contains
`AI-generated commit`, and includes exactly
`Co-authored-by: ValentinViennot <18735898+ValentinViennot@users.noreply.github.com>`.
Then:

```bash
scripts/agent/github push
```

## Feature QA

The stable interface wraps the qualified existing `qa-environment` and
`qa-clean` implementation:

```bash
scripts/agent/qa-up --profile full --dry-run
scripts/agent/qa-up --profile full
scripts/agent/qa-status
scripts/agent/qa-status --json
scripts/agent/qa-status --project
```

Profiles are `full`, `no-documents`, `documents-smoke`, and `clean-install`.
The Compose project is `usl-odoo-qa-<worktree-hash>` with worktree-specific
ports, database, filestore and volumes. The wrappers retain existing shared,
immutable seed reuse and live-safety flags. Status conforms to
`agent/contracts/v1/qa-environment.schema.json`, reports branch/SHA and never
prints credentials.

V1 exposes local HTTP only. Remote phone-accessible HTTPS requires a future
DNS/proxy lease service and a separate Pocket ID QA client/trust configuration.
Production OIDC secrets must never enter QA. Until that exists, record remote
QA as unsupported rather than improvising a public tunnel.

Leave a useful QA environment running for review. After merge and successful
CI, the Lead Agent may remove only the exact owned project:

```bash
project="$(scripts/agent/qa-status --project)"
scripts/agent/qa-down --dry-run --confirm "$project"
scripts/agent/qa-down --confirm "$project"
```

Wrong confirmation, detached/protected feature startup, and foreign ownership
are refused by the wrappers and underlying Compose guards.

## Handoff and PR

Create an ignored v1 JSON artifact after implementation:

```bash
scripts/agent/handoff init \
  --identifier my-feature \
  --goal "Explain in plain language what this delivers, for whom, and why it matters" \
  --acceptance "State each observable user or operator outcome"
```

Edit the generated `artifacts/agent/handoffs/my-feature.json`. Record actual
commands and results, manual journeys and evidence, QA identity/access,
architecture alternatives, changed modules and paths, schema/config/dependency
effects, upgrade modules, forward/recovery procedures, data-loss and
irreversibility risk, resources preserved, overlaps, known issues, unverified
assumptions, release steps, post-merge checks, verdict and blockers.

Treat `goal.summary` and `goal.acceptance` as product communication, not an
implementation changelog. The summary is a concise plain-language description
of the delivered capability, affected people, and practical value. The
acceptance list completes that description exhaustively with observable
journeys, permissions, failure or degraded behavior, and material limitations.
For a technical-only change, describe the operator or maintainer outcome. Put
modules, models, paths, architecture and command evidence in their dedicated
technical fields instead of leading with them.

Also add exactly one canonical line to `integration.concerns`:

```text
Lead handoff: automatic
```

or:

```text
Lead handoff: human-approved after Feature/Worktree-QA review
```

The PR renderer exposes `integration.concerns`, so the generated PR records the
same routing choice without changing the machine-readable v1 schema.

Validate and preview it:

```bash
scripts/agent/handoff validate artifacts/agent/handoffs/my-feature.json --repository
scripts/agent/handoff render artifacts/agent/handoffs/my-feature.json
scripts/agent/verify feature-ready --handoff artifacts/agent/handoffs/my-feature.json
scripts/agent/github pr --handoff artifacts/agent/handoffs/my-feature.json
```

The PR body is the canonical handoff surface. It renders the contract as
human-first GitHub Markdown. It starts with **What this delivers** and the
complete user or operator outcomes, then presents review status, implementation
scope, decisions, evidence, migration/QA, integration, release and limitations.
A collapsed, delimited canonical JSON block remains suitable for validation
and later AI Pipelines. The local artifact is intentionally ignored. Readiness
validation rejects stale branch, head or worktree evidence, dirty state, an
unpushed head, invalid commit attribution, and a target/head pair for which Git
cannot construct a conflict-free merge candidate. The recorded `feature.base_sha`
remains historical evidence and may precede the current fetched target tip.
The PR helper uses the contract's
validated `feature.base`, stripping only the local `origin/` qualifier, so an
explicit stacked handoff opens against its parent feature branch rather than
silently retargeting to `19-usl`.

During `migration-transition`, commit-attribution findings remain visible but
follow the advisory policy declared in `agent/policy.json`; `feature-ready`
blocks them once that policy becomes required. This permits legacy feature
history to be handed off without rewriting already-published commits.

After opening the ready PR, apply the selected gate. Automatic mode proceeds
immediately. Human-approved mode presents the PR, implementation evidence,
Worktree-QA status, validation, and blockers to the designated human,
explicitly asks approval to hand off to Lead, and stops until that approval is
affirmative.

When the gate opens, read the final v1 artifact after it contains the final
head SHA and PR URL. Use Codex Desktop's supported task-to-task message
capability to notify the task titled exactly `19-usl - Lead`. Resolve that task
by title when possible; do not hardcode a historical task identifier into
repository policy. The message contains branch, commit, PR URL, exact
validation and results, and blockers or `none`, followed by the complete
machine-readable JSON contract verbatim. A summary, rendered PR body, or link
without the complete contract is not an effective handoff. Use the same
summary fields when a genuine Lead decision blocks implementation, but do not
label that earlier request as final handoff. If direct task messaging is
unavailable, state that limitation and include the complete contract in the
Coding task's final report for manual delivery.

The final notification is asynchronous and is not approval. After it is sent,
the Coding Agent leaves its branch, worktree, QA resources, and evidence intact
and stops without polling or waiting for the Lead. It waits only when genuinely
blocked on a decision.

## Lead Agent integration

Continue the persistent `19-usl - Lead` task with
`Review and integrate PR <number> as Lead Agent.` It
loads `usl-lead-developer`, fetches current refs, reads the goal and contract,
and independently inspects the exact diff. A local extracted contract can be
checked with:

```bash
scripts/agent/verify lead-start --handoff /path/to/feature-handoff.json
```

The Lead Agent compares with the latest `origin/19-usl` and concurrent
work; detects duplicate or obsolete paths; qualifies architecture, security,
accounting, upgrades, recovery, tests and product/browser evidence; and either
dispatches a separate Coding task for a scoped repair or rejects the PR. The
Lead never authors the repair, including conflict repair. Final
risk-proportionate checks run locally against the exact merge candidate. A clean pushed head can proceed without
containing the target tip when Git can merge it cleanly; catch-up is reserved
for conflicts, dependency/stack changes, or generated-state reconciliation.

Lead review is the integration decision. GitHub requires neither an independent
approval count nor status checks. Once the Lead accepts the evidence and exact
candidate, it may enqueue the PR. The queue uses merge commits and single-entry
groups, preserving reviewed ancestry;
neither role manually deploys production. Owned QA/worktree cleanup follows
merge and successful post-merge OCI publication only.

## Merge queue admission

The repository does not maintain a general GitHub-settings platform. The
narrow desired state is therefore stored in `agent/policy.json` and applied by
the existing isolated GitHub helper. The operation updates the active
`USL Distribution` ruleset in place, preserves deletion, non-fast-forward,
pull-request and bypass rules, and removes required-status-check rules. It
refuses a linear-history rule, a disabled merge-commit repository,
a dirty or stale Lead checkout, a non-Lead identity, or a prerequisite commit
that is not an ancestor of GitHub `19-usl`.

To update the queue policy:

1. Merge the reviewed policy change. Do not update GitHub from its Coding worktree.
2. In the persistent `19-usl - Lead` task, fetch and update the clean
   authoritative checkout to the GitHub tip.
3. Preview the idempotent payload, using the merged prerequisite PR head SHA:

   ```bash
   scripts/agent/github merge-queue --after-sha <PR_HEAD_SHA>
   ```

4. Review that the preserved rules and desired values are present, then apply
   the same payload:

   ```bash
   scripts/agent/github merge-queue --after-sha <PR_HEAD_SHA> --apply
   ```

5. Re-run the preview; it must report that the ruleset already matches policy.

The reviewed settings are merge method `MERGE`, no required status checks,
`ALLGREEN`,
minimum and maximum group size `1`, build concurrency `2`, zero group wait,
and a 120-minute response timeout. The queue constructs the current target
candidate without running GitHub compute qualification. The sole workflow runs
only on an actual `push` to `refs/heads/19-usl` and publishes the immutable
Distribution and backup-tool OCI artifacts, SBOMs, attestations and release
metadata.

## Transition to continuous delivery

Immediately after the migration cutover is admitted and the Community database
becomes canonical:

1. Change `agent/policy.json` phase to `continuous-development` and all three
   enforcement values to `required` in a reviewed policy PR.
2. Retain the active `USL Distribution` static protections: merge queue,
   pull-request integration, deletion and non-fast-forward protection, merge
   commits, and narrowly controlled bypass actors. Do not introduce required
   checks or an approval count without a new explicit cost and governance
   decision.
3. Activate the production backup stack, then add the deployment pipeline with
   preflight, quiesced checkpoint, module/data upgrade, digest deployment,
   verification, and tested recovery. Set
   `continuous_deployment_enabled` only when that pipeline is admitted.
4. Remove any temporary cutover exception that is no longer active. Do not
   retain the Online dump as a rollback assumption.

Repository scripts cannot prevent a determined bypass, prove that Feature and
Lead were separate people, or protect GitHub itself. Those guarantees depend on
the static ruleset, role discipline and narrowly scoped CI permissions.

## Skill ownership and portability

The seven `agent-skills/` packages are repository-owned and provider-neutral.
Relative provider exposures are tested by `scripts/agent/verify skills`.
`CLAUDE.md` imports `AGENTS.md`; it does not duplicate lifecycle policy.

Impeccable 4.1.1 is externally managed and Apache-2.0. Its provider-generated
copies are an intentional exception to the single-source repository Skills; do
not edit them as USL policy. No third-party packs are vendored by this feature.
OpenAI's externally installed `skill-creator` was used to shape and evaluate
the repository-owned Skills but is not a runtime dependency. Provider browser
control Skills are optional implementations of the provider-neutral browser
evidence procedure. Codex Security is a useful optional future installation
for independent security review, not a current dependency. Runtime discovery
still depends on the corresponding installed and authenticated client;
structural link tests remain the deterministic baseline.
