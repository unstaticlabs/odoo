# Odoo Rebuild — Master TODO and Delivery Roadmap

> **Target:** A modern, efficient, trusted, verifiable, AI-native Odoo Community platform for USL (Holding), USL Media (Digital Content Creation monetized mostly through platforms like OnlyFans and Influencer Deals), GBC (eCommerce with Medusa shop), and future entities—covering accounting, banking, expenses, HR, project management, automation, agent collaboration, cloud deployment, backups and controlled migration from Odoo Online.

> **Core constraint:** Extend and compose Odoo rather than creating an irreconcilable fork. Preserve upstream compatibility, standard business semantics, upgradeability and auditability.

## Current Milestone 13 snapshot - 2026-07-22

Detailed status report: `docs/accounting/milestone-13-current-progress-report.md`.

What is complete enough to preserve:

- [x] Isolated source database restore stage for the Odoo Online accounting dump.
- [x] Source dump checksum and private source snapshot generation.
- [x] Clean accounting target reset stage for `odoo_rebuild_accounting_test`.
- [x] Source-traced posted ledger replay into the target validation database.
- [x] Target ledger validation and source/target comparison artifacts for the posted ledger slice.
- [x] Imported source report catalogue preservation.
- [x] Preliminary Odoo evidence views for import runs, discrepancies, source reports, imported reports, assets, deferred schedules and reconciliations.
- [x] Preliminary export wizard for imported report artifacts and FEC TXT generation.
- [x] Diataxis-style user documentation under `docs/users/`.
- [x] Browser-accessible user documentation from Odoo at `/usl/user-docs`.
- [x] Reporting and closing UX target documented from the supplied annual accounts, SIG and tax report samples.
- [x] Accounting development workflow documented to avoid unnecessary full rebuilds and require scoped Conventional Commits.
- [x] Pinned OCA 19.0 accounting/reporting/reconciliation add-ons can be synced locally with `make oca-addons-sync`.
- [x] OCA financial reporting, MIS Builder, reconciliation and bank statement import foundation installed successfully on `odoo_rebuild_accounting_test`.
- [x] Normal Compose Odoo runtime now mounts both `oca-src/` and `oca-addons/` so symlinked OCA modules resolve.
- [x] Local Compose, init and Dev Container Odoo runtimes default to `max_cron_threads = 0` for imported-accounting parity work.

What is not complete:

- [ ] OCA report screens are installed but not yet validated as USL/Odoo Online-equivalent dynamic report workflows.
- [ ] Current PDF/XLSX report exports are not accountant-ready templates.
- [x] OCA Trial Balance no longer fails on duplicate unaffected-earnings accounts after source-traced retained earnings import and empty bootstrap-account archival.
- [x] Imported companies receive a default Odoo report layout so OCA report actions no longer divert users into document-layout setup.
- [x] OCA interactive report launchers now default to the USL benchmark period in posted mode.
- [x] OCA Trial Balance opens through the normal Odoo report viewer and renders imported USL benchmark ledger rows.
- [x] OCA General Ledger, Journal Ledger, VAT Report, Open Items and Aged Partner Balance open with benchmark defaults and render through the normal Odoo report viewer.
- [x] Split OCA Aged Partner Balance into cleaner user-facing Aged Receivable and Aged Payable shortcuts.
- [x] OCA MIS Builder Balance Sheet and Profit and Loss instances are configured for the USL benchmark period and open from the normal Reports and Declarations menu with Preview, Print and Export controls.
- [x] MIS Balance Sheet and Profit and Loss previews render imported benchmark-period values in the browser.
- [x] MIS account-detail expansion works with archived imported accounts that still have posted historical move lines.
- [x] Accounting app entry now targets the accounting dashboard directly.
- [x] Accounting now exposes first-level Review Issues and Reconcile Bank Transactions entries.
- [x] Reconcile Bank Transactions opens the OCA reconciliation kanban workbench with imported bank transactions on `odoo_rebuild_accounting_test`.
- [x] Raw imported report/evidence screens are grouped under Review and Audit > Advanced Audit.
- [ ] Historical bank statement and reconciliation UX is not yet equivalent to the Odoo Online reconciliation workbench.
- [x] OCA `account_reconcile_oca` kanban workbench no longer fails in the Odoo 19 web client after a compatible card override.
- [ ] Customer invoices, vendor bills, refunds and expenses are not yet complete user-facing reconstructed business workflows.
- [ ] French declaration guidance for CFS Pro and Portailpro field entry is not yet implemented.
- [x] FEC test export is available through the custom reviewed export path for accountant-review users.
- [x] Standard French FEC wizard can be opened by accounting review users in forced test mode without granting final lock-affecting FEC permissions.
- [x] Settings behavior with imported cash-basis taxes is diagnosed and normalized during import without changing tax definitions.
- [ ] Menu grouping needs a final CEO/accountant workflow polish pass after report and document workflows are complete.
- [ ] Accountant review and formal acceptance remain pending.

# 0. Programme governance and invariants

- [x] Create `ROADMAP.md` as the canonical programme backlog.
- [x] Create `ARCHITECTURE.md` describing the intended system boundaries.
- [x] Create `CONTRIBUTING.md` for human and AI contributors.
- [ ] Create `SECURITY.md` for vulnerability reporting and security expectations.
- [ ] Create `UPSTREAM.md` documenting the relationship with `odoo/odoo`.
- [x] Create `DECISIONS/` for Architecture Decision Records.
- [x] Create `docs/product/` for approved functional requirements.
- [x] Create `docs/operations/` for deployment and recovery runbooks.
- [x] Create `docs/accounting/` for accounting invariants and parity evidence.
- [x] Create `docs/agents/` for agent mandates, permissions and action policies.
- [x] Assign responsibility for product decisions.
- [x] Assign responsibility for architecture decisions.
- [x] Assign responsibility for accounting acceptance.
- [x] Assign responsibility for production operations.
- [ ] Define how AI-generated changes are reviewed and accepted.
- [ ] Define which changes require Technical Architect approval.
- [ ] Define which changes require Product Manager approval.
- [ ] Define which changes require accountant approval.
- [ ] Define which changes require Valentin’s explicit approval.
- [ ] Establish the rule: no direct production changes outside the deployment process.
- [ ] Establish the rule: no silent modification of posted accounting.
- [ ] Establish the rule: no parallel accounting ledger.
- [ ] Establish the rule: custom workflows orchestrate standard Odoo records.
- [ ] Establish the rule: reuse maintained functionality before custom development.
- [ ] Establish the rule: every material architectural decision compares at least two credible alternatives.
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
  - [x] Branch: `19.0`
  - [x] Initial upstream commit
  - [x] Date of baseline
- [x] Add the official Odoo repository as the canonical `upstream` remote.
- [ ] Define the project’s `origin` repository.
- [ ] Decide whether the repository will:
  - [ ] Vendor the Odoo source directly.
  - [ ] Maintain a thin integration repository around an upstream checkout.
- [ ] Document both repository alternatives and the selected approach.
- [ ] Define how upstream commits will be fetched.
- [ ] Define how upstream security fixes will be identified.
- [ ] Define how upstream changes will be reviewed.
- [ ] Define how upstream changes will be merged or rebased.
- [ ] Define the expected frequency of upstream synchronization.
- [ ] Define a maximum tolerated upstream lag.
- [ ] Create an automated report showing:
  - [ ] Current upstream commit
  - [ ] Current project commit
  - [ ] Upstream commits not yet integrated
  - [ ] Conflicting files
  - [ ] Project modifications to upstream-owned files
- [ ] Minimize direct edits to Odoo core.
- [ ] Inventory every initial core modification, if any.
- [ ] Require an ADR for every core modification.
- [ ] For each core modification, document:
  - [ ] Why extension was insufficient
  - [ ] Alternative approaches considered
  - [ ] Upgrade impact
  - [ ] Test coverage
  - [ ] Removal or upstreaming path
- [ ] Define the custom add-on namespaces.
- [ ] Separate:
  - [ ] Upstream Odoo add-ons
  - [ ] OCA add-ons
  - [ ] Generic reusable USL add-ons
  - [ ] USL-specific add-ons
  - [ ] Experimental add-ons
  - [ ] Migration-only add-ons
- [ ] Define module naming conventions.
- [ ] Define manifest conventions.
- [ ] Define versioning conventions.
- [ ] Define module ownership metadata.
- [ ] Define dependency rules between module categories.
- [ ] Prevent generic modules from depending on company-specific modules.
- [ ] Prevent accounting foundations from depending on experimental AI features.
- [ ] Prevent migration utilities from becoming permanent runtime dependencies.
- [ ] Create a module and dependency map.
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
- [ ] Define whether OCA repositories are pinned, vendored or fetched.
- [ ] Ensure every external dependency is reproducibly pinned.
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
- [ ] Document translation workflows.
- [ ] Document migration-script execution.
- [ ] Document how agents should validate their changes.
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
- [ ] Configure translation validation.
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
- [ ] Prevent staging and test environments from sending Peppol/e-invoicing traffic.
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
- [ ] Track cost per agent workflow.
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

- [ ] Confirm the exact production Odoo Online version.
- [ ] Confirm whether it is an intermediary SaaS version.
- [ ] Determine the supported path to an on-premise-compatible major version.
- [ ] Download and preserve a current production backup.
- [ ] Preserve the filestore.
- [ ] Record backup-generation date and production version.
- [ ] Inventory installed standard modules.
- [ ] Inventory installed Enterprise modules.
- [ ] Inventory Studio-created applications.
- [ ] Inventory Studio-created models.
- [ ] Inventory Studio-created fields.
- [ ] Inventory Studio-modified views.
- [ ] Inventory automated actions.
- [ ] Inventory server actions.
- [ ] Inventory scheduled actions.
- [ ] Inventory email templates.
- [ ] Inventory report templates.
- [ ] Inventory user groups.
- [ ] Inventory access rights.
- [ ] Inventory record rules.
- [ ] Inventory company configuration.
- [ ] Inventory journals.
- [ ] Inventory chart-of-accounts configuration.
- [ ] Inventory taxes.
- [ ] Inventory fiscal positions.
- [ ] Inventory currencies and rates.
- [ ] Inventory analytic plans and accounts.
- [ ] Inventory bank accounts and journals.
- [ ] Inventory reconciliation models.
- [ ] Inventory payment terms.
- [ ] Inventory sequences.
- [ ] Inventory lock dates.
- [ ] Inventory accounting reports actively used.
- [ ] Inventory accountant exports actively used.
- [ ] Inventory FEC behaviour.
- [ ] Inventory attachments and document volumes.
- [ ] Inventory chatter volumes and relevant history.
- [ ] Inventory mail aliases.
- [ ] Inventory inbound-email flows.
- [ ] Inventory outgoing-email configuration.
- [ ] Inventory bank-sync providers.
- [ ] Inventory Peppol/e-invoicing configuration.
- [ ] Inventory external integrations.
- [ ] Inventory API users.
- [ ] Inventory current custom payroll workflow.
- [ ] Inventory current platform-payout workflow.
- [ ] Inventory current accounting-hygiene workflow.
- [ ] Inventory current project-management workflow.
- [ ] Inventory current HR workflow.
- [ ] Inventory GBC workflows.
- [ ] Inventory USL Media workflows.
- [ ] Identify features currently paid for but unused.
- [ ] Identify Community-equivalent features.
- [ ] Identify OCA-equivalent features.
- [ ] Identify features requiring custom replacement.
- [ ] Identify features that can be deliberately dropped.
- [ ] Identify data stored by Enterprise modules that must remain accessible.
- [ ] Identify historical records whose models may disappear.
- [ ] Identify legal and accountant retention requirements.
- [ ] Create the complete feature and migration parity matrix.

## Milestone 10 exit criteria

- [ ] Production functionality and data are inventoried.
- [ ] Enterprise dependencies are known.
- [ ] Studio customizations are known.
- [ ] No migration-critical capability remains represented only by assumptions.
- [ ] The parity matrix is approved.

---

# 11. Build the representative parity laboratory

- [ ] Create an isolated parity environment.
- [ ] Restore or import a representative sanitized production copy.
- [ ] Neutralize all external side effects.
- [ ] Confirm attachment availability.
- [ ] Confirm partner availability.
- [ ] Confirm company availability.
- [ ] Confirm journal and accounting-data availability.
- [ ] Confirm custom field availability.
- [ ] Catalogue models that cannot load without Enterprise components.
- [ ] Catalogue missing views.
- [ ] Catalogue missing reports.
- [ ] Catalogue missing workflows.
- [ ] Catalogue orphaned field references.
- [ ] Catalogue missing external identifiers.
- [ ] Catalogue incompatible Studio artifacts.
- [ ] Catalogue incompatible automated actions.
- [ ] Catalogue migration errors.
- [ ] Decide, item by item:
  - [ ] Preserve
  - [ ] Replace
  - [ ] Transform
  - [ ] Archive
  - [ ] Remove
- [ ] Create migration fixtures for each unsupported object category.
- [ ] Preserve historical records even when the original interactive feature is removed.
- [ ] Create a repeatable import/restore process.
- [ ] Produce a machine-readable parity report.
- [ ] Produce a human-readable parity report.
- [ ] Re-run the parity process from a fresh backup.
- [ ] Confirm repeatability.

## Milestone 11 exit criteria

- [ ] A representative production dataset is usable in a safe lab.
- [ ] Missing Enterprise dependencies are explicitly mapped.
- [ ] Restore/import steps are repeatable.
- [ ] No external side effects occur.
- [ ] Data-loss risks are documented.

---

# 12. Establish the Community/OCA functional foundation

- [ ] Identify the minimum standard Odoo Community module set.
- [ ] Install base company and contact functionality.
- [ ] Install invoicing/accounting foundations.
- [ ] Install project functionality.
- [ ] Install HR/employee foundations.
- [ ] Install expense functionality or selected maintained alternative.
- [ ] Install document/attachment foundations.
- [ ] Install communication/chatter foundations.
- [ ] Evaluate relevant OCA accounting repositories.
- [ ] Evaluate relevant OCA reporting modules.
- [ ] Evaluate relevant OCA reconciliation modules.
- [ ] Evaluate relevant OCA banking modules.
- [ ] Evaluate relevant OCA project modules.
- [ ] Evaluate relevant OCA HR/payroll-support modules.
- [ ] Evaluate relevant OCA queue/background-job modules.
- [ ] Evaluate relevant OCA audit modules.
- [ ] Evaluate relevant OCA storage modules.
- [ ] Evaluate relevant OCA REST/API modules only where justified.
- [ ] Evaluate OpenUpgrade for future major-version migration support.
- [ ] Record rejected OCA modules and reasons.
- [ ] Build the minimum integrated module set.
- [ ] Test clean installation.
- [ ] Test module upgrade.
- [ ] Test module uninstallation where supported.
- [ ] Test multi-company behaviour.
- [ ] Test language and French localization behaviour.
- [ ] Document module ownership and maintenance risk.
- [ ] Freeze the initial foundation set for parity work.

## Milestone 12 exit criteria

- [ ] The minimum Community/OCA foundation is stable.
- [ ] Every external add-on has a documented purpose and maintenance assessment.
- [ ] The baseline installs and upgrades cleanly.
- [ ] Foundation modules do not depend on experimental AI components.

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
- [ ] Validate credit notes.
- [ ] Validate refunds.
- [ ] Validate payment terms.
- [ ] Validate partner ledgers at user-facing report parity level.
- [ ] Validate aged receivables at user-facing report parity level.
- [ ] Validate aged payables at user-facing report parity level.
- [x] Validate general ledger source/target ledger controls.
- [x] Validate trial balance source/target ledger controls.
- [ ] Validate balance sheet at user-facing report parity level.
- [ ] Validate profit and loss at user-facing report parity level.
- [ ] Validate tax reports.
- [ ] Validate VAT/CA12 output.
- [ ] Validate tax carryovers.
- [ ] Validate externally supplied declaration values where required.
- [x] Validate FEC generation through the compatibility harness.
- [x] Validate FEC field content through the compatibility harness.
- [x] Validate FEC chronological consistency through the compatibility harness.
- [ ] Validate FEC after corrections and reversals.
- [x] Validate evidence access from imported entries in the compatibility target.
- [ ] Validate fiscal-year closing.
- [ ] Validate year-opening entries where applicable.
- [ ] Validate shareholder/current-account handling.
- [x] Validate imported asset and amortization evidence if in scope.
- [ ] Validate expense reimbursements.
- [ ] Validate intercompany transactions.
- [ ] Validate bank-fee cases.
- [ ] Validate partial payments.
- [ ] Validate payment differences.
- [ ] Validate multicurrency invoices.
- [ ] Validate multicurrency payments.
- [ ] Validate residual foreign-exchange balances.
- [ ] Validate realized and unrealized exchange differences where required.

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
- [ ] Generate reference reports from current production.
- [x] Generate preliminary equivalent Community report artifacts.
- [x] Compare posted ledger material differences in the compatibility harness.
- [ ] Fix or document every difference.
- [ ] Obtain accountant review of the dataset.
- [ ] Turn accepted cases into permanent regression tests.

## User-facing closing and reporting product

- [x] Capture the annual accounts, SIG and tax-report reference document families.
- [x] Define the target daily workbench: reconcile, review, journal entries, invoices, bills, refunds, expenses and tax readiness.
- [x] Define the target closing package: ledger controls, reports, declaration mappings, FEC, evidence and review state.
- [x] Add a clear Accounting app entry that opens the accounting dashboard.
- [ ] Redesign menus around frequent CEO/accountant workflows.
- [ ] Implement dynamic report screens before export.
- [ ] Implement readable accountant-ready PDF templates.
- [ ] Implement readable templated XLSX exports.
- [ ] Implement declaration guidance views for CFS Pro and Portailpro manual filing.
- [ ] Implement declaration deadline/reminder workbench.
- [ ] Reconstruct customer invoices as usable business documents where source data permits.
- [ ] Reconstruct customer credit notes as usable business documents where source data permits.
- [ ] Reconstruct vendor bills as usable business documents where source data permits.
- [ ] Reconstruct supplier refunds as usable business documents where source data permits.
- [ ] Reconstruct expenses or explicitly document why exact source reconstruction is not available.
- [ ] Implement accountant-readable closing archive package.
- [ ] Keep machine/detail exports available as advanced audit evidence.

## Milestone 13 exit criteria

- [ ] Accounting invariants are explicit and tested.
- [ ] Golden reports match production or have accepted explanations.
- [ ] FEC is accountant-reviewed.
- [ ] Locking, corrections and evidence preservation work.
- [ ] No accounting-critical gap is hidden behind manual assumptions.

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
- [ ] Ensure imported bank lines are idempotent.
- [ ] Ensure repeated synchronization does not duplicate transactions.
- [ ] Preserve provider transaction references.
- [ ] Preserve original transaction text.
- [ ] Preserve enriched merchant information separately.
- [ ] Track ingestion time.
- [ ] Track source update time.
- [ ] Track reconciliation state.
- [ ] Track missing source periods.
- [ ] Alert on stale feeds.
- [ ] Alert on account disconnection.
- [ ] Support manual statement import as a fallback.
- [ ] Support test fixtures without live bank credentials.

## Reconciliation

- [ ] Preserve standard Odoo reconciliation semantics.
- [ ] Define AI-assisted reconciliation as suggestions first.
- [ ] Create candidate-match evidence.
- [ ] Display confidence.
- [ ] Explain proposed matches.
- [ ] Handle one-to-one matches.
- [ ] Handle one-to-many matches.
- [ ] Handle many-to-one matches.
- [ ] Handle bank fees.
- [ ] Handle FX differences.
- [ ] Handle internal transfers.
- [ ] Handle salary payments.
- [ ] Handle TESE payments.
- [ ] Handle platform payouts.
- [ ] Handle partial settlements.
- [ ] Handle duplicate bank lines.
- [ ] Prevent silent automated reconciliation until explicitly approved by policy.
- [ ] Record who or which agent proposed a match.
- [ ] Record who approved it.
- [ ] Test unreconciliation and correction.
- [ ] Build reconciliation regression fixtures.

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
- [ ] Preserve the original file.
- [ ] Preserve source metadata.
- [ ] Detect duplicates.
- [ ] Detect corrupted files.
- [ ] Detect unsupported formats.
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

- [ ] Create draft vendor bills through standard accounting records.
- [ ] Create draft expenses through standard expense records.
- [ ] Attach the original document.
- [ ] Add concise factual review notes.
- [ ] Avoid exposing raw private reasoning in chatter.
- [ ] Identify blocking errors.
- [ ] Identify non-blocking warnings.
- [ ] Prepare supplier-correction requests when appropriate.
- [ ] Prepare one clear human decision when needed.
- [ ] Never silently post accounting by default.
- [ ] Never silently pay.
- [ ] Never silently delete.
- [ ] Never silently reconcile.
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

- [ ] Define recurring accounting-review periods.
- [ ] Collect incomplete expenses.
- [ ] Collect incomplete vendor bills.
- [ ] Collect missing attachments.
- [ ] Collect unreconciled bank transactions.
- [ ] Collect platform payout issues.
- [ ] Collect payroll issues.
- [ ] Collect VAT issues.
- [ ] Collect unusual balances.
- [ ] Collect stale drafts.
- [ ] Separate automatic fixes from approvals.
- [ ] Separate accountant questions from Valentin decisions.
- [ ] Produce a concise readiness summary.
- [ ] Produce evidence links.
- [ ] Track completion.
- [ ] Avoid noisy chatter or duplicate activities.

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

- [ ] Define the accountant user role.
- [ ] Define visible companies.
- [ ] Define visible journals.
- [ ] Define visible accounting documents.
- [ ] Define visible bank context.
- [ ] Define visible attachments.
- [ ] Define visible chatter categories.
- [ ] Define visible AI review notes.
- [ ] Hide irrelevant creator content.
- [ ] Hide private personal material.
- [ ] Hide irrelevant HR information.
- [ ] Hide raw agent scratch work.
- [ ] Test access with a realistic accountant account.
- [ ] Confirm the accountant can:
  - [ ] Open invoices
  - [ ] Open vendor bills
  - [ ] Review journal entries
  - [ ] Review taxes
  - [ ] Review reconciliation state
  - [ ] Review evidence
  - [ ] Generate reports
  - [ ] Export FEC
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
- [ ] Verify electronic-invoicing obligations and dates.
- [ ] Select the approved external platform strategy.
- [ ] Define inbound electronic-invoice flow.
- [ ] Define outbound electronic-invoice flow.
- [ ] Define status and evidence synchronization.
- [ ] Ensure non-production cannot send legal invoices through the live network.
- [ ] Verify the applicable cash-register/certification perimeter.
- [ ] Obtain specialist advice where uncertainty remains.
- [ ] Document what is software-tested versus professionally accepted.
- [ ] Do not claim legal certification without appropriate evidence.

## Milestone 24 exit criteria

- [ ] Accountant can work interactively with appropriate visibility.
- [ ] FEC and tax outputs have been reviewed.
- [ ] Compliance uncertainties have owners.
- [ ] E-invoicing integration strategy is approved.
- [ ] Private information remains inaccessible.

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
- [ ] Define source backup requirements.
- [ ] Define version compatibility requirements.
- [ ] Define Enterprise-to-Community transformation rules.
- [ ] Define Studio transformation rules.
- [ ] Define module replacement order.
- [ ] Define custom-field preservation.
- [ ] Define external-identifier preservation.
- [ ] Define attachment preservation.
- [ ] Define chatter preservation.
- [ ] Define historical-report preservation.
- [ ] Define user mapping.
- [ ] Define permission mapping.
- [ ] Define integration credential rotation.
- [ ] Define bank synchronization handover.
- [ ] Define email alias handover.
- [ ] Define e-invoicing handover.
- [ ] Define agent activation timing.
- [ ] Define rollback limitations.

## Automated migration checks

- [ ] Count records by critical model before and after.
- [ ] Compare posted journal-entry counts.
- [ ] Compare journal totals.
- [ ] Compare partner balances.
- [ ] Compare tax balances.
- [ ] Compare bank balances.
- [ ] Compare unreconciled-item counts.
- [ ] Compare invoices by status.
- [ ] Compare vendor bills by status.
- [ ] Compare attachments.
- [ ] Compare projects and tasks.
- [ ] Compare active users.
- [ ] Compare companies.
- [ ] Compare analytic records.
- [ ] Compare custom workflow records.
- [ ] Compare FEC output.
- [ ] Compare golden reports.
- [ ] Detect orphaned references.
- [ ] Detect missing external identifiers.
- [ ] Detect unsupported models.
- [ ] Detect missing files.
- [ ] Produce a signed migration report.

## Rehearsals

- [ ] Rehearsal 1: early feasibility backup.
- [ ] Document failures and manual interventions.
- [ ] Automate repeatable fixes.
- [ ] Rehearsal 2: recent production backup.
- [ ] Measure total migration duration.
- [ ] Measure service interruption.
- [ ] Run accounting comparisons.
- [ ] Run workflow smoke tests.
- [ ] Run permission tests.
- [ ] Run integration-neutralization checks.
- [ ] Conduct accountant review.
- [ ] Rehearsal 3: full dress rehearsal.
- [ ] Use the intended production procedure.
- [ ] Use intended deployment identities.
- [ ] Use intended backup and restore process.
- [ ] Simulate go/no-go decision.
- [ ] Simulate rollback or abort before activation.
- [ ] Resolve all migration blockers.
- [ ] Freeze the cutover runbook.

## Milestone 26 exit criteria

- [ ] Migration is scripted and repeatable.
- [ ] Record and report comparisons are automated.
- [ ] Full rehearsal passes without improvised rescue.
- [ ] Duration and downtime are known.
- [ ] Accountant accepts the rehearsal output.
- [ ] Rollback limitations are understood.

---

# 27. Production-readiness review

- [ ] Complete architectural review.
- [ ] Complete product-scope review.
- [ ] Complete accounting review.
- [ ] Complete security review.
- [ ] Complete privacy review.
- [ ] Complete infrastructure review.
- [ ] Complete backup and recovery review.
- [ ] Complete observability review.
- [ ] Complete performance review.
- [ ] Complete migration review.
- [ ] Complete integration review.
- [ ] Complete agent-permission review.
- [ ] Complete accountant-access review.
- [ ] Review all open critical risks.
- [ ] Review all accepted differences from Odoo Online.
- [ ] Review all upstream core modifications.
- [ ] Review all unmaintained dependencies.
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
