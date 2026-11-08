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
| Projects and tasks with dependencies, chatter, attachments, stage history and favorite-project navigation | native Projects, `usl_project` | **Projects** | Operational |
| Paperless-backed Documents, OCR, previews, metadata, versions, Trash, search and business links | `usl_documents`, `usl_documents_accounting`, `usl_documents_b2c` | **Documents** and record smart buttons | Operational |
| Governed official PDFs and correspondence | `usl_document_templates`, document renderer | Native print actions, **Official Documents** | Operational |
| Electronic signatures and completion evidence | `usl_sign` | **Sign** | Operational in production with server-managed certificate material |
| Pocket ID OIDC authentication and named-user governance | `usl_pocketid`, `usl_access_control` | Odoo sign-in and **Settings** | Operational in production |
| Company-scoped roles, owned autonomous Agents, irreversible-action controls and immutable audit events | `usl_access_control` | **My Agents**, access rights and protected actions | Operational |
| Historical commerce evidence and native future sales/inventory foundations | `usl_b2c`, `usl_documents_b2c` | **B2C**, **Sales**, **Inventory** | Variants, locations, traceability, UoM and Landed Costs available; physical opening inventory and advanced automation remain |
| Channel export ingestion into native sales, deliveries, invoices, destination VAT and settlement | `usl_b2c_ingest`, `usl_b2c` | **B2C > Channel Imports** | Delivered and proven against a production clone; the chart corrections it requires and the first production drop remain |
| French-first terminology, European dates and company-aware presentation | `usl_locale` | All affected backend views | Operational |
| French electronic-invoice reception for UBL, CII and Factur-X | `rebuild_account_migration`, native Accounting/localization | **Vendors > Incoming E-Invoices** | Ready but inactive pending approved-platform production onboarding |
| Odoo task cards in Mattermost and links between projects and Mattermost channels | `usl_mattermost`, Mattermost plugin | **Projects** (project form) and Mattermost | Shipped in the image; inactive until the deployed stack mounts the plugin and webhook key files |
| Agent-authenticated Odoo automation endpoint and tool contract | separately built `odoo-mcp` image admitted by its environment GitOps ledger | **My Agents** and the MCP service endpoint | MCP operational; governed Agent identities are qualified for compatibility, while MCP advances independently |

Detailed behavior belongs in the relevant product, Accounting, user and
operations documents rather than in this inventory.

## Delivered add-ons

Only `custom-addons/` is part of the normal USL add-ons path.

| Module | Durable responsibility |
| --- | --- |
| `rebuild_account_migration` | Accounting cockpit, Hygiene, configurable controls, reports, declarations, closing and electronic-invoice readiness. The historical technical name is retained to preserve installed model and XML-ID ownership; the module is delivered product code. |
| `usl_access_control` | Named roles, owned autonomous identities, delegated-authority enforcement, governed API credentials, irreversible-action enforcement and security audit evidence. |
| `usl_accounting` | Native/OCA Accounting extensions, expense and bank matching, foreign-currency settlement, [shared Remaining-balance reviews](shared-reconciliation-reviews.md), fiscal-year behavior, scheduled statements, analytics and evidence security. |
| `usl_b2c` | Commerce channels, orders, events, SKU aliases, accounting sessions, controls and analytics. |
| `usl_b2c_ingest` | Etsy, Medusa and Printful ingestion: format detection, deduplication, product mapping, supplier cost, readiness checks, native sales and deliveries, invoicing, commission billing and the OSS return. |
| `usl_document_templates` | Governed report bindings, renderer integration, immutable correspondence and PDF provenance. |
| `usl_documents` | Paperless-backed Documents application, cached metadata, links, versions, operations, access policy and browser client. |
| `usl_documents_accounting` | Authorized Accounting evidence links and exact-version archive controls. |
| `usl_documents_b2c` | Authorized B2C document links and smart buttons. |
| `usl_expense_batch` | Optional expense grouping, shared context, review and native workflow integration. |
| `usl_feedback` | Shared conversational product feedback, private local page previews, bounded assistant processing and reporter-confirmed Project workflow. |
| `usl_home` | Personal launcher, durable destinations and bounded attention summaries. |
| `usl_locale` | European date conventions and company-aware presentation. |
| `usl_mattermost` | Signed API for the Mattermost plugin, task-card refresh and project-channel links. Its source is pinned, not tracked; see below. |
| `usl_platform_billing` | Platform sessions, payouts, generated native Accounting documents and settlement. |
| `usl_platform_billing_pocketid` | Pocket ID role mapping for Platform Billing administrators. |
| `usl_pocketid` | Pocket ID authentication and identity governance. |
| `usl_project` | Focused Project compatibility, task presentation and favorite-project navigation. |
| `usl_sign` | Signature requests, operations, evidence and completion records. |
| `usl_tese_payroll` | External-provider payroll records, evidence, Accounting and settlement. |
| `usl_tese_accounting` | TESE state and evidence in Accounting closing controls. |
| `usl_bootstrap` | Synthetic test fixtures only; forbidden from production dependency graphs. |

### Pinned external add-ons

`usl_mattermost` lives in the public repository
[unstaticlabs/mattermost-odoo](https://github.com/unstaticlabs/mattermost-odoo),
next to the Mattermost plugin that calls it.
`deploy/mattermost-odoo/release.json` pins one commit of that repository.
`scripts/sync-external-addons` exports the module tree of that commit to
`custom-addons/usl_mattermost/`, which Git ignores. `scripts/sync-oca-addons`
and `make oca-addons-sync` run it, so every build, test and check that prepares
the OCA add-ons also gets the module. The release workflow checks out the
pinned commit by name, without a token, and gives it to the script through
`USL_MATTERMOST_ODOO_SOURCE`.

The pinned commit must be in the history of `main` of that repository. GitHub
also serves a commit that exists only in a fork through the URL of the parent
repository, so a checkout by SHA alone accepts a fork-only commit.
`scripts/mattermost-odoo verify` refuses such a pin. The qualification check
and the release workflow run it before they use the pin. On a pull request,
`scripts/mattermost-odoo review` puts the `git diff --stat` and the diff of
the module between the old and the new pin into the job summary.

To ship a new version of the module, merge it to `main` of mattermost-odoo,
change `commit` in the pin file, and run `make external-addons-sync`. Do not edit `custom-addons/usl_mattermost/`: the
next sync replaces it. A pin change changes the `distribution` component
identity and the module source, so it needs the same action-risk refresh as
any other change under `custom-addons/`.

Without the key files that `USL_MATTERMOST_PLUGIN_KEY_FILE` and
`USL_MATTERMOST_WEBHOOK_KEY_FILE` name, the module answers `not_configured` and
its scheduled action returns without a request.

The detailed dependency policy is in
[`docs/accounting/custom-addon-architecture.md`](../accounting/custom-addon-architecture.md).

## Maintained OCA functionality

Reviewed OCA modules provide OIDC, financial and partner statements, bank
reconciliation, statement imports, assets, tax balances, XLSX reports and
selected server UX helpers. Exact pins live in `scripts/sync-oca-addons`.
Compatibility patches are applied deterministically from
`oca-patches/saas-19.3/`; they are not copied into USL modules.

## Odoo core patches

Everything the Distribution changes under `addons/`, `odoo/` and
`requirements.txt` relative to upstream `saas-19.3` is listed here, with the
reason and the commit that introduced it. The list is checked against
`git diff --stat <upstream merge base> HEAD -- addons odoo requirements.txt`;
a file in that output and not on this page is a defect of this page. Where
the last touch of a file is an upstream catch-up (`9c030166`), the originating
commit is the one named.

### Accounting sequences — `account`

- `addons/account/models/account_move.py` and
  `addons/account/wizard/account_resequence.py` (`33e5aa55`, replay of the USL
  product on saas-19.3): journal sequences and the resequence wizard derive the
  fiscal-year range from `compute_fiscalyear_dates`, so a staggered fiscal year
  gives the same `YY-YY` prefix everywhere instead of recomputing it from the
  company's last day and month.

### Link preview — `mail`

- `addons/mail/tools/link_preview.py` (`7868e87e`): unknown remote charset
  names go through the existing UTF-8 fallback, so optional previews do not
  fail after a message is sent. Upgrade cost and removal condition are in
  [`DECISIONS/0002-link-preview-unknown-charsets.md`](../../DECISIONS/0002-link-preview-unknown-charsets.md).

### Navigation — `web`

- `addons/web/static/src/search/` (`search_model.js`, `breadcrumbs/`,
  `with_search/`), `addons/web/static/src/views/` (`list/`, `view.js`,
  `standard_view_props.js`) and
  `addons/web/static/src/webclient/actions/action_service.js` (`c41fefbc`,
  `d64743df`, `e3c2d420`, `38d020cb`, `f441025a`, `8dbacae6`): restore a valid
  dynamic action name when browser history recreates a controller, keep
  portable search, sort and pagination choices in the URL, and render an
  unnamed history entry as a plain Back arrow instead of an *Unnamed* label.
  The contract is [`docs/product/navigation.md`](navigation.md); its tests
  live under `addons/web/static/tests/search/` and
  `addons/web/static/tests/webclient/actions/` (including `86f1d13b`).
  `concurrency.test.js` carries one line from `547f074d`, which gave the
  fixture action a name while stabilizing the Web suite under these patches;
  it is test-only and stays with them.
- `addons/web/static/src/webclient/navbar/navbar.js` (`d3fb00af`, project
  favorites): the compact *More* menu re-renders whenever the collapsed
  section ids change, not only when their count or first app changes, so a
  same-count replacement (a favorite project swapped for another) updates it
  immediately. Test: `addons/web/static/tests/webclient/navbar.test.js`.

### Agents' effective API access — `api_doc`

- `addons/api_doc/controllers/api_doc.py`, `models/__init__.py`,
  `models/base.py` (`bb37d049`): `/doc` reports each model's effective
  `read`/`create`/`write`/`unlink` access for the caller, and `base` gains
  three hooks (`_api_doc_access`, `_api_doc_public_method_allowed`,
  `_api_doc_cache_vary`) that distribution modules override to hide public
  methods the Agent policy denies and to fold that policy into the cache key.
  `93068462`: the generated index document is cached as the real superuser,
  because distributions that keep an Agent actor through `sudo()` would
  otherwise reject this internal write. Test: `addons/api_doc/tests/test_doc.py`.

### Factur-X and UBL import — `l10n_fr_pdp`

- `addons/l10n_fr_pdp/models/account_edi_xml_ubl_21_fr.py` (`b2a546a6`) and
  `account_edi_xml_cii_facturx.py` (`7a904380`): a bill that settles an
  advance bill is matched to it by supplier, company and currency, the
  deductions are created on the destination draft with native tax reversal,
  and the posted source bill, its reconciliation, manual tax amounts and
  analytic allocation are preserved; the CII commit applies the same contract
  to the upstream CII importer and adopts the corrected native billing-period
  exporter. Tests: `addons/l10n_fr_pdp/tests/test_downpayment_import.py` and
  the extended `test_cii_import_facturx_fr.py`.

### Posted completion dates — `mrp_account`

- `addons/mrp_account/models/mrp_production.py` (`f40828c4`): marking an
  order done writes its completion date once without tripping the guard that
  forbids changing the end date of an order with journal entries; a later
  manual edit of a done order still trips it. Test:
  `addons/mrp_account/tests/test_mrp_account.py`.

### Reload safety — `odoo/tools/convert.py`

- `odoo/tools/convert.py` (`9b42238d`): on a module update, the eight native
  manager groups listed in `_USL_NATIVE_SEED_GROUPS` keep their configured
  members, and the company report-style attachment keeps its original row and
  blob when its bytes change. The XML IDs and the native asset-loader
  behaviour are reviewed at every upstream sync. Test:
  `addons/web/tests/test_base_document_layout.py`.

### Dependencies — `requirements.txt`

- Distribution pins added: `httpx` and `openupgradelib` (`bc1f8c0a`, linked
  receipts and OCA queue_job migration), `markdown-it-py`, `phonenumbers` and
  `python-jose` (`33e5aa55`), `opentimestamps` (`89c01bcd`, Sign manifest
  proofs), `responses` (`ad39ab79`, OCA `auth_oidc` test import).
- Upstream's own pins are bumped by Dependabot's `compatible-python-updates`
  group and by `cryptography` maintenance (`2b139980`, `574fc7b5`,
  `39568997`), so the file differs from upstream on most version lines.

### Test-only additions

Files that add fork coverage to upstream suites without changing behaviour:
`addons/account/tests/common.py` (`547f074d`: the generic Accounting fixture
also gets `usl_access_control.group_irreversible_actions` when that module is
installed, so company creation in `setUpClass` stays authorized),
`addons/account_edi_ubl_cii/tests/test_cii_export_facturx_fr.py`
(`0ce5f01c`), `addons/payment/tests/test_flows.py` (`c56de39b`),
`addons/sale_project_stock_account/tests/test_analytics_reinvoice.py`
(`e7273316`), `addons/mail/tests/test_link_preview.py` (`7868e87e`), plus the
tests named with their patches above.

### Formatting only

- `addons/sale_management/static/src/fields/sale_order_template_line_field/sale_order_template_line_field.js`:
  one line differs by trailing whitespace that upstream carries and the
  catch-up reapply `9c030166` stripped. `scripts/commit` refuses to reintroduce
  trailing whitespace, so the line stays; `git diff -w` against upstream is
  empty.

### Retired

- The `USL-TRACE` sign-out instrumentation in `odoo/http/session.py`
  (`87e0110b`, 2026-09-10) was retired on 2026-11-08 by decision of Valentin;
  the file is identical to upstream again. The record is
  [`docs/archive/operations/session-signout-investigation-20260910.md`](../archive/operations/session-signout-investigation-20260910.md).

Each patch should be removed when upstream provides an equivalent extension
point or behaviour. No other product-specific core divergence is allowed
without an explicit architecture decision, upgrade analysis and regression
evidence.

## Runtime and release cohort

Odoo, PostgreSQL, Paperless, its broker and archive state, the document
renderer, Sign services, isolated receipt-fetcher and receipt-egress images,
and the separately built MCP image form one coordinated release and recovery
cohort. The shared MsgVault-owned Ollama service is an external dependency:
releases record and validate the required BGE model, manifest and embedding
dimension without managing or restoring that service. A release records exact
source commits, image digests, modules, configuration identity and backup
identity. Tags alone are not deployment authority.

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
