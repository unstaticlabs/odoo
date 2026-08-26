# Product master restoration

The product-master stage restores the complete Online service catalog instead
of the expense-only subset required by Accounting. It is a temporary migration
add-on and leaves no migration schema in the delivered product.

The stage restores all source product categories, product templates, variants,
original high-resolution product images, attributes, sales prices,
company-dependent costs and their dates, customer and supplier taxes, units of
measure, descriptions, English/French translations, pricelists, warehouses,
locations, routes, rules, operation types, and native `product.value` cost
history. All 46 source templates have exactly one variant; 45 are active and
one is an archived capability-test artifact. The importer verifies those facts
and keeps the stable variant identity used by historical expenses.

One legacy template has no category even though current Odoo requires one. It is
assigned the current Services category, and its business identity is listed in
private validation evidence. Community Sales is installed before import, so
`service_type`, `reinvoice_policy`, and `invoice_policy` are restored to their
native fields. The 45 source `product.value` rows are restored and validated by
business key and digest rather than being replaced by the current standard
price.

The three GBC Goods subcategories retain average-cost configuration. The stage
does not infer retrospective automated perpetual valuation. The source proves
there are zero stock moves, move lines, pickings, quants, or valuation layers;
it does not prove physical stock is zero. A dated, accountant-approved physical
count is therefore a blocking release input. When supplied, it must be entered
as a native opening inventory adjustment at the approved cutoff. Historical
receipts or deliveries must never be manufactured.

Run in an isolated Compose project after Accounting and Identity:

```bash
COMPOSE_PROJECT_NAME=codex-migration-products \
USL_ONLINE_DUMP_DIR=/absolute/path/to/usl-online-dump \
make product-restore
```

Stepwise commands are `make product-restore-install`,
`make product-restore-import`, `make product-restore-validate`, and
`make product-restore-finalize`. Import can be repeated safely. Validation
compares exact counts and SHA-256 digests for the multilingual catalog,
relations, prices, costs, and taxes. Finalization proves row counts survive
temporary-module removal and runs the product/migration boundary check.
