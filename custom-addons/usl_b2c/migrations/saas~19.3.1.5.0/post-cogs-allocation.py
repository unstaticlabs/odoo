"""Give the reconstructed B2C history its cost of goods.

The reconstruction created the catalog with no category and no cost, so every
sale line it promoted reported its full revenue as margin. The cost was there
all along, in two places.

Print-on-demand products: the supplier's cost sits on the fulfilment events
linked to the lines they shipped. Filing the products under their category and
replaying the allocation that new events now perform on their own gives those
lines their cost.

Products made in house: the chains and padlocks they consumed left stock at
their real average cost, but the finished units entered and left it at zero,
because a product with no category is costed at its standard price and that
was zero. Filing them under the finished-products category gives them a moving
cost; valuing each completed production at its components, each delivery at
the unit made for it, and each product at its last production cost gives the
history and the next order what Odoo would have recorded from the start.
"""

from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    templates = env["product.template"].with_context(active_test=False)

    print_on_demand = env.ref("usl_b2c.product_category_gbc_print_on_demand")
    templates.search(
        [("b2c_fulfilment_mode", "=", "printful"), ("categ_id", "=", False)],
    ).write({"categ_id": print_on_demand.id})

    finished = env.ref("usl_b2c.product_category_gbc_finished_products")
    made = templates.search(
        [
            ("b2c_fulfilment_mode", "=", "own_stock"),
            ("categ_id", "=", False),
            ("bom_ids", "!=", False),
        ],
    )
    made.write({"categ_id": finished.id})
    products = made.with_context(active_test=False).product_variant_ids
    if products:
        env["mrp.production"].search(
            [("product_id", "in", products.ids), ("state", "=", "done")],
            order="date_finished, id",
        )._usl_value_finished_from_components()
        env["stock.move"].search(
            [("product_id", "in", products.ids), ("state", "=", "done"), ("is_out", "=", True)],
            order="date, id",
        )._usl_value_from_source_production()
        products._usl_refresh_manufactured_cost()
        # Lines nothing was delivered for yet take the product's cost, and
        # nothing else on them changed, so ask for the recomputation.
        env["sale.order.line"].search(
            [("product_id", "in", products.ids)],
        )._compute_purchase_price()

    env["b2c.fulfilment.event"].search(
        [("sale_order_line_ids", "!=", False)],
    )._usl_allocate_cogs()
