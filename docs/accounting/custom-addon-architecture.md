# USL Accounting add-on architecture

Status: accepted architecture decision
Baseline: Odoo Community `saas~19.3` at
`363b4bb23a56139ca237c833a8348a662b8387f6`

## Decision

USL keeps one forked distribution repository. Odoo core, the exact OCA
integration pins, the isolated USL add-ons, deployment code, reconstruction
harness and durable specifications must evolve together for a release to be
reproducible. Splitting those concerns into separate distribution repositories
would add version coordination without improving runtime isolation.

Runtime ownership inside the repository follows this order:

1. native Odoo Community;
2. maintained OCA functionality;
3. isolated USL add-ons;
4. a fork-level Odoo patch only when no stable extension point exists.

The verified production add-on dependency direction is:

```text
 pinned OCA auth_oidc ---> usl_pocketid --------------------+
                                      \--> usl_documents    |
 native web ------------> usl_locale ----> usl_accounting --+--> rebuild_account_migration
                                      \--> usl_documents    |
 native hr_expense -----> usl_expense_batch ---------------+
  (product compatibility and stable operational XML-ID ownership)

 native project --------> usl_project
 native HR + Accounting + usl_documents -> usl_tese_payroll
 native/OCA Accounting -> usl_platform_billing
 usl_pocketid + usl_platform_billing -> usl_platform_billing_pocketid

usl_bootstrap ---> native modules only (disposable test fixture)

migration/accounting_restore/usl_accounting_restore
  ---> rebuild_account_migration (temporary import-time dependency only)
  <--- migration/project_restore/usl_project_restore
  <--- migration/tese_restore/usl_tese_restore
migration/platform_billing_restore/usl_platform_billing_restore
  ---> rebuild_account_migration + usl_platform_billing
```

`rebuild_account_migration` remains the installed compatibility module during
the staged decomposition. It continues to own existing XML records, seeded
definitions, access records, actions, menus, views and installed `rebuild.*`
models. Shared extensions of pre-existing native and OCA models belong to
`usl_accounting`. This improves the safest dependency boundary without
destructively changing database ownership in the same release.

## Alternatives considered

### Keep the historical module unchanged

This has the lowest immediate migration risk, but production behavior,
reconstruction code and tests remain coupled. A change to reporting or
e-invoicing continues to load an 8,000-line importer and unrelated replay
extensions. Independent test and upgrade scope cannot be expressed.

### Extract cohesive feature modules while preserving database ownership

This is selected only where extracted code extends existing native or OCA
models and therefore does not take ownership of a new model. That proven
boundary is `usl_accounting`. Existing `rebuild_account_migration.*` XML IDs
stay where they are. Updating the compatibility module installs its new
dependency, then loads the same records against the same native/OCA models.

Controls, reports and electronic-invoice reception each define installed
`rebuild.*` models. Extracting those Python classes would make Odoo generate
new-module `ir.model` XML IDs even if table names stayed unchanged. They
therefore remain compatibility-owned in this increment. Dedicated modules are
a future option only with a separately rehearsed ownership migration.

### Replace the product with native or OCA modules

This remains the preferred decision for a capability that is equivalent.
Current OCA reconciliation, asset, statement-import and report utilities are
already retained. The governed USL Controls, report presentation, cash
projections, declaration workflow and safe e-invoice activation boundary do
not have equivalent Community/OCA replacements on the pinned baseline.
Replacing them during a structural refactor would be a product redesign and
is therefore rejected.

### Reassign all XML IDs to the new modules

This would make the source tree look cleaner, but it changes uninstall
ownership and can delete or duplicate configured production records. It also
breaks bookmarks, harness probes and integrations that use stable external
identifiers. It is explicitly rejected for this increment.

## Component inventory

| Component | Resulting owner | Classification | Safety evidence |
| --- | --- | --- | --- |
| Shared locale and company-scope presentation | `usl_locale` | shared runtime presentation foundation | language-format data, list-company defaults, web-client tests and repository architecture guard |
| Fiscal-year API | `usl_accounting` | runtime foundation | model/API tests and governed fiscal-year contract |
| Payment suggestions, partner inference and reconciliation extensions | `usl_accounting` | runtime foundation over native/OCA | backend and browser regression tests; OCA remains authoritative |
| Foreign-currency settlement definitions, views and payment-widget assets | `usl_accounting` | runtime foundation over native/OCA | exact/native-FX, payment-rate, reversal, ACL and browser tests |
| Company-paid expense bank matching | `usl_accounting` | runtime foundation over native expenses and OCA reconciliation | ranked-candidate, ACL, native lifecycle, rollback and reconciliation tests |
| Scheduled bank-export ingestion and monthly certification | `usl_accounting` | native statements/mail plus maintained OCA OFX parsing | provenance, duplicate, identity, balance, continuity, certification and security tests |
| Reconciliation-model intelligence | compatibility module for this stage | runtime behavior derived from native journal lines | rule behavior and OCA tests |
| Read-only evidence, analytic measures and entry-direction guard | `usl_accounting` | runtime foundation | role, analytic and direction-guard tests |
| Hygiene, Closing and Declarations | compatibility module for this stage | stable model/XML-ID ownership, left unchanged | focused lifecycle, ACL, company, period and idempotency tests |
| Interactive reports, definitions, PDF/XLSX and OCA report defaults | compatibility module for this stage | stable report/wizard-model ownership, left unchanged | report screen/export parity and report presentation tests |
| Electronic-invoice readiness and offline reception evidence | compatibility module for this stage | stable reception-model ownership, left unchanged | UBL/CII/Factur-X, duplicate, malformed, ACL and live-guard tests |
| Expense claims/batches | `usl_expense_batch` | retained independent feature | clean module and browser tests |
| External-provider payroll and TESE settlement | `usl_tese_payroll` | focused product module over native HR/Accounting and OCA matching | clean module, security, accounting and settlement tests |
| TESE closing control | `usl_tese_accounting` | focused bridge from TESE payroll to the Accounting closing workspace | period, evidence, posting and liability-state tests |
| Documents archive and access policy | `usl_documents` | Paperless-backed product module over native companies, contacts and access groups | backend/frontend suites, API/read-back and migration parity |
| Accounting document evidence | `usl_documents_accounting` | focused bridge from the Paperless source of truth to Accounting records, including mandatory exact-version bank evidence | checksum/version pinning, record-link, retry, authorization and accounting-view tests |
| Overview and cash projections | compatibility module for this stage | operational cross-feature behavior, left unchanged | native ledger, controls and report tests |
| Currency automation | compatibility module for this stage | stable wizard-model ownership, left unchanged | ECB parsing/upsert and provider ACL tests |
| Source trace, importer, native replay, parity evidence and reconstruction models | temporary `migration/accounting_restore` add-on | migration-only and uninstalled at finalization | canonical harness, idempotency and final-registry boundary gates |
| TESE source translation and parity evidence | temporary `migration/tese_restore` add-on | downstream migration-only importer, uninstalled before product acceptance | exact source counts, idempotency, finalization and product-boundary gates |
| Existing security, views, actions, menus and seeded definitions | `rebuild_account_migration` | compatibility ownership | XML-ID continuity characterization test |
| Configurable-definition mixin | compatibility module for this stage | generated model XML-ID ownership, left unchanged | XML-ID continuity characterization test |
| User-document controller | compatibility module for this stage | shared delivery, left unchanged | authenticated route and Markdown renderer tests |
| Pocket ID authentication and identity governance | `usl_pocketid` over pinned OCA `auth_oidc` | runtime authentication boundary | issuer/audience/nonce/PKCE/JWKS, identity lifecycle and named-profile tests |
| Pocket ID accountant-reviewer profile | compatibility extension over `usl_pocketid` | the stable reviewer group XML ID is still owned here; the base SSO module has no reverse Accounting dependency | clean `usl_pocketid` install plus product-profile integration test |
| Platform Billing Pocket ID profile bridge | `usl_platform_billing_pocketid` | auto-installed integration over two independent product modules | only governed administrator and break-glass profiles receive the app administrator group |
| Canonical reconstruction orchestration | `migration/`, `accounting_compat/` and repository scripts | versioned migration deliverable outside normal runtime | source parity, Project and Platform Billing finalization, product-boundary guard and target-finalization order tests |
| `usl_bootstrap` | isolated test/bootstrap fixture | testing only | no production reverse dependency; synthetic `.test` data |
| `usl_custom_placeholder` | removed | obsolete | uninstallable, no reverse dependency, addon path needs no placeholder |

Overview and user-document delivery are deliberately not forced into a module
in this step. Both span feature boundaries and moving their database records
would provide little isolation. A future extraction is acceptable only after
the compatibility module no longer needs to own their stable actions and
menus.

## Compatibility policy

- Stable model technical names, table names, field names and XML IDs do not
  change as part of source extraction.
- Existing XML/data files stay in `rebuild_account_migration` until a separate
  rehearsed ownership migration proves install, upgrade and uninstall safety.
- New runtime records and assets that have never shipped under a compatibility
  XML ID belong directly to their product module. Immediate-settlement models,
  views, security, payment-widget assets and tests therefore belong to
  `usl_accounting`; no ownership transfer is required.
- Source parity and target environment policy are separate. Odoo Online has no
  Pocket ID state; canonical `odoo_dev` receives SSO only after imported
  Accounting, identity, Product, HR, Projects, Paie TESE, Platform Billing and
  Documents data pass their controls and
  temporary migration modules are removed.
- New feature modules do not seed copies of existing definitions.
- The compatibility module depends on extracted modules, never the reverse.
- A repeated compatibility-module upgrade must not duplicate definitions or
  business records.
- Removing the compatibility module is not supported while it owns production
  XML IDs and operational product models.
- Migration-only models, permissions, fields and menus are absent from the
  delivered product registry, not merely hidden from ordinary navigation.

## OCA integration boundary

`scripts/sync-oca-addons` is authoritative for source and adaptations. It pins:

| Repository | Commit |
| --- | --- |
| `server-auth` | `f51fe1b36965b78ac935e80c6b95d7115440a1b4` |
| `account-financial-reporting` | `aa34bf33fc96fbae7fb5a2b9609b807b4e20514c` |
| `account-reconcile` | `a9bbab67e42f3b762e9c34b30b6c1a77f9c373fb` |
| `bank-statement-import` (base/file and existing formats) | `7c0f95587e3e18f76ad1e8334eb234a41a6c5d7c` |
| `bank-statement-import` (separate OFX checkout) | `861d9610f3aa24cbbdf45578ceba8377aecab8fc` |
| `server-ux` | `1372e6489daa3a639d7542f3dcd60af640fb294b` |
| `reporting-engine` | `6692523980cbc57d414935311d7f7bf1c834edc6` |
| `account-financial-tools` | `3b3b3cf0974d5452734090e5a0421e762089de75` |

Tracked patches under `oca-patches/saas-19.3/` are part of that exact
integration. A manifest version adaptation alone is not compatibility
evidence. OCA compatibility tests must also be independent of restored
candidate data: partner fixtures use unique exact evidence, date assertions
follow the configured `res.lang`, and browser tests use the current Hoot step
API. The complete `/account_reconcile_oca` tag must run in the
Chromium-enabled `test` image so browser wrappers cannot be silently skipped.
The separate OFX checkout exposes only `account_statement_import_ofx`; its
SaaS 19.3 patch uses the current binary API and `res.partner.bank` fields. It
does not replace the newer base/file pin.

## Upstream core patches

The only permitted Odoo-core divergence from the pinned baseline is:

- `addons/account/models/account_move.py`;
- `addons/account/wizard/account_resequence.py`.

Both route fiscal sequence behavior through the company-governed fiscal-year
API and are documented in
[Fiscal-year boundary contract](fiscal-year-boundaries.md). Future Odoo
upgrades must compare these files with upstream first, retain the patches only
if the extension point is still absent, and rerun the fiscal sequence tests.
This refactor does not modify either patch.

## Choosing a home for future work

- Extend native/OCA behavior in `usl_accounting` when it is a shared
  operational Accounting concern.
- Put product-wide locale presentation in `usl_locale`; feature modules must
  use Odoo date components and must not introduce browser-native date inputs
  or month-first date masks.
- Extend a governed Control, report, declaration or e-invoice model in
  `rebuild_account_migration` while it remains the compatibility owner. Keep
  the code grouped by feature and do not introduce reconstruction dependencies
  into runtime methods.
- Extend expense-claim grouping in `usl_expense_batch`.
- Extend external-provider payroll workflow in `usl_tese_payroll`; TESE remains
  the legal calculation authority. Its Documents dependency exposes official
  evidence without making Paperless authoritative for payroll accounting.
- Extend archive behavior and general record links in `usl_documents`; put
  Accounting-only archive links in `usl_documents_accounting`.
- Put source extraction, source tracing, reconstruction and parity-only code
  under `migration/`; load it only in the dedicated migration service and
  uninstall it before product acceptance.
- Do not add a new module unless the boundary improves dependency isolation,
  installability, ownership clarity or independent validation.

## Validation contract

Every architecture change must pass:

1. manifest and acyclic-dependency guards;
2. critical XML-ID and table continuity tests;
3. clean compatibility-module installation;
4. upgrade from the prior installed layout;
5. a repeated upgrade with no duplicate definitions or business records;
6. the focused tests owned by each changed feature module;
7. manager/reviewer navigation checks only when menus, views, assets or ACLs
   changed;
8. finalization proving migration models, fields, metadata and XML IDs are
   absent without changing business facts;
9. canonical reconstruction only when importer, source fields or ownership
   changed materially.
