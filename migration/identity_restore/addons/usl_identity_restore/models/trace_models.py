from odoo import models


class ResPartnerBank(models.Model):
    _name = "res.partner.bank"
    _inherit = ["res.partner.bank", "usl.accounting.restore.source.mixin"]


class ResPartnerCategory(models.Model):
    _name = "res.partner.category"
    _inherit = ["res.partner.category", "usl.accounting.restore.source.mixin"]


class ResPartnerIndustry(models.Model):
    _name = "res.partner.industry"
    _inherit = ["res.partner.industry", "usl.accounting.restore.source.mixin"]


class ResUsers(models.Model):
    _name = "res.users"
    _inherit = ["res.users", "usl.accounting.restore.source.mixin"]
