# ruff: noqa: F821, T201

import json

run = env["usl.product.restore.run"].sudo().search([], order="id desc", limit=1)
assert run and run.status == "passed"
models = (
    "product.category",
    "product.template",
    "product.product",
    "product.attribute",
    "product.pricelist",
    "product.value",
    "stock.warehouse",
    "stock.location",
    "stock.route",
    "stock.rule",
    "stock.picking.type",
)
before = {
    model: env[model].sudo().with_context(active_test=False).search_count([])
    for model in models
}
module = env["ir.module.module"].sudo().search([("name", "=", "usl_product_restore")], limit=1)
module.button_immediate_uninstall()
env.cr.commit()
after = {
    model: env[model].sudo().with_context(active_test=False).search_count([])
    for model in models
}
assert before == after
print(json.dumps({"migration_module": "uninstalled", "before": before, "after": after}, indent=2, sort_keys=True))
