# B2C sales, products and inventory

## Product boundary

USL B2C provides an auditable bridge between incomplete historical commerce
evidence and native Odoo Community operations. The delivered `usl_b2c` add-on
owns stable channels, canonical orders, source lines, payment/refund/fee events,
fulfilment/COGS events, reviewed SKU aliases, monthly accounting sessions, and
links to native evidence. It does not own import runs, source database IDs,
technical row traces, or parity UI.

Future sales, purchase, delivery, valuation, payment, and margin operations use
native Community records. Historical exports remain canonical B2C business
records unless their evidence is complete enough to justify a real native
workflow. A link may point to an existing native sale order, payment,
transaction, journal item, bank transaction, picking, move, purchase order, or
attachment. A missing link stays pending.

The business semantics are fixed:

- journal = financial source or channel;
- account = accounting nature;
- partner = counterparty;
- `b2c.channel` = commercial channel;
- Project/Epic analytics = management attribution only.

Each B2C channel links to the Accounting-owned analytic account in the native
`Channel` plan. B2C neither duplicates native analytic plans nor extends
`usl_platform_billing`, which remains limited to creator/content-platform
payout billing.

## Architecture decision

Three credible treatments were reviewed:

| Alternative | Benefit | Audit and operational consequence | Decision |
| --- | --- | --- | --- |
| A. Materialize every archive row as native sales, payments, and stock moves | Uniform native records | The source contains none of those native transactions; overlapping and incomplete exports would manufacture confirmations, reservations, deliveries, valuations, invoices, or reconciliation | Rejected |
| B. Use native workflows for future and sufficiently complete records; retain incomplete history as canonical B2C records | Preserves evidence and makes gaps visible without creating financial facts | Requires explicit mapping, link, amount, and conversion coverage | Selected |
| C. Retain only aggregate journal totals | Smallest model | Loses order, SKU, country, fulfilment, fee, and refund evidence and prevents source-level audit | Rejected |

The source confirms that B is the only lossless safe option: native Sales,
Purchase, Payment, and Stock transaction counts are all zero; Etsy and Medusa
overlap; current Medusa line evidence lacks immutable provider line IDs and
does not fully allocate every header amount; 35 legacy-only orders remain
header-only; and processor exports span multiple currencies.

## Native and custom choices

The feature deliberately depends on Community `sale_management`, `sale_stock`,
`stock_account`, `purchase`, `purchase_stock`, `sale_margin`, `payment`, and
`account_payment`. `sale_stock_margin` follows its native auto-install contract.
`delivery` supplies future delivery workflow and source-backed customs fields;
`mrp` is required to represent the source's `mrp_operation` operation type.
There is no source BOM, manufacturing order, or quality transaction to invent.
The product install explicitly disables Delivery's install-time Cash on
Delivery provider. No payment provider is activated by this foundation.

`product_margin` was rejected because native sale margin plus valued deliveries
cover the ongoing decision. `website_sale` and provider connectors were rejected
because no eCommerce storefront or live payment integration is required. No
Enterprise substitute or core Odoo patch is introduced.

## Mapping and privacy

An external SKU/listing alias is `pending`, `verified`, or `rejected`. Exact and
fuzzy suggestions are advisory only. Verification requires a user-selected
native product, records the reviewer and evidence note, and never overwrites the
original SKU, item name, variation, or listing ID.

Every provider row is retained in an immutable restricted evidence object with
file checksum, schema digest, payload digest, and optional archive attachment.
Evidence can contain customer PII and is excluded from broad analytics. B2C
readers may inspect structured business records; only the separately scoped
sensitive-evidence group can inspect raw payloads.

Company record rules cover all B2C models. Readers are read-only. Operators may
review mappings, links, and sessions. Managers may configure channels, unlock
sessions, and manage access. Users outside the B2C groups have no model access.

## Inventory position

The restored catalog contains 46 templates and variants, including the archived
capability-test product, and 45 native cost-history rows. Own-stock and
Printful/POD fulfilment are distinct product dimensions. Future own-stock COGS
comes from native valued deliveries where available; provider-evidenced COGS
remains on fulfilment events.

The exact source stock history is zero, but physical opening stock is not
evidenced. Until an approved dated count exists, current quantity and opening
valuation are blocking unknowns. No historical stock operation may be created
to make that gap disappear.
