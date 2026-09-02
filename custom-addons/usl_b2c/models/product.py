from odoo import api, fields, models
from odoo.exceptions import UserError, ValidationError

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
    b2c_inventory_role = fields.Selection(
        [
            ("ordinary", "Ordinary product"),
            ("supplier_pack", "Supplier pack to unpack"),
            ("saleable_unit", "Saleable inventory unit"),
            ("pending_allocation", "Internal units awaiting allocation"),
        ],
        string="Inventory Role",
        default="ordinary",
        required=True,
        index=True,
        copy=False,
        groups="usl_b2c.group_b2c_reader",
        help=(
            "Supplier packs remain distinct purchasing products. Unpacking them "
            "creates the individual inventory units defined by their bill of "
            "materials. Pending-allocation products are internal holding identities "
            "and are never customer-facing variants."
        ),
    )

    @api.constrains(
        "b2c_catalog_classification",
        "b2c_fulfilment_mode",
        "b2c_opening_stock_state",
        "b2c_inventory_role",
    )
    def _check_b2c_product_classification(self):
        for product in self:
            if not all(
                (
                    product.b2c_catalog_classification,
                    product.b2c_fulfilment_mode,
                    product.b2c_opening_stock_state,
                    product.b2c_inventory_role,
                ),
            ):
                raise ValidationError(
                    self.env._("B2C product classification fields may not be empty."),
                )


class ProductProduct(models.Model):
    _inherit = "product.product"

    b2c_inventory_role = fields.Selection(
        related="product_tmpl_id.b2c_inventory_role",
    )

    def action_usl_unpack_supplier_pack(self):
        self.ensure_one()
        if self.b2c_inventory_role != "supplier_pack":
            raise UserError(self.env._("Only a configured supplier pack can be unpacked."))
        company = self.company_id or self.env.company
        boms = self.env["mrp.bom"].search(
            [
                ("product_id", "=", self.id),
                ("type", "=", "normal"),
                ("active", "=", True),
                "|",
                ("company_id", "=", company.id),
                ("company_id", "=", False),
            ],
            limit=2,
        )
        if len(boms) != 1:
            raise UserError(
                self.env._(
                    "This supplier pack needs exactly one active unpacking recipe "
                    "before it can be processed."
                ),
            )
        return {
            "type": "ir.actions.act_window",
            "name": self.env._("Unpack %(product)s", product=self.display_name),
            "res_model": "mrp.unbuild",
            "view_mode": "form",
            "target": "current",
            "context": {
                "default_company_id": company.id,
                "default_product_id": self.id,
                "default_product_qty": 1.0,
                "default_uom_id": self.uom_id.id,
                "default_bom_id": boms.id,
            },
        }
