# Inventory foundations

## Permanent capabilities

`usl_b2c` installs Odoo Landed Costs and enables Variants, Storage Locations,
Lots and Serial Numbers, and Units of Measure and Packagings. The one-time
setting change uses Odoo's settings API, including its normal warehouse and
internal-transfer side effects. It does not assign Inventory or Accounting
roles.

Existing warehouses, locations, units and costing methods remain unchanged.
No lot, serial number, stock move, quant, opening quantity, valuation entry or
landed-cost record is created by the feature upgrade.

## Product normalization

The versioned catalog normalizer is stored beside the `usl_b2c` upgrade code.
Run it first with `USL_B2C_CATALOG_MODE=dry-run` on a coordinated clone. The
report lists every proposed family, exact blocker, count and financial or stock
fingerprint. Run `apply` only after the dry run is accepted and a coordinated
checkpoint exists.

The normalizer groups products only when identity is exact:

- source internal references identify restored stock products;
- an Etsy listing identifies one Etsy product family;
- a Medusa SKU identifies one provider variant;
- an exact Medusa product name and variation may identify a variant when no SKU
  exists;
- similar titles across Etsy and Medusa remain separate.

The expected catalog result includes native variants for POD apparel and
accessories, stocked chain products, collars, padlocks, and resale packs.
Expense, service and delivery classifications remain separate products.
The explicit `40 mm Quandun padlocks — colours unallocated — May 2026 batch`
and `Master Lock 9120EUR — assorted colours — unallocated physical units`
placeholders remain separate and unchanged. Exact colour variants evidenced
for the ordinary Master Lock physical-unit family are still normalized; the
placeholder is not used as their source.

The Etsy hoodie listing `1838821663` is intentionally blocked: the frozen
evidence assigns conflicting SKUs to the same apparent colour/size
combinations. Resolve that provider evidence before normalizing the family.

## Opening inventory and future configuration

The Online source contains no native historical stock operations and no
approved physical opening count. Before entering stock:

1. obtain and approve a dated physical count;
2. decide which products require lot or serial tracking;
3. review product weights, volumes, packagings and replenishment rules;
4. enter opening quantities through a native inventory adjustment;
5. reconcile the resulting valuation with Accounting.

Landed Costs is installed but remains unconfigured if no unique restored stock
journal and exact freight, customs or brokerage product exists. A future
configuration must choose the journal, valuation accounts, eligible cost
products and allocation methods from reviewed business evidence. Do not infer
weights, volumes or historical receipts.

Master Lock commercial packs retain their distinct ASIN/SKU combinations as
variants. Raw AISI 304 chain uses diameter variants; A4/316 remains separate so
Odoo cannot offer impossible material/diameter combinations. Any later merge,
manufacturing design or cross-channel catalog merge requires explicit product
identity and bill-of-material evidence.

## Safety and recovery

Rehearse the module upgrade and normalization on an isolated coordinated clone.
Before applying it to an authoritative database, stop writers and take a
coordinated Odoo, filestore, Paperless and related-state checkpoint. Admission
requires unchanged Accounting fingerprints, zero historical stock operations,
unchanged provider-evidence and cost-history counts, exact alias mappings, and
an identical repeated run.

If a gate fails before users return, restore the checkpoint and previous image.
After writers reopen, investigate on a clone; do not perform an automatic
destructive rollback.
