"""Cost of goods for the stocked products the company makes itself.

A finished unit is worth what went into it. Odoo does that on its own for a
product with a moving cost: when a manufacturing order closes, the finished
move takes the value of the components it consumed, and the delivery that
ships the unit takes the running average. Under standard costing at a zero
standard price both are worth nothing, which is how the reconstructed history
came out: the chains and padlocks left stock at their real cost and the
collars and cuffs made from them entered and left it at zero.

These helpers replay, over an already completed history, what Odoo would have
done with the right cost method. They touch only the value of moves, never
their quantities, dates or state.
"""

from odoo import models


class MrpProduction(models.Model):
    _inherit = "mrp.production"

    def _usl_value_finished_from_components(self):
        """Value each completed production's finished move at what it consumed."""
        for production in self.filtered(lambda record: record.state == "done"):
            finished = production.move_finished_ids.filtered(
                lambda move: move.product_id == production.product_id
                and move.state == "done",
            )
            quantity = sum(move._get_valued_qty() for move in finished)
            if not quantity:
                continue
            consumed = production.move_raw_ids.filtered(
                lambda move: move.state == "done",
            )
            finished.write({"price_unit": sum(consumed.mapped("value")) / quantity})
            finished._set_value()


class StockMove(models.Model):
    _inherit = "stock.move"

    def _usl_value_from_source_production(self):
        """Value each completed outgoing move at the cost of the unit made for it.

        The reconstruction made one production per sold line and shipped that
        unit, so the delivery is worth exactly what the production was. A move
        with no production of its own takes the product's last valued entry
        before it, which is what a running average would have given.
        """
        for move in self.filtered(lambda record: record.state == "done" and record.is_out):
            productions = move.usl_b2c_order_line_id.production_ids.filtered(
                lambda production: production.state == "done"
                and production.product_id == move.product_id,
            )
            source = productions.move_finished_ids.filtered(
                lambda finished: finished.product_id == move.product_id
                and finished.state == "done",
            ) or move.product_id._get_last_in(move.date)
            if not source:
                continue
            move.write({"value": source._get_price_unit() * move._get_valued_qty()})


class ProductProduct(models.Model):
    _inherit = "product.product"

    def _usl_refresh_manufactured_cost(self):
        """Carry each product's last completed production cost onto its standard price.

        With nothing on hand, a moving cost has no stock to average over, so
        the standard price would stay at zero and so would the margin on the
        next order. What the last unit cost to make is the best estimate.
        """
        for product in self:
            last = self.env["mrp.production"].search(
                [("product_id", "=", product.id), ("state", "=", "done")],
                order="date_finished desc, id desc",
                limit=1,
            )
            finished = last.move_finished_ids.filtered(
                lambda move: move.product_id == product and move.state == "done",
            )
            if not finished:
                continue
            unit_cost = finished._get_price_unit()
            product = product.with_company(product.company_id or self.env.company)
            if product.cost_currency_id.compare_amounts(product.standard_price, unit_cost):
                product.write({"standard_price": unit_cost})
