# B2C restore stage

This directory contains the temporary, one-shot B2C reconstruction add-on. It
is not on the delivered add-ons path. Use the repository-level
[`migration/manage`](../manage) interface and see
[`source-field-matrix.md`](source-field-matrix.md) for the archive contract.

The stage accepts the locked source dump/attachment manifest as primary truth
and one explicitly declared post-dump Medusa sold-items export. The supplemental
file must be placed privately at
`artifacts/b2c-restore/source/medusa-sold-items-2026-08-05.csv`; its filename,
size, SHA-1, SHA-256, schema and row baselines are all blocking checks. The
stage reads the Odoo source with `accounting_source_ro`, writes private evidence
below ignored `artifacts/`, and removes its entire registry/database footprint
during finalization.
