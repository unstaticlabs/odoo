# Accounting configuration capability matrix

Last updated: 2026-07-25

Status vocabulary:

- `Implemented`: retained through a native Community or maintained OCA model
  and exposed through a supported route.
- `Replaced`: the Enterprise-specific surface is represented by a documented
  Community/OCA mechanism with the same required accounting outcome.
- `Not applicable`: source usage and the USL legal/operating scope provide no
  records or current requirement.
- `Deferred`: deliberately outside Accounting v1, with the retained boundary
  named explicitly.

This matrix is the configuration-specific companion to the generated
source-report/capability parity matrix. Professional acceptance remains
separate from technical availability.

| Online configuration family | Accounting v1 treatment | Status | Evidence and boundary |
| --- | --- | --- | --- |
| Accounting settings | Source company fiscal dates, lock dates, tax/cash-basis settings and accounting properties are replayed into native company/settings fields. | Implemented | Exact target validation compares the material company and tax behavior; manager access is native. |
| Chart of Accounts | Source accounts, types, reconciliation flags, currencies and company relations are source-traced in native `account.account`. | Implemented | Exact replay/validation and duplicate-trace invariants. |
| Account Groups | Native `account.group` with an explicit manager route; dump import preserves company-specific prefix ranges, hierarchy and translations. | Implemented | `Configuration > Accounting > Account Groups`; `TestRebuildAccountMigration.test_account_group_import_preserves_prefix_hierarchy_and_is_idempotent`. |
| Journals | Source journals, codes, currencies, accounts and payment-method lines are reconstructed in native `account.journal`. | Implemented | Exact replay/validation; journal dashboard and transactions remain native. |
| Bank statement files | Maintained OCA import creates native statements and transactions from CAMT, QIF and configurable CSV/XLSX layouts. Import stays contextual on each bank journal; the generic top-level import menu is hidden. | Implemented | Fresh-database wizard tests cover format discovery, attachment retention and a real QIF transaction. The browser journey proves import to full-width Transactions and an exact-reference match with visible counterpart lines and Undo. Live bank synchronization remains deferred. |
| Taxes | Source taxes, scopes, amounts, cash-basis behavior, children, alternatives and repartition lines are source-traced. | Implemented | Exact tax-configuration comparison and tax-report controls. |
| Tax Groups | Source tax groups are source-traced and exposed through an explicit manager route. | Implemented | Exact comparison plus `Configuration > Accounting > Tax Groups`. |
| Tax Tags | Source tags and repartition/tag relations are source-traced and exposed through an explicit manager route. | Implemented | Exact comparison plus `Accounting and Tax Tags`. |
| Tax Units | The restored source has zero tax-unit rows and Community has no equivalent USL requirement. | Not applicable | French declarations are company/fiscal-period scoped instead of inventing an empty tax-unit abstraction. |
| Fiscal Positions | Source fiscal positions and all tax/account mappings are reconstructed natively. | Implemented | Exact import statistics and reference-integrity failures. |
| Currencies and Rates | All source currencies and 1,877 historical rates are preserved in native `res.currency.rate`; manager execution and daily automation fill every missing ECB publication day after the protected coverage boundary without overwriting restored or manual rates. | Implemented | Exact broad-snapshot comparison, multi-day backfill, source/manual preservation, idempotence and provider/browser evidence. |
| Fiscal Years | Company-configured fiscal-year end and explicit first fiscal-year boundaries drive report, declaration and closing periods through the same Odoo fiscal-year computation. | Implemented | Report/PDF and closing/declaration tests prove the exceptional `10/01/2024`–`30/09/2025` first exercise before the recurring 1 October–30 September cadence. |
| Lock Dates | Native fiscal, tax, sales, purchase and hard-lock fields, with manager-only closing application. | Implemented | Closing controls, lock transition tests and scoped reviewer denial. |
| Accepted closing packages | Accepted XLSX/PDF closing packages are copied into immutable, company-scoped snapshots before standard lock dates can be advanced. | Implemented | Snapshot payload, SHA-256, decision context and reviewer metadata are frozen; write/unlink is denied and acceptance tests cover the gate. |
| Analytic Plans and Accounts | Source plans, accounts, distributions, lines and audited corrections use native analytic models. | Implemented | Exact/Track B analytic comparison and native list/pivot/graph routes. |
| Accounting Reports | One original Community-compatible workbench plus maintained OCA comparison surfaces covers the retained source report families. | Replaced | Generated 38-report technical evidence matrix; professional formula/presentation acceptance is pending. |
| Revenue versus spending trend | A native SQL-backed graph, pivot and exportable list derive monthly revenue, spending and net contribution from posted journal items. | Implemented | The exact target validates 27 rows for October 2025–June 2026 and totals of EUR 176,928.45 revenue, EUR 101,215.69 spending and EUR 75,712.76 net contribution. |
| Report Groups or Variants | Report families and source variants are catalogued; PCG 2024 statement variants have explicit target actions and metadata. | Replaced | Association variants are explicit USL-scope exclusions pending stakeholder/accountant acceptance. |
| Depreciation Models | Maintained OCA asset profiles replace the Enterprise depreciation-model surface for native current workflows. | Replaced | Source assets/schedules remain historical evidence; Track B profile, posting and idempotence tests pass. |
| Tax Return Types | Versioned declaration rules, applicability profiles and filing-state records replace the Enterprise tax-return-type UI. | Replaced | Rules cover the applicable/conditional French forms and retain official version/source metadata. |
| Bank Matching Rules | Source conditions, journal/partner scopes and counterpart lines remain native `account.reconcile.model` records consumed by OCA Bank Matching. The compact list combines usage and open matches as Activity, highlights automated triggers, and separates executable accounting rules, incomplete rules and partner-only mappings made redundant by evidence-backed partner inference. The form keeps native conditions/results editable, folds optional evidence and only surfaces actionable recommendations. **Find** creates deterministic suggestions from repeated reconciled patterns; deterministic or AI-authored proposals are inert until manager approval. | Implemented | On `odoo_dev` company 1, the assessment classifies the native imported and delivered rules as executable, redundant partner-only or needing review; source history and live open-match counts remain drillable. The real list-header RPC contract, manager boundary, view label, company scope, idempotency, empty result, proposal exclusion and approval are regression-tested. |
| Payment Terms | Source payment terms and lines are reconstructed in native models. | Implemented | Exact import statistics/reference mapping; native configuration route. |
| Incoterms | Native `account.incoterms` with an explicit manager route; no custom semantic fork. | Implemented | `Configuration > Invoicing > Incoterms`; clean add-on navigation test. |
| Financial Budgets | The restored source does not have `account_budget` installed and no retained USL budget records exist. | Not applicable | Reassess only when an approved budgeting operating model and source dataset exist. |
| Multi-company | Both scoped source companies, complete charts and journals are reconstructed. Operational records remain isolated; authorized users can select companies for same-currency combined management statements or company-specific detail. Provider-controlled ECB history is synchronized between same-currency companies without overwriting manual or transaction rates. A governed user receives one native employee profile per allowed company for seamless expense entry without merging HR or payroll. Used payment methods map to native Community behavior; unused Enterprise-only transports are classified rather than imitated. | Implemented | Exact configuration and ledger parity, shared-rate and expense-profile tests, global report-model rules, reviewer isolation, reversible second-company workflow acceptance, aggregate/contribution/drill-down tests and different-currency rejection. |
| Legal consolidation and multi-ledger | Ordinary multi-company totals are not presented as legal consolidation. Account mappings, eliminations, consolidation ledgers and currency-translation adjustments require a separate approved design. | Deferred | The source has no retained consolidation setup. FEC, tax and closing outputs stay one company at a time. |
| Electronic declaration submission | Accurate preparation, validation, portal guidance and external filing tracking are retained without an electronic filing client. | Deferred | Accounting v1 explicitly does not require electronic submission. |

## Alternatives retained

For source reconciliation rules, three credible treatments were considered:

1. preserve them in native `account.reconcile.model` and let maintained OCA Bank
   Matching consume them;
2. build a separate USL suggestion engine;
3. keep the rules as review-only source evidence.

The first is implemented because it retains executable operating configuration
without duplicating Odoo/OCA matching logic. A small governance layer exposes
source and target usage, current matching opportunities and structured proposal
evidence. Partner-only source rules are identified as redundant because OCA
excludes them from executable proposals and the separate evidence-backed
partner service now owns that responsibility. Suggested rules are native,
reviewable `account.reconcile.model` records marked inert until approval; the
OCA query is extended only to enforce that safety boundary.

For Enterprise configuration surfaces generally, a direct proprietary module
dependency was rejected because the Community deployment does not contain those
modules. The chosen order is native Community, maintained OCA, and then a small
original USL workflow only for a demonstrated gap.

For the management trend, a live view over posted native journal items was
selected over a copied monthly summary or another reporting engine. It stays
current, respects company scope and retains native journal-item drill-down.
For closing evidence, copying the accepted package bytes and decision context
into an immutable snapshot was selected over retaining only a mutable
attachment reference; the latter could not prove what the reviewer accepted.
