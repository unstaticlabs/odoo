# Product master restoration

The product-master stage restores the complete Online service catalog instead
of the expense-only subset required by Accounting. It is a temporary migration
add-on and leaves no migration schema in the delivered product.

The stage restores all source product categories, product templates, variants,
original high-resolution product images,
attributes, sales prices, company-dependent costs, customer and supplier taxes,
units of measure, descriptions, English/French translations, and pricelists.
All 23 source templates have exactly one variant; the importer verifies that
invariant and keeps the stable variant identity used by historical expenses.

One legacy template has no category even though current Odoo requires one. It is
assigned the current Services category, and its source identity is listed in
validation evidence. Enterprise Sales fields that have no installed Community
field (`service_type`, `expense_policy`, and `invoice_policy`) are captured in a
deterministic digest and delegated to the Sales migration scope. They are not
silently presented as translated product data.

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
