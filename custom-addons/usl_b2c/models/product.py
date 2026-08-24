from odoo import api, fields, models
from odoo.exceptions import ValidationError

from .constants import FULFILMENT_MODES


class ProductTemplate(models.Model):
    _inherit = "product.template"

    b2c_catalog_classification = fields.Selection(
        [
            ("operational", "Operational catalog"),
            ("capability_test", "Archived capability test"),
            ("legacy", "Legacy catalog"),
            ("unclassified", "Unclassified"),
        ],
        string="B2C Catalog Classification",
        default="unclassified",
        index=True,
        copy=False,
        groups="usl_b2c.group_b2c_reader",
    )
    b2c_fulfilment_mode = fields.Selection(
        FULFILMENT_MODES,
        string="B2C Fulfilment Mode",
        default="unknown",
        index=True,
        copy=False,
        groups="usl_b2c.group_b2c_reader",
    )
    b2c_opening_stock_state = fields.Selection(
        [
            ("not_applicable", "Not applicable"),
            ("not_evidenced", "Not evidenced"),
            ("approved", "Approved count received"),
            ("entered", "Opening adjustment entered"),
        ],
        string="Opening Stock Evidence",
        default="not_applicable",
        index=True,
        copy=False,
        groups="usl_b2c.group_b2c_reader",
        help=(
            "The source contains no stock history. Storable products remain "
            "not evidenced until an approved dated count is entered through "
            "a native inventory adjustment."
        ),
    )

    @api.constrains(
        "b2c_catalog_classification",
        "b2c_fulfilment_mode",
        "b2c_opening_stock_state",
    )
    def _check_b2c_product_classification(self):
        for product in self:
            if not all(
                (
                    product.b2c_catalog_classification,
                    product.b2c_fulfilment_mode,
                    product.b2c_opening_stock_state,
                ),
            ):
                raise ValidationError(
                    self.env._("B2C product classification fields may not be empty."),
                )
