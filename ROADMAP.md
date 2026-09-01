# USL Odoo Distribution roadmap

Status: 2 September 2026

The USL Odoo Distribution is live on the production VPS. That database is the
authoritative business record. The former Odoo Online export and its migration
code remain historical evidence and exceptional-recovery material, not the
ordinary product workflow.

## Release policy

- `19-usl` is the production release line.
- Product behavior belongs in `custom-addons/`; migration implementation stays
  isolated under `migration/`.
- Releases use immutable OCI digests and a coordinated Odoo, Paperless, Sign,
  renderer, MCP, and Ollama compatibility contract.
- Production deployment, rollback, and staging refresh use qualified recovery
  cohorts through `scripts/usl-stack`.
- A change is complete only after focused tests, a production-like staging
  deployment, health checks, and relevant business controls pass.

## Current baseline

Production provides:

- multi-company Accounting, invoices, bills, expenses, assets, reconciliation,
  analytics, FEC, French reports, declarations, and closing controls;
- Projects and tasks with preserved identifiers, relationships, chatter,
  attachments, and stage-duration history;
- Expense Batches, Platform Billing, and TESE payroll evidence;
- Paperless-backed Documents with originals, OCR, previews, metadata, search,
  Tantivy, and BGE embeddings;
- Native Sign evidence, governed PDF rendering, Pocket ID authentication, and
  scoped MCP access;
- production inbound/outbound mail foundations, bank ingestion, and French PDP
  reception onboarding;
- deterministic content-addressed images and a coordinated backup/restore
  interface.

The latest independent production-to-staging restore completed in 332.411
seconds without OCR, ingestion, vector rebuilding, or model download and
matched all recorded business controls.

## Now

### Continuous delivery

- merge the migration and post-migration branches into `19-usl` through the
  reviewed stacked changes;
- create and protect `19-usl-staging` as the feature-integration line;
- deploy each staging merge against a fresh production backup;
- schedule the daily production promotion, backup-only no-change run, health
  checks, notifications, and automatic rollback;
- make release manifests the single source of runtime image identity.

### Operational reliability

- monitor backup age, restore duration, free capacity, cron lag, and every
  application queue;
- keep one staging rollback generation and prune older exact-owned resources;
- complete inbound alias operations and duplicate-safe bank ingestion;
- finish production observability and Telegram failure notifications;
- document and rehearse the operator response to a failed upgrade.

### Accounting and compliance

- complete PDP acceptance for USL and USL MEDIA, then gate sending and
  e-reporting separately from reception;
- obtain accountant sign-off on statutory reports, FEC, declarations, and
  closing workflows;
- continue improving Expense Batch, CCA, analytics, evidence defensibility,
  and declaration preparation;
- monitor daily currency retrieval, posting, depreciation, Hygiene,
  declaration, and Sign jobs.

## Next

- production-safe AI Accountant assistance for review, classification,
  reconciliation, and defensibility;
- scoped agent service accounts and Odoo-record-based collaboration;
- richer Project, executive-assistant, and document-research workflows;
- B2C purchasing, sales, fulfilment, refund, replenishment, valuation, and
  margin operations;
- live bank synchronization once a duplicate-safe provider path is accepted;
- improved translations, accessibility, and role-specific product journeys.

## Later

- bounded autonomous reconciliation and posting with explicit authority and
  tested recovery;
- manufacturing, landed costs, and external commerce ingestion;
- Odoo 20 navigation and shareable filtered-link improvements;
- optional Telegram and additional communication integrations.

Agents should save operator time. They must not bypass Accounting, access,
multi-company, evidence, or irreversible-action controls.

## Release definition of done

A production release is complete when:

1. source, component inputs, and image identities are immutable;
2. the running release matches its manifest;
3. the pre-release backup is qualified and independently restorable;
4. Accounting, security, multi-company, Documents, Sign, queues, and required
   integration checks pass;
5. production is unfrozen and staging is recreated from the accepted recovery
   point;
6. rollback inputs, logs, notifications, and ownership are explicit.
