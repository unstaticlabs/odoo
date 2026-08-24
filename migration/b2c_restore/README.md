# B2C restore stage

This directory contains the temporary, one-shot B2C reconstruction add-on. It
is not on the delivered add-ons path. See
[`docs/operations/b2c-migration.md`](../../docs/operations/b2c-migration.md) for
the runbook and [`source-field-matrix.md`](source-field-matrix.md) for the
archive contract.

The stage accepts only the locked source dump and attachment manifest, reads it
with `accounting_source_ro`, writes private evidence below ignored `artifacts/`,
and removes its entire registry/database footprint during finalization.
