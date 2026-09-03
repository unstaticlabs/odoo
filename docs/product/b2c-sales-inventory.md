# B2C sales, products and inventory

## Product boundary

USL B2C provides an auditable bridge between incomplete historical commerce
evidence and native Odoo Community operations. The delivered `usl_b2c` add-on
owns stable channels, canonical orders, source lines, payment/refund/fee events,
fulfilment/COGS events, reviewed SKU aliases, monthly accounting sessions, and
links to native evidence. The small delivered `usl_documents_b2c` integration
adds governed Documents links and smart buttons to those business records. It
contains no source matching or import logic. Neither product module owns import
runs, source database IDs, technical row traces, manifests, or parity UI.

Future sales, purchase, delivery, valuation, payment, and margin operations use
native Community records. The reviewed Etsy and Medusa evidence is promoted to
native Sales and Inventory records when its business meaning is deterministic;
the canonical B2C records remain immutable provenance under **B2C Evidence**. A
link may point to an existing native sale order, payment,
transaction, journal item, bank transaction, picking, move, purchase order, or
attachment. A relationship is direct only when unique transaction evidence
supports it. Monthly aggregate Accounting coverage remains visibly separate
from a direct order or event allocation.

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

The source confirms that B is the only lossless safe option. The 304 canonical
orders have deterministic header identity; 269 also have complete item detail.
Those orders become native Sales history while 35 legacy-only orders use an
explicit header-only line. Any Medusa header/line remainder is represented as a
visible provider adjustment. Processor payments remain evidence unless an
immutable identifier supports a unique direct relationship.

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

Coverage and mapping states have precise meanings:

- `verified` means unique evidence supports the exact direct relationship;
- `partial` means a verified aggregate covers the period, but no individual
  allocation is claimed;
- `not_applicable` means the relationship does not apply, including history
  before the 1 October 2025 Accounting cutoff or a locked source package with
  no corresponding provider ledger or defensible catalog match;
- `pending` means an unexplained gap still requires evidence or review;
- `rejected` means a proposed relationship was disproved.

Exact and fuzzy product suggestions are advisory. Verification requires
governed evidence and never overwrites the original SKU, item name, variation,
or listing ID. The one-shot reconstruction may verify only an exact unique
match to the canonical internal reference; every other historical alias keeps
its original evidence and receives an explicit disposition.

Every provider row is retained in an immutable restricted evidence object with
file checksum, schema digest, payload digest, and optional archive attachment.
Evidence can contain customer PII and is excluded from broad analytics. B2C
readers may inspect structured business records; only the separately scoped
sensitive-evidence group can inspect raw payloads.

Company record rules cover all B2C models. Readers are read-only. Operators may
review mappings, links, and sessions. Managers may configure channels, unlock
sessions, and manage access. Users outside the B2C groups have no model access.

## Inventory position

The lossless restore initially contains 46 source templates, each with one
technical variant, including the archived capability-test product, and 45
native cost-history rows. A reviewed post-restore normalization then turns
defensible product families into native variants. It uses exact internal
references, Etsy listing identities, Medusa SKUs, or—when no provider ID
exists—the exact provider product name and variation. Similar names across
providers are not merged without stronger evidence.

The permanent product foundation enables native Variants, Storage Locations,
Lots and Serial Numbers, Units of Measure and Packagings, and Landed Costs.
Feature visibility does not grant Stock Manager or Accounting rights. These
settings are applied on first installation and once during the corresponding
module upgrade; later module updates do not override an administrator's choice.

Own-stock and Printful/POD fulfilment remain distinct product dimensions.
Printful products are non-storable unless reviewed evidence says otherwise.
Padlocks, raw chain and finished stocked products remain storable, but tracking
stays disabled until a product-level traceability policy exists. Future
own-stock COGS comes from native valued deliveries where available;
provider-evidenced COGS remains on fulfilment events.

The Online database contained no native stock history. Reviewed supplier bills,
provider orders, fulfilment events and reservations nevertheless support a
theoretical history: documented acquisitions minus evidenced delivered
consumption minus known reservations. The reconstruction creates dated native
purchases, receipts, supplier-pack unbuilds, manufacturing, deliveries and
reservations from that evidence only. It never uses Medusa inventory snapshots.
The result is an operational theoretical balance, not a signed physical count;
the 30 September count remains the native reconciliation point.

The normalization preserves raw provider rows, original titles, variations,
SKUs, linked documents and cost-history row identity. It may advance a reviewed
alias from `not_applicable` to `verified`, but never rewrites the source fields.
Commercial supplier packs and individual saleable locks are separate products.
Known pack contents use exact native unpacking recipes, including one blue, one
green, one pink and one purple lock for each assorted four-lock Master pack. The
former pending-colour products are archived because
they have no stock or historical movements; their source identities and evidence
remain available for audit. Gross purchased quantities are evidence, not current
stock.

When a provider reuses one generic SKU for several variants, the source SKU is
preserved but marked non-unique. The alias then uses the exact source product
generation and variation. Etsy listing `1838821663` follows this rule for the
later `_10780` records, while its original catalog generation remains separate.

## Promoted historical operations

Native Sales is the normal operational view. Reconstructed completed orders are
locked and all historical orders are permanently non-invoiceable because their
Accounting was restored independently. The nine known outstanding fulfilments
remain open and reserve native stock. A product-administrator setting can allow
customer-facing messages from historical orders, but it is disabled by default.
The import itself creates no followers or outbound mail.

Native Contacts are company-scoped and keyed by exact provider identity or exact
normalized recipient and address. Names alone are never merged. Native Purchase
lines retain direct vendor-bill evidence without changing posted Accounting.
Printful fulfilment references are normalized only by their evidenced prefixes
and link back to canonical and native Sales records; POD items never create
native stock movements.
