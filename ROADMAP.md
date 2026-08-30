# USL Odoo Distribution roadmap

Status: 30 August 2026

The USL Odoo Distribution is a live product in continuous development. The
current local runtime contains the authoritative working dataset and is used
for real operations. The frozen Odoo Online export is historical evidence, not
a rollback target and not the center of future product development.

The next milestone is to promote this evolving product and dataset safely to
the production VPS. Work that does not reduce cutover risk or unblock ordinary
operations should not delay that milestone.

## Release policy

- `19-usl` is the canonical release line.
- Product behavior belongs in `custom-addons/`; maintained OCA dependencies
  remain pinned and reviewed.
- The live dataset must never be reset from the Online export. Take a
  coordinated checkpoint before risky upgrades or data repairs.
- Every release binds the Odoo, Paperless, Ollama, Sign and MCP versions to
  immutable image digests and a tested compatibility contract.
- Production deployment and rollback use the versioned release and backup
  procedures in [`docs/operations/production.md`](docs/operations/production.md).
- Live mail, banking, e-invoicing, e-reporting and other external side effects
  remain disabled until their own production activation gate passes.

## Current product baseline

The following capabilities are implemented and running locally:

- multi-company Accounting for Unstatic Labs and USL MEDIA, including native
  invoices, bills, expenses, payments, journals, assets, deferrals,
  reconciliation, analytics, FEC, French reports, declarations and closing
  controls;
- a company-aware Home cockpit, Accounting overview, Hygiene and review
  journeys;
- Projects and tasks with preserved history, dependencies, chatter,
  attachments and stage-duration ledgers;
- Expense Batches, Platform Billing and TESE payroll evidence integrated with
  native Accounting;
- Paperless-backed Documents with OCR, previews, versions, metadata, Trash,
  business links, Tantivy search and BGE embeddings;
- governed document rendering and Native Sign evidence;
- Pocket ID authentication, named roles, multi-company record rules and a
  protected break-glass administrator;
- historical B2C evidence plus native product, sales and inventory foundations;
- a separately built and pinned Odoo MCP service in the same release cohort;
- deterministic images, focused regression suites, release identity checks and
  product/migration boundary enforcement.

The detailed capability-to-module map is in
[`docs/product/fork-overview.md`](docs/product/fork-overview.md).

## Now — production admission

These items block declaring the VPS deployment canonical.

### Protect the authoritative dataset

- Capture a coordinated checkpoint of Odoo PostgreSQL and filestore,
  Paperless PostgreSQL/media/data/search/Trash/export, Ollama/BGE data and Sign
  evidence.
- Restore that checkpoint into fresh isolated volumes and prove parity without
  OCR, re-ingestion, vector rebuild or model download.
- Keep the local runtime available as the recovery source until the first
  independent production-backup restore passes.

### Consolidate and identify the release

- Consolidate the current hot-fix branch into `19-usl` through one reviewed
  release change.
- Publish immutable Odoo, backup and MCP image digests.
- Verify the Odoo–MCP compatibility contract and record the complete release
  identity outside the database.
- Run the focused suites affected by the final delta and the release boundary
  checks. Avoid unrelated redesign before cutover.

### Close Accounting and compliance acceptance

- Reconcile every material difference between the working database and the
  frozen Online evidence as either legitimate local work, an intentional
  transformation or a corrected defect.
- Produce final FEC, Bilan, TVA, liasse/CERFA and reporting evidence for both
  companies.
- Obtain Valentin's business approval and Prosper's professional/accountant
  sign-off on statutory outputs, permissions and operating workflows.
- Confirm that production currency retrieval, scheduled posting,
  depreciation, Hygiene, declarations and Sign evidence jobs are enabled only
  where approved.

### Configure production services

- Configure the production domain, TLS, Pocket ID issuer and callbacks,
  secrets, ingress and scoped service identities.
- Configure inbound and outbound mail deliberately. Historical queues must not
  be replayed, and no copied environment may contact real recipients.
- Configure backup retention, monitoring, alerting and a tested recovery
  destination.
- Admit only the approved Odoo, Paperless, Ollama, Sign and MCP cohort.

### Activate electronic invoicing separately

- Complete approved-platform onboarding for both USL and USL MEDIA.
- Verify company identifiers, contracts, credentials, reception journals,
  permissions, support and rollback contacts.
- Activate reception first with a controlled production invoice. Keep
  e-reporting disabled until its separate legal and operational gate passes.

### Cut over

- Freeze writers, capture the final cohort and verify its checksums.
- Restore and configure the exact cohort on the VPS.
- Run release, Accounting, access, multi-company, Documents, Sign, MCP and
  external-side-effect gates before ingress opens.
- Complete signed-in smoke tests for Valentin and Prosper.
- Treat production as canonical only after the first coordinated production
  backup has been restored independently.

## Next — reliable daily operations

After admission, prioritize improvements that remove recurring manual risk:

- finish production mail routing and evidence ingestion for Accounting;
- enable duplicate-safe bank-feed synchronization when a safe provider path is
  available;
- complete Expense Batch, CCA and analytic operating guidance;
- improve declaration preparation, closing review and report explanations;
- validate translations and role-specific journeys continuously;
- add monitoring for queues, stale integrations, backup age and failed jobs;
- finish physical opening inventory and then expand B2C purchasing, sales,
  fulfilment, refunds, replenishment, valuation and margin workflows.

## Later — bounded automation

These capabilities remain valuable, but they do not block production while a
safe human workflow exists:

- AI Accountant assistance for review, classification, reconciliation,
  defensibility and concise collaboration;
- AI Project Manager and Executive Assistant workflows;
- scoped agent service accounts and role-routed inbound requests;
- autonomous reconciliation or posting with explicit authority and recovery;
- Documents-specific semantic tools and feedback-to-repair workflows;
- advanced manufacturing, landed costs and external commerce ingestion;
- Odoo 20 navigation, shareable filtered links and remaining browser-history
  improvements;
- Telegram and other optional communication channels.

Agents should reduce coordination work, not create it. They should use Odoo
records, direct links, status fields, short Chatter notes and Activities only
when these improve accountability. No agent may bypass Accounting, access,
company, evidence or irreversible-action controls.

## Definition of a production release

A release is complete only when:

1. code and image identities are immutable and reproducible;
2. the evolving production dataset has a verified coordinated backup;
3. an independent restore proves the complete application cohort;
4. Accounting, statutory, identity, permission and multi-company controls pass;
5. required integrations are configured and unapproved side effects remain
   disabled;
6. core signed-in journeys pass for the responsible users;
7. rollback and abort procedures name exact artifacts and owners;
8. the first production backup is independently restorable.

Historical reconstruction implementation remains isolated under `migration/`
for audit and exceptional recovery. It is not part of the delivered product or
the ordinary development workflow.
