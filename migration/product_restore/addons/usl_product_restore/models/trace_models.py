from odoo import models


class ProductCategory(models.Model):
    _name = "product.category"
    _inherit = ["product.category", "rebuild.source.trace.mixin"]


class ProductTemplate(models.Model):
    _name = "product.template"
    _inherit = ["product.template", "rebuild.source.trace.mixin"]


class ProductAttribute(models.Model):
    _name = "product.attribute"
    _inherit = ["product.attribute", "rebuild.source.trace.mixin"]


class ProductPricelist(models.Model):
    _name = "product.pricelist"
    _inherit = ["product.pricelist", "rebuild.source.trace.mixin"]
