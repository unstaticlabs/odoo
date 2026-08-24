# Product and migration boundary

## Rule

The delivered USL Odoo Distribution and its finalized database contain
operational features and business data only. Extraction, source bindings,
reconstruction runs, parity reports and other one-shot migration machinery run
around Odoo, not as permanent product functionality.

`custom-addons/` is the production add-ons path. Migration-only Odoo code lives
under `migration/`, is exposed only to a dedicated Docker service, and is
uninstalled before a reconstructed database is accepted as a product
candidate.

Business history is product data. Tasks, chatter, attachments, followers,
activities and lifecycle dates remain in their native Odoo records. Technical
migration history is external evidence and must not remain in ordinary models
or navigation.

Migration is nevertheless a supported, versioned deliverable of this
distribution repository. The boundary is deployment, not maintainership:
`migration/` and the canonical orchestration scripts must remain tested,
repeatable and documented, while only finalized product modules and native
business records cross into the normal runtime.

## Accounting compatibility ownership

`rebuild_account_migration` is a historical technical name for an installed
USL Accounting product module. It retains stable operational `rebuild.*`
models and XML/data identifiers because changing their database ownership is
unrelated to the user-facing name and would create destructive uninstall and
upgrade risk. It contains no source bindings, importer, replay engine, parity
models or migration UI.

The one-off Accounting importer is `usl_accounting_restore` under
`migration/accounting_restore/addons/`. Only migration and test Compose
profiles can load it. The downstream Projects importer declares it explicitly
while both temporary importers share source identities. Finalization requires
a passed import and no active P0/P1 restoration discrepancy, snapshots native
business facts, uninstalls the module, proves those facts did not change, and
then validates the normal product registry.

The Projects product module does not depend on that exception. Only the
temporary Projects importer depends on the temporary Accounting importer.

Paie TESE follows the same downstream contract. `usl_tese_payroll` is the
ongoing product application. The temporary `usl_tese_restore` importer runs
after Accounting and Projects while Accounting source bindings still exist,
then uninstalls before Accounting finalization. The normal product service
cannot load either temporary add-on.

Platform Billing follows that contract as the next downstream stage.
`usl_platform_billing` is the ongoing product application. The temporary
`usl_platform_billing_restore` importer links reconstructed sessions and
payouts to their existing native accounting moves, validates repeatability,
then uninstalls and removes its physical source columns. The product module
does not depend on either temporary importer.

## Required shape

- Product modules may contain only behavior needed after cutover.
- Product manifests must not depend on migration modules.
- Normal Odoo services must not include migration directories in their
  add-ons paths.
- Temporary migration modules may add source bindings while an import is being
  rehearsed, but finalization must uninstall them.
- Finalization must also remove the physical source-binding columns left by
  those temporary modules; registry and metadata checks alone are insufficient.
- Treat each app import as a downstream reconciliation stage: require earlier
  business perimeters to be present and link them by stable identity instead
  of duplicating their data in a later importer.
- Final-state validation must prove that migration models, fields, XML IDs,
  menus and dependencies are absent while imported business records remain.
- Import logs and parity evidence belong in private external artifacts, not in
  the delivered database or repository.
- Apply environment-specific target configuration only after source parity and
  migration finalization. For local development,
  `make target-reconstruct-product` ends with Pocket ID target finalization on
  canonical `odoo_dev`. Only `make migrate-production SOURCE_SHA=<sha256>` may
  claim source-wide production migration; it requires the strict source and
  attachment gates before the target reset.

## Alternatives considered

### Permanent restoration add-on

This makes repeat imports convenient, but leaks source identifiers, run models
and reconstruction terminology into every future product upgrade. It is
rejected.

### Direct SQL or CSV import

This avoids a permanent add-on but bypasses Odoo invariants and cannot safely
reconcile chatter, followers, activities, attachments and access-sensitive
relationships. It is rejected for the Projects perimeter.

### Temporary Odoo migration add-on

This is selected for both Accounting and Projects. Each importer uses the ORM
and temporary source bindings, validates parity, and uninstalls itself. The
normal registry retains only native business records and ongoing product
features.

## Agent checklist

1. Identify which changes are ongoing product behavior and which are one-shot
   migration mechanics.
2. Keep product behavior in native/OCA functionality or a focused product
   add-on.
3. Keep migration mechanics outside the normal add-ons path.
4. Validate idempotency before finalization.
5. Finalize, remove allow-listed migration columns, and verify that business
   counts remain stable.
6. Run `make product-migration-source-boundary` while iterating without a
   database, then run `make product-migration-boundary` against the finalized
   target. The full target checks registry models/fields/XML IDs, module
   dependencies, installed product versions and physical schema residue.
7. Test the finalized database using the normal Odoo service and production
   add-ons path only.
