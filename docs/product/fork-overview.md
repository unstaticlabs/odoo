# USL Distribution capability and module map

This page maps the capabilities added to Odoo Community to their durable
technical owners. It describes the product that runs continuously; it is not a
migration status report.

The Distribution follows upstream Odoo `saas~19.3`, uses reviewed OCA modules
for maintained Community extensions, and keeps USL behavior in isolated custom
add-ons. [`ROADMAP.md`](../../ROADMAP.md) records current priorities and
production-admission work.

## Product capabilities

| Capability | Technical owner | Main entry points | State |
| --- | --- | --- | --- |
| Multi-company Accounting cockpit, Hygiene, controls, French reports, declarations, closing and FEC | `rebuild_account_migration`, `usl_accounting`, native Accounting and pinned OCA modules | **Accounting** | Operational in production; final statutory and professional sign-off remains |
| Customer invoices, supplier bills, expenses, payments, assets, deferrals, bank matching, reconciliation and analytics | native Accounting, `usl_accounting` | **Accounting**, **Expenses** | Operational |
| Company-aware personal Home and attention summaries | `usl_home` | **Home** | Operational |
| Expense Batches with shared business and analytic context | `usl_expense_batch`, `usl_accounting` | **Expenses > Expense Batches** | Operational |
| Content-platform payout billing and settlement | `usl_platform_billing`, `usl_platform_billing_pocketid` | **Platform Billing** | Operational |
| TESE payroll evidence, entries and settlement controls | `usl_tese_payroll`, `usl_tese_accounting` | **Paie TESE** | Operational; TESE remains the legal payroll calculator |
| Projects and tasks with dependencies, chatter, attachments and stage history | native Projects, `usl_project` | **Projects** | Operational |
| Paperless-backed Documents, OCR, previews, metadata, versions, Trash, search and business links | `usl_documents`, `usl_documents_accounting`, `usl_documents_b2c` | **Documents** and record smart buttons | Operational |
| Governed official PDFs and correspondence | `usl_document_templates`, document renderer | Native print actions, **Official Documents** | Operational |
| Electronic signatures and completion evidence | `usl_sign` | **Sign** | Operational in production with server-managed certificate material |
| Pocket ID OIDC authentication and named-user governance | `usl_pocketid`, `usl_access_control` | Odoo sign-in and **Settings** | Operational in production |
| Company-scoped roles, owned autonomous Agents, irreversible-action controls and immutable audit events | `usl_access_control` | **My Agents**, access rights and protected actions | Operational |
| Historical commerce evidence and native future sales/inventory foundations | `usl_b2c`, `usl_documents_b2c` | **B2C**, **Sales**, **Inventory** | Variants, locations, traceability, UoM and Landed Costs available; physical opening inventory and advanced automation remain |
| French-first terminology, European dates and company-aware presentation | `usl_locale` | All affected backend views | Operational |
| French electronic-invoice reception for UBL, CII and Factur-X | `rebuild_account_migration`, native Accounting/localization | **Vendors > Incoming E-Invoices** | Ready but inactive pending approved-platform production onboarding |
| Agent-authenticated Odoo automation endpoint and tool contract | separately built `odoo-mcp` image pinned by the release | **My Agents** and the MCP service endpoint | MCP operational; governed Agent identities are qualified for coordinated release |

Detailed behavior belongs in the relevant product, Accounting, user and
operations documents rather than in this inventory.

## Delivered add-ons

Only `custom-addons/` is part of the normal USL add-ons path.

| Module | Durable responsibility |
| --- | --- |
| `rebuild_account_migration` | Accounting cockpit, Hygiene, configurable controls, reports, declarations, closing and electronic-invoice readiness. The historical technical name is retained to preserve installed model and XML-ID ownership; the module is delivered product code. |
| `usl_access_control` | Named roles, owned autonomous identities, delegated-authority enforcement, governed API credentials, irreversible-action enforcement and security audit evidence. |
| `usl_accounting` | Native/OCA Accounting extensions, expense and bank matching, foreign-currency settlement, fiscal-year behavior, scheduled statements, analytics and evidence security. |
| `usl_b2c` | Commerce channels, orders, events, SKU aliases, accounting sessions, controls and analytics. |
| `usl_document_templates` | Governed report bindings, renderer integration, immutable correspondence and PDF provenance. |
| `usl_documents` | Paperless-backed Documents application, cached metadata, links, versions, operations, access policy and browser client. |
| `usl_documents_accounting` | Authorized Accounting evidence links and exact-version archive controls. |
| `usl_documents_b2c` | Authorized B2C document links and smart buttons. |
| `usl_expense_batch` | Optional expense grouping, shared context, review and native workflow integration. |
| `usl_home` | Personal launcher, durable destinations and bounded attention summaries. |
| `usl_locale` | European date conventions and company-aware presentation. |
| `usl_platform_billing` | Platform sessions, payouts, generated native Accounting documents and settlement. |
| `usl_platform_billing_pocketid` | Pocket ID role mapping for Platform Billing administrators. |
| `usl_pocketid` | Pocket ID authentication and identity governance. |
| `usl_project` | Focused Project compatibility and task presentation. |
| `usl_sign` | Signature requests, operations, evidence and completion records. |
| `usl_tese_payroll` | External-provider payroll records, evidence, Accounting and settlement. |
| `usl_tese_accounting` | TESE state and evidence in Accounting closing controls. |
| `usl_bootstrap` | Synthetic test fixtures only; forbidden from production dependency graphs. |

The detailed dependency policy is in
[`docs/accounting/custom-addon-architecture.md`](../accounting/custom-addon-architecture.md).

## Maintained OCA functionality

Reviewed OCA modules provide OIDC, financial and partner statements, bank
reconciliation, statement imports, assets, tax balances, XLSX reports and
selected server UX helpers. Exact pins live in `scripts/sync-oca-addons`.
Compatibility patches are applied deterministically from
`oca-patches/saas-19.3/`; they are not copied into USL modules.

## Odoo core patches

The Distribution currently carries focused patches in:

- `addons/account/models/account_move.py`;
- `addons/account/wizard/account_resequence.py`;
- `addons/web/static/src/webclient/actions/action_service.js`.

The Accounting patches keep journal sequences and resequencing aligned with
the company fiscal year. The webclient patch restores a valid dynamic action
name when browser history recreates a controller. Each patch has focused
regression coverage and should be removed when upstream provides an equivalent
extension point or behavior.

No other product-specific core divergence is allowed without an explicit
architecture decision, upgrade analysis and regression evidence.

## Runtime and release cohort

Odoo, PostgreSQL, Paperless, its broker and archive state, the document
renderer, Sign services and the separately built MCP image form one coordinated
release and recovery cohort. The shared MsgVault-owned Ollama service is an
external dependency: releases record and validate the required BGE model,
manifest and embedding dimension without managing or restoring that service.
A release records exact source commits, image digests, modules, configuration
identity and backup identity. Tags alone are not deployment authority.

Production changes use the procedures in
[`docs/operations/production.md`](../operations/production.md). Live mail,
banking, e-invoicing and e-reporting require separate activation gates.

## Boundaries

- Odoo owns structured operational and Accounting truth.
- Paperless owns archived originals, derivatives, OCR and search artifacts;
  Odoo owns authorized business links and workflow state.
- Pocket ID authenticates humans; governed API keys authenticate non-interactive Agents. Odoo owns both identities' roles, companies and record rules.
- TESE remains the legal payroll calculator.
- External commerce and bank systems remain authoritative for their source
  events; imports must be duplicate-safe and auditable.
- Probabilistic automation may suggest work but may not bypass posting,
  company, access, evidence or irreversible-action controls.
- One-shot reconstruction code under `migration/` is excluded from the normal
  add-ons path and from finalized product databases.

Update this map whenever a durable capability, module owner, core patch or
runtime dependency changes. Temporary project status and migration counts do
not belong here.
