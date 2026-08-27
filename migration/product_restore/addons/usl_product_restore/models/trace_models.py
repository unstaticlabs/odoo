from odoo import models


class ProductCategory(models.Model):
    _name = "product.category"
    _inherit = ["product.category", "usl.accounting.restore.source.mixin"]


class ProductTemplate(models.Model):
    _name = "product.template"
    _inherit = ["product.template", "usl.accounting.restore.source.mixin"]


class ProductAttribute(models.Model):
    _name = "product.attribute"
    _inherit = ["product.attribute", "usl.accounting.restore.source.mixin"]


class ProductPricelist(models.Model):
    _name = "product.pricelist"
    _inherit = ["product.pricelist", "usl.accounting.restore.source.mixin"]


class ProductValue(models.Model):
    _name = "product.value"
    _inherit = ["product.value", "usl.accounting.restore.source.mixin"]


class StockWarehouse(models.Model):
    _name = "stock.warehouse"
    _inherit = ["stock.warehouse", "usl.accounting.restore.source.mixin"]


class StockLocation(models.Model):
    _name = "stock.location"
    _inherit = ["stock.location", "usl.accounting.restore.source.mixin"]


class StockRoute(models.Model):
    _name = "stock.route"
    _inherit = ["stock.route", "usl.accounting.restore.source.mixin"]


class StockRule(models.Model):
    _name = "stock.rule"
    _inherit = ["stock.rule", "usl.accounting.restore.source.mixin"]


class StockPickingType(models.Model):
    _name = "stock.picking.type"
    _inherit = ["stock.picking.type", "usl.accounting.restore.source.mixin"]
