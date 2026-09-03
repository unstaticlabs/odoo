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
- `QCOLNOP` produces one blue, one green, one pink and one purple individual lock.

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
Quandun placeholder. Both placeholders have no operational role now that the
real colour variants and exact pack recipe exist. Their source identities, cost
history and provider evidence remain available for audit. The active assorted
supplier pack remains a purchasing product and its native unbuild recipe creates
the four evidenced individual colours.

## Theoretical history and physical reconciliation

The one-off native-history materializer uses documented acquisitions, evidenced
delivered consumption and known reservations. It creates 17 dated Purchase
orders and receipts, 12 supplier-pack unbuilds, native manufacturing and
deliveries, and open reservations. It excludes Medusa inventory quantities and
does not infer losses, gifts, damage or a physical count.

The second Quandun batch is allocated from the supplier invoice remark: 25
purple, 25 black, 20 red and 20 blue. Eight documented prototype samples are
recorded as internal consumption. Master packs use their exact two-black,
four-black or four-colour recipes. A missing fulfilment timestamp uses the order
date as an explicit approximation.

Historical Landed Costs are created only for documented relationships: Quandun
freight and duty by quantity, and shared Chonghong chain freight by current
receipt cost. Import VAT remains outside product cost. Because valuation is
periodic/manual, these operations create no Accounting journal entry.

The result is Odoo's theoretical stock source of truth until the 30 September
physical inventory. At that point, record the measured differences through a
native Inventory Adjustment and document their business explanation. Do not
rewrite the reconstructed acquisition or consumption history.

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
