# Accounting restoration

This directory contains the one-off, versioned Accounting importer for the
USL Odoo Distribution. It is maintained and tested with the product, but it is
not a delivered Odoo product add-on.

`usl_accounting_restore` is available only through migration and test Compose
profiles. During restoration it provides source bindings, replay services and
parity records needed to reconstruct the Online snapshot through Odoo's ORM.
The downstream Projects importer temporarily depends on the same source
identity layer. The normal `odoo`, `init-db` and Dev Container add-ons paths
cannot load it.

The canonical lifecycle is:

```text
reset target → install temporary importer → import → validate
→ restore downstream Projects → uninstall importers
→ validate product-only registry → apply target configuration
```

Run the complete lifecycle with:

```bash
make target-reconstruct
```

For focused development of this importer:

```bash
make accounting-restore-tests
scripts/accounting-restore finalize
scripts/accounting-restore product-validate
```

Finalization requires the latest Accounting import run to have passed and no
active P0/P1 restoration discrepancy. It compares native business counts and
posted totals before and after uninstalling the temporary module. The final
validator then rejects any remaining migration model, source field, metadata,
view or XML ID.

Evidence files staged on temporary asset snapshots are reassigned to native
`account.asset` records during native asset replay. Finalization compares the
complete attachment count so importer removal cannot silently delete them.

Import/parity logs and source identifiers are technical evidence. They remain
in ignored private artifacts and are not copied into the finalized database.
Native accounting records, chatter and attachments remain as operational
business history.
