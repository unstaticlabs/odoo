"""What each B2C channel needs before a drop can be turned into commerce.

A channel is where the configuration of an ingest lives: which supplier store
fulfils it, and which product carries the carriage it charges customers.  None
of it belongs in code, because none of it is the same for the next shop.
"""

from odoo import fields, models


class B2cChannel(models.Model):
    _inherit = "b2c.channel"

    printful_store_id = fields.Char(
        index=True,
        copy=False,
        help="The Printful store that fulfils this channel. Configuration, not code.",
    )
    shipping_product_id = fields.Many2one(
        "product.product",
        string="Carriage product",
        check_company=True,
        ondelete="restrict",
        help="The product that carries what customers are charged to ship, so it "
             "reaches the carriage account rather than the revenue account.",
    )

    _company_printful_store_unique = models.Constraint(
        "UNIQUE(company_id, printful_store_id)",
        "A Printful store fulfils one B2C channel per company.",
    )
