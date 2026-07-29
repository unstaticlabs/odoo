# USL Accounting add-on architecture

Status: accepted architecture decision  
Baseline: Odoo Community `saas~19.2` at
`8a44ecc8da96e341ac472fec27352d138ed2edd7`

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

The production add-on dependency direction is:

```text
native Odoo + pinned OCA
          |
          v
   usl_accounting
      /        \
     v          v
usl_accounting_controls   usl_accounting_einvoice
     |
     v
usl_accounting_reports
     \          /
      v        v
rebuild_account_migration
  (compatibility, XML-ID ownership and reconstruction)

usl_expense_batch -> native hr_expense
rebuild_account_migration -> usl_expense_batch
```

`rebuild_account_migration` remains the installed compatibility module during
the staged decomposition. It continues to own existing XML records, seeded
definitions, access records, actions, menus and views. The source code that
implements production behavior belongs to the feature module shown above.
This distinction improves code and dependency ownership without destructively
changing database ownership in the same release.

## Alternatives considered

### Keep the historical module unchanged

This has the lowest immediate migration risk, but production behavior,
reconstruction code and tests remain coupled. A change to reporting or
e-invoicing continues to load an 8,000-line importer and unrelated replay
extensions. Independent test and upgrade scope cannot be expressed.

### Extract cohesive feature modules while preserving database ownership

This is selected. Python models, tests and non-database assets can move behind
acyclic manifests. Existing `rebuild_account_migration.*` XML IDs stay where
they are. Updating the compatibility module installs its new dependencies,
then loads the same records against the same model and table names.

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
| Shared definition mixin, fiscal-year API, currency automation | `usl_accounting` | runtime foundation | model/API tests and governed fiscal-year contract |
| Payment suggestions, partner inference, reconciliation extensions and rule intelligence | `usl_accounting` | runtime foundation over native/OCA | backend and browser regression tests; OCA remains authoritative |
| Read-only evidence, analytic measures and entry-direction guard | `usl_accounting` | runtime foundation | role, analytic and direction-guard tests |
| Hygiene, Closing and Declarations | `usl_accounting_controls` | cohesive feature | focused lifecycle, ACL, company, period and idempotency tests |
| Interactive reports, definitions, PDF/XLSX and OCA report defaults | `usl_accounting_reports` | cohesive feature | report screen/export parity and report presentation tests |
| Electronic-invoice readiness and offline reception evidence | `usl_accounting_einvoice` | cohesive feature | UBL/CII/Factur-X, duplicate, malformed, ACL and live-guard tests |
| Expense claims/batches | `usl_expense_batch` | retained independent feature | clean module and browser tests |
| Overview and cash projections | compatibility module for this stage | uncertain cross-feature boundary, left unchanged | depends on controls, reports and reconstructed evidence |
| Source trace, importer, native replay, parity evidence and reconstruction models | `rebuild_account_migration` | migration-only | canonical harness and idempotent reconstruction gates |
| Existing security, views, actions, menus and seeded definitions | `rebuild_account_migration` | compatibility ownership | XML-ID continuity characterization test |
| User-document controller | compatibility module for this stage | shared delivery, left unchanged | authenticated route and Markdown renderer tests |
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
- New feature modules do not seed copies of existing definitions.
- The compatibility module depends on extracted modules, never the reverse.
- A repeated compatibility-module upgrade must not duplicate definitions or
  business records.
- Removing the compatibility module is not supported while it owns production
  XML IDs and reconstruction fields.
- Migration-only models, permissions and menus remain restricted to technical
  administrators and absent from ordinary Accounting navigation.

## OCA integration boundary

`scripts/sync-oca-addons` is authoritative for source and adaptations. It pins:

| Repository | Commit |
| --- | --- |
| `account-financial-reporting` | `aa34bf33fc96fbae7fb5a2b9609b807b4e20514c` |
| `account-reconcile` | `a9bbab67e42f3b762e9c34b30b6c1a77f9c373fb` |
| `bank-statement-import` | `7c0f95587e3e18f76ad1e8334eb234a41a6c5d7c` |
| `server-ux` | `1372e6489daa3a639d7542f3dcd60af640fb294b` |
| `reporting-engine` | `6692523980cbc57d414935311d7f7bf1c834edc6` |
| `account-financial-tools` | `3b3b3cf0974d5452734090e5a0421e762089de75` |

Tracked patches under `oca-patches/saas-19.2/` are part of that exact
integration. A manifest version adaptation alone is not compatibility
evidence.

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
- Extend one governed Control, Closing or Declaration lifecycle in
  `usl_accounting_controls`.
- Extend statement presentation, filters or exports in
  `usl_accounting_reports`.
- Extend offline readiness, reception evidence or activation safety in
  `usl_accounting_einvoice`.
- Extend expense-claim grouping in `usl_expense_batch`.
- Put source extraction, source tracing, reconstruction and parity-only code
  in `rebuild_account_migration`; never expose it in ordinary UI.
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
8. canonical reconstruction only when importer, source fields or ownership
   changed materially.

