# Accounting migration acceptance

The Accounting migration harness converts the frozen Online package into
native Odoo records and deterministic private evidence. It reads the restored
source through a read-only PostgreSQL role and writes target data only through
the target Odoo ORM.

The harness is an internal stage of `migration/manage`; it is not a public
command catalogue. Ordinary Accounting development uses focused module tests
and disposable fixtures.

## Ordered controls

The full reconstruction verifies:

1. source package format, dump and filestore identity;
2. isolated read-only source restore, inspection, and attachment audit;
3. deterministic extraction with per-file digests;
4. clean target initialization and exact historical replay;
5. balance, uniqueness, sequence, lock, currency, tax, reconciliation,
   analytic, expense, asset, deferral, and evidence controls;
6. idempotent repeated import and explicit conflict failure;
7. native current-period workflow proofs on separate disposable databases;
8. reports, drill-downs, exports, FEC, company controls, and multi-company
   isolation;
9. source-target comparison and final product-registry cleanup.

The source database remains read-only throughout. Validation databases are
disposable and never become parallel development environments.

## Evidence

Private artifacts bind source checksum, extraction identity, stage timing,
exact counts and totals, discrepancies, attachment disposition, FEC results,
reports, and final readiness to the runtime release identity. They remain
under ignored `private/` or compatibility evidence directories and are never
committed.

No accepted control may rely on an older source checksum, reconstruction seed,
candidate, or count baseline. A fresh Online package requires fresh evidence.

## Development

Use the smallest relevant commands for product work:

```bash
make accounting-addon-tests
make accounting-multicompany-acceptance
make product-migration-boundary
```

Use a fresh `migration/manage qa refresh` only when a change affects stored
reconstruction output or source interpretation. See
[Accounting development](../operations/accounting-development-workflow.md) and
[Migration operations](../operations/migration.md).
