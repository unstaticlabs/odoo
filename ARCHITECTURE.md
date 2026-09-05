# Architecture

## Purpose

The USL Odoo Distribution is the structured operational core of Unstatic Labs.

It must provide a trusted, durable and extensible foundation for:

- accounting;
- banking;
- expenses;
- HR;
- projects;
- tasks;
- documents;
- company operations;
- AI-agent collaboration;
- future creator-business workflows.

The system must remain recognizably Odoo.

It should extend standard Odoo behaviour, reuse maintained Community and OCA modules where appropriate, and avoid changes that would make future upstream upgrades impractical.

The target is not a generic ERP fork.

The target is a modern, reliable and AI-ready Odoo distribution tailored to USL, USL Media, GBC and future entities.

---

# 1. User experience

Users should experience one coherent operational system even when information originates from several external services.

They should be able to:

- understand the current state of a company, project or process;
- inspect the records and evidence supporting that state;
- see what is blocked and what event is expected;
- receive clear prepared decisions instead of vague reminders;
- trace actions taken by humans and agents;
- collaborate without exposing unnecessary private information;
- use Odoo directly or interact through conversational interfaces;
- trust that accounting records remain controlled and auditable.

The system should reduce manual coordination.

It should not require users to repeatedly reconstruct context from email, documents, banking interfaces, task managers and conversations.

---

# 2. Core architectural principles

## 2.1 Odoo owns structured operational truth

Odoo is the primary source of truth for structured company operations.

It owns records such as:

- companies;
- partners;
- employees;
- projects;
- tasks;
- activities;
- invoices;
- vendor bills;
- expenses;
- journal entries;
- bank statement lines;
- analytic accounts;
- operational states;
- approvals;
- business evidence;
- agent actions.

Custom features should enrich or orchestrate these records rather than create competing representations of the same reality.

## 2.2 External systems keep their natural authority

Odoo should not unnecessarily replace systems that already own a domain well.

Examples:

- GitHub owns source code, pull requests and engineering history.
- Banks and payment providers own source financial transactions.
- Gmail owns email threads.
- Google Calendar owns calendar events and commitments.
- Google Drive owns many source documents.
- Social and creator platforms own source publication and performance data.
- Obsidian owns long-form personal or strategic knowledge.
- AI providers perform reasoning but do not own durable business truth.

Odoo stores the business interpretation, links, status, consequences and evidence needed to make those external facts operational.

## 2.3 AI infers; systems remember

AI may:

- classify;
- extract;
- summarize;
- compare;
- recommend;
- prepare;
- detect;
- route;
- explain.

Durable facts must be committed to the appropriate source-of-truth system.

AI output is not automatically authoritative.

Material inferences should retain:

- evidence;
- confidence;
- policy context;
- responsible agent;
- human approval where required.

## 2.4 State drives behaviour

The architecture is state-based and event-driven.

An event changes operational state.

The resulting state determines what should happen next.

Examples:

- an email satisfies an expected reply;
- a bank transaction allows reconciliation;
- a signed document unblocks incorporation;
- a merged pull request advances a product milestone;
- an uploaded receipt creates an expense-review workflow.

Workflows should be restartable from current state.

They must not depend on fragile chains of one-time commands.

## 2.5 Human attention is reserved for judgment

The system should automate mechanical and investigative work where safe.

Humans should primarily receive:

- decisions;
- approvals;
- signatures;
- exceptions;
- conflicts;
- strategic choices;
- requests for information that cannot reasonably be inferred.

A human activity should explain:

- what happened;
- why it matters;
- the recommended action;
- what approval will do;
- what happens if no action is taken;
- the evidence supporting the recommendation.

## 2.6 Upstream compatibility is a constraint

The project should prefer:

1. standard Odoo functionality;
2. supported Odoo extension mechanisms;
3. maintained OCA modules;
4. isolated custom modules;
5. direct core modification only as a last resort.

Any modification to upstream-owned code must be:

- necessary;
- documented;
- tested;
- isolated where possible;
- evaluated for upgrade impact;
- accompanied by a reconciliation or removal strategy.

---

# 3. System boundaries

## 3.1 Odoo Core

Odoo Core provides the standard business foundation.

It includes:

- ORM and business models;
- security and record rules;
- multi-company support;
- messaging and chatter;
- activities;
- accounting primitives;
- projects and tasks;
- employees;
- documents and attachments;
- scheduled actions;
- web and mobile interfaces.

The project must avoid duplicating core capabilities unnecessarily.

## 3.2 OCA and maintained extensions

OCA modules may extend missing Community capabilities.

They are treated as external dependencies and must be evaluated for:

- maintenance status;
- Odoo 19 compatibility;
- licensing;
- upgrade history;
- test quality;
- overlap with standard Odoo;
- long-term importance to the project.

OCA modules should remain distinguishable from custom USL modules.

## 3.3 USL domain modules

USL modules provide business behaviour specific to the organization.

Initial domains include:

- TESE payroll preparation;
- platform payout accounting;
- accounting hygiene;
- USL Media intercompany operations;
- banking ingestion;
- AI-assisted invoice and expense review;
- event-aware project management;
- agent execution and auditability;
- creator operations;
- future Yoshi workflows.

These modules should use standard Odoo records as their operational consequences.

For example, a platform payout session may prepare:

- a customer invoice;
- a supplier bill;
- a compensation entry;
- a bank reconciliation link;
- supporting evidence.

The session must not become a parallel ledger.

## 3.4 Integration layer

The integration layer connects Odoo with external systems.

It is responsible for:

- receiving events;
- preserving external identifiers;
- validating source and authenticity;
- translating external data into operational context;
- preventing duplicate processing;
- reporting failures;
- respecting system ownership.

Integrations should be independently disableable.

Non-production environments must not cause real external side effects.

## 3.5 Agent and orchestration layer

Hermes, OpenClaw or an equivalent system coordinates AI agents.

This layer is responsible for:

- receiving events;
- gathering context;
- selecting specialist agents;
- invoking permitted capabilities;
- preparing decisions;
- routing approvals;
- coordinating notifications.

Odoo remains functional when the agent layer is unavailable.

Agent failures must not corrupt core business records.

## 3.6 MCP capability layer

MCP exposes controlled business capabilities to agents.

Preferred capabilities are domain-oriented, such as:

- inspect a supplier invoice;
- prepare an expense review;
- process a platform payout draft;
- inspect project blockers;
- resolve an expected event;
- prepare an accountant review package;
- link an email to operational context.

Low-level unrestricted CRUD access should be limited.

Capabilities must define:

- permissions;
- company context;
- validation;
- idempotency;
- audit behaviour;
- failure behaviour;
- preview or dry-run behaviour where appropriate.

## 3.7 Infrastructure layer

The infrastructure layer provides:

- application hosting;
- PostgreSQL;
- persistent filestore or object storage;
- TLS and networking;
- secret management;
- monitoring;
- logging;
- backups;
- recovery;
- deployment environments;
- CI/CD.

Infrastructure is replaceable.

Business logic must not depend unnecessarily on one cloud provider.

---

# 4. Sources of truth

| Domain | Source of truth | Odoo’s role |
|---|---|---|
| Accounting | Odoo | Authoritative accounting records |
| Projects and operational tasks | Odoo | Authoritative operational state |
| Source code and pull requests | GitHub | Track business relevance and milestones |
| Email | Gmail | Link threads, extract events and preserve references |
| Calendar | Google Calendar | Link commitments to operational context |
| Documents | Drive or Odoo, depending on purpose | Store evidence links or durable accounting copies |
| Bank transactions | Bank/provider or Bank Hub | Store normalized statement lines and reconciliation state |
| Social publications | Platform | Store structured publication references and metrics |
| Creator-platform revenue | Platform source data | Connect revenue events to accounting and operations |
| Long-form personal knowledge | Obsidian | Reference relevant knowledge without replacing it |
| Agent reasoning | Agent runtime | Store concise evidence and resulting decisions in Odoo |

The same fact should not be independently editable in multiple systems without an explicit synchronization policy.

---

# 5. Company and privacy boundaries

The system must support multiple legal and economic entities, including:

- USL;
- USL Media;
- GBC;
- future subsidiaries;
- possible future personal-finance structures.

Legal entities, brands, activities and projects must remain distinct concepts.

An expense may be connected to several contexts while preserving legal meaning, for example:

- paid by Valentin;
- incurred for USL;
- reimbursable;
- linked to a Berlin trip;
- related to a creator collaboration;
- awaiting evidence.

Permissions must be based on legitimate operational need.

Examples:

- the accountant can inspect accounting evidence without seeing unrelated creator content;
- HR agents cannot access private creator operations;
- creator-operations agents cannot post accounting entries;
- project agents cannot access private personal records;
- agents should not receive administrator access by default.

---

# 6. Identity and accountability

Every human and agent must use an attributable identity.

The system should avoid shared omnipotent technical accounts.

Each agent should have:

- a name;
- an owner;
- a mandate;
- permitted companies;
- permitted models;
- permitted capabilities;
- approval thresholds;
- external-system permissions;
- a current version;
- an active or suspended state.

Every meaningful automated action should record:

- the triggering event;
- the responsible agent;
- the affected records;
- the capability used;
- the evidence considered;
- the resulting action;
- the policy applied;
- confidence where relevant;
- human approval where required;
- errors or partial completion.

Private chain-of-thought is not part of the audit requirement.

The audit trail should contain concise, reviewable evidence and rationale.

---

# 7. Events and workflows

## Event model

An event represents a change in reality.

Examples:

- email received;
- document uploaded;
- bank transaction booked;
- invoice received;
- payment confirmed;
- task unblocked;
- document signed;
- pull request merged;
- content published;
- payout received.

Each event should have:

- stable identity;
- source;
- type;
- occurrence time;
- reception time;
- related company;
- related objects;
- correlation context;
- processing status;
- retry status;
- failure state.

Events must be safe to receive more than once.

## Workflow model

A workflow should expose:

- current state;
- responsible owner or agent;
- expected next event;
- blockers;
- evidence;
- permitted transitions;
- downstream consequences;
- escalation policy.

A workflow is not complete because an action was attempted.

It is complete only when the required state is reached and supported by evidence.

---

# 8. Tasks and activities

## Tasks

Tasks represent durable operational work.

A task may include:

- expected event;
- completion detector;
- blocking reason;
- dependencies;
- accountable human;
- owning agent;
- deadline;
- urgency;
- evidence requirements;
- next action;
- review requirements.

A task waiting for an external reply should remain in a waiting state.

Sending an email does not by itself complete the task.

## Activities

Activities represent useful human interventions.

They should not be used as generic reminders created by agents.

A high-quality activity is a prepared decision with enough context to act immediately.

Activities may support:

- approve;
- edit and approve;
- reject;
- request another iteration;
- delegate;
- defer with reason.

Notification is separate from assignment.

The Executive Assistant may decide when and where an assigned activity should interrupt Valentin.

---

# 9. Accounting boundary

Accounting is a protected core domain.

Custom applications may prepare and orchestrate accounting records, but should not redefine accounting semantics casually.

The accounting system must preserve:

- standard journal entries;
- posting controls;
- chronological sequences;
- lock dates;
- proper corrections and reversals;
- supporting evidence;
- tax treatment;
- multi-company separation;
- multi-currency behaviour;
- reconciliation history;
- FEC generation;
- accountant-reviewable audit trails.

AI may propose:

- document extraction;
- supplier matching;
- accounts;
- taxes;
- analytic allocation;
- reconciliation candidates;
- anomaly detection.

AI should not silently:

- post entries;
- pay bills;
- delete accounting records;
- alter posted history;
- reconcile transactions;

unless an explicit, tested and approved policy grants that authority.

---

# 10. Banking boundary

Banking data should enter through a controlled ingestion process.

A future Bank Hub may normalize transactions independently of Odoo.

The banking layer should preserve:

- provider account identity;
- stable transaction identity;
- original transaction description;
- booked or pending status;
- amount and currency;
- source timestamps;
- company ownership;
- normalized merchant information;
- ingestion history.

Odoo owns:

- bank statement representation;
- accounting interpretation;
- reconciliation;
- links to invoices, bills, expenses and internal transfers.

Repeated synchronization must not create duplicate financial events.

Manual import must remain available as a fallback.

---

# 11. Documents and AI-assisted capture

The system should accept documents through low-friction channels, including:

- email;
- upload;
- mobile camera;
- Telegram;
- batch import.

The original document and provenance must be preserved.

AI-assisted processing may:

- extract invoice data;
- identify suppliers;
- detect duplicates;
- gather related context;
- propose company, project and analytic links;
- propose accounting treatment;
- create draft records;
- explain uncertainty.

Extraction confidence and accounting confidence are separate.

The system should ask humans only for genuinely ambiguous or sensitive decisions.

---

# 12. HR boundary

Odoo owns structured employee and employment records.

TESE remains the source of declared French payroll values for the current workflow.

The system may:

- store TESE payroll periods;
- prepare journal entries;
- attach payroll evidence;
- link salary and contribution payments;
- identify missing documents or inconsistencies.

It must not independently invent payroll declarations.

HR information requires stronger privacy controls than ordinary project data.

---

# 13. Creator operations boundary

Creator operations may extend Odoo with records such as:

- trips;
- collaborations;
- shoots;
- content assets;
- edits;
- publications;
- campaigns;
- platform accounts;
- metrics;
- revenue attribution.

These records connect operational activity to accounting without replacing it.

Revenue attribution may be:

- direct;
- campaign-based;
- manual;
- inferred.

Inferred attribution must remain editable, evidence-based and confidence-scored.

The system must not present uncertain attribution as accounting fact.

---

# 14. User interfaces

The same operational system may be accessed through several interfaces.

## Odoo web and mobile

Used for:

- structured review;
- record management;
- dashboards;
- accounting;
- projects;
- detailed investigation;
- administration.

## Telegram or conversational interface

Used for:

- voice notes;
- receipt capture;
- quick questions;
- prepared decisions;
- approvals;
- feedback;
- status summaries.

## GitHub

Used for:

- engineering implementation;
- issues;
- pull requests;
- technical review;
- release evidence.

## Accountant interface

Used for:

- reports;
- entries;
- invoices;
- bills;
- evidence;
- reconciliation;
- questions tied to records.

All interfaces should act on the same structured operational state.

---

# 15. Reliability requirements

The system must be safe under:

- duplicate events;
- retries;
- interrupted executions;
- unavailable AI providers;
- unavailable banks;
- unavailable email services;
- partial external failures;
- delayed messages;
- stale context;
- deployment rollback;
- restored backups.

Background work must be:

- inspectable;
- restartable;
- idempotent;
- attributable;
- observable.

A failed action must not disappear.

It must create visible state that can be retried, corrected or escalated.

---

# 16. Environment boundaries

The project uses distinct environments:

- local development;
- automated test;
- integration;
- accounting validation;
- staging;
- production;
- disaster-recovery restoration.

Non-production environments must be neutralized.

They must not:

- send real emails;
- post to social platforms;
- initiate payments;
- connect to live Peppol or legal invoicing networks;
- notify real external contacts;
- allow test agents to act on production systems.

Production-derived data must be protected and anonymized where appropriate.

---

# 17. Deployment, backup and recovery

The production environment must be reproducibly deployable.

The complete recoverable system includes:

- PostgreSQL data;
- filestore and attachments;
- exact Odoo source revision;
- exact OCA and custom module revisions;
- configuration;
- infrastructure definitions;
- external object references;
- secret-recovery procedures.

Backups must be:

- automatic;
- monitored;
- encrypted;
- stored separately from production;
- protected from accidental deletion;
- regularly restored in tests.

Recovery is considered valid only after verifying:

- database integrity;
- attachments;
- accounting reports;
- permissions;
- custom modules;
- neutralized external integrations.

---

# 18. Observability

The system must expose the health of:

- Odoo;
- PostgreSQL;
- storage;
- scheduled jobs;
- background work;
- integrations;
- bank feeds;
- email ingestion;
- document processing;
- agent executions;
- backups;
- external certificates.

Operators should be able to determine:

- what failed;
- when;
- why;
- which records were affected;
- whether work can be retried;
- whether a human must intervene.

The system should also measure:

- processing accuracy;
- human correction rate;
- workflow latency;
- agent cost;
- infrastructure cost;
- unnecessary notification rate;
- time saved.

---

# 19. Upgrade and durability boundary

The project should remain close enough to upstream Odoo that major-version upgrades remain realistic.

To preserve durability:

- upstream changes are reviewed regularly;
- modifications to upstream-owned files are measured;
- custom modules use supported extension points;
- migration scripts accompany data-model changes;
- deprecated APIs are tracked;
- critical OCA dependencies are monitored;
- representative databases are used for upgrade tests;
- accounting parity tests are rerun after upgrades;
- agent capabilities are regression-tested after upgrades.

The architecture should prefer removable layers over permanent forks.

---

# 20. Product domains

The first production-oriented scope includes:

1. Reproducible development and deployment
2. Cloud hosting and recovery
3. Accounting continuity and French statutory controls
4. French accounting and FEC
5. Banking ingestion and reconciliation support
6. Invoice and expense capture
7. TESE payroll preparation
8. Platform payout accounting
9. Multi-company operations
10. Project, task and activity improvements
11. Email-to-project event handling
12. Agent identities and audit trails
13. MCP domain capabilities
14. Prepared decisions and notifications
15. Accountant collaboration
16. Creator-operation foundations

Later scope may include:

- advanced creator analytics;
- personal finance;
- Yoshi productization;
- broader external customer deployment.

---

# 21. Explicit non-goals

The architecture does not aim to:

- recreate every Enterprise feature;
- rewrite Odoo accounting;
- create a parallel ledger;
- replace GitHub, Gmail, Drive, Calendar or banks;
- store all documents redundantly;
- make AI the source of truth;
- automate every human decision;
- give agents unrestricted administrator access;
- couple the system permanently to one AI provider;
- generalize every internal workflow into a SaaS product immediately;
- prioritize visual modernization over accounting correctness;
- accept irreconcilable divergence from upstream Odoo.

---

# 22. Architectural success criteria

The architecture is successful when:

- Odoo remains a trusted and understandable operational core;
- accounting is accurate, controlled and accountant-approved;
- external systems retain clear ownership of their native data;
- events update operational state reliably;
- agents collaborate through attributable, durable records;
- human activities are high-signal prepared decisions;
- failures are visible and recoverable;
- company and privacy boundaries are enforced;
- deployments and restorations are repeatable;
- upstream divergence remains limited and measurable;
- future Odoo upgrades remain feasible;
- new workflows can be added without creating conflicting sources of truth;
- the system materially reduces Valentin’s coordination and administrative burden.
