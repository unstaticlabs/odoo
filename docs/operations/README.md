# Operations

Start with the production entry point:

- [Production operations](production.md) for immutable releases, coordinated
  backup, deployment, admission, upgrades and recovery.
- [Continuous delivery](continuous-delivery.md) for release branches, v3
  manifests, exact module plans, unattended reconciliation and rollback.

The [historical reconstruction interface](migration.md) is retained for audit,
exceptional recovery and transfer of the evolved data cohort. It is not a
routine product-development or production-operations guide.

Specialized product procedures remain separate when they require a deliberate
activation or domain-specific response:

- [Staging on demand](staging-on-demand.md): sleeping staging, wake, idle sleep
- [Developing in a git worktree](worktree-development.md)
- [Production image CI contract](production-image-ci.md)
- [Backup and recovery](backup-and-recovery.md)
- [Product and migration boundary](product-migration-boundary.md)
- [Pocket ID SSO](pocket-id-sso-runbook.md)
- [Electronic-invoice activation](activate-french-electronic-invoicing.md)
- [Document renderer](document-renderer-runbook.md)
- [Paperless and Documents](paperless-documents-runbook.md)
- [Linked expense receipts](linked-receipt-runbook.md)
- [Sign](sign-runbook.md)
- [Odoo MCP](odoo-mcp.md)
- [Personal Gemini](personal-gemini-runbook.md)
- [Shine bank export](shine-bank-export-runbook.md)
- [Importing channel exports](b2c-channel-imports.md)
- [Rolling out the money-handler statements](b2c-statements-rollout-runbook.md)
- [Access controls](distribution-access-control-runbook.md)
- [Product performance](product-performance.md)
- [Action-risk inventory](action-risk-inventory.md)
- [Secure agent document materialization](document-materialization.md)
- [Receipt recovery runtime](receipt-runtime.md)
- [Scanning historical expense emails](linked-receipt-rescan.md)
- [B2C operator and accounting-session workflow](b2c-operations.md)
- [Mail seed identifiers during upgrades](mail-seed-upgrades.md)
- [Manual recovery release provenance](manual-release-provenance.md)

Dated incident reviews, security reviews, QA fixtures and evidence folders
live in [`docs/archive/operations/`](../archive/operations/). Action-risk
reviews and sync records named by the access-control policy stay here.
