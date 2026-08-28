# USL Distribution fork overview

This page is the canonical map of what the USL Odoo Distribution adds to
upstream Odoo Community. It connects user capabilities to their technical
owners, normal screens, detailed specifications, migration support and current
delivery state. It is an index, not a replacement for the linked product,
accounting, user or operations specifications.

The comparison point is upstream Odoo Community `saas~19.3` at
`aef56898d9ea5a97948af04c03ae101d17b8b4a3`. The current release line is
`19-usl`. [ROADMAP.md](../../ROADMAP.md) remains authoritative for changing
release status and outstanding gates.

## How to read delivery state

- **Integrated** means the capability and its tests are present on `19-usl`
  and it is available for development or pre-production use. It does not mean
  that the complete Distribution has passed final production admission.
- **Ready but inactive** means the product is implemented and tested with
  synthetic or offline services, but an explicit production activation is
  still required.
- **Transitional owner** means delivered product behavior remains in a
  historically named module to preserve installed database and XML-ID
  ownership. It is not permission to add migration machinery to that module.
- **Migration-only** means one-shot reconstruction code that is excluded from
  the normal add-ons path and removed from the final product database.
- **Test-only** means a disposable fixture that must not enter the production
  dependency graph.

No individual feature state overrides the release-level blockers in the
[production cut-over readiness register](../operations/production-cutover-readiness.md).

## User-facing capability map

| Capability added by the Distribution | Main technical owner | Normal user entry points | Detailed documentation | Current state |
| --- | --- | --- | --- | --- |
| Daily Accounting cockpit, cash and tax projections, Hygiene, reconciliation review, configurable Controls, Reports, Declarations, closing workspaces, FEC and scoped read-only-accountant access | `rebuild_account_migration`, `usl_accounting`, native Accounting and pinned OCA modules | **Accounting > Overview**, **Transactions**, **Bank Matching**, **Review**, **Reporting**, **Declarations**, **Closing**, **Configuration** | [Accounting core](accounting-core.md), [Accounting specifications](../accounting/README.md), [menus and screens](../users/reference/menus-and-screens.md) | **Integrated**; Accounting v1 is engineering-complete for internal daily use, while final Distribution qualification and professional sign-off remain release gates |
| Governed LaTeX PDFs for customer invoices and credit notes, canonical accounting statements and immutable official correspondence, with explicit classification for every installed report | `usl_document_templates`; adapters in `usl_accounting` and `rebuild_account_migration`; isolated `services/usl-document-renderer` | Existing invoice/report print actions; **Official Documents > Correspondence**; **Settings > Document Templates** | [Document system](latex-document-system.md), [accounting presentation](../accounting/accounting-report-presentation.md), [31-report visual audit](../accounting/accounting-document-visual-audit.md), [user guide](../users/guides/official-documents.md), [renderer runbook](../operations/document-renderer-runbook.md) | **Integration candidate**; accounting statement v2 and three document families are implemented, but merge remains blocked until Native Sign lands and its real completion-certificate adapter passes |
| Company-paid expense-to-bank matching, payment suggestions and controlled foreign-currency settlement | `usl_accounting` over native Expenses, payments and OCA reconciliation | Expense **Find bank transactions**; Bank Matching payment suggestions; **Add**, **Settle**, and **Use payment rate** actions | [Expense bank matching](expense-bank-matching.md), [immediate settlement](../accounting/immediate-settlement.md), [expense how-to](../users/how-to/process-expense.md) | **Integrated** |
| Optional Expense Batches with shared business, analytic and controlled account context, mixed-payer review and native posting | `usl_expense_batch`; accounting dimensions in `usl_accounting` | **Expenses > Expense Batches** / **Lots de dépenses** and **Add to a Batch** | [Expense batches](expense-batches.md), [user guide](../users/guides/expense-batches.md), [accounting contract](../accounting/expense-batch-accounting.md) | **Integrated** |
| Scheduled Shine email ingestion, retained RFC822/ZIP/OFX/PDF evidence, native bank statements, monthly completeness checks, certification and reopening | `usl_accounting`; archive bridge in `usl_documents_accounting`; pinned OCA OFX importer | **Accounting > Bank Statements**, journal dashboards, Overview and Hygiene | [Scheduled bank statements](scheduled-bank-statements.md), [accounting controls](../accounting/scheduled-bank-statement-controls.md), [operations runbook](../operations/shine-bank-export-runbook.md) | **Integrated**; production MTA/MX routing, exact-identity cut-over and a routed synthetic email remain rollout gates |
| Auditable historical Etsy, Medusa, Stripe, Revolut and Printful commerce; canonical orders and events; SKU review; monthly accounting coverage; B2C analytics; native future sales/inventory workflows | `usl_b2c`; archive bridge in `usl_documents_b2c` | **B2C > Orders**, **Operations**, **Accounting Sessions**, **Analytics**, **Configuration** | [B2C product contract](b2c-sales-inventory.md), [operator guide](../operations/b2c-operations.md), [user guide](../users/guides/review-b2c-commerce.md), [metric contract](../accounting/b2c-metrics.md) | **Integrated**; evidence and relationship dispositions are complete, while the separately governed physical opening-stock count is not yet available |
| Native Odoo Documents workspace backed by Paperless-ngx search, OCR, previews, metadata, versions, Trash, smart views and governed links to business records | `usl_documents`; contextual bridges in `usl_documents_accounting` and `usl_documents_b2c` | **Documents** application and Documents smart buttons on authorized records | [Documents product](documents-paperless.md), [architecture](documents-paperless-architecture.md), [user QA guide](../users/guides/test-paperless-documents.md), [operations runbook](../operations/paperless-documents-runbook.md) | **Integrated baseline**; the separate Paperless 3.0 workstream and final full-archive qualification remain unmerged release work |
| Restored native Projects and tasks with dependencies, milestones, recurrence, chatter, evidence and tracking compatibility | native `project` plus `usl_project` | **Projects**, project overview, task forms and chatter | [Work management](work-management.md), [restoration runbook](../operations/project-restoration.md), [QA guide](../operations/project-restoration-qa-guide.md) | **Integrated** |
| Source-backed business collaboration history across Accounting, Projects, HR, Documents, declarations and Sign evidence | native Odoo mail models; declaration chatter in `rebuild_account_migration`; one-shot `migration/collaboration_restore` | Chatter, activities and followers on each authorized business record; **Discuss** for retained conversations | [Collaboration restoration](../operations/collaboration-restoration.md) | **Integrated**; no historical outbound queue is recreated; default/demo Knowledge and non-business technical chatter have explicit non-copy dispositions |
| External-provider payroll accounting for TESE: versioned profiles, immutable monthly snapshots, provider-PDF gate, native entries, settlement and closing controls | `usl_tese_payroll` plus `usl_tese_accounting` | **Paie TESE**, payroll records, linked entries and Accounting closing controls | [Paie TESE](paie-tese.md), [French user guide](../users/guides/paie-tese.md), [restoration runbook](../operations/tese-restoration.md) | **Integrated**; TESE remains the legal payroll calculator |
| Content-platform payout billing with monthly sessions, payouts, native customer invoices, commission bills, compensation and bank settlement | `usl_platform_billing`; optional identity-role bridge in `usl_platform_billing_pocketid` | **Platform Billing** sessions, payouts and bank-import/reconciliation actions | [Platform Billing product](platform-billing.md), [accounting design](../accounting/platform-billing.md), [operator guide](../users/how-to/process-platform-payouts.md) | **Integrated** |
| Pocket ID OIDC login with PKCE, state/nonce/JWKS validation, immutable identity links, named-user policy and sealed emergency access | `usl_pocketid` over pinned OCA `auth_oidc` | Odoo sign-in and governed user/identity configuration | [Pocket ID architecture](pocket-id-sso.md), [sign-in guide](../users/how-to/sign-in-with-pocket-id.md), [operations runbook](../operations/pocket-id-sso-runbook.md) | **Integrated**; external production provider configuration is environment-specific and applied after reconstruction |
| Recoverability-based named roles, explicit Agent denial, separately governed irreversible actions and immutable security audit evidence | `usl_access_control` over native ACLs, record rules and company scope | User **Access Rights**, protected workflows and **Settings > Distribution Audit** | [Distribution access control](distribution-access-control.md), [operations runbook](../operations/distribution-access-control-runbook.md), [action review procedure](../agents/distribution-access-risk-inventory.md) | **Integration candidate qualified**; clean and canonical backend acceptance pass, while merge-commit PR and live browser acceptance remain |
| French-first terminology, European date presentation, company context and company-aware list presentation | `usl_locale` plus translations owned by each feature | All affected backend views; **Settings > Companies** for company presentation | [European date presentation](european-date-presentation.md), [localization rules](../agents/french-localization.md), [multi-company Accounting](../accounting/multi-company-accounting.md) | **Integrated** |
| French electronic-invoice reception for UBL, CII and Factur-X, duplicate/retry controls, review states and guarded activation | `rebuild_account_migration` over native Accounting/localization capabilities | **Vendors > Incoming E-Invoices** and **Configuration > Invoicing > E-Invoicing** | [Reception readiness](../accounting/french-electronic-invoicing-readiness.md), [validation evidence](../accounting/french-electronic-invoicing-validation.md), [activation runbook](../operations/activate-french-electronic-invoicing.md) | **Ready but inactive**; live reception and e-reporting flags remain `0` until separately approved |
| Reproducible local development, QA, pre-production candidates, backup/recovery, immutable image publication and controlled production admission | Compose files, `Dockerfile`, `scripts/`, `deploy/`, and `.github/workflows/product-image.yml` | Command-line operational workflows; no business-user menu | [README](../../README.md), [environment policy](../operations/environment-and-release-policy.md), [pre-production release](../operations/preproduction-release.md), [production readiness](../operations/production-cutover-readiness.md) | **Integrated tooling**; the final production candidate has not yet been admitted |

## Delivered add-on inventory

Only modules under `custom-addons/` are considered USL add-ons on the normal
product path. Migration modules under `migration/` are deliberately excluded.

| Technical module | What it implements | Classification |
| --- | --- | --- |
| `rebuild_account_migration` | Accounting cockpit, Hygiene, Controls, report definitions and presentation, declarations, closing, e-invoice readiness/reception, navigation and stable operational XML IDs | Delivered product; **transitional owner** despite its historical name |
| `usl_access_control` | Named Distribution roles, irreversible-action enforcement, Agent denial, action-policy runtime and immutable audit events | Delivered security foundation on the integration candidate |
| `usl_accounting` | Shared native/OCA Accounting extensions, expense bank matching, foreign-currency settlement, scheduled bank statements, fiscal-year API, analytic measures and evidence security | Delivered product foundation |
| `usl_b2c` | Canonical B2C channels, orders, lines, events, SKU aliases, sessions, evidence controls and analytics | Delivered product application |
| `usl_documents` | Paperless-backed Documents application, metadata cache, business links, versions, operations, access policy and browser client | Delivered product application |
| `usl_documents_accounting` | Authorized Documents links and exact-version evidence for Accounting, declarations, closing and bank statements | Delivered integration module |
| `usl_documents_b2c` | Authorized Documents links and smart buttons for B2C orders, events, evidence and accounting sessions | Delivered integration module |
| `usl_document_templates` | Governed report bindings and output policies, company legal readiness, renderer client, immutable correspondence and PDF provenance | Delivered product foundation on the integration candidate |
| `usl_expense_batch` | Optional expense grouping, shared context, review, security and native workflow integration | Delivered product feature |
| `usl_locale` | European date conventions, company theming and company-aware list presentation | Auto-installed product foundation |
| `usl_platform_billing` | Platform configurations, sessions, payouts, generated native accounting documents and settlement | Delivered product application |
| `usl_platform_billing_pocketid` | Governed Pocket ID profile mapping for Platform Billing administrators | Auto-installed integration module |
| `usl_pocketid` | Pocket ID OIDC authentication and identity governance | Delivered security integration |
| `usl_project` | Focused Community Project compatibility and task presentation | Delivered product extension |
| `usl_tese_payroll` | External-provider payroll records, evidence, accounting and settlement | Delivered product application |
| `usl_tese_accounting` | TESE state and evidence in Accounting closing controls | Delivered integration module |
| `usl_bootstrap` | Reproducible synthetic local/demo baseline | **Test-only**; forbidden from production dependency graphs |

The detailed dependency and ownership rationale lives in the
[custom add-on architecture](../accounting/custom-addon-architecture.md).

## Maintained OCA functionality

The Distribution reuses reviewed OCA modules instead of reimplementing
Community gaps. The authoritative pins are in `scripts/sync-oca-addons` and are
documented in the [OCA integration boundary](../accounting/custom-addon-architecture.md#oca-integration-boundary).
They cover:

- OIDC authentication;
- financial and partner statements;
- bank reconciliation;
- CAMT, QIF, spreadsheet and OFX statement import;
- assets, tax balances and financial tooling;
- XLSX/reporting support and selected server UX helpers.

Compatibility adaptations for the pinned SaaS 19.3 runtime live under
`oca-patches/saas-19.3/`. They are applied deterministically and are not copied
into USL product modules.

## Deliberate Odoo-core divergence

The fork keeps upstream Odoo core unchanged except for three documented
files:

- `addons/account/models/account_move.py`;
- `addons/account/wizard/account_resequence.py`;
- `addons/web/static/src/webclient/actions/action_service.js`.

The two Accounting files route journal sequence and resequencing behavior
through the same company-governed fiscal-year calculation used by reports,
declarations and closing. Upstream SaaS 19.3 has no sufficient extension point
for this case. The trade-off, tests and removal rule are documented in the
[fiscal-year boundary contract](../accounting/fiscal-year-boundaries.md) and
[custom add-on architecture](../accounting/custom-addon-architecture.md#upstream-core-patches).

The webclient patch preserves a dynamic action display name from Odoo's
navigation state when browser history rebuilds the current controller. This
keeps project-specific task titles in the browser tab and breadcrumb instead
of falling back to the static **Tasks** action name. Changing that static name
cannot represent multiple projects, while a custom add-on cannot safely patch
the private action-manager closure where history controllers are rebuilt. The
patch is limited to restoring the already-authoritative navigation-state
label, has focused webclient regression coverage, and should be removed when
upstream Odoo preserves dynamic display names on history restoration.

No other product-specific Odoo-core divergence is permitted without an
explicit architecture decision and regression evidence.

## One-shot migration and reconstruction tooling

Migration is a production-critical delivery tool, not part of the final Odoo
product. The canonical flow is orchestrated by `scripts/target-reconstruct`
and finalized by `scripts/target-finalize`. Temporary add-ons are loaded from
their dedicated migration paths, then uninstalled and removed from the final
registry. The final database must have no migration models, menus, fields,
source bindings or technical reconstruction provenance.

| Migration area | Responsibility | Durable reference |
| --- | --- | --- |
| `migration/source_truth` | Whole-source coverage ledger, stage ownership and fatal unexplained-scope checks | [Source-truth migration](../operations/source-truth-migration.md) |
| `migration/runtime_preflight` | Resource and runtime safety checks before destructive reconstruction stages | [Accounting development workflow](../operations/accounting-development-workflow.md) |
| `migration/accounting_restore` | Native Accounting reconstruction, exact ledger/reconciliation parity and temporary source identity | [Accounting restoration boundary](../../migration/accounting_restore/README.md) |
| `migration/attachment_ledger` | Source attachment disposition and completeness evidence | [Source-truth migration](../operations/source-truth-migration.md#attachment-disposition-ledger) |
| `migration/product_restore` | Product templates, variants, attributes, values, categories, costs, warehouses and locations | [Product master restoration](../operations/product-master-restoration.md) |
| `migration/identity_restore` | Users, companies and governed target identity preparation | [Identity restoration](../operations/identity-restoration.md) |
| `migration/hr_restore` | Employees, versions and related HR configuration needed by product workflows | [Source-truth migration](../operations/source-truth-migration.md) |
| `migration/project_restore` | Native Projects, tasks, messages, tracking, followers, dependencies and evidence | [Projects restoration](../operations/project-restoration.md) |
| `migration/tese_restore` | TESE profiles, payroll records, PDFs, accounting relationships and parity | [TESE restoration](../operations/tese-restoration.md) |
| `migration/platform_billing_restore` | Historical platform configurations, sessions, payouts and native Accounting links | [Platform Billing restoration](../operations/platform-billing-migration.md) |
| `migration/b2c_restore` | Locked provider evidence, historical B2C records, accounting coverage, SKU dispositions and Documents links | [B2C migration](../operations/b2c-migration.md), [source-field matrix](../../migration/b2c_restore/source-field-matrix.md) |
| `migration/documents_archive` | Paperless archive reconstruction, checksums, access, business links and resumable evidence processing | [Documents archive boundary](../../migration/documents_archive/README.md) |
| `migration/collaboration_restore` | Final source-wide restoration of native chatter, tracking, followers, activities, recipients, reactions and evidence relationships after operational and Documents reconstruction | [Collaboration restoration](../operations/collaboration-restoration.md) |
| `migration/bank_statement_ingestion` | One-time exact-FITID adoption of migrated Shine statement lines before scheduled ingestion starts | [Bank identity cut-over](../../migration/bank_statement_ingestion/README.md) |

The product/migration separation is enforced by
`make product-migration-boundary`, database-boundary checks and finalization.
See the [product and migration boundary](../agents/product-migration-boundary.md).

## Important non-features and boundaries

The Distribution intentionally does **not**:

- manufacture historical sale orders, payments, invoices, stock moves,
  valuation layers or opening quantities when source evidence is incomplete;
- claim that monthly aggregate B2C accounting is a one-to-one allocation to
  individual orders;
- calculate the French legal payroll that TESE owns;
- copy Paperless archive binaries into Odoo as a second document authority;
- let Pocket ID claims assign Odoo roles or companies;
- enable live banking, electronic-invoice reception or e-reporting merely
  because offline tests pass;
- permit probabilistic or autonomous accounting posting;
- ship source-dump importers or migration diagnostics in the end-user product.

Paperless 3.0, Collaboration History, the migration-performance cache, and
Distribution Access Control and the project/task browser-title fix are
consolidated on the current production-migration readiness candidate with their
reviewed ancestry preserved. Native Sign remains the named release workstream.
Neither feature worktrees nor integration/archive branches are production
authority; the consolidated result must pass final qualification and land on
`19-usl` through an auditable merge commit.

## Inspecting the literal fork delta

Documentation explains business intent; Git and manifests are the exact code
inventory. From the repository root:

```bash
# First-parent Distribution history
git log --first-parent --oneline \
  aef56898d9ea5a97948af04c03ae101d17b8b4a3..19-usl

# Every USL module on the delivered add-ons path
find custom-addons -mindepth 2 -maxdepth 2 -name __manifest__.py -print | sort

# Literal file delta against the integrated upstream baseline
git diff --name-status \
  aef56898d9ea5a97948af04c03ae101d17b8b4a3..19-usl
```

When implementation and documentation disagree, resolve the discrepancy; do
not silently reinterpret product behavior. Update this map whenever a product
module, user-facing capability, core patch, migration stage or delivery state
is added, removed or materially reassigned.
