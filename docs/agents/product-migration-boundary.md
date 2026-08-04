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

## Existing transitional exception

`rebuild_account_migration` still owns pre-existing accounting compatibility
models and XML/data identifiers in current reconstruction candidates. That is
documented migration debt, not the target product architecture. Do not extend
it with new product behavior or provenance dependencies. Move stable behavior
to `usl_accounting` and retire the technical ownership through a separately
rehearsed migration before final delivery.

The Projects product module does not depend on that exception. Only the
temporary Projects importer uses it while reconciling source identities.

## Required shape

- Product modules may contain only behavior needed after cutover.
- Product manifests must not depend on migration modules.
- Normal Odoo services must not include migration directories in their
  add-ons paths.
- Temporary migration modules may add source bindings while an import is being
  rehearsed, but finalization must uninstall them.
- Treat each app import as a downstream reconciliation stage: require earlier
  business perimeters to be present and link them by stable identity instead
  of duplicating their data in a later importer.
- Final-state validation must prove that migration models, fields, XML IDs,
  menus and dependencies are absent while imported business records remain.
- Import logs and parity evidence belong in private external artifacts, not in
  the delivered database or repository.
- Apply environment-specific target configuration only after source parity and
  migration finalization. For local development, `make target-reconstruct`
  ends with Pocket ID target finalization on canonical `odoo_dev`.

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

This is selected. It uses the ORM and temporary source bindings during import,
then validates parity and uninstalls itself. A small independent product
module retains only the Planned Start behavior required for ongoing work.

## Agent checklist

1. Identify which changes are ongoing product behavior and which are one-shot
   migration mechanics.
2. Keep product behavior in native/OCA functionality or a focused product
   add-on.
3. Keep migration mechanics outside the normal add-ons path.
4. Validate idempotency before finalization.
5. Finalize and verify that business counts remain stable.
6. Run `make product-migration-boundary`.
7. Test the finalized database using the normal Odoo service and production
   add-ons path only.
