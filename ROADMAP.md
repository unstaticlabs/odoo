# USL Odoo Distribution roadmap

Status: 8 September 2026

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

Four capabilities shipped since this file was last written and belong in the
baseline rather than in the plan:

- **Owned autonomous Agents** (`usl_access_control`). Non-human identities owned
  by one accountable human, authority bounded by the intersection of the owner's
  rights, the delegation on the Agent, and the Distribution safety policy;
  per-application access levels; read-only enforcement at both the ORM and JSON-2
  boundaries, generated from the audited action-risk inventory and failing closed
  on drift; immutable audit events; named delegable decisions such as recording a
  receipt decision. Behavior is documented in `docs/product/agents.md`.
- **The Feedback Assistant** (`usl_feedback`). A conversational reporting path
  beside New Message in every tab: local page preview, sanitized page details,
  attachments, a card created in Inbox *before* any provider call, and a bounded
  assistant that asks one focused question per turn until the reporter sends the
  draft to the product team. Cards are ordinary `project.task` records on the
  shared **Odoo Product Feedback** board, which is what lets separately governed
  AI Pipelines drive delivery without an Odoo-to-GitHub write bridge. The board
  now also carries approval state and delivery metadata, and a maintainer can
  open a card without the assistant.
- **The Home cockpit** (`usl_home`). Activities, My Tasks, Favorite Views,
  Accounting & Compliance alerts, and an **AI Pipelines** widget that discovers
  agent-driven Project work at runtime from the `Agent Ready`, `Agent Failed`,
  `Needs Human`, `Human Approved`, `Has PR` and `Pipeline` tags. Each provider
  runs under the current user's own ACLs and record rules.
- **Channel ingestion into native operations** (`usl_b2c_ingest`, `usl_b2c`).
  Etsy, Medusa and Printful exports become native sales orders, deliveries,
  invoices, destination VAT and commission billing, with deduplication, reviewed
  SKU mapping, supplier cost, cost of goods and supplier settlement. Proven
  against a production clone; the chart corrections it depends on and the first
  production drop remain.

The delivery chain itself also became provable in this period: a production
promotion is now refused unless GitHub records that staging actually deployed
that exact commit, an hourly check reports a release chain that has failed or
stalled, and each release records the commit production is running.

The latest independent production-to-staging restore completed in 332.411
seconds without OCR, ingestion, vector rebuilding, or model download and
matched all recorded business controls.

## How work is chosen

Items here are ranked with the rest of the backlog by BRICE, defined in
`.claude/skills/usl-feedback-triage/SKILL.md`. `VALUES.md` gives the ordering the
business-value and impact terms read from, and `ARCHITECTURE.md` §21 is the veto
list. An item carrying an ID below can be pulled directly by a delivery agent;
one without an ID needs shaping into a unit of work first.

## Now

### Continuous delivery

- **CD-1** complete the reviewed historical consolidation into `19-usl` and make
  its qualified tree the production source of truth;
- **CD-2** ~~create and protect `19-usl-staging` as the feature-integration
  line~~ — **shipped**: the `USL Distribution — Staging` ruleset is active and
  validated in CI by `scripts/check-github-governance`;
- **CD-3** deploy each staging merge against a fresh production backup;
- **CD-4** schedule the daily production promotion, backup-only no-change run,
  and automatic rollback. The monitoring half of this is **shipped** —
  `scripts/check-release-health` runs hourly and
  `scripts/check-staging-deployment` gates the promotion — but the promotion
  itself is still opened by hand each time, and rollback is still an operator
  decision;
- **CD-5** ~~make release manifests the single source of runtime image
  identity~~ — **shipped**: `usl-release/v3` is published as an immutable OCI
  artifact and `scripts/release-manifest validate` gates it.

### Operational reliability

- monitor backup age, restore duration, free capacity, cron lag, and every
  application queue;
- keep one staging rollback generation and prune older exact-owned resources;
- complete inbound alias operations and duplicate-safe bank ingestion;
- finish production observability and Telegram failure notifications;
- document and rehearse the operator response to a failed upgrade;
- repair the staging runtime lookup: `operations/targets/staging.json` names a
  compose project the host does not answer to, so `usl-stack-observe staging
  runtime` fails while staging is healthy.

### Agents and AI operations

- turn the AI Pipelines tag convention into a named operational framework —
  states, hand-offs, retries, escalation to a human, and where the durable record
  of a decision lives — rather than a discovery rule the Home widget happens to
  share with the delivery skills;
- widen delegable Agent authority case by case, each one an audited named
  decision with a tested denial, never a general capability grant;
- give agent work a first-class record in Odoo: what was attempted, on whose
  authority, with what evidence, and what a human still has to approve.

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

These are the stated priorities, in the order they were named. Several build
directly on what the baseline above already provides.

- **Agentic capabilities** — scoped agent service accounts operating ordinary
  Odoo records, and the collaboration surface that makes their work reviewable
  in the product rather than in a log.
- **An AI pipelines operational framework** — the durable version of the item in
  *Now*: pipelines as declared, monitored operational objects with owners,
  inputs, evidence and failure handling, applicable beyond product delivery.
- **Sales auto-ingestion** — remove the export step. `usl_b2c_ingest` already
  reads Etsy, Medusa and Printful drops; the work is scheduled, duplicate-safe
  connections to those platforms plus Stripe, and the readiness checks that let
  an ingestion run unattended.
- **Live banking** — bank synchronization once a duplicate-safe provider path is
  accepted. Statements and reconciliation already work without it, so this is a
  volume and latency improvement, not a missing capability.
- **Smarter accounting automation, toward full AI accounting** — production-safe
  assistance for review, classification, reconciliation and defensibility, then
  bounded posting. `docs/product/expense-batch-automation.md` is the shape of the
  contract: proposals with evidence and uncertainty, ambiguity left as a draft,
  no automatic submission.
- **Socials and content platforms** — statistics, audience, engagement and
  conversations as first-class operational objects. `usl_platform_billing`
  already turns platform payouts into auditable invoices and bills; this extends
  the same integrations from money to reach and interaction.
- **Content Creators Vault** — a governed home for creative assets, rights,
  usage and provenance, linked to the platforms and the accounting consequences
  they produce.
- **Knowledge, modeled for the AI/Agents era** — structured operational memory
  agents can read and cite under the same access rules as a person, following
  `docs/product/structured-operational-memory.md` rather than a free-text wiki.

Also queued: richer Project and executive-assistant workflows; B2C purchasing,
fulfilment, refund, replenishment, valuation and margin operations; improved
translations, accessibility, and role-specific product journeys.

## Later

- bounded autonomous reconciliation and posting with explicit authority and
  tested recovery;
- manufacturing and landed costs at operating volume;
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
