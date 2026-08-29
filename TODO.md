# Odoo Rebuild — Master TODO and Delivery Roadmap

> **Target:** A modern, efficient, trusted, verifiable, AI-native Odoo Community platform for USL (Holding), USL Media (Digital Content Creation monetized mostly through platforms like OnlyFans and Influencer Deals), GBC (eCommerce with Medusa shop), and future entities—covering accounting, banking, expenses, HR, project management, automation, agent collaboration, cloud deployment, backups and controlled migration from Odoo Online.

> **Core constraint:** Extend and compose Odoo rather than creating an irreconcilable fork. Preserve upstream compatibility, standard business semantics, upgradeability and auditability.

## Current release preparation — updated 2026-08-30

Status: **The product and migration implementation are ready for fresh
frozen-source QA, but production is not yet admitted.** Older source checksums,
reconstruction seeds, candidates, count baselines, and fingerprints are stale
for final evidence.

### Current production-candidate checklist

- [x] Consolidate migration lifecycle operations under `migration/manage`.
- [x] Remove shared reconstruction seeds, cache hydration, and resume paths.
- [x] Preserve the product/migration boundary and CI-built release identities.
- [ ] Run one fresh full-profile QA reconstruction from the frozen Online
  package and retain the runtime for review.
- [ ] Pass source-wide, attachment, Accounting, access, multi-company,
  Projects, Expenses, Platform Billing, TESE, Inventory, Documents, Sign,
  queue, repeated-upgrade, restart, and coordinated-recovery gates.
- [ ] Complete changed-journey browser QA and obtain explicit QA acceptance.
- [ ] Open a new production-grade transition runtime from the frozen source;
  do not promote QA volumes.
- [ ] Complete the local working period and explicit final cutoff.
- [ ] Build and independently restore the final evolved cohort without OCR,
  re-ingestion, vector rebuild, or model download.
- [ ] Admit the exact production release and verify the first coordinated
  production backup restore.
- [ ] Activate each external integration only through its separate production
  gate.

The authoritative sequence is in
[`docs/operations/migration.md`](docs/operations/migration.md) and
[`docs/operations/production.md`](docs/operations/production.md).

## Historical Milestone 13 candidate — verified 2026-07-26

Status: **Accounting v1 engineering complete; ready for internal daily use with
documented source/scope assumptions**.
Professional approval and external filing are explicitly outside the engineering
completion gate.

### Verified on `odoo_dev`

- [x] The complete latest accounting snapshot is represented in the single
  developer/QA product database, including `5,044` moves (`4,849` posted,
  `193` draft and `2` cancelled), `3,046` bank transactions, `1,889`
  historical currency rates, `632` analytic lines, `360` native expenses and
  native asset schedules.
- [x] The complete reconciliation graph is native: `2,584` partial and `1,260`
  full reconciliations, with no review-only placeholder records.
- [x] All `704` Accounting-relevant attachments are present, readable and
  linked through native records.
- [x] Current-period parity for 1 October 2025 through 30 June 2026 is exact:
  `2,694` posted moves, `6,319` lines and debit/credit of `1,708,270.52`, with
  zero account or journal differences.
- [x] Overview, Journals, document workflows, Transactions, Bank Matching,
  General Reconciliation, Accounting Hygiene, Declarations, Closing and native
  FEC are coherent user-facing journeys.
- [x] Canonical reports open directly with interactive filters, hierarchy,
  drill-down and immediate screen-consistent PDF/XLSX downloads; the generic
  export model is restricted to Advanced Audit.
- [x] Closing Controls are configurable and focused Hygiene issues are
  persistent, traceable and resolution-aware.
- [x] The end-user guide is organized as one tutorial, mission-based how-to
  guides, reference and explanations. Superseded checkpoints are archived.

### Final release work

- [x] Complete the exact scoped read-only browser walkthrough on the current
  candidate.
- [x] Run the final clean current-HEAD reconstruction and independent parity
  validation.
- [x] Run the full targeted add-on, report/export and browser journey suites.
- [x] Capture the final screenshot parity matrix and release evidence index.
- [x] Commit a clean merge-ready candidate.

Final evidence and residual advisories are recorded in
`docs/accounting/milestone-13-final-candidate.md`.

### Explicitly deferred

- [ ] Professional accounting sign-off and live tax/electronic filing.
- [ ] Selection and activation of a production approved electronic-invoicing
  platform.
- [ ] Probabilistic or AI-powered matching and autonomous posting.
- [ ] Live bank synchronization and payment-provider ingestion.
- [ ] Production deployment and cutover from disposable development.

# 0. Programme governance and invariants

- [x] Create `ROADMAP.md` as the canonical programme backlog.
- [x] Create `ARCHITECTURE.md` describing the intended system boundaries.
- [x] Create `CONTRIBUTING.md` for human and AI contributors.
- [x] Create `SECURITY.md` for vulnerability reporting and security expectations.
- [ ] Create `UPSTREAM.md` documenting the relationship with `odoo/odoo`.
- [x] Create `DECISIONS/` for Architecture Decision Records.
- [x] Create `docs/product/` for approved functional requirements.
- [x] Create `docs/operations/` for deployment and recovery runbooks.
- [x] Create `docs/accounting/` for accounting invariants and parity evidence.
- [x] Create a canonical feature-to-module, screen, documentation and maturity
  map in `docs/product/fork-overview.md`.
- [x] Assign responsibility for product decisions.
- [x] Assign responsibility for architecture decisions.
- [x] Assign responsibility for accounting acceptance.
- [x] Assign responsibility for production operations.
- [x] Define how AI-generated changes are reviewed and accepted.
- [ ] Define which changes require Technical Architect approval.
- [ ] Define which changes require Product Manager approval.
- [ ] Define which changes require accountant approval.
- [ ] Define which changes require Valentin’s explicit approval.
- [ ] Establish the rule: no direct production changes outside the deployment process.
- [ ] Establish the rule: no silent modification of posted accounting.
- [ ] Establish the rule: no parallel accounting ledger.
- [ ] Establish the rule: custom workflows orchestrate standard Odoo records.
- [ ] Establish the rule: reuse maintained functionality before custom development.
- [x] Establish the rule: every material architectural decision compares at least two credible alternatives.
- [ ] Establish the rule: every divergence from upstream is documented.
- [ ] Establish the rule: every external side effect must be attributable and retry-safe.
- [ ] Establish the rule: failures must leave visible and actionable state.
- [ ] Establish the rule: tasks waiting for external events are not marked complete.
- [ ] Establish the rule: agents operate under bounded identities and permissions.
- [ ] Establish the rule: production readiness must be demonstrated, not inferred.
- [ ] Define the programme’s risk classification.
  - [ ] Accounting-critical
  - [ ] Security-critical
  - [ ] Data-loss-critical
  - [ ] Privacy-critical
  - [ ] Migration-critical
  - [ ] Operational
  - [ ] Product usability
- [ ] Define change approval requirements for each risk class.
- [ ] Define the minimum evidence required to close each type of task.
- [ ] Define the meaning of:
  - [ ] Proposed
  - [ ] Accepted
  - [ ] In progress
  - [ ] Blocked
  - [ ] Waiting for external event
  - [ ] Ready for review
  - [ ] Verified
  - [ ] Production-ready
  - [ ] Done
- [ ] Establish a living risk register.
- [ ] Establish a living technical-debt register.
- [ ] Establish a living assumptions register.
- [ ] Establish a living migration-blocker register.

## Milestone 0 exit criteria

- [x] Governance documents exist.
- [ ] Architectural and product authority are separated.
- [ ] Critical invariants are explicit.
- [ ] Risks, assumptions and decisions have canonical locations.
- [ ] Contributors know how work is proposed, reviewed, verified and released.

---

# 1. Establish the upstream and repository strategy

- [x] Confirm the exact upstream baseline:
  - [x] Repository: `odoo/odoo`
  - [x] Branch: `saas-19.3`
  - [x] Pinned commit: `aef56898d9ea5a97948af04c03ae101d17b8b4a3`
  - [x] Integrated into `19-usl`: 26 August 2026
- [x] Add the official Odoo repository as the canonical `upstream` remote.
- [x] Define the project repository as the `usl` remote
  (`unstaticlabs/odoo`).
- [x] Decide whether the repository will:
  - [x] Vendor the Odoo source directly.
  - [ ] Maintain a thin integration repository around an upstream checkout.
- [x] Document both repository alternatives and the selected approach.
- [x] Define how upstream commits will be fetched.
- [ ] Define how upstream security fixes will be identified.
- [ ] Define how upstream changes will be reviewed.
- [x] Define how upstream changes will be merged or rebased.
- [ ] Define the expected frequency of upstream synchronization.
- [ ] Define a maximum tolerated upstream lag.
- [ ] Create an automated report showing:
  - [ ] Current upstream commit
  - [ ] Current project commit
  - [ ] Upstream commits not yet integrated
  - [ ] Conflicting files
  - [ ] Project modifications to upstream-owned files
- [x] Minimize direct edits to Odoo core.
- [x] Inventory every initial core modification, if any.
- [ ] Require an ADR for every core modification.
- [ ] For each core modification, document:
  - [ ] Why extension was insufficient
  - [ ] Alternative approaches considered
  - [ ] Upgrade impact
  - [ ] Test coverage
  - [ ] Removal or upstreaming path
- [x] Define the custom add-on namespaces.
- [x] Separate:
  - [x] Upstream Odoo add-ons
  - [x] OCA add-ons
  - [x] Generic reusable USL add-ons
  - [x] USL-specific add-ons
  - [x] Experimental or test-only add-ons
  - [x] Migration-only add-ons
- [ ] Define module naming conventions.
- [ ] Define manifest conventions.
- [ ] Define versioning conventions.
- [ ] Define module ownership metadata.
- [ ] Define dependency rules between module categories.
- [ ] Prevent generic modules from depending on company-specific modules.
- [ ] Prevent accounting foundations from depending on experimental AI features.
- [x] Prevent migration utilities from becoming permanent runtime dependencies.
- [x] Create a module and dependency map.
- [ ] Add automated detection of circular or forbidden dependencies.
- [ ] Create an OCA evaluation policy.
- [ ] For every OCA dependency, record:
  - [ ] Repository
  - [ ] Module
  - [ ] Version/branch
  - [ ] License
  - [ ] Maintenance state
  - [ ] Maintainers
  - [ ] Test status
  - [ ] Upgrade implications
  - [ ] Reason for adoption
- [x] Define whether OCA repositories are pinned, vendored or fetched.
- [x] Ensure every external dependency is reproducibly pinned.
- [ ] Create a dependency update procedure.
- [ ] Create a dependency removal procedure.
- [ ] Create a license compatibility inventory.
- [ ] Confirm that every distributed or hosted component is used consistently with its license.

## Milestone 1 exit criteria

- [ ] Upstream relationship is explicit and testable.
- [ ] Custom code boundaries are defined.
- [ ] Core modifications are absent or individually justified.
- [ ] Dependencies are pinned and attributable.
- [ ] An upstream synchronization can be rehearsed safely.

---

# 2. Reproducible development environment

## Devcontainer

- [x] Create `.devcontainer/`.
- [x] Create the Dev Container definition.
- [x] Pin the base development image.
- [x] Install the supported Python runtime and system dependencies.
- [x] Install PostgreSQL client tooling.
- [x] Install Node.js and frontend tooling required by Odoo 19.
- [x] Install Git and repository tooling.
- [x] Install XML, translation and asset-processing dependencies.
- [x] Install PDF/report rendering dependencies.
- [ ] Install browser dependencies required for tours and frontend tests.
- [x] Install linting and formatting tools.
- [x] Install debugging tools.
- [x] Install database inspection tools.
- [ ] Install Odoo shell helpers.
- [x] Configure VS Code.
- [x] Configure Cursor.
- [x] Add recommended extensions.
- [x] Configure Python language support.
- [ ] Configure JavaScript language support.
- [x] Configure XML support.
- [x] Configure debugging launch profiles.
- [x] Configure Odoo server launch profiles.
- [x] Configure test launch profiles.
- [x] Configure environment-variable loading.
- [x] Configure source and add-on paths.
- [x] Configure persistent development volumes.
- [x] Ensure the Dev Container does not require host-specific paths.
- [x] Ensure it works on macOS.
- [ ] Ensure it works on Linux.
- [ ] Document Windows/WSL expectations if supported.
- [ ] Add a one-command bootstrap.
- [x] Add a one-command reset.
- [x] Add a one-command test-database creation.
- [ ] Add a one-command sample-data load.
- [ ] Add a one-command clean rebuild.
- [ ] Verify a new engineer can open the repository and run Odoo without undocumented steps.
- [x] Verify a coding agent can discover the same commands from repository documentation.

## Local Docker Compose

- [x] Create the base `compose.yaml`.
- [x] Add an Odoo application service.
- [x] Add a PostgreSQL service.
- [x] Add persistent PostgreSQL storage.
- [x] Add persistent filestore storage.
- [ ] Add configuration mounting.
- [x] Add custom add-on mounting.
- [x] Add OCA add-on mounting or reproducible fetching.
- [x] Add health checks.
- [x] Add service dependency health conditions.
- [x] Add a database initialization path.
- [ ] Add a test database profile.
- [ ] Add a mail-capture service for local development.
- [ ] Add a local object-storage emulator if required.
- [ ] Add a reverse-proxy profile if needed for realistic testing.
- [ ] Add an optional worker/background-processing profile.
- [ ] Add an optional observability profile.
- [x] Ensure secrets are not committed.
- [x] Provide `.env.example`.
- [ ] Validate required environment variables at startup.
- [x] Add safe local defaults.
- [ ] Distinguish development, test, staging and production configuration.
- [ ] Ensure local development cannot accidentally contact production integrations.
- [ ] Ensure local databases are clearly marked as non-production.
- [ ] Add database neutralization procedures.
- [ ] Disable outgoing email by default outside production.
- [ ] Disable bank writes and legal-network actions outside production.
- [ ] Disable real webhooks outside production.
- [ ] Add deterministic seed/demo data.
- [ ] Verify full rebuild from an empty Docker state.
- [ ] Verify data persists across container recreation.
- [ ] Verify complete environment destruction and restoration.

## Developer workflow

- [x] Document the repository bootstrap command.
- [x] Document the Odoo startup command.
- [x] Document module installation.
- [x] Document module upgrades.
- [x] Document test selection.
- [x] Document database reset.
- [x] Document log inspection.
- [x] Document debugging.
- [ ] Document frontend asset rebuilding.
- [x] Document translation workflows.
- [x] Document migration-script execution.
- [x] Document how agents should validate their changes.
- [ ] Add a preflight command that checks local readiness.
- [ ] Add a doctor command that diagnoses common setup failures.

## Milestone 2 exit criteria

- [ ] A fresh machine can run the project reproducibly.
- [ ] The same source and dependency versions are used by humans, agents and CI.
- [x] Local Odoo scheduler threads are disabled by default for imported-accounting parity work.
- [ ] Complete local neutralization review for outgoing network access, credentials, mail servers, payment providers and electronic-invoicing services.
- [ ] There are no undocumented bootstrap steps.
- [ ] A clean rebuild is repeatable.

---

# 3. Repository quality and continuous integration

- [ ] Define supported Python, PostgreSQL, Node and browser versions.
- [ ] Pin or constrain dependency versions appropriately.
- [ ] Adopt Odoo’s current linting configuration where applicable.
- [ ] Evaluate relevant OCA quality conventions.
- [ ] Configure Python linting.
- [ ] Configure JavaScript linting.
- [ ] Configure XML validation.
- [ ] Configure manifest validation.
- [x] Configure contextual French translation validation for maintained USL
  catalogs; keep broader upstream/OCA translation review in the release QA.
- [ ] Configure security-focused static checks.
- [ ] Configure license-header checks.
- [ ] Configure forbidden-import checks.
- [ ] Configure dependency-boundary checks.
- [ ] Configure formatting checks.
- [ ] Add pre-commit hooks.
- [ ] Ensure the same checks run locally and in CI.
- [ ] Build a fast CI path for small changes.
- [ ] Build a complete CI path for merge candidates.
- [ ] Add unit-test execution.
- [ ] Add integration-test execution.
- [ ] Add JavaScript-test execution.
- [ ] Add Odoo tour execution.
- [ ] Add module-install tests.
- [ ] Add module-upgrade tests.
- [ ] Add database-initialization tests.
- [ ] Add migration tests.
- [ ] Add permission tests.
- [ ] Add multi-company tests.
- [ ] Add multi-currency tests.
- [ ] Add idempotency tests.
- [ ] Add retry-behaviour tests.
- [ ] Add duplicate-event tests.
- [ ] Add audit-trail tests.
- [ ] Add negative security tests.
- [ ] Add accounting golden-fixture tests.
- [ ] Add critical-report snapshot tests.
- [ ] Add performance smoke tests.
- [ ] Add backup-restore smoke tests.
- [ ] Add container-build tests.
- [ ] Add infrastructure configuration validation.
- [ ] Add documentation-link validation.
- [ ] Add test coverage reporting.
- [ ] Define minimum coverage expectations by risk level.
- [ ] Do not use overall coverage as the only quality signal.
- [ ] Require explicit tests for accounting-critical behaviour.
- [ ] Require explicit tests for every fixed regression.
- [ ] Store sanitized failing fixtures where useful.
- [ ] Create a quarantine policy for flaky tests.
- [ ] Forbid silently ignored flaky tests.
- [ ] Add required status checks to protected branches.
- [ ] Protect the primary branch.
- [ ] Require reviewed pull requests.
- [ ] Prevent direct force-pushes.
- [ ] Require signed or attributable commits where practical.
- [ ] Add automated release notes.
- [ ] Add automated module changelog checks.
- [ ] Generate build provenance.
- [ ] Retain CI evidence for production releases.
- [ ] Add dependency-vulnerability scanning.
- [ ] Add container-image scanning.
- [ ] Add secret scanning.
- [ ] Add source-composition/license reporting.
- [ ] Create a standard AI-agent pull-request template.
- [ ] Require agents to state:
  - [ ] Requirement addressed
  - [ ] Alternatives considered
  - [ ] Files changed
  - [ ] Risks
  - [ ] Tests executed
  - [ ] Known limitations
  - [ ] Upstream impact
  - [ ] Migration impact
  - [ ] Screenshots or evidence where relevant

## Milestone 3 exit criteria

- [ ] Every merge passes automated quality gates.
- [ ] Critical business behaviour has explicit tests.
- [ ] Releases are attributable and reproducible.
- [ ] Security and dependency risks are visible.
- [ ] Agents cannot merge unverifiable work directly.

---

# 4. Architectural baseline

- [ ] Document the current Odoo 19 architecture relevant to the project.
- [ ] Map the Odoo server, ORM, add-ons, web client, cron and reporting boundaries.
- [ ] Identify standard extension mechanisms.
- [ ] Identify areas where inheritance is appropriate.
- [ ] Identify areas where composition is preferable.
- [ ] Identify APIs and behaviours considered internal or unstable.
- [ ] Define when direct SQL is prohibited.
- [ ] Define the exceptional situations where direct SQL is permitted.
- [ ] Require ORM use for normal business operations.
- [ ] Define transactional boundaries for custom workflows.
- [ ] Define external-side-effect boundaries.
- [ ] Define idempotency requirements.
- [ ] Define job retry requirements.
- [ ] Define concurrency expectations.
- [ ] Define duplicate-event handling.
- [ ] Define event ordering expectations.
- [ ] Define failure and dead-letter semantics.
- [ ] Define observability requirements for background work.
- [ ] Define durable workflow-state requirements.
- [ ] Define how blocking and expected events are represented.
- [ ] Define how actions and approvals are represented.
- [ ] Define how agent evidence is stored.
- [ ] Define how machine confidence is represented.
- [ ] Define how human overrides are recorded.
- [ ] Define how policy versions are recorded.
- [ ] Define how external references are stored.
- [ ] Define how documents remain linked without unnecessary duplication.
- [ ] Define which system owns each major category of truth:
  - [ ] Odoo
  - [ ] GitHub
  - [ ] Gmail
  - [ ] Google Drive
  - [ ] Calendar
  - [ ] Banking provider
  - [ ] Creator/social platform
  - [ ] Obsidian
  - [ ] Agent runtime
- [ ] Define the event taxonomy.
- [ ] Define the capability taxonomy.
- [ ] Define the audit-event taxonomy.
- [ ] Define the notification taxonomy.
- [ ] Define how custom modules publish state changes.
- [ ] Define how external events become Odoo context.
- [ ] Define how automation can be disabled per company, model or workflow.
- [ ] Define emergency stop mechanisms.
- [ ] Define the acceptable coupling between Odoo and Hermes/OpenClaw.
- [ ] Ensure Odoo remains functional when the AI layer is unavailable.
- [ ] Ensure AI components cannot become the only repository of business facts.
- [ ] Keep provider-specific AI integration behind replaceable boundaries.
- [ ] Document architecture alternatives for:
  - [ ] Background jobs
  - [ ] Event delivery
  - [ ] Bank aggregation
  - [ ] Document storage
  - [ ] AI orchestration
  - [ ] External identity
  - [ ] Analytics
- [ ] Select initial approaches through ADRs.
- [ ] Define criteria for revisiting each decision.

## Milestone 4 exit criteria

- [ ] System boundaries are explicit.
- [ ] Sources of truth are unambiguous.
- [ ] AI is optional for core ERP correctness.
- [ ] Side effects, retries and auditability have defined semantics.
- [ ] Future integrations have stable conceptual boundaries.

---

# 5. Environment and release model

- [ ] Define local-development environments.
- [ ] Define ephemeral pull-request environments.
- [ ] Define shared integration environment.
- [ ] Define accounting-validation environment.
- [ ] Define staging environment.
- [ ] Define production environment.
- [ ] Define disaster-recovery environment or restoration target.
- [ ] Define which data may exist in each environment.
- [ ] Define anonymization requirements.
- [ ] Define neutralization requirements.
- [ ] Define how production copies are approved.
- [ ] Define environment naming conventions.
- [ ] Add unmistakable environment banners.
- [ ] Prevent staging and test environments from sending production email.
- [x] Prevent staging and test environments from sending Peppol/e-invoicing traffic through default-off reception and e-reporting guards.
- [ ] Prevent staging and test environments from initiating real payments.
- [ ] Prevent staging and test environments from notifying real customers or suppliers.
- [ ] Prevent staging agents from acting on production systems.
- [ ] Define release channels:
  - [ ] Development
  - [ ] Candidate
  - [ ] Production
  - [ ] Emergency hotfix
- [ ] Define semantic versioning for custom add-ons.
- [ ] Define database migration expectations for each release.
- [ ] Define pre-deployment checks.
- [ ] Define post-deployment checks.
- [ ] Define rollback criteria.
- [ ] Define rollback limitations for schema/data migrations.
- [ ] Define forward-fix procedures.
- [ ] Create release manifests listing:
  - [ ] Odoo upstream commit
  - [ ] OCA commits
  - [ ] Custom module versions
  - [ ] Container versions
  - [ ] Database migration set
  - [ ] Configuration version
  - [ ] AI policy versions
- [ ] Create a standard release approval record.
- [ ] Create a standard production change log.

## Milestone 5 exit criteria

- [ ] Environments have distinct purposes and safety policies.
- [ ] Releases are reproducible and attributable.
- [ ] External side effects cannot leak from non-production.
- [ ] Rollback and forward-fix expectations are explicit.

---

# 6. Cloud infrastructure foundation

## Architecture selection

- [ ] Define expected user count.
- [ ] Define expected agent count.
- [ ] Define concurrent workload assumptions.
- [ ] Define document-volume assumptions.
- [ ] Define database-growth assumptions.
- [ ] Define AI workload assumptions.
- [ ] Define recovery-time objective.
- [ ] Define recovery-point objective.
- [ ] Define availability objective.
- [ ] Define acceptable maintenance windows.
- [ ] Compare at least two credible hosting architectures.
- [ ] Compare managed database versus self-managed PostgreSQL.
- [ ] Compare single-node versus separated application/database services.
- [ ] Compare virtual machines versus managed container services.
- [ ] Compare provider-native backups versus independent backup tooling.
- [ ] Select the initial architecture.
- [ ] Document the scaling path.
- [ ] Document the expected monthly cost.
- [ ] Document provider lock-in.
- [ ] Document exit and restore procedures.

## Infrastructure as code

- [ ] Represent cloud resources as reviewed code.
- [ ] Define network resources.
- [ ] Define subnets and boundaries.
- [ ] Define application compute.
- [ ] Define PostgreSQL.
- [ ] Define persistent filestore/object storage.
- [ ] Define load balancer or reverse proxy.
- [ ] Define DNS.
- [ ] Define TLS certificate management.
- [ ] Define secret management.
- [ ] Define backup storage.
- [ ] Define monitoring.
- [ ] Define logging.
- [ ] Define alerting.
- [ ] Define access-control roles.
- [ ] Define deployment identities.
- [ ] Define maintenance access.
- [ ] Define environment separation.
- [ ] Validate infrastructure plans in CI.
- [ ] Prevent production infrastructure deletion without explicit approval.
- [ ] Tag resources by environment, owner, purpose and cost centre.
- [ ] Add budget and unexpected-cost alerts.

## Network and edge security

- [ ] Require HTTPS.
- [ ] Redirect insecure traffic.
- [ ] Configure proxy awareness correctly.
- [ ] Restrict database ports to trusted services.
- [ ] Restrict administrative access.
- [ ] Restrict `/web/database` management routes.
- [ ] Store the Odoo master password securely.
- [ ] Disable public database listing where appropriate.
- [ ] Configure secure session-cookie behaviour.
- [ ] Configure trusted proxy headers.
- [ ] Configure request-size limits.
- [ ] Configure rate protection for public endpoints.
- [ ] Configure denial-of-service protections appropriate to scale.
- [ ] Configure web application firewall rules if justified.
- [ ] Configure secure administrative access through VPN, identity-aware proxy or equivalent.
- [ ] Eliminate permanent shared SSH credentials.
- [ ] Log administrative access.
- [ ] Test access revocation.
- [ ] Test expired credential behaviour.

## Milestone 6 exit criteria

- [ ] Staging can be deployed reproducibly from code.
- [ ] Network exposure is minimal.
- [ ] Secrets are managed outside source control.
- [ ] Cost, recovery and scaling expectations are documented.
- [ ] Infrastructure destruction and recreation have been rehearsed outside production.

---

# 7. Database, filestore and backup strategy

- [ ] Inventory all state required to restore Odoo completely.
- [ ] Include PostgreSQL.
- [ ] Include the Odoo filestore.
- [ ] Include custom configuration.
- [ ] Include custom add-ons and exact source revisions.
- [ ] Include external object-storage references.
- [ ] Include secrets-recovery procedures.
- [ ] Include infrastructure definitions.
- [ ] Define database backup frequency.
- [ ] Define filestore backup frequency.
- [ ] Define transaction-log or point-in-time recovery expectations.
- [ ] Define backup retention periods.
- [ ] Define daily retention.
- [ ] Define weekly retention.
- [ ] Define monthly retention.
- [ ] Define yearly/legal retention where needed.
- [ ] Store backups in a separate failure domain.
- [ ] Protect backups against accidental deletion.
- [ ] Encrypt backups at rest.
- [ ] Encrypt backups in transit.
- [ ] Control backup access separately from production access.
- [ ] Monitor backup completion.
- [ ] Monitor backup age.
- [ ] Alert on missed backups.
- [ ] Verify backup integrity automatically.
- [ ] Perform automated restoration tests.
- [ ] Perform full database-and-filestore restoration tests.
- [ ] Verify attachment integrity after restoration.
- [ ] Verify accounting reports after restoration.
- [ ] Verify user access after restoration.
- [ ] Verify custom module availability after restoration.
- [ ] Verify external integrations remain neutralized after test restoration.
- [ ] Document complete disaster-recovery steps.
- [ ] Document recovery when the primary cloud account is unavailable.
- [ ] Document recovery when production secrets are compromised.
- [ ] Document recovery from accidental deletion.
- [ ] Document recovery from corrupted migrations.
- [ ] Document recovery from ransomware or malicious administration.
- [ ] Schedule recurring recovery drills.
- [ ] Record actual recovery time and recovery point.
- [ ] Compare actual results with stated objectives.
- [ ] Correct gaps.
- [ ] Define legal hold and archival procedures.
- [ ] Ensure expired data can be removed consistently with retention policy.

## Milestone 7 exit criteria

- [ ] Backups are automated, monitored and independently stored.
- [ ] Full restorations have succeeded.
- [ ] Restored databases retain attachments and accounting consistency.
- [ ] Recovery objectives are demonstrated rather than theoretical.

---

# 8. Observability and production operations

- [ ] Centralize application logs.
- [ ] Centralize PostgreSQL logs.
- [ ] Centralize proxy/load-balancer logs.
- [ ] Centralize background-job logs.
- [ ] Centralize agent-action logs.
- [ ] Correlate related actions with execution identifiers.
- [ ] Ensure logs do not expose unnecessary secrets or private document contents.
- [ ] Define log retention.
- [ ] Define searchable audit retention.
- [ ] Add application-health monitoring.
- [ ] Add database-health monitoring.
- [ ] Add storage-health monitoring.
- [ ] Add queue/background-work monitoring.
- [ ] Add cron monitoring.
- [ ] Add integration-health monitoring.
- [ ] Add bank-feed freshness monitoring.
- [ ] Add email-ingestion freshness monitoring.
- [ ] Add backup freshness monitoring.
- [ ] Add certificate-expiry monitoring.
- [ ] Add disk-capacity alerts.
- [ ] Add database-capacity alerts.
- [ ] Add error-rate alerts.
- [ ] Add latency alerts.
- [ ] Add failed-login alerts.
- [ ] Add privileged-action alerts.
- [ ] Add failed-agent-action alerts.
- [ ] Add dead-letter/unprocessed-event alerts.
- [ ] Define service-level indicators.
- [ ] Define alert severity.
- [ ] Define who receives each alert.
- [ ] Define escalation timing.
- [ ] Define out-of-hours expectations.
- [ ] Create runbooks for common alerts.
- [ ] Create a production status dashboard.
- [ ] Create an accounting-pipeline status dashboard.
- [ ] Create an integration status dashboard.
- [ ] Create an agent activity and exception dashboard.
- [ ] Track infrastructure and AI costs.
- [ ] Track cost per processed document.
- [ ] Track cost per automated workflow.
- [ ] Track error and human-intervention rates.
- [ ] Test monitoring by intentionally triggering safe failures.
- [ ] Ensure monitoring itself is monitored.

## Milestone 8 exit criteria

- [ ] Operational failures are detected without relying on users.
- [ ] Errors can be correlated across services.
- [ ] Runbooks exist for critical alerts.
- [ ] Agent work and exceptions are inspectable.
- [ ] Costs are measurable.

---

# 9. Security and identity baseline

## Human identity

- [ ] Inventory human user roles.
- [ ] Define administrator roles.
- [ ] Define accountant roles.
- [ ] Define employee roles.
- [ ] Define manager roles.
- [ ] Define contractor roles.
- [ ] Define limited external collaborator roles.
- [ ] Define portal-user roles.
- [ ] Define offboarding procedures.
- [ ] Require individual accounts.
- [ ] Eliminate shared human credentials.
- [ ] Define authentication requirements.
- [ ] Define multi-factor authentication expectations.
- [ ] Define session expiration.
- [ ] Define privileged-session requirements.
- [ ] Define password recovery.
- [ ] Define emergency administrator access.
- [ ] Test user deactivation.
- [ ] Test company-access revocation.
- [ ] Test former-employee access revocation.

## Agent identity

- [ ] Create an agent identity model and policy.
- [ ] Give each operational agent a distinct identity or execution principal.
- [ ] Assign each agent an owner.
- [ ] Assign each agent a mandate.
- [ ] Assign each agent allowed companies.
- [ ] Assign each agent allowed models.
- [ ] Assign each agent allowed actions.
- [ ] Assign each agent external-system access.
- [ ] Assign each agent approval thresholds.
- [ ] Assign each agent budget limits.
- [ ] Define agent credential rotation.
- [ ] Define agent suspension.
- [ ] Define agent emergency revocation.
- [ ] Prohibit agent superuser access by default.
- [ ] Prohibit sharing one unrestricted technical account across all agents.
- [ ] Record every agent-originated change.
- [ ] Record the triggering event.
- [ ] Record evidence references.
- [ ] Record applicable policy.
- [ ] Record confidence where inference is involved.
- [ ] Record human approval where required.

## Odoo permissions

- [ ] Audit all model access controls.
- [ ] Audit all record rules.
- [ ] Audit all field-level restrictions.
- [ ] Audit all public callable methods added by custom modules.
- [ ] Audit all uses of `sudo`.
- [ ] Audit all direct SQL.
- [ ] Audit all controller routes.
- [ ] Audit all webhook endpoints.
- [ ] Audit all attachment access.
- [ ] Audit all multi-company behaviours.
- [ ] Test cross-company isolation.
- [ ] Test accountant visibility.
- [ ] Test creator-content privacy.
- [ ] Test HR privacy.
- [ ] Test personal-data privacy.
- [ ] Test agent access with negative cases.
- [ ] Add automated permission-regression tests.
- [ ] Add a periodic permission review.
- [ ] Create a least-privilege report.
- [ ] Create a privileged-account report.

## Secure development

- [ ] Establish secure coding guidelines.
- [ ] Define acceptable secret handling.
- [ ] Define input-validation expectations.
- [ ] Define attachment and file-upload protections.
- [ ] Define webhook authentication.
- [ ] Define replay protection.
- [ ] Define outbound-request restrictions.
- [ ] Define dependency-security response.
- [ ] Define vulnerability-disclosure response.
- [ ] Define patch urgency by severity.
- [ ] Schedule periodic threat modelling.
- [ ] Schedule independent security review before production cutover.
- [ ] Schedule penetration testing when externally exposed capabilities justify it.

## Milestone 9 exit criteria

- [ ] Humans and agents have individually attributable access.
- [ ] Multi-company and privacy boundaries have negative tests.
- [ ] Privileged methods and routes have been reviewed.
- [ ] Emergency revocation works.
- [ ] Security findings have owners and deadlines.

---

# 10. Inventory the current Odoo Online production system

- [x] Confirm the exact production Odoo Online version.
- [x] Confirm whether it is an intermediary SaaS version.
- [ ] Determine an officially supported path to an on-premise-compatible
  stable major version; the current exact SaaS reconstruction is an internal
  migration path.
- [x] Download and preserve a current production backup.
- [x] Preserve the filestore.
- [x] Record backup-generation date and production version.
- [x] Inventory installed standard modules.
- [x] Inventory installed Enterprise modules.
- [ ] Inventory Studio-created applications.
- [ ] Inventory Studio-created models.
- [x] Inventory Accounting-relevant Studio-created fields.
- [x] Inventory Accounting-relevant Studio-modified views.
- [ ] Inventory automated actions.
- [ ] Inventory server actions.
- [ ] Inventory scheduled actions.
- [ ] Inventory email templates.
- [x] Inventory report templates.
- [x] Inventory Accounting-relevant user groups.
- [x] Inventory Accounting-relevant access rights.
- [x] Inventory Accounting-relevant record rules.
- [x] Inventory company configuration.
- [x] Inventory journals.
- [x] Inventory chart-of-accounts configuration.
- [x] Inventory taxes.
- [x] Inventory fiscal positions.
- [x] Inventory currencies and rates. The restored source contains `1,889`
  native historical rates; the importer replays and source-traces every rate.
- [x] Inventory analytic plans and accounts.
- [x] Inventory bank accounts and journals.
- [x] Inventory reconciliation models.
- [x] Inventory payment terms.
- [x] Inventory sequences.
- [x] Inventory lock dates.
- [x] Inventory accounting reports actively used.
- [x] Inventory accountant exports actively used.
- [x] Inventory FEC behaviour.
- [x] Inventory attachments and document volumes.
- [ ] Inventory chatter volumes and relevant history.
- [ ] Inventory mail aliases.
- [ ] Inventory inbound-email flows.
- [ ] Inventory outgoing-email configuration.
- [ ] Inventory bank-sync providers.
- [x] Inventory Peppol/e-invoicing configuration.
- [ ] Inventory external integrations.
- [ ] Inventory API users.
- [ ] Inventory current custom payroll workflow.
- [ ] Inventory current platform-payout workflow.
- [x] Inventory current accounting-hygiene workflow.
- [ ] Inventory current project-management workflow.
- [ ] Inventory current HR workflow.
- [ ] Inventory GBC workflows.
- [ ] Inventory USL Media workflows.
- [ ] Identify features currently paid for but unused.
- [x] Identify Community-equivalent features.
- [x] Identify OCA-equivalent features.
- [x] Identify features requiring custom replacement.
- [x] Identify features that can be deliberately dropped.
- [x] Identify Accounting data stored by Enterprise modules that must remain accessible.
- [x] Identify Accounting historical records whose models may disappear.
- [x] Identify legal and accountant retention requirements.
- [x] Create the complete Accounting feature and migration parity matrix.

## Milestone 10 exit criteria

- [ ] Production functionality and data are inventoried.
- [ ] Enterprise dependencies are known.
- [ ] Studio customizations are known.
- [ ] No migration-critical capability remains represented only by assumptions.
- [ ] The parity matrix is approved.

---

# 11. Build the representative parity laboratory

The checked progress in this milestone is verified for the Accounting
reconstruction scope. Broader operational domains remain governed by their
own later milestones.

- [x] Create an isolated parity environment.
- [x] Restore a protected production-derived source copy into the isolated,
  read-only source service.
- [x] Neutralize all external side effects.
- [x] Confirm attachment availability.
- [x] Confirm partner availability.
- [x] Confirm company availability.
- [x] Confirm journal and accounting-data availability.
- [x] Confirm custom field availability.
- [x] Catalogue Accounting models that cannot load without Enterprise components.
- [x] Catalogue missing Accounting views.
- [x] Catalogue missing Accounting reports.
- [x] Catalogue missing Accounting workflows.
- [x] Catalogue orphaned Accounting field references.
- [x] Catalogue missing Accounting external identifiers.
- [ ] Catalogue incompatible Studio artifacts.
- [ ] Catalogue incompatible automated actions.
- [x] Catalogue migration errors.
- [x] Decide, item by item:
  - [x] Preserve
  - [x] Replace
  - [x] Transform
  - [x] Archive
  - [x] Remove
- [ ] Create migration fixtures for each unsupported object category.
- [x] Preserve historical records even when the original interactive feature is removed.
- [x] Create a repeatable import/restore process.
- [x] Produce a machine-readable parity report.
- [x] Produce a human-readable parity report.
- [x] Re-run the parity process from a fresh backup.
- [x] Confirm repeatability.

## Milestone 11 exit criteria

- [x] A representative production dataset is usable in a safe lab.
- [x] Missing Enterprise dependencies are explicitly mapped.
- [x] Restore/import steps are repeatable.
- [x] No external side effects occur.
- [x] Data-loss risks are documented.

---

# 12. Establish the Community/OCA functional foundation

- [x] Identify the minimum standard Odoo Community module set.
- [x] Install base company and contact functionality.
- [x] Install invoicing/accounting foundations.
- [x] Install project functionality.
- [x] Install HR/employee foundations.
- [x] Install native Odoo expense functionality.
- [x] Install document/attachment foundations.
- [x] Install communication/chatter foundations.
- [x] Evaluate relevant OCA accounting repositories.
- [x] Evaluate relevant OCA reporting modules.
- [x] Evaluate relevant OCA reconciliation modules.
- [x] Evaluate relevant OCA banking modules.
- [ ] Evaluate relevant OCA project modules.
- [ ] Evaluate relevant OCA HR/payroll-support modules.
- [ ] Evaluate relevant OCA queue/background-job modules.
- [ ] Evaluate relevant OCA audit modules.
- [ ] Evaluate relevant OCA storage modules.
- [ ] Evaluate relevant OCA REST/API modules only where justified.
- [ ] Evaluate OpenUpgrade for future major-version migration support.
- [x] Record rejected OCA modules and reasons.
- [x] Build the minimum integrated Accounting module set.
- [x] Test clean installation.
- [x] Test module upgrade.
- [ ] Test module uninstallation where supported.
- [x] Test multi-company behaviour.
- [x] Test language and French localization behaviour.
- [x] Document module ownership and maintenance risk.
- [x] Freeze the initial Accounting foundation set for parity work.

## Milestone 12 exit criteria

- [x] The minimum Community/OCA Accounting foundation is stable.
- [x] Every retained Accounting add-on has a documented purpose and maintenance assessment.
- [x] The Accounting baseline installs and upgrades cleanly.
- [x] Accounting foundation modules do not depend on experimental AI components.

---

# 13. Accounting core and French localization

## Accounting invariants

- [x] Document the accounting invariants that must never be violated.
- [x] Define posting semantics.
- [x] Define correction semantics.
- [x] Define reversal semantics.
- [x] Define lock-date semantics.
- [x] Define sequence semantics.
- [x] Define document-retention semantics.
- [x] Define multi-company semantics.
- [x] Define multi-currency semantics.
- [x] Define tax-calculation semantics.
- [x] Define reconciliation semantics.
- [x] Define audit-trail semantics.
- [ ] Review invariants with the accountant.

## French accounting foundation

- [x] Validate the French chart of accounts.
- [x] Validate company fiscal settings.
- [x] Validate journals.
- [x] Validate taxes.
- [x] Validate tax repartitions.
- [x] Validate fiscal positions.
- [ ] Validate EU supplier/customer treatment.
- [ ] Validate reverse-charge cases.
- [ ] Validate intra-community service cases.
- [ ] Validate deductible and non-deductible VAT cases.
- [ ] Validate cash-versus-accrual tax behaviours where applicable.
- [ ] Validate rounding.
- [x] Validate invoice numbering.
- [x] Validate credit notes present in the source corpus.
- [x] Validate refunds present in the source corpus.
- [x] Validate payment terms.
- [x] Validate partner ledgers at user-facing technical parity level.
- [x] Validate aged receivables at user-facing technical parity level.
- [x] Validate aged payables at user-facing technical parity level.
- [x] Validate general ledger source/target ledger controls.
- [x] Validate trial balance source/target ledger controls.
- [x] Validate balance sheet at user-facing technical parity level.
- [x] Validate profit and loss at user-facing technical parity level.
- [x] Validate tax reports.
- [x] Validate VAT/CA12 preparation output; professional filing acceptance
  remains deferred.
- [x] Validate tax carryovers represented in the source corpus.
- [x] Validate externally supplied declaration-value handling where required.
- [x] Validate FEC generation through the compatibility harness.
- [x] Validate FEC field content through the compatibility harness.
- [x] Validate FEC chronological consistency through the compatibility harness.
- [ ] Validate FEC for a period containing the approved EUR 942 correction.
- [x] Validate evidence access from imported entries in the compatibility target.
- [x] Validate configurable fiscal-year closing controls and workspaces.
- [ ] Validate year-opening entries where applicable.
- [ ] Validate shareholder/current-account handling.
- [x] Validate imported asset and amortization evidence if in scope.
- [x] Validate expense reimbursements represented in the source corpus.
- [x] Validate intercompany transactions represented in the source corpus.
- [x] Validate bank-fee cases represented in the source corpus.
- [x] Validate partial payments.
- [x] Validate payment differences represented in the source corpus.
- [x] Validate multicurrency invoices.
- [x] Validate multicurrency payments.
- [x] Validate restored native currency-rate parity across the full source
  snapshot (`1,889/1,889` rates, provider and retrieval metadata, no
  mismatches or duplicate traces).
- [x] Configure and validate automatic future ECB reference rates with native rows, daily scheduling, source-history preservation, idempotence and manager/reviewer access controls.
- [x] Validate residual foreign-exchange balances represented in the source corpus.
- [x] Validate realized exchange differences represented in the source corpus.

## Accounting golden dataset

- [ ] Build a sanitized USL accounting fixture.
- [x] Include production-derived normal customer invoices in the private reconstruction corpus.
- [x] Include production-derived vendor bills in the private reconstruction corpus.
- [x] Include production-derived expenses in the private reconstruction corpus.
- [x] Include production-derived credit notes in the private reconstruction corpus.
- [x] Include production-derived partial payments in the private reconstruction corpus.
- [x] Include production-derived multicurrency transactions in the private reconstruction corpus.
- [x] Include production-derived platform commissions in the private reconstruction corpus where present.
- [x] Include production-derived compensation entries in the private reconstruction corpus where present.
- [x] Include production-derived bank fees in the private reconstruction corpus where present.
- [x] Include production-derived CCA transactions in the private reconstruction corpus where present.
- [x] Include production-derived intercompany transactions in the private reconstruction corpus where present.
- [x] Include production-derived VAT edge cases in the private reconstruction corpus.
- [x] Include production-derived year-end and lock-date cases in the private reconstruction corpus.
- [x] Include production-derived corrected accounting errors in the private reconstruction corpus where present.
- [x] Generate and preserve trusted source report definitions and benchmark outputs.
- [x] Generate preliminary equivalent Community report artifacts.
- [x] Compare posted ledger material differences in the compatibility harness.
- [x] Fix or document every Accounting difference.
- [ ] Obtain accountant review of the dataset.
- [x] Turn material accepted Accounting cases into permanent regression tests.

## User-facing closing and reporting product

- [x] Capture the annual accounts, SIG and tax-report reference document families.
- [x] Define the target daily workbench: reconcile, review, journal entries, invoices, bills, refunds, expenses and tax readiness.
- [x] Define the target closing package: ledger controls, reports, declaration mappings, FEC, evidence and review state.
- [x] Add a clear Accounting app entry that opens the operational Accounting Home while retaining the native journal Dashboard.
- [x] Complete the menu redesign around frequent CEO/accountant workflows. The seven-area top-level navigation, operational Home and distinct Bank Matching, General Reconciliation, Closing and Declarations destinations are implemented.
- [x] Implement dynamic report screens before export.
- [x] Implement readable accountant-ready PDF templates.
- [x] Implement readable templated XLSX exports.
- [x] Implement declaration guidance views for CFS Pro and Portailpro manual filing.
- [x] Implement declaration deadline/reminder workbench and expose its status on Accounting Home.
- [x] Integrate customer invoices as usable native business documents into the disposable hybrid replacement candidate, retaining checksum-verified source attachments.
- [ ] Establish customer credit-note replacement behavior when source data permits; the confirmed source period contains no customer credit-note case.
- [x] Integrate vendor bills as usable native business documents into the disposable hybrid replacement candidate, retaining original PDF evidence and source-designated main attachments.
- [x] Integrate supplier refunds as usable native business documents into the disposable hybrid replacement candidate.
- [x] Integrate current-period source expenses into the disposable hybrid replacement candidate through native Odoo, including checksum-verified receipts and source-designated main attachments.
- [x] Remove professional acceptance from the engineering completion gate while retaining explicit assumptions and optional review.
- [x] Implement accountant-readable closing archive package.
- [x] Keep machine/detail exports available as advanced audit evidence.

## Milestone 13 exit criteria

- [x] Accounting invariants are explicit and tested.
- [x] Golden reports match production or have explicit reproducible classifications.
- [x] FEC generation and structural validation work; professional review is outside the engineering gate.
- [x] Locking, corrections and evidence preservation work.
- [x] No accounting-critical gap is hidden behind manual assumptions.

---

# 14. Banking and financial-event ingestion

## Banking architecture

Scope note: payment-provider product support is not a Milestone 13 requirement. Bank synchronization and financial-event ingestion remain later roadmap work because they affect ongoing accounting operations.

- [ ] Inventory current business bank accounts.
- [ ] Inventory current personal accounts relevant to future use.
- [ ] Inventory supported bank APIs.
- [ ] Inventory PSD2/Open Banking aggregators.
- [ ] Inventory direct bank integrations.
- [ ] Inventory PayPal, Wise, Revolut, Stripe and platform payout sources.
- [ ] Compare at least two banking architecture alternatives:
  - [ ] Odoo-centric connectivity
  - [ ] Independent canonical Bank Hub
- [ ] Select the initial architecture.
- [ ] Define the canonical transaction identity.
- [ ] Define duplicate detection.
- [ ] Define pending versus booked transactions.
- [ ] Define transaction updates.
- [ ] Define balance snapshots.
- [ ] Define provider reconnection handling.
- [ ] Define consent-expiry handling.
- [ ] Define historical backfill.
- [ ] Define provider outage handling.
- [ ] Define institution metadata.
- [ ] Define multi-account ownership.
- [ ] Define company mapping.
- [ ] Define raw-versus-normalized data retention.
- [ ] Define how banking data enters Odoo.
- [x] Ensure imported bank lines are idempotent.
- [x] Ensure repeated file import does not duplicate transactions.
- [x] Preserve provider transaction references when supplied by an import.
- [x] Preserve original transaction text.
- [ ] Preserve enriched merchant information separately.
- [ ] Track ingestion time.
- [ ] Track source update time.
- [x] Track reconciliation state.
- [ ] Track missing source periods.
- [ ] Alert on stale feeds.
- [ ] Alert on account disconnection.
- [x] Support contextual CAMT, QIF and mapped CSV/XLSX statement import from each bank journal.
- [x] Support repeatable statement-import fixtures without live bank credentials.

## Reconciliation

- [x] Preserve standard Odoo reconciliation semantics.
- [ ] Define AI-assisted reconciliation as suggestions first.
- [ ] Create candidate-match evidence.
- [ ] Display confidence.
- [ ] Explain proposed matches.
- [x] Handle one-to-one matches.
- [x] Handle one-to-many matches.
- [x] Handle many-to-one matches.
- [x] Handle bank fees.
- [x] Handle FX differences.
- [x] Handle internal transfers.
- [ ] Handle salary payments.
- [ ] Handle TESE payments.
- [ ] Handle platform payouts.
- [x] Handle partial settlements.
- [ ] Handle duplicate bank lines.
- [ ] Prevent silent automated reconciliation until explicitly approved by policy.
- [ ] Record who or which agent proposed a match.
- [ ] Record who approved it.
- [x] Test unreconciliation and correction.
- [x] Build reconciliation regression fixtures.

## Milestone 14 exit criteria

- [ ] Bank data arrives automatically and reproducibly.
- [ ] Missing or stale feeds are visible.
- [ ] Duplicate ingestion is prevented.
- [ ] Reconciliation remains standard, explainable and controlled.
- [ ] Manual imports remain available as disaster fallback.

---

# 15. Documents, invoices, expenses and AI-assisted capture

## Capture channels

- [ ] Define receipt-photo ingestion.
- [ ] Define invoice-PDF ingestion.
- [ ] Define email-attachment ingestion.
- [ ] Define forwarded-email ingestion.
- [ ] Define mobile upload.
- [ ] Define Telegram upload.
- [ ] Define batch upload.
- [ ] Define scanner/import-folder ingestion if useful.
- [ ] Assign a stable ingestion identifier.
- [x] Preserve the original file for native Accounting documents and expenses.
- [x] Preserve source metadata for reconstructed Accounting attachments.
- [x] Detect duplicate incoming electronic invoices.
- [x] Detect malformed or unreadable incoming electronic invoices.
- [x] Detect unsupported incoming electronic-invoice formats.
- [ ] Confirm upload completion to the originating channel.

## Document understanding

- [ ] Extract supplier identity.
- [ ] Extract invoice number.
- [ ] Extract document date.
- [ ] Extract due date.
- [ ] Extract currency.
- [ ] Extract total.
- [ ] Extract untaxed amount.
- [ ] Extract taxes.
- [ ] Extract line items.
- [ ] Extract payment details.
- [ ] Extract VAT identifiers.
- [ ] Detect credit notes.
- [ ] Detect duplicates.
- [ ] Match existing suppliers.
- [ ] Propose new supplier creation when necessary.
- [ ] Gather previous supplier treatment.
- [ ] Gather related emails.
- [ ] Gather related bank transactions.
- [ ] Gather calendar/trip context.
- [ ] Gather project context.
- [ ] Gather company context.
- [ ] Gather existing accounting policy.
- [ ] Propose account and tax treatment.
- [ ] Propose analytic allocation.
- [ ] Propose project/trip/campaign links.
- [ ] Expose uncertainty by field.
- [ ] Explain material proposals.
- [ ] Distinguish extraction confidence from accounting confidence.
- [ ] Distinguish missing evidence from ambiguous evidence.

## Draft creation and review

- [x] Create draft vendor bills through standard accounting records.
- [x] Create draft expenses through standard expense records.
- [x] Attach the original document.
- [ ] Add concise factual review notes.
- [ ] Avoid exposing raw private reasoning in chatter.
- [ ] Identify blocking errors.
- [ ] Identify non-blocking warnings.
- [ ] Prepare supplier-correction requests when appropriate.
- [ ] Prepare one clear human decision when needed.
- [x] Never silently post accounting by default.
- [x] Never silently pay.
- [x] Never silently delete.
- [x] Never silently reconcile.
- [ ] Record human changes to AI proposals.
- [ ] Use corrected records as evaluation feedback.
- [ ] Create a document-processing evaluation dataset.
- [ ] Measure extraction accuracy.
- [ ] Measure field correction rate.
- [ ] Measure accounting-treatment correction rate.
- [ ] Measure end-to-end human time.
- [ ] Measure cost per processed document.
- [ ] Define acceptable thresholds before broader automation.

## Milestone 15 exit criteria

- [ ] Receipts and invoices can enter through low-friction channels.
- [ ] Originals and provenance are preserved.
- [ ] Draft Odoo records are accurate enough for efficient review.
- [ ] Uncertainty is explicit.
- [ ] No posting or payment occurs outside approved policies.
- [ ] Quality and cost are measured continuously.

---

# 16. HR and TESE payroll workflow

## HR foundation

- [ ] Define the employee master record.
- [ ] Define employment-version/history records.
- [ ] Define contract and working-arrangement context.
- [ ] Define confidential HR document access.
- [ ] Define manager access.
- [ ] Define accountant access.
- [ ] Define agent access.
- [ ] Define employee onboarding workflow.
- [ ] Define employee offboarding workflow.
- [ ] Define leave/absence requirements.
- [ ] Define expense reimbursement relationship.
- [ ] Define equipment and access provisioning.
- [ ] Define HR evidence retention.
- [ ] Test strict HR privacy.

## TESE workflow

- [ ] Preserve TESE as the source of declared payroll values.
- [ ] Do not independently invent payroll calculations.
- [ ] Model the monthly TESE payroll cycle.
- [ ] Link payroll periods to employees.
- [ ] Capture declared gross salary.
- [ ] Capture net salary.
- [ ] Capture employee contributions.
- [ ] Capture employer contributions.
- [ ] Capture withholding tax.
- [ ] Capture TESE settlement values.
- [ ] Attach the TESE document.
- [ ] Prepare standard payroll journal entries.
- [ ] Link salary-payment bank transactions.
- [ ] Link TESE-payment bank transactions.
- [ ] Track payment timing differences.
- [ ] Track outstanding payroll liabilities.
- [ ] Detect duplicate payroll periods.
- [ ] Detect conflicting declared values.
- [ ] Detect missing documents.
- [ ] Detect missing payments.
- [ ] Detect incorrect employee/company linkage.
- [ ] Expose readiness checks before posting.
- [ ] Expose blocking errors.
- [ ] Expose non-blocking warnings.
- [ ] Require explicit validation before posting.
- [ ] Preserve audit history.
- [ ] Support corrections through proper accounting entries.
- [ ] Test the existing November-to-May historical cases.
- [ ] Test delayed TESE direct debits.
- [ ] Test salary timing differences.
- [ ] Test the January 2026 duplicate-net correction case.
- [ ] Compare results with production.
- [ ] Obtain accountant validation.

## Milestone 16 exit criteria

- [ ] Employee and payroll data are appropriately private.
- [ ] TESE values remain authoritative.
- [ ] Payroll entries and payments are traceable.
- [ ] Historical edge cases are covered by tests.
- [ ] Accountant acceptance is documented.

---

# 17. Projects, tasks, activities and operational state

## Project taxonomy

- [ ] Define the difference between:
  - [ ] Legal-entity project
  - [ ] Product project
  - [ ] Operational project
  - [ ] Client project
  - [ ] Campaign
  - [ ] Trip
  - [ ] Administrative process
  - [ ] Recurring process
  - [ ] Research
  - [ ] Archive
- [ ] Define when a project should exist.
- [ ] Define when a task should exist.
- [ ] Define when an activity should exist.
- [ ] Define when a note or chatter message is sufficient.
- [ ] Define when GitHub owns the implementation item.
- [ ] Define when Odoo tracks only the business milestone.
- [ ] Reduce duplicated work items across systems.

## Task semantics

- [ ] Define task states.
- [ ] Separate stage from completion.
- [ ] Separate waiting from completion.
- [ ] Separate approval from completion.
- [ ] Separate archived from completed.
- [ ] Add explicit blocking reason.
- [ ] Add expected event.
- [ ] Add completion detector.
- [ ] Add downstream dependencies.
- [ ] Add escalation policy.
- [ ] Add owning agent.
- [ ] Add accountable human.
- [ ] Add evidence requirements.
- [ ] Add next-action semantics.
- [ ] Add urgency policy.
- [ ] Add deadline policy.
- [ ] Add review policy.
- [ ] Add stale-item policy.
- [ ] Define automatic state-transition boundaries.
- [ ] Define which transitions require approval.
- [ ] Log machine-driven state changes.
- [ ] Explain why a task changed state.
- [ ] Prevent tasks from being completed merely because an outbound message was sent.
- [ ] Detect when an expected reply arrives.
- [ ] Detect when downstream work becomes unblocked.
- [ ] Create new work only when it represents durable operational state.

## Activities and prepared decisions

- [ ] Redefine activities as high-signal human interventions.
- [ ] Prevent agents from creating vague reminder activities.
- [ ] Require each activity to explain:
  - [ ] What happened
  - [ ] Why it matters
  - [ ] Recommended action
  - [ ] Consequence of approval
  - [ ] Consequence of delay
  - [ ] Supporting evidence
- [ ] Support approve.
- [ ] Support edit then approve.
- [ ] Support reject.
- [ ] Support request another iteration.
- [ ] Support delegate.
- [ ] Support defer with a reason.
- [ ] Record feedback for the responsible agent.
- [ ] Define notification policy separately from activity assignment.
- [ ] Allow the Executive Assistant to choose notification timing and channel.
- [ ] Prevent duplicate notifications across Odoo, email and Telegram.

## Project hygiene

- [ ] Audit existing projects.
- [ ] Audit existing task stages.
- [ ] Audit stale in-progress tasks.
- [ ] Audit excessive urgency.
- [ ] Audit missing ownership.
- [ ] Audit ambiguous completion.
- [ ] Audit duplicate tasks.
- [ ] Audit strategic ideas mixed with operational work.
- [ ] Audit research mixed with committed delivery.
- [ ] Archive or reclassify noise.
- [ ] Establish recurring project-hygiene reviews.
- [ ] Measure:
  - [ ] Stale tasks
  - [ ] Tasks without owner
  - [ ] Tasks without next action
  - [ ] Tasks blocked without expected event
  - [ ] Overdue decisions
  - [ ] Unexplained urgency
  - [ ] Agent-created noise

## Milestone 17 exit criteria

- [ ] Project state expresses reality rather than administrative activity.
- [ ] Blocked work identifies what it awaits.
- [ ] Activities represent useful human interventions.
- [ ] Agent updates are attributable.
- [ ] Existing project noise has been reduced.

---

# 18. Email, calendar, Drive and external-context integration

## Email

- [ ] Ingest relevant incoming email events.
- [ ] Preserve thread identity.
- [ ] Identify sender and recipients.
- [ ] Identify relevant company.
- [ ] Identify relevant partner.
- [ ] Identify relevant project.
- [ ] Identify relevant task.
- [ ] Identify expected-event matches.
- [ ] Link email evidence without unnecessary duplication.
- [ ] Detect replies that complete waiting states.
- [ ] Detect new commitments.
- [ ] Detect deadlines.
- [ ] Detect documents.
- [ ] Detect accounting implications.
- [ ] Prepare replies through the appropriate specialist agent.
- [ ] Require approval according to communication policy.
- [ ] Record sent-message references.
- [ ] Avoid sending duplicate replies.
- [ ] Respect private-email boundaries.
- [ ] Define which mailboxes agents may inspect.
- [ ] Define retention and deletion expectations.

## Calendar

- [ ] Link calendar events to Odoo projects, trips and partners.
- [ ] Detect completed meetings.
- [ ] Detect missed or changed commitments.
- [ ] Extract agreed follow-up where supported by evidence.
- [ ] Prepare next actions.
- [ ] Preserve Calendar as the commitment source of truth.
- [ ] Avoid duplicating full calendar state unnecessarily.
- [ ] Define scheduling approval policies.
- [ ] Define private-calendar boundaries.

## Google Drive and documents

- [ ] Link external documents to relevant Odoo records.
- [ ] Preserve external document identity.
- [ ] Preserve version/reference metadata.
- [ ] Distinguish accounting evidence from general project documents.
- [ ] Respect Drive permissions.
- [ ] Avoid broadening access through Odoo links.
- [ ] Detect missing or inaccessible evidence.
- [ ] Define when a durable copy must be retained inside the accounting system.
- [ ] Define document lifecycle and archival expectations.

## USL Media reference workflow

- [ ] Model the complete USL Media setup process.
- [ ] Link historical emails and documents.
- [ ] Represent bank-account applications.
- [ ] Represent waiting states.
- [ ] Represent partner confirmations.
- [ ] Represent capital and incorporation milestones.
- [ ] Represent intercompany setup.
- [ ] Detect new partner replies.
- [ ] Close only the exact waiting step satisfied by evidence.
- [ ] Unblock downstream tasks.
- [ ] Prepare the next actionable decision.
- [ ] Validate that the whole process can be understood from one project view.

## Milestone 18 exit criteria

- [ ] Relevant external communications update operational state.
- [ ] Email is evidence, not the project tracker.
- [ ] Calendar and Drive remain authoritative in their domains.
- [ ] Privacy boundaries are preserved.
- [ ] USL Media demonstrates end-to-end event-driven progression.

---

# 19. Agent event and execution foundation

## Agent registry

- [ ] Create a registry of agents.
- [ ] Define agent name.
- [ ] Define domain.
- [ ] Define owner.
- [ ] Define mandate.
- [ ] Define allowed companies.
- [ ] Define allowed capabilities.
- [ ] Define external integrations.
- [ ] Define approval policy.
- [ ] Define notification policy.
- [ ] Define budget.
- [ ] Define model/provider independence requirements.
- [ ] Define current status.
- [ ] Define version.
- [ ] Define evaluation dataset.
- [ ] Define rollback/suspension procedure.

## Event model

- [ ] Define canonical event envelope.
- [ ] Define unique event identity.
- [ ] Define source.
- [ ] Define event type.
- [ ] Define occurred time.
- [ ] Define received time.
- [ ] Define company context.
- [ ] Define actor.
- [ ] Define related objects.
- [ ] Define payload reference.
- [ ] Define privacy classification.
- [ ] Define correlation identifier.
- [ ] Define causation identifier.
- [ ] Define replay semantics.
- [ ] Define duplicate handling.
- [ ] Define processing status.
- [ ] Define failure status.
- [ ] Define retry status.
- [ ] Define dead-letter handling.
- [ ] Define retention.

## Execution model

- [ ] Define an agent execution record.
- [ ] Record triggering event.
- [ ] Record gathered context references.
- [ ] Record selected capability.
- [ ] Record policy version.
- [ ] Record agent version.
- [ ] Record model/provider where relevant.
- [ ] Record prompt or instruction version where appropriate.
- [ ] Record proposed actions.
- [ ] Record executed actions.
- [ ] Record failed actions.
- [ ] Record approvals.
- [ ] Record outputs.
- [ ] Record costs.
- [ ] Record latency.
- [ ] Record confidence.
- [ ] Record final state.
- [ ] Avoid storing unnecessary private chain-of-thought.
- [ ] Store concise, reviewable decision evidence instead.

## Reliability

- [ ] Ensure every execution can be retried safely.
- [ ] Ensure repeated events do not duplicate consequences.
- [ ] Ensure partially completed work can resume.
- [ ] Ensure external side effects have stable idempotency references.
- [ ] Ensure timeouts leave inspectable state.
- [ ] Ensure unavailable AI providers do not corrupt Odoo.
- [ ] Ensure unavailable external systems do not falsely complete tasks.
- [ ] Ensure human overrides take precedence.
- [ ] Ensure emergency automation suspension works.
- [ ] Build replay tests.
- [ ] Build duplicate-event tests.
- [ ] Build provider-outage tests.
- [ ] Build partial-failure tests.
- [ ] Build stale-context tests.

## Milestone 19 exit criteria

- [ ] Agents and executions are individually attributable.
- [ ] Events are durable and replay-safe.
- [ ] Partial failures do not disappear.
- [ ] Odoo remains correct when agents or providers are unavailable.
- [ ] Human intervention and override are explicit.

---

# 20. MCP and domain capability layer

- [ ] Inventory existing low-level MCP operations.
- [ ] Classify operations by domain.
- [ ] Identify unsafe generic write operations.
- [ ] Identify missing read capabilities.
- [ ] Identify missing evidence retrieval.
- [ ] Define domain-level capabilities.
- [ ] Start with read-only capabilities.
- [ ] Add draft/preparation capabilities.
- [ ] Add controlled execution capabilities only after evaluation.
- [ ] Define capability input contracts.
- [ ] Define capability output contracts.
- [ ] Define capability permission requirements.
- [ ] Define company-context requirements.
- [ ] Define idempotency requirements.
- [ ] Define validation requirements.
- [ ] Define audit requirements.
- [ ] Define failure semantics.
- [ ] Define dry-run mode.
- [ ] Define preview mode.
- [ ] Define approval-token or confirmation semantics.
- [ ] Define capability versioning.
- [ ] Define deprecation.
- [ ] Define compatibility testing.
- [ ] Create initial capabilities:
  - [ ] Inspect accounting document
  - [ ] Prepare supplier bill review
  - [ ] Prepare expense review
  - [ ] Process platform payout draft
  - [ ] Prepare payroll period
  - [ ] Inspect project status
  - [ ] Resolve expected project event
  - [ ] Prepare next project action
  - [ ] Prepare accountant review package
  - [ ] Link email to operational context
  - [ ] Prepare reply
  - [ ] Inspect bank reconciliation candidates
- [ ] Keep generic CRUD available only where justified and strongly permissioned.
- [ ] Add contract tests for every capability.
- [ ] Add permission tests for every capability.
- [ ] Add idempotency tests for every capability.
- [ ] Add audit tests for every capability.
- [ ] Add realistic agent evaluations.
- [ ] Generate capability documentation automatically.
- [ ] Make capabilities discoverable by agents.
- [ ] Avoid coupling capability semantics to one agent framework.

## Milestone 20 exit criteria

- [ ] Agents use meaningful domain operations rather than uncontrolled CRUD.
- [ ] Capabilities are versioned, testable and permissioned.
- [ ] Sensitive actions support preview and approval.
- [ ] MCP remains independent of a single orchestration framework.

---

# 21. Specialist agents and prepared-decision workflows

## Executive Assistant

- [ ] Define responsibility for Valentin’s attention.
- [ ] Define interruption thresholds.
- [ ] Define urgency policy.
- [ ] Define notification channels.
- [ ] Define batching policy.
- [ ] Define quiet periods.
- [ ] Define escalation policy.
- [ ] Define duplicate-notification prevention.
- [ ] Require prepared-decision format.
- [ ] Test notification usefulness.
- [ ] Measure rejected or unnecessary interruptions.

## Email Intake Agent

- [ ] Classify incoming emails.
- [ ] Link relevant operational context.
- [ ] Detect expected events.
- [ ] Extract commitments and deadlines.
- [ ] Hand off domain implications.
- [ ] Avoid acting beyond intake mandate.
- [ ] Record uncertain links for review.

## Finance Agent

- [ ] Monitor accounting hygiene.
- [ ] Inspect new financial documents.
- [ ] Detect missing evidence.
- [ ] Detect unusual entries.
- [ ] Prepare reconciliation suggestions.
- [ ] Prepare accountant questions.
- [ ] Respect posting/payment approval policy.

## Project Agent

- [ ] Maintain task and dependency health.
- [ ] Detect resolved blockers.
- [ ] Detect stale waiting states.
- [ ] Prepare next actions.
- [ ] Avoid manufacturing unnecessary tasks.
- [ ] Maintain project summaries.

## Product Agent

- [ ] Link product conversations to projects.
- [ ] Gather prior decisions.
- [ ] Prepare product specifications.
- [ ] Prepare GitHub work items.
- [ ] Track business milestones.
- [ ] Preserve GitHub as engineering truth.

## Creator Operations Agent

- [ ] Link trips, collaborations, shoots and assets.
- [ ] Track publication state.
- [ ] Track platform metrics.
- [ ] Track monetization.
- [ ] Detect missing operational steps.
- [ ] Prepare performance insights.

## Human feedback

- [ ] Allow approve.
- [ ] Allow reject.
- [ ] Allow edit.
- [ ] Allow request-more-context.
- [ ] Allow change-policy.
- [ ] Attribute feedback to the relevant agent and workflow.
- [ ] Measure approval rate.
- [ ] Measure correction rate.
- [ ] Measure escalation quality.
- [ ] Measure time saved.
- [ ] Prevent learning from one-off corrections without policy review where risky.

## Milestone 21 exit criteria

- [ ] Specialist agents remain within bounded mandates.
- [ ] Valentin receives prepared decisions rather than raw tasks.
- [ ] Agent usefulness is measured.
- [ ] Feedback is attributable and improves controlled policies.

---

# 22. USL-specific accounting and operational modules

## Platform payout sessions

- [ ] Formalize the approved accounting specification.
- [ ] Model platform payout sessions without parallel accounting truth.
- [ ] Support OnlyFans.
- [ ] Support JustForFans.
- [ ] Support DarkFans.
- [ ] Support additional platforms through configuration where appropriate.
- [ ] Capture source-currency payout data.
- [ ] Capture gross revenue.
- [ ] Capture commission.
- [ ] Capture net payout.
- [ ] Capture payout date.
- [ ] Capture accounting period.
- [ ] Capture settlement currency.
- [ ] Capture evidence.
- [ ] Prepare customer invoice.
- [ ] Prepare supplier commission bill.
- [ ] Prepare compensation entry.
- [ ] Link bank statement line.
- [ ] Track reconciliation.
- [ ] Support zero-payout periods where relevant.
- [ ] Support multiple payouts per month.
- [ ] Support corrections.
- [ ] Support historical import.
- [ ] Prevent duplicate session creation.
- [ ] Prevent duplicate accounting generation.
- [ ] Validate tax treatment per platform.
- [ ] Validate multi-currency handling.
- [ ] Compare outputs with production.
- [ ] Obtain accountant acceptance.

## Accounting Hygiene

- [x] Define recurring accounting-review periods through the monthly, quarterly and annual closing workspaces.
- [x] Collect incomplete expenses in the company-scoped Accounting Hygiene workbench.
- [x] Collect incomplete vendor bills and other draft business documents.
- [x] Collect missing vendor-document and expense-receipt evidence.
- [x] Collect unreconciled bank transactions with a direct Bank Matching route.
- [ ] Collect platform payout issues.
- [x] Collect payroll status through the current period controls, including the explicit external-payroll boundary when payroll is not installed.
- [x] Collect VAT/declaration readiness through the current period and declaration workspaces.
- [x] Collect unusual aggregate account balances from the current close with direct journal-item drilldown and configurable natural-balance policies.
- [x] Collect draft document and expense work older than 30 days.
- [x] Separate automatic checks and refreshes from named professional approvals.
- [x] Separate questions and decisions prepared for Prosper from those prepared for Valentin.
- [x] Produce a concise company-scoped hygiene and readiness summary.
- [x] Produce direct links to native records, closing controls, discrepancies and import evidence.
- [x] Track completion through native record state, period controls and durable review decisions.
- [x] Avoid noisy chatter or duplicate activities; Hygiene is a queryable workbench and creates no notification stream.

## Intercompany and USL Media

- [ ] Define legal entity boundaries.
- [ ] Define ownership relationships.
- [ ] Define management-fee workflows.
- [ ] Define travel recharge workflows.
- [ ] Define production-service workflows.
- [ ] Define shared-cost allocation.
- [ ] Define intercompany invoices.
- [ ] Define intercompany payments.
- [ ] Define analytic treatment.
- [ ] Define documentary evidence.
- [ ] Define approval.
- [ ] Define reconciliation.
- [ ] Test asymmetric company permissions.
- [ ] Test accountant visibility.
- [ ] Test consolidation/reporting needs without inventing unsupported legal meaning.

## GBC

- [ ] Separate POD and stocked products.
- [ ] Track product costs.
- [ ] Track packaging costs.
- [ ] Track fulfilment costs.
- [ ] Track stock movement.
- [ ] Track sales channels.
- [ ] Track VAT context.
- [ ] Track OSS-relevant information where applicable.
- [ ] Calculate product-level margin.
- [ ] Calculate order-level margin.
- [ ] Link operational evidence to accounting.

## Milestone 22 exit criteria

- [ ] Current custom workflows are reproduced or deliberately improved.
- [ ] Standard Odoo accounting records remain authoritative.
- [ ] Multi-company boundaries are explicit.
- [ ] Historical and edge cases are tested.
- [ ] Accountant acceptance is recorded.

---

# 23. Creator operations and Yoshi internal foundation

- [ ] Define the creator-business domain model.
- [ ] Define creator profiles.
- [ ] Define collaborators.
- [ ] Define trips.
- [ ] Define shoots.
- [ ] Define scenes/content units.
- [ ] Define assets.
- [ ] Define edits.
- [ ] Define publications.
- [ ] Define campaigns.
- [ ] Define platforms.
- [ ] Define platform accounts.
- [ ] Define revenue events.
- [ ] Define audience metrics.
- [ ] Define expenses.
- [ ] Define rights and consent metadata where required.
- [ ] Define profitability links.
- [ ] Connect trips to expenses.
- [ ] Connect trips to collaborations.
- [ ] Connect collaborations to content.
- [ ] Connect content to publications.
- [ ] Connect publications to metrics.
- [ ] Connect content to direct monetization.
- [ ] Connect payouts to accounting.
- [ ] Preserve the distinction between:
  - [ ] Direct attribution
  - [ ] Campaign-window attribution
  - [ ] Manual attribution
  - [ ] Inferred attribution
- [ ] Represent attribution confidence.
- [ ] Represent attribution evidence.
- [ ] Allow human correction.
- [ ] Avoid pretending uncertain attribution is fact.
- [ ] Import platform metrics automatically where permitted.
- [ ] Detect missing or stale metrics.
- [ ] Track unpublished assets.
- [ ] Track under-monetized assets.
- [ ] Produce trip profitability.
- [ ] Produce collaboration profitability.
- [ ] Produce growth-versus-revenue analysis.
- [ ] Produce content-lifecycle analysis.
- [ ] Produce actionable recommendations.
- [ ] Build the internal dashboard: “What actually made money?”
- [ ] Build the internal dashboard: “What should happen next?”
- [ ] Use USL/SBFH as the proving ground.
- [ ] Defer generic SaaS onboarding until internal workflows are validated.

## Milestone 23 exit criteria

- [ ] Trips, collabs, content, metrics, revenue and accounting are connected.
- [ ] Attribution remains transparent and editable.
- [ ] Internal dashboards answer concrete management questions.
- [ ] The domain model has been proven through real USL use.

---

# 24. Accountant collaboration and compliance review

- [x] Define the scoped read-only accountant user role.
- [x] Define visible companies.
- [x] Define visible journals.
- [x] Define visible accounting documents.
- [x] Define visible bank context.
- [x] Define visible attachments.
- [x] Define visible chatter categories.
- [ ] Define visible AI review notes.
- [x] Hide irrelevant creator content from the scoped Accounting role.
- [x] Hide private personal material from the scoped Accounting role.
- [ ] Hide irrelevant HR information.
- [x] Hide raw agent scratch work from normal Accounting navigation.
- [x] Test access with the scoped read-only accountant role.
- [x] Confirm the accountant can:
  - [x] Open invoices
  - [x] Open vendor bills
  - [x] Review journal entries
  - [x] Review taxes
  - [x] Review reconciliation state
  - [x] Review evidence
  - [x] Generate reports
  - [x] Export permitted test FEC evidence
  - [ ] Leave questions tied to records
- [ ] Define accountant-question workflows.
- [ ] Route questions to the appropriate human or agent.
- [ ] Preserve resolution history.
- [ ] Conduct first accountant review.
- [ ] Record every concern.
- [ ] Prioritize compliance and correctness concerns.
- [ ] Resolve critical findings.
- [ ] Conduct second accountant review using updated data.
- [ ] Conduct a final pre-migration review.
- [x] Verify electronic-invoicing obligations and dates.
- [ ] Select the approved external platform strategy.
- [x] Define and safely test the inactive inbound electronic-invoice flow.
- [ ] Define outbound electronic-invoice flow.
- [x] Define status and evidence preservation for received test documents.
- [x] Ensure non-production cannot send legal invoices through the live network.
- [ ] Verify the applicable cash-register/certification perimeter.
- [ ] Obtain specialist advice where uncertainty remains.
- [x] Document what is software-tested versus professionally accepted.
- [x] Do not claim legal certification without appropriate evidence.

## Milestone 24 exit criteria

- [x] Scoped read-only accountant can work interactively with appropriate visibility.
- [ ] FEC and tax outputs have been reviewed.
- [ ] Compliance uncertainties have owners.
- [ ] E-invoicing integration strategy is approved.
- [x] Private information remains inaccessible to the scoped Accounting role.

---

# 25. Performance, efficiency and scalability

- [ ] Establish representative workload profiles.
- [ ] Measure baseline Odoo performance before customization.
- [ ] Measure common page loads.
- [ ] Measure accounting report generation.
- [ ] Measure document ingestion.
- [ ] Measure bank synchronization.
- [ ] Measure agent context gathering.
- [ ] Measure background-job throughput.
- [ ] Measure database growth.
- [ ] Identify slow queries.
- [ ] Identify excessive ORM calls.
- [ ] Identify N+1 patterns.
- [ ] Identify inefficient computed fields.
- [ ] Identify excessive chatter or tracking.
- [ ] Identify oversized attachments.
- [ ] Identify expensive agent loops.
- [ ] Define performance budgets for critical flows.
- [ ] Optimize only after measurement.
- [ ] Preserve correctness while optimizing.
- [ ] Add regression benchmarks for critical flows.
- [ ] Configure production worker capacity.
- [ ] Configure cron capacity.
- [ ] Configure memory and timeout policies.
- [ ] Configure connection-pool expectations.
- [ ] Configure attachment delivery.
- [ ] Configure caching only where safe.
- [ ] Define horizontal/vertical scaling triggers.
- [ ] Define database scaling triggers.
- [ ] Define archival triggers.
- [ ] Load test realistic concurrent users and agents.
- [ ] Test behaviour under provider slowness.
- [ ] Test behaviour under bank API slowness.
- [ ] Test behaviour under AI API slowness.
- [ ] Test behaviour under database pressure.
- [ ] Ensure graceful degradation.
- [ ] Track infrastructure cost against workload.
- [ ] Track AI cost against value delivered.

## Milestone 25 exit criteria

- [ ] Critical workloads meet documented performance budgets.
- [ ] Capacity assumptions are supported by measurement.
- [ ] Provider slowness does not corrupt workflows.
- [ ] Cost and performance regressions are visible.

---

# 26. Migration tooling and rehearsals

## Migration design

- [ ] Define the authoritative production cut-off.
- [x] Define source backup requirements.
- [x] Define a fail-closed portable production-candidate format and independent
  fingerprint approval workflow (integrated into `19-usl`).
- [x] Define external-Pocket-ID preflight, fresh-volume stage, configuration,
  gate, pre-admission reset and permanent admission semantics (integrated into
  `19-usl`).
- [x] Define version compatibility requirements.
- [x] Define Enterprise-to-Community Accounting transformation rules.
- [x] Define Studio Accounting-field and view transformation rules.
- [x] Define Accounting module replacement order.
- [x] Define custom Accounting-field preservation.
- [x] Define external-identifier preservation for the Accounting scope.
- [x] Define attachment preservation.
- [x] Define Accounting chatter/attachment preservation.
- [x] Define historical-report-definition preservation.
- [x] Define Accounting user mapping.
- [x] Define Accounting permission mapping.
- [ ] Define integration credential rotation.
- [ ] Define bank synchronization handover.
- [ ] Define email alias handover.
- [ ] Define e-invoicing handover.
- [ ] Define agent activation timing.
- [ ] Define rollback limitations.

## Automated migration checks

- [x] Count records by critical Accounting model before and after.
- [x] Compare posted journal-entry counts.
- [x] Compare journal totals.
- [x] Compare partner balances.
- [x] Compare tax balances.
- [x] Compare bank balances.
- [x] Compare unreconciled-item counts and reconciliation relationships.
- [x] Compare invoices by status.
- [x] Compare vendor bills by status.
- [x] Compare Accounting attachments.
- [x] Compare projects and tasks.
- [ ] Compare active users.
- [x] Compare Accounting companies.
- [x] Compare analytic records.
- [x] Compare custom Accounting workflow records.
- [x] Compare FEC output.
- [x] Compare golden Accounting reports.
- [x] Detect orphaned Accounting references.
- [x] Detect missing Accounting external identifiers.
- [x] Detect unsupported Accounting models.
- [x] Detect missing Accounting files.
- [ ] Produce a signed migration report.

## Rehearsals

- [x] Rehearsal 1: early feasibility backup.
- [x] Document failures and manual interventions.
- [x] Automate repeatable fixes.
- [x] Rehearsal 2: latest supplied production backup.
- [x] Measure total and per-stage rehearsal duration.
- [ ] Measure service interruption.
- [x] Run accounting comparisons.
- [x] Run Accounting workflow smoke tests.
- [x] Run Accounting permission tests.
- [x] Run integration-neutralization checks.
- [ ] Conduct accountant review.
- [ ] Rehearsal 3: full dress rehearsal.
- [ ] Use the intended production procedure.
- [ ] Use intended deployment identities.
- [ ] Use intended backup and restore process.
- [ ] Simulate go/no-go decision.
- [ ] Simulate rollback or abort before activation.
- [ ] Resolve all migration blockers.
- [ ] Freeze the cutover runbook.

Historical reconstruction results do not close final qualification. The
interruption-window measurement and intended-host dress rehearsal must use the
current release and fresh frozen source.

## Milestone 26 exit criteria

- [x] Accounting migration is scripted and repeatable.
- [x] Accounting record and report comparisons are automated.
- [ ] Full rehearsal passes without improvised rescue.
- [ ] Duration and downtime are known.
- [ ] Accountant accepts the rehearsal output.
- [ ] Rollback limitations are understood.

---

# 27. Production-readiness review

- [ ] Complete architectural review.
- [x] Complete Accounting v1 product-scope review.
- [x] Complete engineering Accounting review; professional acceptance remains
  deferred.
- [ ] Complete security review.
- [ ] Complete privacy review.
- [ ] Complete infrastructure review.
- [ ] Complete backup and recovery review.
- [ ] Complete observability review.
- [ ] Complete performance review.
- [x] Complete Accounting migration review.
- [ ] Complete integration review.
- [ ] Complete agent-permission review.
- [x] Complete scoped accountant-access review.
- [ ] Review all open critical risks.
- [x] Review all accepted Accounting differences from Odoo Online.
- [x] Review all upstream core modifications in the Accounting replay scope.
- [x] Review all retained Accounting dependencies and pins.
- [ ] Review all known manual operations.
- [ ] Review all untested failure modes.
- [ ] Verify current backup.
- [ ] Verify recovery drill.
- [ ] Verify production secrets.
- [ ] Verify DNS and certificates.
- [ ] Verify alert destinations.
- [ ] Verify incident contacts.
- [ ] Verify cutover staffing.
- [ ] Verify accountant availability.
- [ ] Verify rollback/abort point.
- [ ] Produce the final go/no-go package.
- [ ] Obtain Technical Architect recommendation.
- [ ] Obtain Product Manager recommendation.
- [ ] Obtain accountant acceptance.
- [ ] Obtain Valentin’s final decision.

## Milestone 27 exit criteria

- [ ] No unresolved accounting-, security-, privacy- or data-loss-critical blocker.
- [ ] Remaining risks are explicitly accepted.
- [ ] Recovery has been demonstrated.
- [ ] Cutover has named owners and decision points.
- [ ] Final migration authority is clear.

---

# 28. Production cutover

Execution follows the phased readiness register. Human admission, backup
activation, outbound mail, inbound aliases, bank-statement ingestion,
Paperless mail/webhooks and regulatory services are separate gates; admission
must not mass-enable reconstructed scheduled actions or outbound queues.

- [ ] Announce the change window.
- [ ] Freeze relevant Odoo Online writes.
- [ ] Freeze structural configuration changes.
- [ ] Capture final source backup.
- [ ] Verify backup integrity.
- [ ] Record source version and timestamp.
- [ ] Execute the migration.
- [ ] Deploy exact approved release.
- [ ] Execute database transformations.
- [ ] Restore and verify filestore.
- [ ] Run automated migration checks.
- [ ] Run accounting golden-report checks.
- [ ] Run FEC comparison.
- [ ] Run user and permission checks.
- [ ] Run core workflow smoke tests.
- [ ] Run expense/invoice capture smoke test.
- [ ] Run bank-feed smoke test.
- [ ] Run email-ingestion smoke test.
- [ ] Run project-state smoke test.
- [ ] Verify external integrations remain disabled until approved.
- [ ] Obtain technical go/no-go.
- [ ] Obtain accounting go/no-go.
- [ ] Activate human access.
- [ ] Activate inbound email.
- [ ] Activate banking ingestion.
- [ ] Activate external document integrations.
- [ ] Activate approved e-invoicing integration.
- [ ] Activate read-only agent functions.
- [ ] Activate draft/preparation agent functions.
- [ ] Delay sensitive autonomous actions until post-cutover stability is proven.
- [ ] Monitor errors continuously.
- [ ] Monitor bank-feed freshness.
- [ ] Monitor email processing.
- [ ] Monitor background jobs.
- [ ] Monitor accounting inconsistencies.
- [ ] Record all cutover deviations.
- [ ] Keep Odoo Online preserved in accordance with the rollback/archive plan.
- [ ] Communicate successful cutover.
- [ ] Start heightened-support period.

## Milestone 28 exit criteria

- [ ] Production is operational.
- [ ] Accounting and critical reports are verified.
- [ ] Bank and email ingestion are functional.
- [ ] Humans can work.
- [ ] Agent actions remain within approved activation levels.
- [ ] No critical unexplained discrepancy exists.

---

# 29. Stabilization after migration

- [ ] Run daily technical review during the initial stability period.
- [ ] Run daily accounting-anomaly review.
- [ ] Run daily failed-agent-action review.
- [ ] Run daily integration-health review.
- [ ] Run daily backup verification.
- [ ] Compare key balances with cutover baseline.
- [ ] Review accountant questions.
- [ ] Review user friction.
- [ ] Review unnecessary notifications.
- [ ] Review failed automations.
- [ ] Review permission issues.
- [ ] Fix critical regressions immediately.
- [ ] Avoid broad feature expansion during stabilization.
- [ ] Record every hotfix.
- [ ] Add regression tests for every production issue.
- [ ] Conduct first post-cutover recovery test.
- [ ] Conduct first month-end close.
- [ ] Conduct first VAT/accounting review.
- [ ] Conduct first accountant-led audit of interactive access.
- [ ] Confirm FEC generation from the new production system.
- [ ] Confirm backup retention.
- [ ] Confirm actual infrastructure cost.
- [ ] Confirm actual AI cost.
- [ ] Confirm actual support burden.
- [ ] End heightened support only after explicit review.

## Milestone 29 exit criteria

- [ ] First accounting cycle closes successfully.
- [ ] Accountant is comfortable with outputs and evidence.
- [ ] Critical integrations are stable.
- [ ] Production incidents have regression coverage.
- [ ] Actual costs and workload are understood.

---

# 30. Continuous upstream compatibility and major-version durability

- [ ] Run scheduled upstream-difference reports.
- [ ] Review upstream security fixes promptly.
- [ ] Integrate upstream bug fixes on a controlled cadence.
- [ ] Run the full suite after every upstream integration.
- [ ] Maintain a list of touched upstream files.
- [ ] Reduce touched upstream files over time.
- [ ] Upstream generic fixes when practical.
- [ ] Contribute reusable fixes to OCA where appropriate.
- [ ] Track OCA branch availability for Odoo 19.
- [ ] Track Odoo 20 development and breaking changes.
- [ ] Create an annual upgrade-readiness assessment.
- [ ] Test custom modules against future Odoo versions when practical.
- [ ] Maintain migration scripts with every schema change.
- [ ] Never postpone all migration work until the next major upgrade.
- [ ] Maintain representative anonymized upgrade fixtures.
- [ ] Test OpenUpgrade or selected upgrade path in advance.
- [ ] Track deprecated APIs.
- [ ] Remove deprecated usage before it becomes blocking.
- [ ] Review abandoned dependencies.
- [ ] Replace or adopt maintenance of critical abandoned modules.
- [ ] Review architecture decisions periodically.
- [ ] Review whether previous core divergences remain necessary.
- [ ] Maintain rollback-independent backups before major upgrades.
- [ ] Re-run accountant parity tests after major upgrades.
- [ ] Re-run security and permission tests after major upgrades.
- [ ] Re-run agent capability tests after major upgrades.

## Milestone 30 exit criteria

- [ ] Upstream divergence is measured and controlled.
- [ ] Major-version upgrades are prepared continuously.
- [ ] Deprecated dependencies are not allowed to accumulate invisibly.
- [ ] Accounting and agent behaviour remain regression-tested across upgrades.

---

# 31. Expand autonomy progressively

## Autonomy level 0 — Observe

- [ ] Agents read permitted data.
- [ ] Agents detect issues.
- [ ] Agents generate reports.
- [ ] Agents cannot alter business state.

## Autonomy level 1 — Recommend

- [ ] Agents propose classifications.
- [ ] Agents propose task updates.
- [ ] Agents propose accounting treatment.
- [ ] Agents propose replies.
- [ ] Humans execute all changes.

## Autonomy level 2 — Prepare drafts

- [ ] Agents create draft records.
- [ ] Agents attach evidence.
- [ ] Agents prepare messages.
- [ ] Agents prepare reconciliation candidates.
- [ ] Humans approve external or posted consequences.

## Autonomy level 3 — Execute reversible internal actions

- [ ] Agents link records.
- [ ] Agents update non-sensitive internal metadata.
- [ ] Agents resolve well-defined waiting states.
- [ ] Agents schedule controlled internal follow-up.
- [ ] Every action remains auditable and reversible.

## Autonomy level 4 — Execute approved bounded workflows

- [ ] Specific agents execute pre-approved low-risk workflows.
- [ ] Policies define exact boundaries.
- [ ] Budgets and thresholds are enforced.
- [ ] Exceptions escalate.
- [ ] Sampling and review continue.

## Autonomy level 5 — Domain responsibility

- [ ] Agents maintain bounded operational domains.
- [ ] Humans review outcomes, exceptions and strategy.
- [ ] Autonomy remains revocable.
- [ ] Audit evidence remains complete.
- [ ] Critical legal, financial and reputational decisions remain appropriately controlled.

## For every autonomy increase

- [ ] Define the business benefit.
- [ ] Define the action scope.
- [ ] Define the failure modes.
- [ ] Define reversal.
- [ ] Define approval thresholds.
- [ ] Define monitoring.
- [ ] Define evaluation metrics.
- [ ] Run shadow mode.
- [ ] Run limited pilot.
- [ ] Review results.
- [ ] Obtain explicit approval.
- [ ] Document the policy change.

---

# 32. Long-term productization readiness

- [ ] Keep internal USL-specific policy separate from reusable product logic.
- [ ] Identify generic modules proven through real use.
- [ ] Identify creator-specific Yoshi modules.
- [ ] Identify infrastructure that should remain internal.
- [ ] Identify configuration currently hard-coded to USL.
- [ ] Convert reusable policy into explicit configuration where valuable.
- [ ] Preserve multi-tenant/productization options without compromising the internal system.
- [ ] Do not prematurely generalize unproven workflows.
- [ ] Document measurable internal value:
  - [ ] Founder time saved
  - [ ] Accountant time saved
  - [ ] Processing accuracy
  - [ ] Error reduction
  - [ ] Faster project progression
  - [ ] Faster product delivery
  - [ ] Better creator profitability insight
- [ ] Build Yoshi from proven creator workflows.
- [ ] Keep the broader automated-organization product deferred until Yoshi and the internal operating model are validated.
- [ ] Review whether Odoo remains an embedded component, deployment option or internal implementation detail for future products.
- [ ] Review licensing and distribution implications before external productization.
- [ ] Separate customer product promises from internal architecture assumptions.

---

# Target state

The Odoo Rebuild reaches its intended target when:

- [ ] Odoo Community is the trusted structured operational and accounting core for USL.
- [ ] USL, USL Media, GBC and future entities are correctly separated and connected.
- [ ] Accounting is accurate, auditable, restorable and accountant-approved.
- [ ] Bank transactions arrive automatically and reconcile through controlled workflows.
- [ ] Receipts and invoices become accurate draft records with minimal human effort.
- [ ] TESE payroll is correctly documented, posted and reconciled.
- [ ] Projects represent real operational state, dependencies and waiting events.
- [ ] Email and external events move work forward automatically.
- [ ] Human activities represent prepared decisions rather than vague tasks.
- [ ] Agents have distinct identities, mandates, permissions and audit trails.
- [ ] MCP exposes safe domain capabilities rather than uncontrolled generic writes.
- [ ] AI improves operations without becoming the source of truth.
- [ ] External side effects are controlled, attributable and retry-safe.
- [ ] Deployments are reproducible.
- [ ] Backups are monitored and regularly restored.
- [ ] Security and privacy boundaries are tested.
- [ ] Production is observable and recoverable.
- [ ] Upstream Odoo divergence is limited, measurable and reconcilable.
- [ ] Major-version upgrades remain realistic.
- [ ] USL can operate with a small human team and highly leveraged specialist agents.
- [ ] The system materially reduces Valentin’s administrative and coordination load.
- [ ] The internal organization can reliably build and deliver products such as Yoshi, Smash and KinkVerse.

- [ ] Devcontainer
- [ ] Docker compose
- [ ]


https://github.com/odoo/odoo/tree/19.0/odoo
https://www.odoo.com/documentation/19.0/administration/on_premise/source.html
