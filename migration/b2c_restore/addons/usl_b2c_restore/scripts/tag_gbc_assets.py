"""Name the Good Boys Club assets, and retire the placeholders that never were.

The company sells more than one thing from one database, so a person filtering
products or orders needs to see which business a record belongs to without
knowing which internal flag marks it. A tag is what does that.

An asset belongs to Good Boys Club when the reconstruction sells it, stocks it,
or made it: a template a channel identity resolves to, a template carrying
reconstructed opening stock, or one under a reserved code. Components count —
the chain and padlock stock a bill of materials consumes is as much a Good Boys
Club asset as the collar it becomes.

The same pass retires the unallocated placeholders an earlier reconstruction
left behind. Those exist only because a colour was once unknown; now that every
variant carries the attribute it was sold with, a placeholder that never held
stock and was never sold is not a product, and leaving it visible invites
someone to sell it.
"""
import json
import os

TAG = "GBC"
apply = os.environ.get("USL_B2C_TAG_MODE", "dry_run") == "apply"

Template = env["product.template"].sudo().with_context(active_test=False)

aliased = env["b2c.product.alias"].sudo().search([]).product_id.product_tmpl_id
stocked = Template.search([("b2c_opening_stock_state", "!=", "not_applicable")])
reserved = Template.search(
    ["|", ("default_code", "=like", "B2C-%"), ("default_code", "=like", "GBC-%")],
)
assets = aliased | stocked | reserved

orders = env["sale.order"].sudo().search([("usl_historical_b2c", "=", True)])


def _tag(model, name):
    tag = env[model].sudo().search([("name", "=", name)], limit=1)
    if not tag and apply:
        tag = env[model].sudo().create({"name": name})
    return tag


product_tag = _tag("product.tag", TAG)
order_tag = _tag("crm.tag", TAG)

untagged = assets.filtered(lambda t: product_tag not in t.product_tag_ids)
untagged_orders = orders.filtered(lambda o: order_tag not in o.tag_ids)

# A placeholder stands for a product nobody could name. Retire it only when it
# is demonstrably empty: never sold, holding nothing, and part of no recipe.
placeholders = Template.search([
    ("b2c_opening_stock_state", "=", "not_evidenced"),
    ("active", "=", True),
])
retirable = placeholders.filtered(
    lambda t: not env["sale.order.line"].sudo().search_count(
        [("product_id", "in", t.product_variant_ids.ids)],
    )
    and not env["stock.quant"].sudo().search_count([
        ("product_id", "in", t.product_variant_ids.ids),
        ("location_id.usage", "=", "internal"),
        ("quantity", "!=", 0),
    ])
    and not env["mrp.bom.line"].sudo().search_count(
        [("product_id", "in", t.product_variant_ids.ids)],
    ),
)
kept = placeholders - retirable

if apply:
    if untagged:
        untagged.write({"product_tag_ids": [(4, product_tag.id)]})
    if untagged_orders:
        untagged_orders.write({"tag_ids": [(4, order_tag.id)]})
    if retirable:
        retirable.write({"active": False})
    env.cr.commit()
    env.invalidate_all()

tagged = Template.search([("product_tag_ids", "in", product_tag.ids)]) if product_tag else Template
print("USL_B2C_TAG_RESULT=" + json.dumps({
    "mode": "apply" if apply else "dry_run",
    "assets": len(assets),
    "from_sales_aliases": len(aliased),
    "from_reconstructed_stock": len(stocked),
    "from_reserved_codes": len(reserved),
    "newly_tagged": len(untagged),
    "tagged_now": len(tagged),
    "tagged_components": len(tagged.filtered(lambda t: not t.sale_ok)),
    "orders": len(orders),
    "orders_newly_tagged": len(untagged_orders),
    "placeholders_retired": sorted(retirable.mapped("default_code")),
    "placeholders_kept": sorted(kept.mapped("default_code")),
}, sort_keys=True))
