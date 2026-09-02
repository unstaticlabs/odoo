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
The Etsy hoodie listing `1838821663` contains two documented catalog
generations. The normalizer keeps the original and summer 2025 products as
separate templates. Etsy later reused `_10780` across several colour/size
combinations, so that value remains immutable source evidence but is explicitly
marked non-unique. It is never assigned as the internal reference of those
later variants; exact title and variation identify them instead.

Master Lock commercial packs remain distinct purchasing products. They are
storable and purchasable, but not saleable. The individual coloured locks are
separate saleable inventory variants and are not directly purchased. Packs with
an exact known composition have native unpacking recipes:

- `TBLK` produces two black individual locks;
- `QBLKNOP` produces four black individual locks;
- `QCOLNOP` has no recipe until reviewed evidence proves its colour mix.

The supplier-pack product form exposes **Unpack supplier pack**, which opens Odoo's
native Unbuild Order with the exact pack recipe. It creates inventory movements
only when an authorized operator validates the order; the migration itself
creates no quantities or movements.

The newer provider inventory export identifies all eight Quandun colours and
five Master Lock colours (Black, Blue, Green, Pink and Purple). It is accepted
as product-identity evidence, but not as an approved physical opening count.
The old Quandun “colours unallocated” template is therefore archived as a
historical source identity; it is not deleted and its cost history is preserved.

The Master Lock assorted-pack holding product is archived alongside the old
Quandun placeholder. Both have zero stock and no historical stock movements, so
neither has an operational role now that the real colour variants exist. Their
source identities, cost history and provider evidence remain available for
audit. The assorted supplier pack remains a purchasing product, but it has no
unpacking recipe until reviewed evidence defines its colour mix; Odoo must not
invent that composition.

## Opening inventory and future configuration

Supplier evidence reconstructs gross acquisitions, including 200 Quandun locks
from the first order, 90 colour-unallocated Quandun locks from the later order,
130 black Master Lock units and 12 assorted Master Lock units. Those figures do
not establish present on-hand stock: sales, gifts, samples, damage and other
movements occurred outside native Inventory. The known 130-versus-39 Master
Lock variance demonstrates why gross purchases cannot be posted as current
quants.

The Online source contains no native historical stock operations and no
approved physical opening count. Before entering stock:

1. obtain and approve a dated physical count;
2. decide which products require lot or serial tracking;
3. review product weights, volumes, packagings and replenishment rules;
4. enter opening quantities through a native inventory adjustment;
5. reconcile the resulting valuation with Accounting;
6. allocate assorted units to colour variants only from that signed count and
   reviewed pack evidence.

Landed Costs is installed but remains unconfigured if no unique restored stock
journal and exact freight, customs or brokerage product exists. A future
configuration must choose the journal, valuation accounts, eligible cost
products and allocation methods from reviewed business evidence. Do not infer
weights, volumes or historical receipts.

Raw AISI 304 chain uses diameter variants; A4/316 remains separate so
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
